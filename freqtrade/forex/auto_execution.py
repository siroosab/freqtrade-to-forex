"""Execute approved strategy signals against the selected OANDA account."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
from typing import Any

import pandas as pd

from freqtrade.forex.conditions import normalize_approved_ema_trend_condition
from freqtrade.forex.config import OandaSettings
from freqtrade.forex.models import OandaHomeConversion, OandaInstrument, OandaPrice
from freqtrade.forex.oanda import OandaAPIError, OandaClient
from freqtrade.forex.risk import units_for_fixed_risk
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    approved_stop_distance_pips,
    freqtrade_timeframe,
    load_strategy,
    oanda_granularity,
    strategy_informative_candle_count,
    strategy_required_informative_timeframes,
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
                raise AutoExecutionError("Approved Hyperopt stop loss is invalid") from exc
            if not value.is_finite() or value <= 0:
                raise AutoExecutionError("Approved Hyperopt stop loss must be positive")
            return {**risk, "stopLoss": str(value), "stopLossMode": str(mode)}
        raise AutoExecutionError(
            "Approved Hyperopt stop loss must use a positive pips or percent value"
        )
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
        self._conversion_cache: dict[str, OandaHomeConversion] = {}
        self._available_instruments: list[OandaInstrument] | None = None

    async def run_cycle(self) -> list[dict[str, object]]:
        self._conversion_cache.clear()
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
            except (AutoExecutionError, OandaAPIError, OSError, InvalidOperation, ValueError) as exc:
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
        run_settings = parameters.get("runSettings")
        run_settings = run_settings if isinstance(run_settings, dict) else {}
        ema_trend_condition = normalize_approved_ema_trend_condition(
            parameters.get("emaTrendCondition", run_settings.get("emaTrendCondition"))
        )
        strategy = load_strategy(
            str(revision["strategyClass"]),
            freqtrade_timeframe(timeframe),
            pair,
            parameter_values=parameter_values if isinstance(parameter_values, dict) else None,
            minimal_roi=minimal_roi if isinstance(minimal_roi, dict) else None,
            use_roi=(
                parameters.get("optimizeRoi")
                if isinstance(parameters.get("optimizeRoi"), bool)
                else None
            ),
            use_exit_signal=(
                parameters.get("useExitSignal")
                if isinstance(parameters.get("useExitSignal"), bool)
                else None
            ),
        )
        informative_candles: dict[str, pd.DataFrame] = {}
        for informative_timeframe in strategy_required_informative_timeframes(
            strategy, pair, ema_trend_condition
        ):
            count = strategy_informative_candle_count(
                strategy,
                informative_timeframe,
                len(candles),
                ema_trend_condition,
            )
            raw_informative = await self.client.get_candles(
                instrument,
                oanda_granularity(informative_timeframe),
                count=min(count, 5000),
            )
            informative_candles[informative_timeframe] = _candle_frame(raw_informative)

        adapter = FreqtradeStrategyAdapter(
            strategy,
            pair,
            informative_candles,
            ema_trend_condition=ema_trend_condition,
        )
        signal = adapter.signal(candles)
        last_candle = candles.iloc[-1]
        candle_time = pd.Timestamp(last_candle["date"]).isoformat()
        risk = self.risk_configs.get(pair, {})
        panel_stop_loss = risk.get("stopLoss")
        trailing_stop_loss = (
            parameters.get("trailingStopLoss") is True
        )
        if (
            panel_stop_loss is None
            or not str(panel_stop_loss).strip()
            or (
                risk.get("stopLossOverride") is not True
                and risk.get("stopLossSource") in {"approved-hyperopt", "default-policy"}
            )
        ):
            risk = _risk_with_approved_stop_loss(
                risk,
                parameters.get("stopLoss"),
                use_approved_setting=True,
            )
        price_values = await self.client.get_prices((instrument,))
        if not price_values:
            raise AutoExecutionError(f"OANDA returned no current quote for {pair}")
        price = price_values[0]
        if not price.tradeable:
            raise AutoExecutionError(f"{pair} is not currently tradeable")
        strategy_trades = [trade for trade in open_trades if trade.get("instrument") == instrument]
        automated_trades = [trade for trade in strategy_trades if _is_automated_trade(trade)]
        take_profit_configured = bool(str(risk.get("takeProfit") or "").strip())

        close_result = None
        if self.last_processed.get(instrument) != candle_time:
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
                ignore_strategy_exits=take_profit_configured,
                instrument=instrument,
            )
        if close_result:
            return close_result

        trailing_result = await self._activate_profit_trailing_stop(
            pair,
            timeframe,
            instrument,
            candle_time,
            [trade for trade in automated_trades if trade in open_trades],
            price,
            parameters,
            candles,
        )
        if trailing_result is not None:
            return trailing_result
        if self.last_processed.get(instrument) == candle_time:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "already_processed",
            }

        if signal not in {Signal.LONG, Signal.SHORT}:
            self._mark_processed(instrument, candle_time)
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "no_entry",
            }
        if any(trade.get("instrument") == instrument for trade in open_trades):
            self._mark_processed(instrument, candle_time)
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
            self._mark_processed(instrument, candle_time)
            raise AutoExecutionError(f"{pair} risk policy does not allow {side} entries")

        return await self._execute_signal_entry(
            pair,
            timeframe,
            instrument,
            candle_time,
            signal,
            side,
            risk,
            account,
            price,
            parameters,
            trailing_stop_loss=trailing_stop_loss,
        )

    async def _execute_signal_entry(
        self,
        pair: str,
        timeframe: str,
        instrument: str,
        candle_time: str,
        signal: Signal,
        side: str,
        risk: dict[str, Any],
        account: Any,
        price: OandaPrice,
        parameters: dict[str, Any],
        *,
        trailing_stop_loss: bool,
    ) -> dict[str, object]:
        approved_stop_loss = parameters.get("stopLoss")
        try:
            stop_price, take_profit, units, _ = await self._protected_order_size(
                pair,
                side,
                risk,
                account,
                price,
                trailing_stop_loss=approved_stop_loss if trailing_stop_loss else None,
            )
        except (AutoExecutionError, OandaAPIError, OSError, InvalidOperation, ValueError) as exc:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "blocked",
                "reason": str(exc),
            }
        signed_units = units if side == "long" else -units
        client_order_id = _client_order_id(instrument, candle_time, side)
        self._mark_processed(instrument, candle_time)
        try:
            result = await self.client.create_market_order(
                instrument,
                signed_units,
                stop_loss_price=str(stop_price),
                trailing_stop_loss_distance=None,
                take_profit_price=str(take_profit) if take_profit is not None else None,
                client_order_id=client_order_id,
                trade_client_extensions={
                    "id": client_order_id,
                    "tag": _AUTO_TRADE_TAG,
                    "comment": "approved-strategy",
                },
            )
        except (OandaAPIError, OSError, InvalidOperation, ValueError) as exc:
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": signal.value,
                "candleTime": candle_time,
                "status": "blocked",
                "reason": str(exc),
            }
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
            "units": _reported_units(result.units),
            "clientOrderId": client_order_id,
        }

    async def _activate_profit_trailing_stop(
        self,
        pair: str,
        timeframe: str,
        instrument: str,
        candle_time: str,
        automated_trades: list[dict[str, Any]],
        price: OandaPrice,
        parameters: dict[str, Any],
        candles: pd.DataFrame,
    ) -> dict[str, object] | None:
        if not automated_trades or parameters.get("trailingStopLoss") is not True:
            return None
        trailing = parameters.get("stopLoss")
        if not isinstance(trailing, dict) or trailing.get("mode") != "pips":
            raise AutoExecutionError(
                f"Approved trailing stop for {pair} must use the Hyperopt pips distance"
            )
        try:
            trailing_pips = Decimal(str(trailing.get("value", "")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AutoExecutionError(
                f"Approved trailing-stop settings for {pair} are invalid"
            ) from exc
        if not trailing_pips.is_finite() or trailing_pips <= 0:
            raise AutoExecutionError(f"Approved trailing-stop distance for {pair} must be positive")

        metadata_values = await self.client.get_instruments((instrument,))
        if not metadata_values:
            raise AutoExecutionError(f"OANDA instrument metadata is unavailable for {pair}")
        metadata = metadata_values[0]
        distance = _oanda_trailing_distance(trailing_pips, metadata)
        for trade in automated_trades:
            update = _adaptive_trailing_stop_update(
                trade,
                price,
                metadata,
                distance,
                candles,
            )
            if update is None:
                continue
            old_distance, formatted_distance = update
            try:
                await self.client.modify_trade_orders(
                    str(trade.get("id", "")),
                    trailing_stop_loss_distance=formatted_distance,
                    instrument=instrument,
                )
            except (OandaAPIError, OSError, InvalidOperation, ValueError) as exc:
                return {
                    "pair": pair,
                    "timeframe": timeframe,
                    "signal": "—",
                    "candleTime": candle_time,
                    "status": "trailing_update_failed",
                    "reason": str(exc),
                }
            return {
                "pair": pair,
                "timeframe": timeframe,
                "signal": "—",
                "candleTime": candle_time,
                "status": "trailing_updated" if old_distance is not None else "trailing_activated",
                "tradeId": trade.get("id"),
                "trailingStopLossDistance": formatted_distance,
                "previousTrailingStopLossDistance": (
                    str(old_distance) if old_distance is not None else None
                ),
            }
        return None

    def _mark_processed(self, instrument: str, candle_time: str) -> None:
        self.last_processed[instrument] = candle_time
        if self.checkpoint:
            self.checkpoint()

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
        depth_units = _depth_available_units(price, side, metadata.trade_units_precision)
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
        conversion = await self._quote_to_account_conversion(
            metadata.quote_currency,
            account.currency,
            preferred_instrument=metadata.name,
        )
        risk_units = units_for_fixed_risk(
            account_equity=account.nav,
            risk_fraction=risk_fraction,
            entry_price=entry_price,
            stop_price=stop_price,
            instrument=metadata,
            quote_to_account_rate=conversion.account_loss,
        )
        gbp_to_account_rate = None
        if str(risk.get("maxExposureMode", "margin_percent")) == "margin_gbp":
            gbp_conversion = await self._quote_to_account_conversion(
                "GBP",
                account.currency,
            )
            gbp_to_account_rate = gbp_conversion.position_value
        units = min(
            risk_units,
            depth_units,
            _max_exposure_units(
                risk.get("maxExposure"),
                str(risk.get("maxExposureMode", "margin_percent")),
                entry_price,
                conversion.position_value,
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
        depth_levels = price.asks if side == "long" else price.bids
        if depth_levels:
            entry_price = _depth_execution_price(price, side, units)
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
            risk_units = units_for_fixed_risk(
                account_equity=account.nav,
                risk_fraction=risk_fraction,
                entry_price=entry_price,
                stop_price=stop_price,
                instrument=metadata,
                quote_to_account_rate=conversion.account_loss,
            )
            units = min(
                units,
                risk_units,
                depth_units,
                _max_exposure_units(
                    risk.get("maxExposure"),
                    str(risk.get("maxExposureMode", "margin_percent")),
                    entry_price,
                    conversion.position_value,
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
            if mode != "pips":
                raise AutoExecutionError("OANDA trailing stops must be configured in pips")
            trailing_distance = _oanda_trailing_distance(value, metadata)
        if units < int(metadata.minimum_trade_size):
            raise AutoExecutionError(
                f"{pair} risk size or available market depth is below OANDA's minimum trade size"
            )
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
        *,
        ignore_strategy_exits: bool = False,
        instrument: str,
    ) -> dict[str, object] | None:
        if not automated_trades:
            return None
        automated_side = _trade_side(automated_trades[0])
        should_exit = (
            not ignore_strategy_exits
            and adapter.use_exit_signal
            and adapter.exit_signal(
                candles,
                Signal.LONG if automated_side == "long" else Signal.SHORT,
            )
        )
        opposite = (
            adapter.use_exit_signal
            and (
                (signal is Signal.SHORT and automated_side == "long")
                or (signal is Signal.LONG and automated_side == "short")
            )
        )
        roi_exit = (
            _roi_exit_due(automated_trades[0], adapter.minimal_roi, price, automated_side)
            if not should_exit and not opposite and not ignore_strategy_exits
            else False
        )
        if not should_exit and not opposite and not roi_exit:
            return None

        closed = await self._close_automated_trades(automated_trades)
        open_trades[:] = [trade for trade in open_trades if trade not in automated_trades]
        if not _filled_close(closed):
            self._mark_processed(instrument, candle_time)
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
        if signal in {Signal.LONG, Signal.SHORT} and not same_direction_signal:
            return None
        self._mark_processed(instrument, candle_time)
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

    async def _quote_to_account_conversion(
        self,
        quote_currency: str | None,
        account_currency: str,
        *,
        preferred_instrument: str | None = None,
    ) -> OandaHomeConversion:
        if not quote_currency:
            raise AutoExecutionError("OANDA instrument has no quote currency")
        currency = quote_currency.upper()
        account_home = account_currency.upper()
        cached = self._conversion_cache.get(currency)
        if cached is not None:
            return cached
        if currency == account_home:
            conversion = OandaHomeConversion(
                currency=currency,
                account_gain=Decimal(1),
                account_loss=Decimal(1),
                position_value=Decimal(1),
            )
            self._conversion_cache[currency] = conversion
            return conversion

        instrument_name = preferred_instrument
        if instrument_name is None:
            if self._available_instruments is None:
                try:
                    self._available_instruments = await self.client.get_instruments()
                except OandaAPIError as exc:
                    raise AutoExecutionError(
                        f"Could not discover OANDA instruments for {currency} conversion: {exc}"
                    ) from exc
            candidate = next(
                (
                    item
                    for item in self._available_instruments
                    if item.base_currency == currency or item.quote_currency == currency
                ),
                None,
            )
            if candidate is None:
                raise AutoExecutionError(
                    f"OANDA account has no available instrument containing {currency}; "
                    "cannot obtain its home-currency conversion"
                )
            instrument_name = candidate.name

        try:
            pricing = await self.client.get_pricing(
                (instrument_name,),
                include_home_conversions=True,
            )
        except OandaAPIError as exc:
            raise AutoExecutionError(
                f"OANDA home-currency conversion request failed for {currency} "
                f"using {instrument_name}: {exc}"
            ) from exc
        conversion = pricing.home_conversions.get(currency)
        if conversion is None:
            raise AutoExecutionError(
                f"OANDA returned no home-currency conversion for {currency} "
                f"using {instrument_name}"
            )
        factors = (
            conversion.account_gain,
            conversion.account_loss,
            conversion.position_value,
        )
        if any(not factor.is_finite() or factor <= 0 for factor in factors):
            raise AutoExecutionError(
                f"OANDA returned invalid home-currency conversion factors for {currency}"
            )
        self._conversion_cache[currency] = conversion
        return conversion


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
    price_precision = min(
        instrument.display_precision,
        max(0, -price.as_tuple().exponent),
    )
    price_step = Decimal(1).scaleb(-price_precision)
    rounding = (
        ROUND_UP
        if (side == "long" and is_stop) or (side == "short" and not is_stop)
        else ROUND_DOWN
    )
    price = price.quantize(price_step, rounding=rounding)
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
    levels = price.asks if side == "long" else price.bids
    side_available = (
        price.units_available.get("default", {}).get(side)
        if price.units_available is not None
        else None
    )
    has_unit_limit = side_available is not None
    if not levels and not has_unit_limit:
        raise AutoExecutionError(
            f"OANDA returned neither {side} quote liquidity nor available-unit limits"
        )
    try:
        visible = sum((liquidity for _, liquidity in levels), Decimal(0))
        available = Decimal(str(side_available)) if side_available is not None else None
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AutoExecutionError("OANDA returned invalid depth data") from exc
    if available is not None and (not available.is_finite() or available <= 0):
        raise AutoExecutionError(f"OANDA reports no available {side} units")
    if levels and (not visible.is_finite() or visible <= 0):
        raise AutoExecutionError(f"OANDA reports no visible {side} quote liquidity")
    caps = [cap for cap in (available, visible if levels else None) if cap is not None]
    step = Decimal(1).scaleb(-units_precision)
    return int(min(caps).quantize(step, rounding=ROUND_DOWN))


def _depth_execution_price(price: OandaPrice, side: str, units: int) -> Decimal:
    levels = price.asks if side == "long" else price.bids
    if not levels:
        return price.price_for_side(side)
    ordered_levels = sorted(levels, key=lambda level: level[0], reverse=side == "short")
    remaining = Decimal(units)
    notional = Decimal(0)
    for level_price, liquidity in ordered_levels:
        if (
            not level_price.is_finite()
            or level_price <= 0
            or not liquidity.is_finite()
            or liquidity <= 0
        ):
            raise AutoExecutionError("OANDA returned invalid order-book levels")
        filled_at_level = min(remaining, liquidity)
        notional += level_price * filled_at_level
        remaining -= filled_at_level
        if remaining <= 0:
            break
    if remaining > 0:
        raise AutoExecutionError("OANDA order-book depth is below the calculated order size")
    return notional / Decimal(units)


def _oanda_trailing_distance(value: Decimal, instrument: OandaInstrument) -> Decimal:
    try:
        return instrument.round_trailing_stop_distance(value * instrument.pip_size)
    except ValueError as exc:
        raise AutoExecutionError(str(exc)) from exc


def _adaptive_trailing_stop_update(
    trade: dict[str, Any],
    price: OandaPrice,
    instrument: OandaInstrument,
    base_distance: Decimal,
    candles: pd.DataFrame,
) -> tuple[Decimal | None, str] | None:
    try:
        entry = Decimal(str(trade.get("price", "")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AutoExecutionError("OANDA returned an invalid trailing-stop entry price") from exc
    if not entry.is_finite() or entry <= 0:
        raise AutoExecutionError("OANDA returned an invalid trailing-stop entry price")

    side = _trade_side(trade)
    executable_price = price.bid if side == "long" else price.ask
    profit_pips = max(
        Decimal(0),
        (executable_price - entry if side == "long" else entry - executable_price)
        / instrument.pip_size,
    )
    existing_trailing = trade.get("trailingStopLossOrder")
    previous_stop_price: Decimal | None = None
    minimum_distance = (
        instrument.round_trailing_stop_distance(instrument.minimum_trailing_stop_distance)
        if instrument.minimum_trailing_stop_distance is not None
        else Decimal(0)
    )
    activation_profit_pips = max(
        Decimal(10),
        minimum_distance / instrument.pip_size + Decimal(1),
    )
    if existing_trailing is not None:
        if not isinstance(existing_trailing, dict):
            return None
        try:
            old_distance = Decimal(str(existing_trailing.get("distance", "")))
            previous_stop_price = Decimal(
                str(existing_trailing.get("trailingStopValue", ""))
            )
        except (InvalidOperation, TypeError, ValueError):
            return None
        if (
            not old_distance.is_finite()
            or old_distance <= 0
            or not previous_stop_price.is_finite()
            or previous_stop_price <= 0
        ):
            return None
    else:
        if profit_pips < activation_profit_pips:
            return None
        old_distance = None

    adaptive_distance = _adaptive_trailing_distance(
        base_distance,
        candles,
        instrument,
        profit_pips=profit_pips,
        age_hours=_trade_age_hours(trade, price.time),
    )
    profit_tightening_cap = (
        (activation_profit_pips - Decimal(1))
        * instrument.pip_size
        * activation_profit_pips
        / max(profit_pips, activation_profit_pips)
    )
    adaptive_distance = max(
        minimum_distance,
        min(adaptive_distance, profit_tightening_cap),
    )
    formatted_distance = instrument.format_trailing_stop_distance(adaptive_distance)
    if old_distance is not None and Decimal(formatted_distance) >= old_distance:
        return None
    if previous_stop_price is not None:
        next_stop_price = (
            executable_price - Decimal(formatted_distance)
            if side == "long"
            else executable_price + Decimal(formatted_distance)
        )
        if (
            next_stop_price <= previous_stop_price
            if side == "long"
            else next_stop_price >= previous_stop_price
        ):
            return None
    return old_distance, formatted_distance


def _trade_age_hours(trade: dict[str, Any], observed_at: str) -> Decimal:
    opened_at_value = trade.get("openTime")
    if not opened_at_value:
        return Decimal(0)
    try:
        opened_at = datetime.fromisoformat(str(opened_at_value))
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return Decimal(0)
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    elapsed_seconds = max(0.0, (observed - opened_at).total_seconds())
    return Decimal(str(elapsed_seconds / 3600))


def _adaptive_trailing_distance(
    base_distance: Decimal,
    candles: pd.DataFrame,
    instrument: OandaInstrument,
    *,
    profit_pips: Decimal,
    age_hours: Decimal,
) -> Decimal:
    if len(candles) < 2:
        raise AutoExecutionError(
            "At least two completed candles are required for trailing-stop ATR"
        )
    high = candles["high"].astype(float)
    low = candles["low"].astype(float)
    close = candles["close"].astype(float)
    true_range = pd.concat(
        (
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ),
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=2).mean().iloc[-1]
    if not math.isfinite(atr) or atr <= 0:
        raise AutoExecutionError("A valid ATR could not be calculated for the trailing stop")

    volatility_distance = Decimal(str(atr)) * 2
    volatility_adjusted_distance = min(base_distance, volatility_distance)
    base_pips = base_distance / instrument.pip_size
    profit_factor = max(
        Decimal("0.5"),
        Decimal(1) / (Decimal(1) + max(Decimal(0), profit_pips) / (base_pips * 4)),
    )
    age_factor = max(
        Decimal("0.75"),
        Decimal(1) - min(max(Decimal(0), age_hours), Decimal(24)) / Decimal(96),
    )
    return min(
        base_distance,
        volatility_adjusted_distance * profit_factor * age_factor,
    )


def _reported_units(units: Decimal) -> int | str:
    if units == units.to_integral_value():
        return int(units)
    return format(units, "f")


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
