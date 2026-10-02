"""Small async client for the OANDA REST-v20 API."""

import asyncio
from collections.abc import Sequence
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
import json
from typing import Any

import httpx

from freqtrade.forex.models import (
    OandaCandle,
    OandaEnvironment,
    OandaInstrument,
    OandaOrderResult,
    OandaOrderActionResult,
    OandaPrice,
)
from freqtrade.forex.order_validation import BrokerOrderValidator
from freqtrade.forex.state import OandaAccountState, OandaPosition
from freqtrade.forex.transactions import OandaTransaction


class OandaAPIError(RuntimeError):
    """Raised when OANDA rejects an API request."""


class OandaMarketNotTradeableError(OandaAPIError):
    """Raised when OANDA marks the instrument as non-tradeable or halted."""


_SUPPORTED_ACCOUNT_TAGS = {
    "CFD": ("003", "CFD"),
    "SPREAD_BETTING": ("002", "Spread Betting"),
}


def classify_oanda_account(tags: Any) -> dict[str, str] | None:
    """Return the supported bot account class for OANDA's account tags."""
    if isinstance(tags, str):
        normalized_tags = {tags.strip().upper()}
    elif isinstance(tags, Sequence | set | frozenset):
        normalized_tags = {str(tag).strip().upper() for tag in tags}
    else:
        normalized_tags = set()

    for tag, (code, label) in _SUPPORTED_ACCOUNT_TAGS.items():
        if tag in normalized_tags:
            return {"code": code, "label": label}
    return None


