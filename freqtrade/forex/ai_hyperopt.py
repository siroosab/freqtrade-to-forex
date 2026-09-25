"""Bounded hyperopt for the safe FX AI baseline."""

from __future__ import annotations

from collections.abc import Callable
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

import pandas as pd

from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline
from freqtrade.forex.backtest import BacktestResult, ForexBacktester
from freqtrade.forex.models import OandaInstrument

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


@dataclass(frozen=True)
class AiHyperoptCandidate:
    entry_threshold: Decimal
    max_spread_pct: Decimal
    train_result: BacktestResult
    validation_result: BacktestResult
    objective: Decimal


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
    on_attempt: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[dict[str, object]]:
    """Aggregate the bounded search across at least two pairs and two periods."""
    if len(candles_by_pair) < 2:
        raise ValueError("robust AI hyperopt requires at least 2 pairs")
    aggregate: dict[tuple[str, str], dict[str, object]] = {}
    coverage = len(candles_by_pair) * 2
    progress = {"completed_slices": 0}

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
                on_attempt=slice_on_attempt,
                should_stop=should_stop,
            )
            progress["completed_slices"] += 1
            for candidate in result.candidates:
                key = (str(candidate.entry_threshold), str(candidate.max_spread_pct))
                row = aggregate.setdefault(key, {"entryThreshold": key[0], "maxSpreadPct": key[1], "objective": Decimal("0"), "trainNetPl": Decimal("0"), "validationNetPl": Decimal("0"), "validationDrawdown": Decimal("0"), "validationTrades": 0, "coverage": 0})
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
        {key: (format(value, ".2f") if isinstance(value, Decimal) else value) for key, value in row.items()}
        for row in ordered
    ]


def run_ai_hyperopt(
    candles: pd.DataFrame,
    instrument: OandaInstrument,
    *,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    spread: Decimal,
    volatility_window: int = 5,
    atr_window: int = 14,
    entry_thresholds: tuple[Decimal, ...] = tuple(Decimal(index) / Decimal("20") for index in range(1, 31)),
    max_spreads: tuple[Decimal, ...] = tuple(Decimal(index) / Decimal("10") for index in range(1, 31)),
    max_attempts: int | None = None,
    hyperopt_loss: str = DEFAULT_HYPEROPT_LOSS,
    on_attempt: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AiHyperoptResult:
    if len(candles) < 20:
        raise ValueError("AI hyperopt requires at least 20 complete candles")
    if hyperopt_loss not in HYPEROPT_LOSS_FUNCTIONS:
        raise ValueError(f"Unsupported hyperopt loss function: {hyperopt_loss!r}")
    split = max(10, min(len(candles) - 10, int(len(candles) * 0.7)))
    train = candles.iloc[:split]
    validation = candles.iloc[split:]
    candidates: list[AiHyperoptCandidate] = []

    search_space = list(product(entry_thresholds, max_spreads))
    if max_attempts is not None:
        search_space = search_space[: max(1, min(max_attempts, len(search_space)))]
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
        strategy = ForexAIStrategyBaseline(strategy_config)
        backtester = dict(
            starting_balance=starting_balance,
            risk_fraction=risk_fraction,
            stop_pips=Decimal("0.5"),
            spread=spread,
            slippage=Decimal("0"),
            financing_rate_per_day=Decimal("0"),
            quote_to_account_rate=Decimal("1"),
        )
        train_result = ForexBacktester(strategy, instrument, **backtester).run(train)
        validation_result = ForexBacktester(strategy, instrument, **backtester).run(validation)
        objective = compute_hyperopt_objective(validation_result, hyperopt_loss)
        candidates.append(AiHyperoptCandidate(entry_threshold, max_spread_pct, train_result, validation_result, objective))
        if on_attempt is not None:
            on_attempt(len(candidates), total_attempts)

    if not candidates:
        raise ValueError("AI hyperopt was stopped before completing any attempt")
    best = max(candidates, key=lambda candidate: candidate.objective)
    ordered_candidates = tuple(sorted(candidates, key=lambda candidate: candidate.objective, reverse=True))
    return AiHyperoptResult(best, ordered_candidates, len(candidates), len(train), len(validation))
