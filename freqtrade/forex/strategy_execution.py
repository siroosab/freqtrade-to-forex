"""Load and adapt native Freqtrade strategies to the forex backtest contract."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from freqtrade.enums import CandleType, RunMode
from freqtrade.forex.strategy_catalog import discover_strategy_files
from freqtrade.forex.strategy_loop import Signal
from freqtrade.strategy import IStrategy
from freqtrade.strategy.strategy_helper import merge_informative_pair
from freqtrade.timeframe import timeframe_to_minutes


_OANDA_UNITS = {"m": "M", "h": "H", "d": "D", "w": "W", "mo": "M"}
_OANDA_TIMEFRAMES = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H2": "2h", "H4": "4h", "H6": "6h", "H8": "8h",
    "H12": "12h", "D": "1d", "D1": "1d", "W": "1w", "W1": "1w",
    "M": "1M", "MN1": "1M",
}


def freqtrade_timeframe(timeframe: str) -> str:
    normalized = timeframe.strip().upper()
    if normalized in {"M", "MN1", "1M", "1MO"}:
        return "1M"
    if normalized in _OANDA_TIMEFRAMES:
        return _OANDA_TIMEFRAMES[normalized]
    return timeframe.strip().lower()


def oanda_granularity(timeframe: str) -> str:
    if timeframe.strip() in {"1M", "1mo", "1MO"}:
        return "M"
    normalized = timeframe.strip().lower()
    digits = "".join(character for character in normalized if character.isdigit())
    unit = normalized[len(digits):]
    if not digits or unit not in _OANDA_UNITS:
        raise ValueError(f"Unsupported OANDA timeframe: {timeframe}")
    return f"{_OANDA_UNITS[unit]}{digits}"


def load_strategy(
    strategy_name: str,
    timeframe: str,
    pair: str,
    parameter_values: dict[str, object] | None = None,
    config_overrides: dict[str, object] | None = None,
) -> IStrategy:
    selected = next((item for item in discover_strategy_files() if item.name == strategy_name), None)
    if selected is None:
        available = ", ".join(item.name for item in discover_strategy_files())
        detail = f"Available classes: {available}" if available else "No strategy classes were discovered"
        raise ValueError(
            f"Strategy class {strategy_name} is not available. {detail}. "
            "Place it in user_data/strategies or configure FOREX_STRATEGIES_DIR."
        )
    module_name = f"freqtrade_uploaded_strategy_{hashlib.sha256(str(selected.path.resolve()).encode()).hexdigest()[:16]}"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, selected.path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Unable to load strategy file {selected.path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise ValueError(f"Strategy import failed: {exc}") from exc
    strategy_class = getattr(module, strategy_name, None)
    if not inspect.isclass(strategy_class) or not issubclass(strategy_class, IStrategy):
        raise ValueError(f"{strategy_name} must be a concrete IStrategy class")
    if inspect.isabstract(strategy_class):
        raise ValueError(f"{strategy_name} is abstract and cannot be run")
    config: dict[str, Any] = {
        "strategy": strategy_name,
        "strategy_path": str(selected.path.parent),
        "user_data_dir": str(Path("user_data").resolve()),
        "timeframe": timeframe,
        "runmode": RunMode.BACKTEST,
        "candle_type_def": CandleType.SPOT,
        "stake_currency": pair.split("/")[-1],
        "stake_amount": 1000,
        "exchange": {"name": "oanda"},
    }
    config.update(config_overrides or {})
    try:
        strategy = strategy_class(config)
    except Exception as exc:
        raise ValueError(f"Strategy initialization failed: {exc}") from exc
    strategy.timeframe = timeframe
    if isinstance(config.get("freqai"), dict):
        strategy.freqai_info = config["freqai"]
    for name, value in (parameter_values or {}).items():
        current = getattr(strategy, name, None)
        if hasattr(current, "value"):
            parameter = deepcopy(current)
            parameter.value = value
            setattr(strategy, name, parameter)
        else:
            setattr(strategy, name, value)
    strategy_informative_timeframes(strategy, pair)
    return strategy


def strategy_informative_timeframes(strategy: IStrategy, pair: str) -> tuple[str, ...]:
    timeframes: set[str] = set()
    for informative, _ in strategy._ft_informative:
        if informative.asset and informative.asset.replace("/", "_").upper() != pair.replace("/", "_").upper():
            raise ValueError("Informative strategies currently support the selected pair only")
        if timeframe_to_minutes(informative.timeframe) < timeframe_to_minutes(strategy.timeframe):
            raise ValueError("Informative timeframe must be equal to or higher than the strategy timeframe")
        timeframes.add(informative.timeframe)
    return tuple(sorted(timeframes, key=timeframe_to_minutes))


def strategy_informative_candle_count(
    strategy: IStrategy, timeframe: str, base_candle_count: int
) -> int:
    """Return enough informative candles for both coverage and strategy warmup."""
    base_minutes = timeframe_to_minutes(strategy.timeframe)
    informative_minutes = timeframe_to_minutes(timeframe)
    covered_candles = (
        base_candle_count * base_minutes + informative_minutes - 1
    ) // informative_minutes
    return max(10, covered_candles + 5, strategy.startup_candle_count + 5)


class FreqtradeStrategyAdapter:
    """Evaluate standard strategy callbacks using causal, prefix-only candle data."""

    supports_explicit_exit = True

    def __init__(
        self,
        strategy: IStrategy,
        pair: str,
        informative_candles: dict[str, pd.DataFrame] | None = None,
        live_informative_candles: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self.strategy = strategy
        self.pair = pair
        self.informative_candles = informative_candles or {}
        self.live_informative_candles = live_informative_candles or {}
        self._cached_length = -1
        self._cached_frame: pd.DataFrame | None = None
        self._prepared_frame: pd.DataFrame | None = None
        self._prepared_positions: dict[pd.Timestamp, int] = {}

    def update_informative_candles(
        self,
        candles: dict[str, pd.DataFrame],
        live_candles: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self.informative_candles = candles
        self.live_informative_candles = live_candles or {}
        self._cached_length = -1
        self._cached_frame = None
        self._prepared_frame = None
        self._prepared_positions.clear()

    def prepare_backtest(self, candles: pd.DataFrame) -> None:
        """Precompute opted-in causal indicators once for a full backtest window."""
        if not getattr(self.strategy, "precompute_backtest_indicators", False):
            return
        if candles.empty:
            self._prepared_frame = None
            self._prepared_positions.clear()
            return
        self._prepared_frame = self._populate(candles)
        dates = pd.to_datetime(candles["date"], utc=True)
        self._prepared_positions = {
            timestamp: position for position, timestamp in enumerate(dates)
        }

    def _latest_populated_row(self, candles: pd.DataFrame) -> pd.Series | None:
        if candles.empty:
            return None
        if self._prepared_frame is not None:
            timestamp = pd.Timestamp(candles.iloc[-1]["date"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            position = self._prepared_positions.get(timestamp)
            if position is not None:
                return self._prepared_frame.iloc[position]
        populated = self._populate(candles)
        return None if populated.empty else populated.iloc[-1]

    def _populate(self, candles: pd.DataFrame) -> pd.DataFrame:
        if self._cached_length == len(candles) and self._cached_frame is not None:
            return self._cached_frame
        metadata = {"pair": self.pair}
        result = candles.copy()
        for informative, populate in self.strategy._ft_informative:
            source = self.informative_candles.get(informative.timeframe)
            if source is None:
                raise ValueError(f"Missing informative candles for timeframe {informative.timeframe}")
            source = source[source["date"] <= result.iloc[-1]["date"]].copy()
            populated = populate(self.strategy, source, metadata)
            result = merge_informative_pair(
                result,
                populated,
                self.strategy.timeframe,
                informative.timeframe,
                ffill=informative.ffill,
            )
        indicators = self.strategy.populate_indicators(result, metadata)
        if indicators is None:
            raise ValueError("populate_indicators must return a dataframe")
        populate_live = getattr(
            self.strategy, "populate_live_informative_indicators", None
        )
        if self.live_informative_candles and callable(populate_live):
            indicators = populate_live(
                indicators, metadata, self.live_informative_candles
            )
            if indicators is None:
                raise ValueError(
                    "populate_live_informative_indicators must return a dataframe"
                )
        entries = self.strategy.populate_entry_trend(indicators, metadata)
        if entries is None:
            raise ValueError("populate_entry_trend must return a dataframe")
        exits = self.strategy.populate_exit_trend(entries, metadata)
        if exits is None:
            raise ValueError("populate_exit_trend must return a dataframe")
        self._cached_length = len(candles)
        self._cached_frame = exits
        return exits

    def signal(self, candles: pd.DataFrame) -> Signal:
        latest = self._latest_populated_row(candles)
        if latest is None:
            return Signal.FLAT
        if bool(latest.get("enter_long", False)):
            return Signal.LONG
        if bool(latest.get("enter_short", False)):
            return Signal.SHORT
        return Signal.FLAT

    def exit_signal(self, candles: pd.DataFrame, direction: Signal) -> bool:
        if hasattr(self.strategy, "signal"):
            return False
        latest = self._latest_populated_row(candles)
        if latest is None:
            return False
        column = "exit_long" if direction is Signal.LONG else "exit_short"
        return bool(latest.get(column, False))


class CachedFreqAIPredictions:
    """Serve a fitted model's cached timestamp predictions to a strategy."""

    def __init__(self, predictions: dict[int, dict[str, object]]) -> None:
        self.predictions = predictions

    def start(self, dataframe: pd.DataFrame, metadata: dict, strategy: IStrategy) -> pd.DataFrame:
        result = dataframe.copy()
        timestamps = [int(pd.Timestamp(value).value) for value in result["date"]]
        rows = [self.predictions.get(timestamp, {}) for timestamp in timestamps]
        prediction_columns = {
            column for row in rows for column in row if column != "do_predict"
        }
        for column in prediction_columns:
            result[column] = [row.get(column, pd.NA) for row in rows]
        result["do_predict"] = [int(row.get("do_predict", 0)) for row in rows]
        return result