async def discover_oanda_accounts(
    token: str,
    environment: OandaEnvironment,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Discover supported accounts using read-only OANDA GET endpoints."""
    if not token.strip():
        raise ValueError("OANDA token is required")

    client = http_client or httpx.AsyncClient(
        base_url=environment.rest_url,
        headers={"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"},
        timeout=20.0,
    )
    owns_client = http_client is None
    try:
        response = await client.get("/v3/accounts")
        if response.is_error:
            raise OandaAPIError(f"OANDA account discovery failed with HTTP {response.status_code}")
        payload = response.json()
        accounts: list[dict[str, Any]] = []
        excluded_count = 0
        for account in payload.get("accounts", []):
            account_id = str(account.get("id", "")).strip()
            tags = classify_tags(account.get("tags", []))
            account_type = classify_oanda_account(tags)
            practice_v20_candidate = (
                environment is OandaEnvironment.PRACTICE and not tags
            )
            if not account_id or (account_type is None and not practice_v20_candidate):
                excluded_count += 1
                continue

            summary: dict[str, Any] | None = None
            summary_response = await client.get(f"/v3/accounts/{account_id}/summary")
            if not summary_response.is_error:
                summary_payload = summary_response.json().get("account", {})
                summary = {
                    key: summary_payload[key]
                    for key in (
                        "alias",
                        "currency",
                        "balance",
                        "NAV",
                        "marginAvailable",
                    )
                    if key in summary_payload
                }

            instrument_count: int | None = None
            if practice_v20_candidate:
                instruments_response = await client.get(
                    f"/v3/accounts/{account_id}/instruments"
                )
                if not instruments_response.is_error:
                    instruments = instruments_response.json().get("instruments", [])
                    instrument_count = len(instruments)
                if summary is not None and instrument_count:
                    account_type = {"code": "PRACTICE", "label": "Practice / V20"}
                else:
                    excluded_count += 1
                    continue

            if account_type is None:
                excluded_count += 1
                continue

            accounts.append(
                {
                    "accountId": account_id,
                    "accountTypeCode": account_type["code"],
                    "accountType": account_type["label"],
                    "tags": sorted(tags),
                    "summary": summary,
                    "summaryAccessible": summary is not None,
                    "instrumentCount": instrument_count,
                }
            )
        return {"accounts": accounts, "excludedAccountCount": excluded_count}
    except httpx.RequestError as exc:
        raise OandaAPIError("Could not connect to the selected OANDA environment") from exc
    except (ValueError, KeyError) as exc:
        raise OandaAPIError("OANDA returned an invalid account discovery response") from exc
    finally:
        if owns_client:
            await client.aclose()


def classify_tags(tags: Any) -> set[str]:
    if isinstance(tags, str):
        return {tags.strip().upper()}
    if isinstance(tags, Sequence):
        return {str(tag).strip().upper() for tag in tags}
    return set()


class OandaClient:
    def __init__(
        self,
        token: str,
        account_id: str,
        environment: OandaEnvironment = OandaEnvironment.PRACTICE,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        retry_backoff: float = 0.25,
    ) -> None:
        if not token:
            raise ValueError("OANDA API token is required")
        if not account_id:
            raise ValueError("OANDA account ID is required")
        self.account_id = account_id
        self.environment = environment
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._client = http_client or httpx.AsyncClient(
            base_url=environment.rest_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._owns_client = http_client is None

    async def __aenter__(self) -> "OandaClient":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_instruments(
        self, instruments: Sequence[str] | None = None
    ) -> list[OandaInstrument]:
        params = {"instruments": ",".join(instruments)} if instruments else None
        payload = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/instruments", params=params
        )
        return [OandaInstrument.from_payload(item) for item in payload["instruments"]]

    @staticmethod
    def _normalize_time(value: str) -> str:
        return value.replace(".000000000Z", "Z").replace(".000000Z", "Z")

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        *,
        count: int | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        price: str = "M",
    ) -> list[OandaCandle]:
        if count is None:
            count = 500

        if count <= 5000:
            params: dict[str, Any] = {"granularity": granularity, "price": price}
            if count is not None:
                params["count"] = count
            if from_time is not None:
                params["from"] = self._normalize_time(from_time)
            if to_time is not None:
                params["to"] = self._normalize_time(to_time)
            payload = await self._request(
                "GET", f"/v3/instruments/{instrument}/candles", params=params
            )
            return [OandaCandle.from_payload(item) for item in payload["candles"]]

        collected: list[OandaCandle] = []
        seen_times: set[str] = set()
        remaining = count
        page_from = from_time
        page_to = to_time
        fetch_older = from_time is None
        while remaining > 0:
            page_count = min(remaining + int(fetch_older and bool(collected)), 5000)
            params = {"granularity": granularity, "price": price, "count": page_count}
            if page_from is not None:
                params["from"] = self._normalize_time(page_from)
            if page_to is not None:
                params["to"] = self._normalize_time(page_to)
            payload = await self._request(
                "GET", f"/v3/instruments/{instrument}/candles", params=params
            )
            page = [OandaCandle.from_payload(item) for item in payload["candles"]]
            if not page:
                break
            filtered: list[OandaCandle] = []
            for candle in page:
                if candle.time in seen_times:
                    continue
                seen_times.add(candle.time)
                filtered.append(candle)
            if not filtered:
                break
            if fetch_older:
                collected = filtered + collected
            else:
                collected.extend(filtered)
            remaining -= len(filtered)
            if remaining <= 0:
                break
            if fetch_older:
                page_to = self._normalize_time(collected[0].time)
            elif to_time is not None:
                break
            else:
                page_from = self._normalize_time(filtered[-1].time)
        return sorted(collected, key=lambda candle: candle.time)[-count:]

    async def get_prices(self, instruments: Sequence[str]) -> list[OandaPrice]:
        payload = await self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": ",".join(instruments)},
        )
        return [OandaPrice.from_payload(item) for item in payload["prices"]]

    async def ensure_tradeable(self, instrument: str) -> OandaPrice:
        prices = await self.get_prices((instrument,))
        if not prices:
            raise OandaMarketNotTradeableError(f"OANDA pricing is unavailable for {instrument}.")
        price = prices[0]
        if not price.tradeable:
            raise OandaMarketNotTradeableError(
                f"Order rejected: instrument {instrument} is not tradeable right now "
                "(OANDA market halted, closed, or otherwise non-tradeable)."
            )
        return price

    async def get_account_summary(self) -> OandaAccountState:
        payload = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/summary"
        )
        return OandaAccountState.from_payload(payload)

    async def get_open_positions(self) -> list[OandaPosition]:
        payload = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/openPositions"
        )
        return [OandaPosition.from_payload(item) for item in payload["positions"]]

    async def get_open_trades(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/openTrades"
        )
        return payload.get("trades", [])

    async def get_closed_trades(self, *, count: int = 100) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/trades",
            params={"state": "CLOSED", "pageSize": max(1, min(count, 500))},
        )
        return payload.get("trades", [])

    async def get_pending_orders(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/pendingOrders"
        )
        return payload.get("orders", [])

    async def close_trade(self, trade_id: str) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/v3/accounts/{self.account_id}/trades/{trade_id}/close",
            json={"units": "ALL"},
        )

    async def modify_trade_orders(
        self,
        trade_id: str,
        *,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        trailing_stop_loss_distance: str | None = None,
    ) -> dict[str, Any]:
        if not trade_id:
            raise ValueError("trade_id is required")
        if stop_loss_price is not None and trailing_stop_loss_distance is not None:
            raise ValueError(
                "A fixed stop loss and a trailing stop loss cannot be set simultaneously"
            )
        if trailing_stop_loss_distance is not None:
            try:
                distance = Decimal(trailing_stop_loss_distance)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(
                    "trailing_stop_loss_distance must be a positive number"
                ) from exc
            if not distance.is_finite() or distance <= 0:
                raise ValueError(
                    "trailing_stop_loss_distance must be a positive number"
                )
        orders: dict[str, Any] = {}
        if stop_loss_price is not None:
            orders["stopLoss"] = {"timeInForce": "GTC", "price": stop_loss_price}
        if take_profit_price is not None:
            orders["takeProfit"] = {"timeInForce": "GTC", "price": take_profit_price}
        if trailing_stop_loss_distance is not None:
            orders["stopLoss"] = None
            orders["trailingStopLoss"] = {"distance": str(distance)}
        if not orders:
            raise ValueError("at least one protective order is required")
        return await self._request(
            "PUT",
            f"/v3/accounts/{self.account_id}/trades/{trade_id}/orders",
            json=orders,
        )

    async def iter_transactions(
        self, *, since_transaction_id: str | None = None
    ) -> AsyncIterator[OandaTransaction]:
        params: dict[str, str] = {
            "type": "ORDER_CREATE,ORDER_FILL,ORDER_CANCEL,ORDER_REJECT,ORDER_CANCEL_REJECT"
        }
        if since_transaction_id is not None:
            params["sinceTransactionID"] = since_transaction_id
        path = f"/v3/accounts/{self.account_id}/transactions/stream"
        async with self._client.stream("GET", path, params=params) as response:
            if response.is_error:
                raise OandaAPIError(f"OANDA {response.status_code}: {response.text}")
            async for line in response.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("type") in {"HEARTBEAT", "DISCONNECTED"}:
                    continue
                yield OandaTransaction.from_payload(payload)

    async def create_market_order(
        self,
        instrument: str,
        units: int,
        *,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        client_order_id: str | None = None,
        trade_client_extensions: dict[str, str] | None = None,
    ) -> OandaOrderResult:
        if units == 0:
            raise ValueError("OANDA order units cannot be zero")
        instruments = await self.get_instruments((instrument,))
        if not instruments:
            raise ValueError(f"Instrument {instrument} is not available on this OANDA account")
        broker_instrument = instruments[0]
        quote = await self.ensure_tradeable(instrument)
        validator = BrokerOrderValidator(
            broker_instrument,
            minimum_stop_distance=Decimal("0"),
        )
        validator.validate_units(abs(units))
        order_side = "long" if units > 0 else "short"
        entry_price = quote.price_for_side(order_side)
        try:
            if stop_loss_price is not None:
                validator.validate_stop(
                    side=order_side,
                    entry_price=entry_price,
                    stop_price=Decimal(stop_loss_price),
                )
            if take_profit_price is not None:
                validator.validate_take_profit(
                    side=order_side,
                    entry_price=entry_price,
                    take_profit_price=Decimal(take_profit_price),
                )
        except InvalidOperation as exc:
            raise ValueError("Protective order prices must be valid decimal numbers") from exc
        order: dict[str, Any] = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
        }
        if stop_loss_price is not None:
            order["stopLossOnFill"] = {"timeInForce": "GTC", "price": stop_loss_price}
        if take_profit_price is not None:
            order["takeProfitOnFill"] = {"timeInForce": "GTC", "price": take_profit_price}
        if client_order_id is not None:
            order["clientExtensions"] = {"id": client_order_id}
        if trade_client_extensions is not None:
            order["tradeClientExtensions"] = trade_client_extensions
        payload = await self._request(
            "POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order}
        )
        return OandaOrderResult.from_payload(payload)

    async def create_stop_order(
        self,
        instrument: str,
        units: int,
        price: str,
        *,
        client_order_id: str | None = None,
    ) -> OandaOrderResult:
        return await self._create_pending_order(
            "STOP", instrument, units, price, client_order_id=client_order_id
        )

    async def create_take_profit_order(
        self,
        instrument: str,
        units: int,
        price: str,
        *,
        client_order_id: str | None = None,
    ) -> OandaOrderResult:
        return await self._create_pending_order(
            "TAKE_PROFIT", instrument, units, price, client_order_id=client_order_id
        )

    async def create_limit_order(
        self,
        instrument: str,
        units: int,
        price: str,
        *,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        client_order_id: str | None = None,
        trade_client_extensions: dict[str, str] | None = None,
    ) -> OandaOrderResult:
        if units == 0:
            raise ValueError("OANDA order units cannot be zero")
        try:
            limit_price = Decimal(price)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("OANDA limit price must be a valid decimal number") from exc
        if not limit_price.is_finite() or limit_price <= 0:
            raise ValueError("OANDA limit price must be positive")
        instruments = await self.get_instruments((instrument,))
        if not instruments:
            raise ValueError(f"Instrument {instrument} is not available on this OANDA account")
        validator = BrokerOrderValidator(
            instruments[0],
            minimum_stop_distance=Decimal("0"),
        )
        validator.validate_units(abs(units))
        validator.validate_price(limit_price, "limit price")
        order_side = "long" if units > 0 else "short"
        try:
            if stop_loss_price is not None:
                validator.validate_stop(
                    side=order_side,
                    entry_price=limit_price,
                    stop_price=Decimal(stop_loss_price),
                )
            if take_profit_price is not None:
                validator.validate_take_profit(
                    side=order_side,
                    entry_price=limit_price,
                    take_profit_price=Decimal(take_profit_price),
                )
        except InvalidOperation as exc:
            raise ValueError("Protective order prices must be valid decimal numbers") from exc
        order: dict[str, Any] = {
            "type": "LIMIT",
            "instrument": instrument,
            "units": str(units),
            "price": price,
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
        }
        if stop_loss_price is not None:
            order["stopLossOnFill"] = {"timeInForce": "GTC", "price": stop_loss_price}
        if take_profit_price is not None:
            order["takeProfitOnFill"] = {"timeInForce": "GTC", "price": take_profit_price}
        if client_order_id is not None:
            order["clientExtensions"] = {"id": client_order_id}
        if trade_client_extensions is not None:
            order["tradeClientExtensions"] = trade_client_extensions
        payload = await self._request(
            "POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order}
        )
        return OandaOrderResult.from_payload(payload)

    async def _create_pending_order(
        self,
        order_type: str,
        instrument: str,
        units: int,
        price: str,
        *,
        client_order_id: str | None,
    ) -> OandaOrderResult:
        if units == 0:
            raise ValueError("OANDA order units cannot be zero")
        order: dict[str, Any] = {
            "type": order_type,
            "instrument": instrument,
            "units": str(units),
            "price": price,
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
        }
        if client_order_id is not None:
            order["clientExtensions"] = {"id": client_order_id}
        payload = await self._request(
            "POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order}
        )
        return OandaOrderResult.from_payload(payload)

    async def modify_order(
        self,
        order_id: str,
        *,
        price: str | None = None,
        units: int | None = None,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
    ) -> OandaOrderActionResult:
        if not order_id:
            raise ValueError("order_id is required")
        order: dict[str, Any] = {}
        if price is not None:
            order["price"] = price
        if units is not None:
            if units == 0:
                raise ValueError("OANDA order units cannot be zero")
            order["units"] = str(units)
        if stop_loss_price is not None:
            order["stopLossOnFill"] = {"timeInForce": "GTC", "price": stop_loss_price}
        if take_profit_price is not None:
            order["takeProfitOnFill"] = {"timeInForce": "GTC", "price": take_profit_price}
        if not order:
            raise ValueError("at least one order field is required")
        payload = await self._request(
            "PUT", f"/v3/accounts/{self.account_id}/orders/{order_id}", json={"order": order}
        )
        return OandaOrderActionResult.from_payload(payload)

    async def cancel_order(self, order_id: str) -> OandaOrderActionResult:
        if not order_id:
            raise ValueError("order_id is required")
        payload = await self._request(
            "DELETE", f"/v3/accounts/{self.account_id}/orders/{order_id}"
        )
        return OandaOrderActionResult.from_payload(payload)

    @staticmethod
    def _is_retryable(status_code: int, detail: Any) -> bool:
        if status_code in {408, 429, 500, 502, 503, 504}:
            return True
        if isinstance(detail, dict):
            code = detail.get("errorCode") or detail.get("code")
            return str(code).upper() in {"RATE_LIMITED", "TIMEOUT", "SERVER_ERROR", "TOO_MANY_REQUESTS"}
        return False

    def _retry_delay(self, status_code: int, attempt: int) -> float:
        if status_code == 429:
            retry_after = self._client.headers.get("Retry-After") if self._client else None
            if retry_after is not None:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
        return self.retry_backoff * (2**attempt)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self.max_retries:
                    raise OandaAPIError(f"OANDA transport error: {exc}") from exc
                await asyncio.sleep(self._retry_delay(0, attempt))
                attempt += 1
                continue
            if not response.is_error:
                return response.json()
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            if not self._is_retryable(response.status_code, detail) or attempt >= self.max_retries:
                raise OandaAPIError(f"OANDA {response.status_code}: {detail}")
            await asyncio.sleep(self._retry_delay(response.status_code, attempt))
            attempt += 1
