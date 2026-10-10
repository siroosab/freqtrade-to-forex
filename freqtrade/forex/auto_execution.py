"""Execute approved strategy signals against the selected OANDA account."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

import pandas as pd

from freqtrade.forex.config import OandaSettings
from freqtrade.forex.models import OandaInstrument, OandaPrice
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.risk import units_for_fixed_risk
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    approved_stop_distance_pips,
    freqtrade_timeframe,
    load_strategy,
    oanda_granularity,
    strategy_informative_candle_count,
    strategy_informative_timeframes,
)
from freqtrade.forex.strategy_loop import Signal


_AUTO_TRADE_TAG = "auto"


class AutoExecutionError(ValueError):
    """Raised when a signal cannot safely be converted into an OANDA order."""


def _risk_with_approved_stop_loss(
    risk: dict[str, object],
    approved_stop_loss: object,
    *,
    use_approved_setting: bool = False,
) -> dict[str, object]:
    if use_approved_setting and isinstance(approved_stop_loss, dict):
        mode = approved_stop_loss.get("mode")
        if mode in {"pips", "percent"}:
            try:
                value = Decimal(str(approved_stop_loss.get("value", "")))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise AutoExecutionError("Approved trailing-stop distance is invalid") from exc
            if not value.is_finite() or value <= 0:
                raise AutoExecutionError("Approved trailing-stop distance must be positive")
            return {**risk, "stopLoss": str(value), "stopLossMode": str(mode)}
    try:
        stop_pips = approved_stop_distance_pips(approved_stop_loss)
    except ValueError as exc:
        raise AutoExecutionError(str(exc)) from exc
    if stop_pips is None:
        return risk
    return {**risk, "stopLoss": str(stop_pips), "stopLossMode": "pips"}


class OandaAutoStrategyExecutor:
    """Evaluate approved strategies and submit protected, risk-sized market orders."""

    def __init__(
        self,
        client: OandaClient,
        settings: OandaSettings,
        setup: dict[str, Any],
        risk_configs: dict[str, dict[str, object]],
        *,
        last_processed: dict[str, str] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        if settings.environment.value not in {"practice", "live"}:
            raise AutoExecutionError(
                "Automatic orders require the configured Practice or Live account"
            )
        if settings.execution_mode not in {"practice", "live"}:
            raise AutoExecutionError(
                "Automatic orders require the configured Practice or Live mode"
            )
        if settings.environment.value != settings.execution_mode:
            raise AutoExecutionError("Configured OANDA environment and execution mode do not match")
        self.client = client
        self.settings = settings
        self.setup = setup
        self.risk_configs = risk_configs
        self.last_processed = last_processed if last_processed is not None else {}
        self.checkpoint = checkpoint

    async def run_cycle(self) -> list[dict[str, object]]:
        instruments = tuple(self.settings.instruments)
        if not instruments:
            raise AutoExecutionError("Setup must contain at least one instrument")

        account = await self.client.get_account_summary()
        open_trades = await self.client.get_open_trades()
        results: list[dict[str, object]] = []
        for instrument in instruments:
            pair = instrument.replace("_", "/").upper()
            timeframe = str(self.setup.get("pair_timeframes", {}).get(instrument, ""))
            try:
                revision = self._approved_revision(instrument, pair)
                timeframe = str(revision["timeframe"])
                result = await self._run_pair(
                    instrument,
                    pair,
                    timeframe,
                    revision,
                    account,
                    open_trades,
                )
            except (AutoExecutionError, OSError, InvalidOperation, ValueError) as exc:
                result = {
                    "pair": pair,
                    "timeframe": timeframe,
                    "status": "blocked",
                    "reason": str(exc),
                }
            results.append(result)
        return results

    def _approved_revision(self, instrument: str, pair: str) -> dict[str, Any]:
        pair_key = instrument.upper().replace("/", "_")
        revisions = self.setup.get("pair_approved_revisions", {})
        revision = revisions.get(pair_key) if isinstance(revisions, dict) else None
        configured_strategies = self.setup.get("pair_strategies", {})
        configured_timeframes = self.setup.get("pair_timeframes", {})
        configured_strategy = (
            configured_strategies.get(pair_key) if isinstance(configured_strategies, dict) else None
        )
        configured_timeframe = (
            configured_timeframes.get(pair_key) if isinstance(configured_timeframes, dict) else None
        )
        if not isinstance(revision, dict):
            raise AutoExecutionError(f"{pair} has no approved strategy revision")
        strategy_name = str(revision.get("strategyClass", ""))
        timeframe = str(revision.get("timeframe", ""))
        if (
            revision.get("pair") != pair
            or strategy_name != configured_strategy
            or not timeframe
            or freqtrade_timeframe(timeframe)
            != freqtrade_timeframe(str(configured_timeframe or ""))
        ):
            raise AutoExecutionError(f"{pair} strategy configuration is not the approved revision")
        return revision

    async def _run_pair(
        self,
        instrument: str,
        pair: str,
        timeframe: str,
        revision: dict[str, Any],
        account: Any,
        open_trades: list[dict[str, Any]],
    ) -> dict[str, object]:
        granularity = oanda_granularity(freqtrade_timeframe(timeframe))
        raw_candles = await self.client.get_candles(
            instrument,
            granularity,
            count=500,
        )
        candles = _candle_frame(raw_candles)
        if candles.empty:
            raise AutoExecutionError(f"No completed candles returned for {pair}")

        parameters = revision.get("hyperopt")
        parameters = parameters if isinstance(parameters, dict) else {}
        parameter_values = parameters.get("parameters", parameters.get("bestParameters", {}))
        minimal_roi = parameters.get("minimal_roi", parameters.get("bestMinimalRoi"))
        strategy = load_strategy(
            str(revision["strategyClass"]),
            freqtrade_timeframe(timeframe),
            pair,
            parameter_values=parameter_values if isinstance(parameter_values, dict) else None,
            minimal_roi=minimal_roi if isinstance(minimal_roi, dict) else None,
        )
        informative_candles: dict[str, pd.DataFrame] = {}
        for informative_timeframe in strategy_informative_timeframes(strategy, pair):
            count = strategy_informative_candle_count(strategy, informative_timeframe, len(candles))
            raw_informative = await self.client.get_candles(
                instrument,
                oanda_granularity(informative_timeframe),
                count=min(count, 5000),
            )
            informative_candles[informative_timeframe] = _candle_frame(raw_informative)

        adapter = FreqtradeStrategyAdapter(strategy, pair, informative_candles)
        signal = adapter.signal(candles)
        last_candle = candles.iloc[-1]
        candle_time = pd.Timestamp(last_candle["date"]).isoformat()
        if self.last_processed.get(instrument) == candle_time:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "already_processed",
            }
        self.last_processed[instrument] = candle_time
        if self.checkpoint:
            self.checkpoint()

        risk = self.risk_configs.get(pair, {})
        panel_stop_loss = risk.get("stopLoss")
        trailing_stop_loss = (
            parameters.get("trailingStopLoss") is True
            and (panel_stop_loss is None or not str(panel_stop_loss).strip())
        )
        if panel_stop_loss is None or not str(panel_stop_loss).strip():
            risk = _risk_with_approved_stop_loss(
                risk,
                parameters.get("stopLoss"),
                use_approved_setting=trailing_stop_loss,
            )
        price_values = await self.client.get_prices((instrument,))
        if not price_values:
            raise AutoExecutionError(f"OANDA returned no current quote for {pair}")
        price = price_values[0]
        if not price.tradeable:
            raise AutoExecutionError(f"{pair} is not currently tradeable")
        strategy_trades = [trade for trade in open_trades if trade.get("instrument") == instrument]
        automated_trades = [trade for trade in strategy_trades if _is_automated_trade(trade)]
        manual_trades = [trade for trade in strategy_trades if not _is_automated_trade(trade)]

        close_result = await self._close_on_strategy_exit(
            pair,
            timeframe,
            candle_time,
            candles,
            adapter,
            signal,
            price,
            automated_trades,
            open_trades,
        )
        if close_result:
            return close_result

        if signal not in {Signal.LONG, Signal.SHORT}:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "no_entry",
            }
        if manual_trades or any(trade.get("instrument") == instrument for trade in open_trades):
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "blocked",
                "reason": "An existing non-automated OANDA trade is open for this instrument",
            }

        side = "long" if signal is Signal.LONG else "short"
        allowed_side = str(risk.get("side", "NONE")).upper()
        if allowed_side not in {"BOTH", side.upper()}:
            raise AutoExecutionError(f"{pair} risk policy does not allow {side} entries")

        approved_stop_loss = parameters.get("stopLoss")
        stop_price, take_profit, units, trailing_distance = await self._protected_order_size(
            pair,
            side,
            risk,
            account,
            price,
            trailing_stop_loss=approved_stop_loss if trailing_stop_loss else None,
        )
        signed_units = units if side == "long" else -units
        client_order_id = _client_order_id(instrument, candle_time, side)
        result = await self.client.create_market_order(
            instrument,
            signed_units,
            stop_loss_price=None if trailing_stop_loss else str(stop_price),
            trailing_stop_loss_distance=(
                str(trailing_distance)
                if trailing_stop_loss and trailing_distance is not None
                else None
            ),
            take_profit_price=str(take_profit) if take_profit is not None else None,
            client_order_id=client_order_id,
            trade_client_extensions={
                "id": client_order_id,
                "tag": _AUTO_TRADE_TAG,
                "comment": "approved-strategy",
            },
        )
        if result.fill_price is None:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "not_filled",
                "orderId": result.order_id,
                "transactionId": result.transaction_id,
                "reason": result.cancel_reason or "OANDA did not confirm a fill",
            }
        return {
            "pair": pair,
            "timeframe": timeframe,
            "signal": signal.value,
            "candleTime": candle_time,
            "status": "filled",
            "orderId": result.order_id,
            "transactionId": result.transaction_id,
            "fillPrice": str(result.fill_price),
            "units": signed_units,
            "clientOrderId": client_order_id,
        }

    async def _protected_order_size(
        self,
        pair: str,
        side: str,
        risk: dict[str, object],
        account: Any,
        price: OandaPrice,
        *,
        trailing_stop_loss: object | None = None,
    ) -> tuple[Decimal, Decimal | None, int, Decimal | None]:
        instrument_metadata = await self.client.get_instruments((price.instrument,))
        if not instrument_metadata:
            raise AutoExecutionError(f"OANDA instrument metadata is unavailable for {pair}")
        metadata = instrument_metadata[0]
        entry_price = price.price_for_side(side)
        stop_price = _protection_price(
            risk.get("stopLoss"),
            str(risk.get("stopLossMode", "price")),
            entry_price,
            metadata,
            side,
            is_stop=True,
        )
        take_profit = _protection_price(
            risk.get("takeProfit"),
            str(risk.get("takeProfitMode", "price")),
            entry_price,
            metadata,
            side,
            is_stop=False,
        )
        risk_fraction = _configured_risk_fraction(self.settings.risk_fraction)
        conversion = await self._quote_to_account_rate(
            metadata.quote_currency,
            account.currency,
        )
        risk_units = units_for_fixed_risk(
            account_equity=account.nav,
            risk_fraction=risk_fraction,
            entry_price=entry_price,
            stop_price=stop_price,
            instrument=metadata,
            quote_to_account_rate=conversion,
        )
        gbp_to_account_rate = None
        if str(risk.get("maxExposureMode", "margin_percent")) == "margin_gbp":
            gbp_to_account_rate = await self._quote_to_account_rate("GBP", account.currency)
        units = min(
            risk_units,
            _depth_available_units(price, side, metadata.trade_units_precision),
            _max_exposure_units(
                risk.get("maxExposure"),
                str(risk.get("maxExposureMode", "margin_percent")),
                entry_price,
                conversion,
                metadata.trade_units_precision,
                account.nav,
                margin_available=account.margin_available,
                margin_rate=metadata.margin_rate,
                gbp_to_account_rate=gbp_to_account_rate,
            ),
        )
        units = int(units)
        if units < int(metadata.minimum_trade_size):
            raise AutoExecutionError(
                f"{pair} risk size or available market depth is below OANDA's minimum trade size"
            )
        trailing_distance: Decimal | None = None
        if trailing_stop_loss is not None:
            if not isinstance(trailing_stop_loss, dict):
                raise AutoExecutionError("Approved trailing-stop settings are invalid")
            mode = trailing_stop_loss.get("mode")
            try:
                value = Decimal(str(trailing_stop_loss.get("value", "")))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise AutoExecutionError("Approved trailing-stop distance is invalid") from exc
            if not value.is_finite() or value <= 0:
                raise AutoExecutionError("Approved trailing-stop distance must be positive")
            if mode == "pips":
                trailing_distance = value * metadata.pip_size
            elif mode == "percent":
                trailing_distance = entry_price * value / Decimal("100")
            elif mode == "money":
                trailing_distance = value / (conversion * Decimal(units))
            else:
                raise AutoExecutionError(
                    "Approved trailing-stop mode must be pips, percent, or money"
                )
            if not trailing_distance.is_finite() or trailing_distance <= 0:
                raise AutoExecutionError("Calculated trailing-stop distance must be positive")
        return stop_price, take_profit, units, trailing_distance

    async def _close_on_strategy_exit(
        self,
        pair: str,
        timeframe: str,
        candle_time: str,
        candles: pd.DataFrame,
        adapter: FreqtradeStrategyAdapter,
        signal: Signal,
        price: OandaPrice,
        automated_trades: list[dict[str, Any]],
        open_trades: list[dict[str, Any]],
    ) -> dict[str, object] | None:
        if not automated_trades:
            return None
        automated_side = _trade_side(automated_trades[0])
        should_exit = adapter.exit_signal(
            candles,
            Signal.LONG if automated_side == "long" else Signal.SHORT,
        )
        opposite = (signal is Signal.SHORT and automated_side == "long") or (
            signal is Signal.LONG and automated_side == "short"
        )
        roi_exit = (
            _roi_exit_due(automated_trades[0], adapter.minimal_roi, price, automated_side)
            if not should_exit and not opposite
            else False
        )
        if not should_exit and not opposite and not roi_exit:
            return None

        closed = await self._close_automated_trades(automated_trades)
        open_trades[:] = [trade for trade in open_trades if trade not in automated_trades]
        if not _filled_close(closed):
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "close_not_filled",
                "reason": "OANDA did not confirm a fill while closing the automated trade",
            }
        same_direction_signal = (signal is Signal.LONG and automated_side == "long") or (
            signal is Signal.SHORT and automated_side == "short"
        )
        if signal not in {Signal.LONG, Signal.SHORT} or same_direction_signal:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "closed",
                "transactionId": closed.get("orderFillTransaction", {}).get("id"),
            }
        return None

    async def _close_automated_trades(
        self,
        trades: list[dict[str, Any]],
    ) -> dict[str, Any]:
        fills: list[dict[str, Any]] = []
        for trade in trades:
            result = await self.client.close_trade(str(trade.get("id", "")))
            fills.append(result)
        return (
            fills[0]
            if len(fills) == 1
            else {
                "orderFillTransaction": (
                    fills[0].get("orderFillTransaction")
                    if fills and all(_filled_close(item) for item in fills)
                    else None
                )
            }
        )

    async def _quote_to_account_rate(
        self,
        quote_currency: str | None,
        account_currency: str,
    ) -> Decimal:
        if not quote_currency:
            raise AutoExecutionError("OANDA instrument has no quote currency")
        if quote_currency.upper() == account_currency.upper():
            return Decimal(1)
        direct = f"{quote_currency}_{account_currency}"
        inverse = f"{account_currency}_{quote_currency}"
        direct_quotes = await self.client.get_prices((direct,))
        if direct_quotes:
            return direct_quotes[0].bid
        inverse_quotes = await self.client.get_prices((inverse,))
        if inverse_quotes:
            return Decimal(1) / inverse_quotes[0].ask
        raise AutoExecutionError(
            f"Cannot convert {quote_currency} to account currency {account_currency}"
        )


def _candle_frame(candles: list[Any]) -> pd.DataFrame:
    completed = [candle for candle in candles if getattr(candle, "complete", False)]
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(candle.time).tz_convert("UTC"),
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": int(candle.volume),
            }
            for candle in completed
        ],
        columns=["date", "open", "high", "low", "close", "volume"],
    )


def _is_automated_trade(trade: dict[str, Any]) -> bool:
    extensions = trade.get("tradeClientExtensions") or trade.get("clientExtensions") or {}
    return isinstance(extensions, dict) and extensions.get("tag") == _AUTO_TRADE_TAG


def _trade_side(trade: dict[str, Any]) -> str:
    units = Decimal(str(trade.get("currentUnits") or trade.get("initialUnits") or "0"))
    return "long" if units > 0 else "short"


def _roi_exit_due(
    trade: dict[str, Any],
    minimal_roi: dict[str, float],
    price: OandaPrice,
    side: str,
) -> bool:
    if not minimal_roi:
        return False
    try:
        opened_at = datetime.fromisoformat(str(trade["openTime"]))
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=UTC)
        observed_at = datetime.fromisoformat(price.time)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        entry = Decimal(str(trade["price"]))
        if not entry.is_finite() or entry <= 0:
            raise ValueError
        elapsed_minutes = (observed_at - opened_at).total_seconds() / 60
        if elapsed_minutes < 0:
            raise ValueError
        thresholds = sorted(
            (int(minute), Decimal(str(rate))) for minute, rate in minimal_roi.items()
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise AutoExecutionError(
            "Cannot evaluate approved minimal ROI: open trade or ROI schedule is invalid"
        ) from exc
    active_thresholds = [rate for minute, rate in thresholds if minute <= elapsed_minutes]
    if not active_thresholds:
        return False
    current = price.bid if side == "long" else price.ask
    profit_ratio = (current - entry) / entry if side == "long" else (entry - current) / entry
    return profit_ratio >= active_thresholds[-1]


def _filled_close(result: dict[str, Any]) -> bool:
    return bool(result.get("orderFillTransaction"))


def _configured_risk_fraction(value: object) -> Decimal:
    fraction = _positive_decimal(value, "configured risk fraction")
    if fraction >= Decimal(1):
        raise AutoExecutionError("Configured risk fraction must be less than 1")
    return fraction


def _protection_price(
    raw_value: object,
    mode: str,
    entry_price: Decimal,
    instrument: OandaInstrument,
    side: str,
    *,
    is_stop: bool,
) -> Decimal | None:
    if raw_value is None or not str(raw_value).strip():
        if is_stop:
            raise AutoExecutionError(
                "Configure a stop loss for this pair before enabling automatic orders"
            )
        return None
    value = _positive_decimal(raw_value, "stop loss" if is_stop else "take profit")
    if mode == "price":
        price = value
    elif mode == "pips":
        distance = value * instrument.pip_size
        price = entry_price - distance if (side == "long") == is_stop else entry_price + distance
    elif mode == "percent":
        distance = entry_price * value / Decimal(100)
        price = entry_price - distance if (side == "long") == is_stop else entry_price + distance
    else:
        raise AutoExecutionError("Protection mode must be price, pips, or percent")
    if price <= 0:
        raise AutoExecutionError("Calculated protection price must be positive")
    if (side == "long" and is_stop and price >= entry_price) or (
        side == "short" and is_stop and price <= entry_price
    ):
        raise AutoExecutionError("Stop loss is on the wrong side of the entry price")
    if (side == "long" and not is_stop and price <= entry_price) or (
        side == "short" and not is_stop and price >= entry_price
    ):
        raise AutoExecutionError("Take profit is on the wrong side of the entry price")
    return price


def _depth_available_units(
    price: OandaPrice,
    side: str,
    units_precision: int,
) -> int:
    if not price.units_available or not price.bids or not price.asks:
        raise AutoExecutionError(
            "OANDA depth and units-available data are required for automatic execution"
        )
    side_available = price.units_available.get("default", {}).get(side)
    if side_available is None:
        raise AutoExecutionError(f"OANDA did not report available {side} units")
    try:
        available = Decimal(str(side_available))
        levels = price.asks if side == "long" else price.bids
        visible = sum((liquidity for _, liquidity in levels), Decimal(0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AutoExecutionError("OANDA returned invalid depth data") from exc
    if not available.is_finite() or available <= 0 or visible <= 0:
        raise AutoExecutionError("OANDA reports no available liquidity for this side")
    step = Decimal(1).scaleb(-units_precision)
    return int(min(available, visible).quantize(step, rounding=ROUND_DOWN))


def _max_exposure_units(
    raw_value: object,
    mode: str,
    entry_price: Decimal,
    quote_to_account_rate: Decimal,
    units_precision: int,
    account_equity: Decimal,
    *,
    margin_available: Decimal,
    margin_rate: Decimal | None,
    gbp_to_account_rate: Decimal | None,
) -> int:
    exposure = _positive_decimal(raw_value, "maximum exposure")
    if mode == "units":
        maximum_units = exposure
    elif mode in {"margin_gbp", "margin_percent"}:
        if margin_rate is None or not margin_rate.is_finite() or margin_rate <= 0:
            raise AutoExecutionError("OANDA did not report a valid margin rate for this instrument")
        if mode == "margin_gbp":
            if (
                gbp_to_account_rate is None
                or not gbp_to_account_rate.is_finite()
                or gbp_to_account_rate <= 0
            ):
                raise AutoExecutionError("Cannot convert the GBP margin limit to account currency")
            margin_cap = exposure * gbp_to_account_rate
        else:
            if exposure > 100:
                raise AutoExecutionError("Maximum margin percent cannot exceed 100%")
            if not margin_available.is_finite() or margin_available <= 0:
                raise AutoExecutionError("OANDA account has no available margin")
            margin_cap = margin_available * exposure / Decimal(100)
        maximum_units = margin_cap / (
            entry_price * quote_to_account_rate * margin_rate
        )
    elif mode in {"absolute", "percent"}:
        # Preserve existing saved notional limits until an operator selects a live margin mode.
        maximum_notional = (
            exposure
            if mode == "absolute"
            else account_equity * exposure / Decimal(100)
        )
        maximum_units = maximum_notional / (entry_price * quote_to_account_rate)
    else:
        raise AutoExecutionError("Maximum exposure mode is unsupported")
    step = Decimal(1).scaleb(-units_precision)
    return int(
        maximum_units.quantize(step, rounding=ROUND_DOWN)
    )


def _positive_decimal(value: object, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AutoExecutionError(f"{name} must be a positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise AutoExecutionError(f"{name} must be a positive number")
    return parsed


def _client_order_id(instrument: str, candle_time: str, side: str) -> str:
    timestamp = pd.Timestamp(candle_time).tz_convert(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = "L" if side == "long" else "S"
    return f"auto-{instrument.replace('_', '')}-{timestamp}-{suffix}"
