"""Bounded hyperopt for the safe FX AI baseline."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

import pandas as pd

from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline
from freqtrade.forex.backtest import BacktestResult, ForexBacktester
from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    load_strategy,
    strategy_informative_timeframes,
)
from freqtrade.strategy.parameters import BaseParameter
from freqtrade.timeframe import timeframe_to_minutes


# Mirrors freqtrade's --hyperopt-loss / --hyperoptloss NAME options; every
# value below is computed from the actual validation BacktestResult, not a
# placeholder.
HYPEROPT_LOSS_FUNCTIONS: tuple[str, ...] = (
    "ShortTradeDurHyperOptLoss",
    "OnlyProfitHyperOptLoss",
    "SharpeHyperOptLoss",
    "SharpeHyperOptLossDaily",
    "SortinoHyperOptLoss",
    "SortinoHyperOptLossDaily",
    "CalmarHyperOptLoss",
    "MaxDrawDownHyperOptLoss",
    "MaxDrawDownRelativeHyperOptLoss",
    "MaxDrawDownPerPairHyperOptLoss",
    "ProfitDrawDownHyperOptLoss",
    "MultiMetricHyperOptLoss",
)
DEFAULT_HYPEROPT_LOSS = "ProfitDrawDownHyperOptLoss"
_REFERENCE_ROI_VOLATILITY_PER_5M = 0.0002
_MIN_ROI_PROFIT_RATE = 0.00001
_MAX_ROI_PROFIT_INCREMENT = 0.05


def _estimate_roi_volatility_per_5m(
    candles: pd.DataFrame, timeframe: str
) -> float:
    """Estimate typical true-range volatility and normalize it to a five-minute bar."""
    required = {"high", "low", "close"}
    if not required.issubset(candles.columns) or candles.empty:
        return _REFERENCE_ROI_VOLATILITY_PER_5M
    high = pd.to_numeric(candles["high"], errors="coerce")
    low = pd.to_numeric(candles["low"], errors="coerce")
    close = pd.to_numeric(candles["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        (
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    volatility = (
        true_range / close.where(close > 0)
    ).replace([float("inf"), float("-inf")], float("nan")).dropna()
    if volatility.empty:
        return _REFERENCE_ROI_VOLATILITY_PER_5M

    timeframe_minutes = timeframe_to_minutes(timeframe)
    volatility_per_5m = float(volatility.median()) / math.sqrt(
        max(timeframe_minutes, 1) / 5
    )
    return min(max(volatility_per_5m, 0.00001), 0.002)


def _roi_regime(volatility_per_5m: float) -> str:
    relative_volatility = volatility_per_5m / _REFERENCE_ROI_VOLATILITY_PER_5M
    if relative_volatility < 0.75:
        return "low"
    if relative_volatility > 1.5:
        return "high"
    return "medium"


def _roi_profit_bounds(
    duration_minutes: int, volatility_per_5m: float
) -> tuple[float, float]:
    expected_move = volatility_per_5m * math.sqrt(max(duration_minutes, 5) / 5)
    lower = max(_MIN_ROI_PROFIT_RATE, expected_move * 0.5)
    upper = max(lower, min(_MAX_ROI_PROFIT_INCREMENT, expected_move * 2.5))
    return lower, upper


def _roi_space(
    timeframe: str,
    volatility_per_5m: float = _REFERENCE_ROI_VOLATILITY_PER_5M,
) -> dict[str, tuple[int, int] | tuple[float, float]]:
    timeframe_minutes = timeframe_to_minutes(timeframe)
    relative_volatility = volatility_per_5m / _REFERENCE_ROI_VOLATILITY_PER_5M
    time_volatility_scale = min(max(math.sqrt(1 / relative_volatility), 0.5), 2.0)
    time_scale = (timeframe_minutes / 5) * time_volatility_scale
    time_bounds = {
        "roi_t1": (int(10 * time_scale), int(120 * time_scale)),
        "roi_t2": (int(10 * time_scale), int(60 * time_scale)),
        "roi_t3": (int(10 * time_scale), int(40 * time_scale)),
    }
    bounds: dict[str, tuple[int, int] | tuple[float, float]] = dict(time_bounds)
    for profit_name, time_name in (
        ("roi_p1", "roi_t1"),
        ("roi_p2", "roi_t2"),
        ("roi_p3", "roi_t3"),
    ):
        low_time, high_time = time_bounds[time_name]
        bounds[profit_name] = _roi_profit_bounds(
            max((low_time + high_time) // 2, timeframe_minutes),
            volatility_per_5m,
        )
    return bounds


def _sample_roi_parameters(
    rng: random.Random,
    timeframe: str,
    volatility_per_5m: float = _REFERENCE_ROI_VOLATILITY_PER_5M,
) -> dict[str, int | float]:
    space = _roi_space(timeframe, volatility_per_5m)
    sampled: dict[str, int | float] = {}
    for name in ("roi_t1", "roi_t2", "roi_t3"):
        bounds = space[name]
        low, high = bounds
        sampled[name] = rng.randint(int(low), int(high))
    for profit_name, time_name in (
        ("roi_p1", "roi_t1"),
        ("roi_p2", "roi_t2"),
        ("roi_p3", "roi_t3"),
    ):
        low, high = _roi_profit_bounds(
            int(sampled[time_name]), volatility_per_5m
        )
        sampled[profit_name] = round(rng.uniform(low, high), 6)
    return sampled


def _generate_roi_table(parameters: dict[str, int | float]) -> dict[str, float]:
    roi_p1 = float(parameters["roi_p1"])
    roi_p2 = float(parameters["roi_p2"])
    roi_p3 = float(parameters["roi_p3"])
    roi_t1 = int(parameters["roi_t1"])
    roi_t2 = int(parameters["roi_t2"])
    roi_t3 = int(parameters["roi_t3"])
    return {
        "0": round(roi_p1 + roi_p2 + roi_p3, 6),
        str(roi_t3): round(roi_p1 + roi_p2, 6),
        str(roi_t3 + roi_t2): round(roi_p1, 6),
        str(roi_t3 + roi_t2 + roi_t1): 0.0,
    }


def _is_exit_parameter(name: str, parameter: BaseParameter) -> bool:
    if parameter.space is not None:
        return parameter.space in {"sell", "exit"}
    return name.startswith(("sell_", "exit_"))


def _average_trade_duration_minutes(result: BacktestResult) -> Decimal:
    total_seconds = Decimal("0")
    counted = 0
    for trade in result.trades:
        try:
            delta = trade.exit_time - trade.entry_time
            total_seconds += Decimal(str(delta.total_seconds()))
            counted += 1
        except (TypeError, AttributeError):
            continue
    if counted == 0:
        return Decimal("0")
    return (total_seconds / Decimal(counted)) / Decimal("60")


def _daily_returns(result: BacktestResult) -> list[Decimal]:
    buckets: dict[object, Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in result.trades:
        try:
            day = pd.Timestamp(trade.exit_time).date()
        except (TypeError, ValueError):
            day = None
        buckets[day] += trade.net_pl
    return list(buckets.values())


def _sharpe_like(returns: list[Decimal], *, downside_only: bool) -> Decimal:
    if len(returns) < 2:
        return Decimal("0")
    values = [float(value) for value in returns]
    mean = sum(values) / len(values)
    if downside_only:
        spread = (sum(min(0.0, value) ** 2 for value in values) / len(values)) ** 0.5
    else:
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        spread = variance ** 0.5
    if spread == 0:
        return Decimal("0")
    return Decimal(str((mean / spread) * (len(values) ** 0.5)))


def compute_hyperopt_objective(result: BacktestResult, loss_name: str) -> Decimal:
    """Objective is always higher-is-better, computed from the real BacktestResult."""
    if loss_name not in HYPEROPT_LOSS_FUNCTIONS:
        raise ValueError(f"Unsupported hyperopt loss function: {loss_name!r}")
    net_pl = result.net_pl
    drawdown = result.max_drawdown
    drawdown_rate = result.max_drawdown_rate
    if loss_name == "OnlyProfitHyperOptLoss":
        return net_pl
    if loss_name == "ShortTradeDurHyperOptLoss":
        return net_pl - (_average_trade_duration_minutes(result) / Decimal("60"))
    if loss_name == "SharpeHyperOptLoss":
        return _sharpe_like([trade.net_pl for trade in result.trades], downside_only=False)
    if loss_name == "SharpeHyperOptLossDaily":
        return _sharpe_like(_daily_returns(result), downside_only=False)
    if loss_name == "SortinoHyperOptLoss":
        return _sharpe_like([trade.net_pl for trade in result.trades], downside_only=True)
    if loss_name == "SortinoHyperOptLossDaily":
        return _sharpe_like(_daily_returns(result), downside_only=True)
    if loss_name == "CalmarHyperOptLoss":
        return net_pl / drawdown if drawdown > 0 else net_pl
    if loss_name == "MaxDrawDownHyperOptLoss":
        return -drawdown
    if loss_name in ("MaxDrawDownRelativeHyperOptLoss", "MaxDrawDownPerPairHyperOptLoss"):
        return -drawdown_rate
    if loss_name == "MultiMetricHyperOptLoss":
        return net_pl - drawdown + (result.win_rate * Decimal("100"))
    return net_pl - drawdown - result.total_costs  # ProfitDrawDownHyperOptLoss


def run_strategy_hyperopt(
    candles: pd.DataFrame,
    informative_candles: dict[str, pd.DataFrame],
    instrument: OandaInstrument,
    *,
    pair: str,
    strategy_class: str,
    timeframe: str,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    spread: Decimal,
    stop_pips: Decimal = Decimal("0.5"),
    slippage: Decimal = Decimal("0"),
    financing_rate_per_day: Decimal = Decimal("0"),
    quote_to_account_rate: Decimal = Decimal("1"),
    max_attempts: int,
    hyperopt_loss: str,
    on_attempt: Callable[[int, int], None] | None = None,
    on_candidate: Callable[[int, int, dict[str, object]], None] | None = None,
    freqai_config: dict[str, object] | None = None,
    freqai_predictions: dict[int, dict[str, object]] | None = None,
    freqai_target_column: str | None = None,
    indicator_context: pd.DataFrame | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[dict[str, object]]:
    """Optimize entry parameters and the Freqtrade-style ROI space on held-out candles."""
    if len(candles) < 40:
        raise ValueError("strategy hyperopt requires at least 40 candles")
    strategy = load_strategy(
        strategy_class,
        timeframe,
        pair,
        config_overrides={"freqai": freqai_config} if freqai_config is not None else None,
    )
    class_attributes: dict[str, object] = {}
    for strategy_type in reversed(type(strategy).__mro__):
        class_attributes.update(vars(strategy_type))
    parameters = {
        name: value
        for name, value in class_attributes.items()
        if isinstance(value, BaseParameter)
        and value.optimize
        and not _is_exit_parameter(name, value)
    }
    regression_parameter_names = set(
        getattr(strategy, "freqai_hyperopt_parameters", ())
    )
    classifier_parameter_names = set(
        getattr(strategy, "freqai_classifier_hyperopt_parameters", ())
    )
    all_freqai_parameter_names = (
        regression_parameter_names | classifier_parameter_names
    )
    prediction_is_classifier = (
        freqai_predictions is not None
        and freqai_target_column is not None
        and any(
            isinstance(row.get(freqai_target_column), str)
            for row in freqai_predictions.values()
        )
    )
    if prediction_is_classifier:
        inactive_names = regression_parameter_names
    elif freqai_predictions is not None:
        inactive_names = classifier_parameter_names
    else:
        inactive_names = all_freqai_parameter_names
    parameters = {
        name: parameter
        for name, parameter in parameters.items()
        if name not in inactive_names
    }
    original_minimal_roi = getattr(strategy, "minimal_roi", {})
    if not isinstance(original_minimal_roi, dict):
        raise ValueError("strategy minimal_roi must be a mapping of minutes to returns")
    optimize_roi = bool(original_minimal_roi)
    if not parameters and not optimize_roi:
        raise ValueError(f"{strategy_class} has no optimizable Freqtrade parameters")
    informative_timeframes = strategy_informative_timeframes(strategy, pair)
    missing_timeframes = set(informative_timeframes) - set(informative_candles)
    if missing_timeframes:
        raise ValueError(f"Missing informative candles for: {', '.join(sorted(missing_timeframes))}")
    split_index = max(20, min(len(candles) - 20, len(candles) // 2))
    roi_volatility_per_5m = _estimate_roi_volatility_per_5m(
        candles.iloc[:split_index], timeframe
    )
    if freqai_predictions is not None:
        if freqai_config is None or freqai_target_column is None:
            raise ValueError("cached FreqAI predictions require their config and target column")
        train = candles.iloc[:0]
        validation = candles
    else:
        train = candles.iloc[:split_index]
        validation = candles.iloc[split_index:]
    rng = random.Random()
    rows: list[dict[str, object]] = []

    def sample(parameter: BaseParameter) -> object:
        if hasattr(parameter, "low") and hasattr(parameter, "high"):
            low, high = parameter.low, parameter.high
            if isinstance(low, int) and isinstance(high, int):
                return rng.randint(low, high)
            decimals = int(getattr(parameter, "decimals", 6))
            return round(rng.uniform(float(low), float(high)), decimals)
        categories = getattr(parameter, "opt_range", None)
        if categories:
            return rng.choice(list(categories))
        raise ValueError(f"Unsupported optimization range for parameter {parameter.name}")

    total_attempts = max(1, min(max_attempts, 900))
    for attempt in range(1, total_attempts + 1):
        if should_stop is not None and should_stop():
            break
        values = {name: sample(parameter) for name, parameter in parameters.items()}
        roi_parameters = (
            _sample_roi_parameters(rng, timeframe, roi_volatility_per_5m)
            if optimize_roi else {}
        )
        minimal_roi = (
            _generate_roi_table(roi_parameters) if roi_parameters else None
        )

        def evaluate(
            data: pd.DataFrame,
            *,
            indicator_context: pd.DataFrame | None = None,
            candidate_values: dict[str, object] = values,
            candidate_minimal_roi: dict[str, float] | None = minimal_roi,
        ) -> BacktestResult:
            candidate_strategy = load_strategy(
                strategy_class,
                timeframe,
                pair,
                config_overrides={
                    "freqai": freqai_config
                } if freqai_config is not None else None,
            )
            for name, value in candidate_values.items():
                parameter = deepcopy(parameters[name])
                parameter.value = value
                setattr(candidate_strategy, name, parameter)
            if candidate_minimal_roi is not None:
                candidate_strategy.minimal_roi = candidate_minimal_roi
            if freqai_predictions is not None:
                from freqtrade.forex.strategy_execution import CachedFreqAIPredictions

                candidate_strategy.freqai_info = freqai_config
                candidate_strategy.freqai = CachedFreqAIPredictions(freqai_predictions)
            if data.empty:
                return BacktestResult(starting_balance, starting_balance, ())
            adapter = FreqtradeStrategyAdapter(candidate_strategy, pair, informative_candles)
            context = indicator_context if indicator_context is not None else data
            if "date" not in context.columns:
                raise ValueError("indicator context candles must include a date column")
            context = context.loc[context["date"] <= data["date"].iloc[-1]]
            if context.empty or context["date"].iloc[-1] < data["date"].iloc[-1]:
                raise ValueError("indicator context must cover all evaluated candles")
            adapter.prepare_backtest(context)
            return ForexBacktester(
                adapter,
                instrument,
                starting_balance=starting_balance,
                risk_fraction=risk_fraction,
                stop_pips=stop_pips,
                spread=spread,
                slippage=slippage,
                financing_rate_per_day=financing_rate_per_day,
                quote_to_account_rate=quote_to_account_rate,
            ).run(data, detail_candles=data)

        train_result = evaluate(train)
        validation_result = evaluate(
            validation,
            indicator_context=(
                indicator_context if indicator_context is not None else candles
            ),
        )
        objective = compute_hyperopt_objective(validation_result, hyperopt_loss)
        row = {
            "parameters": values,
            "minimal_roi": minimal_roi,
            "roi_parameters": roi_parameters,
            "roi_volatility_per_5m": (
                roi_volatility_per_5m if optimize_roi else None
            ),
            "roi_volatility_regime": (
                _roi_regime(roi_volatility_per_5m) if optimize_roi else None
            ),
            "objective": format(objective, ".2f"),
            "trainNetPl": format(train_result.net_pl, ".2f"),
            "validationNetPl": format(validation_result.net_pl, ".2f"),
            "validationDrawdown": format(validation_result.max_drawdown, ".2f"),
            "validationTrades": len(validation_result.trades),
            "coverage": 2,
            "trainTrades": len(train_result.trades),
        }
        rows.append(row)
        if on_candidate is not None:
            on_candidate(attempt, total_attempts, row)
        if on_attempt is not None:
            on_attempt(attempt, total_attempts)
    rows.sort(key=lambda row: Decimal(str(row["objective"])), reverse=True)
    if not rows:
        raise ValueError("strategy hyperopt was stopped before completing any attempt")
    return rows


@dataclass(frozen=True)
class AiHyperoptCandidate:
    entry_threshold: Decimal
    max_spread_pct: Decimal
    train_result: BacktestResult
    validation_result: BacktestResult
    objective: Decimal
    minimal_roi: dict[str, float]
    roi_parameters: dict[str, int | float]
    roi_volatility_per_5m: float
    roi_volatility_regime: str


@dataclass(frozen=True)
class AiHyperoptResult:
    best: AiHyperoptCandidate
    candidates: tuple[AiHyperoptCandidate, ...]
    candidates_tested: int
    train_candles: int
    validation_candles: int


def run_ai_hyperopt_robust(
    candles_by_pair: dict[str, pd.DataFrame],
    instruments: dict[str, OandaInstrument],
    *,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    spreads: dict[str, Decimal],
    max_attempts: int = 6,
    hyperopt_loss: str = DEFAULT_HYPEROPT_LOSS,
    timeframe: str = "5m",
    on_attempt: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[dict[str, object]]:
    """Aggregate the bounded search across at least two pairs and two periods."""
    if len(candles_by_pair) < 2:
        raise ValueError("robust AI hyperopt requires at least 2 pairs")
    aggregate: dict[tuple[str, str, str], dict[str, object]] = {}
    coverage = len(candles_by_pair) * 2
    progress = {"completed_slices": 0}
    training_volatilities = [
        _estimate_roi_volatility_per_5m(
            candles.iloc[: max(1, len(candles) // 2)], timeframe
        )
        for candles in candles_by_pair.values()
    ]
    aggregate_volatility = float(pd.Series(training_volatilities).median())

    def slice_on_attempt(done: int, total: int) -> None:
        if on_attempt is not None:
            on_attempt(progress["completed_slices"] * total + done, coverage * total)

    stopped_early = False
    for pair, candles in candles_by_pair.items():
        if len(candles) < 40:
            raise ValueError("robust AI hyperopt requires at least 40 candles per pair")
        midpoint = len(candles) // 2
        for period in (candles.iloc[:midpoint], candles.iloc[midpoint:]):
            if should_stop is not None and should_stop():
                stopped_early = True
                break
            result = run_ai_hyperopt(
                period,
                instruments[pair],
                starting_balance=starting_balance,
                risk_fraction=risk_fraction,
                spread=spreads[pair],
                max_attempts=max_attempts,
                hyperopt_loss=hyperopt_loss,
                timeframe=timeframe,
                roi_volatility_override=aggregate_volatility,
                roi_random_seed=1,
                on_attempt=slice_on_attempt,
                should_stop=should_stop,
            )
            progress["completed_slices"] += 1
            for candidate in result.candidates:
                roi_key = tuple(sorted(candidate.minimal_roi.items()))
                key = (
                    str(candidate.entry_threshold),
                    str(candidate.max_spread_pct),
                    repr(roi_key),
                )
                row = aggregate.setdefault(
                    key,
                    {
                        "entryThreshold": key[0],
                        "maxSpreadPct": key[1],
                        "minimal_roi": candidate.minimal_roi,
                        "roi_parameters": candidate.roi_parameters,
                        "roi_volatility_per_5m": candidate.roi_volatility_per_5m,
                        "roi_volatility_regime": candidate.roi_volatility_regime,
                        "objective": Decimal("0"),
                        "trainNetPl": Decimal("0"),
                        "validationNetPl": Decimal("0"),
                        "validationDrawdown": Decimal("0"),
                        "validationTrades": 0,
                        "coverage": 0,
                    },
                )
                row["objective"] += candidate.objective
                row["trainNetPl"] += candidate.train_result.net_pl
                row["validationNetPl"] += candidate.validation_result.net_pl
                row["validationDrawdown"] += candidate.validation_result.max_drawdown
                row["validationTrades"] += len(candidate.validation_result.trades)
                row["coverage"] += 1
        if stopped_early:
            break
    if not aggregate:
        raise ValueError("robust AI hyperopt produced no candidates before stopping")
    max_coverage = max(int(row["coverage"]) for row in aggregate.values())
    if not stopped_early and max_coverage != coverage:
        raise ValueError("robust hyperopt candidate coverage is incomplete")
    usable = [row for row in aggregate.values() if int(row["coverage"]) == max_coverage]
    ordered = sorted(usable, key=lambda row: row["objective"], reverse=True)
    return [
        {
            key: (format(value, ".2f") if isinstance(value, Decimal) else value)
            for key, value in row.items()
        }
        for row in ordered
    ]


def run_ai_hyperopt(
    candles: pd.DataFrame,
    instrument: OandaInstrument,
    *,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    spread: Decimal,
    slippage: Decimal = Decimal("0"),
    financing_rate_per_day: Decimal = Decimal("0"),
    quote_to_account_rate: Decimal = Decimal("1"),
    volatility_window: int = 5,
    atr_window: int = 14,
    entry_thresholds: tuple[Decimal, ...] = tuple(Decimal(index) / Decimal("20") for index in range(1, 31)),
    max_spreads: tuple[Decimal, ...] = tuple(Decimal(index) / Decimal("10") for index in range(1, 31)),
    max_attempts: int | None = None,
    hyperopt_loss: str = DEFAULT_HYPEROPT_LOSS,
    timeframe: str = "5m",
    roi_volatility_override: float | None = None,
    roi_random_seed: int | None = None,
    on_attempt: Callable[[int, int], None] | None = None,
    on_candidate: Callable[[int, int, AiHyperoptCandidate], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AiHyperoptResult:
    if len(candles) < 20:
        raise ValueError("AI hyperopt requires at least 20 complete candles")
    if hyperopt_loss not in HYPEROPT_LOSS_FUNCTIONS:
        raise ValueError(f"Unsupported hyperopt loss function: {hyperopt_loss!r}")
    split = max(10, min(len(candles) - 10, int(len(candles) * 0.7)))
    train = candles.iloc[:split]
    validation = candles.iloc[split:]
    roi_volatility_per_5m = (
        roi_volatility_override
        if roi_volatility_override is not None
        else _estimate_roi_volatility_per_5m(train, timeframe)
    )
    candidates: list[AiHyperoptCandidate] = []
    roi_rng = random.Random(roi_random_seed)

    search_space = list(product(entry_thresholds, max_spreads))
    if max_attempts is not None and max_attempts < len(search_space):
        attempt_count = max(1, max_attempts)
        if attempt_count == 1:
            search_space = [search_space[len(search_space) // 2]]
        else:
            last_index = len(search_space) - 1
            search_space = [
                search_space[round(index * last_index / (attempt_count - 1))]
                for index in range(attempt_count)
            ]
    total_attempts = len(search_space)
    for entry_threshold, max_spread_pct in search_space:
        if should_stop is not None and should_stop():
            break
        strategy_config = {
            "forex_ai_entry_threshold": str(entry_threshold),
            "forex_ai_max_spread_pct": str(max_spread_pct),
            "forex_ai_volatility_window": str(volatility_window),
            "forex_ai_atr_window": str(atr_window),
        }
        roi_parameters = _sample_roi_parameters(
            roi_rng, timeframe, roi_volatility_per_5m
        )
        minimal_roi = _generate_roi_table(roi_parameters)
        strategy = ForexAIStrategyBaseline(strategy_config)
        strategy.minimal_roi = minimal_roi
        backtester = dict(
            starting_balance=starting_balance,
            risk_fraction=risk_fraction,
            stop_pips=Decimal("0.5"),
            spread=spread,
            slippage=slippage,
            financing_rate_per_day=financing_rate_per_day,
            quote_to_account_rate=quote_to_account_rate,
        )
        train_result = ForexBacktester(strategy, instrument, **backtester).run(
            train, detail_candles=train
        )
        validation_result = ForexBacktester(strategy, instrument, **backtester).run(
            validation, detail_candles=validation
        )
        objective = compute_hyperopt_objective(validation_result, hyperopt_loss)
        candidate = AiHyperoptCandidate(
            entry_threshold,
            max_spread_pct,
            train_result,
            validation_result,
            objective,
            minimal_roi,
            roi_parameters,
            roi_volatility_per_5m,
            _roi_regime(roi_volatility_per_5m),
        )
        candidates.append(candidate)
        if on_candidate is not None:
            on_candidate(len(candidates), total_attempts, candidate)
        if on_attempt is not None:
            on_attempt(len(candidates), total_attempts)

    if not candidates:
        raise ValueError("AI hyperopt was stopped before completing any attempt")
    best = max(candidates, key=lambda candidate: candidate.objective)
    ordered_candidates = tuple(sorted(candidates, key=lambda candidate: candidate.objective, reverse=True))
    return AiHyperoptResult(best, ordered_candidates, len(candidates), len(train), len(validation))
