"""Small async client for the OANDA REST-v20 API."""

import asyncio
from collections.abc import Sequence
from collections.abc import AsyncIterator
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
from freqtrade.forex.state import OandaAccountState, OandaPosition
from freqtrade.forex.transactions import OandaTransaction


class OandaAPIError(RuntimeError):
    """Raised when OANDA rejects an API request."""


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
        while remaining > 0:
            page_count = min(remaining, 5000)
            params = {"granularity": granularity, "price": price, "count": page_count}
            if page_from is not None:
                params["from"] = self._normalize_time(page_from)
            if to_time is not None:
                params["to"] = self._normalize_time(to_time)
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
            if page_from is not None and filtered and filtered[0].time == page_from:
                filtered = filtered[1:]
            if not filtered:
                break
            collected.extend(filtered)
            remaining -= len(filtered)
            if remaining <= 0:
                break
            if to_time is not None:
                break
            if page_from is not None and len(filtered) < page_count:
                break
            page_from = self._normalize_time(filtered[-1].time)
        return collected[:count]

    async def get_prices(self, instruments: Sequence[str]) -> list[OandaPrice]:
        payload = await self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": ",".join(instruments)},
        )
        return [OandaPrice.from_payload(item) for item in payload["prices"]]

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
    ) -> OandaOrderResult:
        if units == 0:
            raise ValueError("OANDA order units cannot be zero")
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
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
    ) -> OandaOrderActionResult:
        if not order_id:
            raise ValueError("order_id is required")
        order: dict[str, Any] = {}
        if price is not None:
            order["price"] = price
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
