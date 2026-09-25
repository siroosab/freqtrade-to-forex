"""Small deterministic grid hyperopt for the forex backtest slice."""

import json
import random
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from pathlib import Path

import pandas as pd

from freqtrade.forex.backtest import BacktestResult, ForexBacktester, validate_backtest_split
from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_loop import EmaCrossStrategy


@dataclass(frozen=True)
class HyperoptCandidate:
    fast_period: int
    slow_period: int
    stop_pips: Decimal
    risk_fraction: Decimal
    result: BacktestResult
    max_drawdown: Decimal
    objective: Decimal


@dataclass(frozen=True)
class HyperoptResult:
    best: HyperoptCandidate
    candidates_tested: int
    validation: dict[str, object] | None = None


class ForexHyperopt:
    def __init__(
        self,
        instrument: OandaInstrument,
        *,
        starting_balance: Decimal,
        risk_fraction: Decimal,
        spread: Decimal,
        slippage: Decimal,
        financing_rate_per_day: Decimal,
        quote_to_account_rate: Decimal,
    ) -> None:
        self.instrument = instrument
        self.starting_balance = starting_balance
        self.risk_fraction = risk_fraction
        self.spread = spread
        self.slippage = slippage
        self.financing_rate_per_day = financing_rate_per_day
        self.quote_to_account_rate = quote_to_account_rate

    def run(
        self,
        candles: pd.DataFrame,
        *,
        fast_periods: tuple[int, ...] = (8, 12, 16),
        slow_periods: tuple[int, ...] = (26, 32, 40),
        stop_pips: tuple[Decimal, ...] = (Decimal("8"), Decimal("10"), Decimal("15")),
        train_fraction: Decimal = Decimal("0.7"),
        walk_forward_steps: int = 2,
        parameter_space: Iterable[dict[str, object]] | None = None,
        objective_fn: Callable[[BacktestResult, Decimal], Decimal] | None = None,
        loss_function: Callable[[BacktestResult, Decimal], Decimal | float | int] | None = None,
        random_seed: int | None = None,
        save_path: str | Path | None = None,
        resume_from: str | Path | None = None,
        pair_names: tuple[str, ...] | None = None,
        periods: tuple[str, ...] | None = None,
        parallel: bool = False,
        workers: int = 1,
    ) -> HyperoptResult:
        if pair_names is not None and len(pair_names) < 2:
            raise ValueError("pair robustness guard: at least 2 pairs are required before accepting a hyperopt result")
        if periods is not None and len(periods) < 2:
            raise ValueError("period robustness guard: at least 2 periods are required before accepting a hyperopt result")

        if walk_forward_steps < 2:
            raise ValueError(
                "walk-forward and out-of-sample validation are mandatory: walk_forward_steps must be at least 2"
            )

        if parallel and workers < 1:
            raise ValueError("parallel hyperopt requires at least 1 worker")

        if parameter_space is None:
            parameter_space = (
                {"fast_period": fast, "slow_period": slow, "stop_pips": stop, "risk_fraction": self.risk_fraction}
                for fast, slow, stop in product(fast_periods, slow_periods, stop_pips)
            )

        ordered_space = list(parameter_space)
        if random_seed is not None:
            shuffled = list(ordered_space)
            random.Random(random_seed).shuffle(shuffled)
            ordered_space = shuffled

        if resume_from is not None:
            resume_path = Path(resume_from)
            if resume_path.exists():
                self._load_resume_state(resume_path)

        candidates: list[HyperoptCandidate] = []
        train_window = candles.iloc[: max(2, min(len(candles) - 2, int(len(candles) * float(train_fraction))))]

        def _evaluate(params: dict[str, object]) -> HyperoptCandidate:
            fast = int(params.get("fast_period", 8))
            slow = int(params.get("slow_period", 26))
            if fast >= slow:
                raise ValueError("fast_period must be lower than slow_period")
            candidate_stop = Decimal(str(params.get("stop_pips", Decimal("10"))))
            candidate_risk = Decimal(str(params.get("risk_fraction", self.risk_fraction)))
            if candidate_risk <= 0 or candidate_risk >= 1:
                raise ValueError("candidate risk_fraction must be between 0 and 1")
            result = ForexBacktester(
                EmaCrossStrategy(fast_period=fast, slow_period=slow),
                self.instrument,
                starting_balance=self.starting_balance,
                risk_fraction=candidate_risk,
                stop_pips=candidate_stop,
                spread=self.spread,
                slippage=self.slippage,
                financing_rate_per_day=self.financing_rate_per_day,
                quote_to_account_rate=self.quote_to_account_rate,
            ).run(train_window)
            drawdown = self._max_drawdown(result)
            objective = self._resolve_objective(result, drawdown, objective_fn=objective_fn, loss_function=loss_function)
            return HyperoptCandidate(fast, slow, candidate_stop, candidate_risk, result, drawdown, objective)

        if parallel:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                candidates = list(executor.map(_evaluate, ordered_space))
        else:
            for params in ordered_space:
                try:
                    candidates.append(_evaluate(params))
                except ValueError:
                    continue

        if not candidates:
            raise ValueError("no valid hyperopt candidates")
        if loss_function is not None:
            best = min(candidates, key=lambda candidate: candidate.objective)
        else:
            best = max(candidates, key=lambda candidate: candidate.objective)
        validation = validate_backtest_split(
            candles,
            strategy=EmaCrossStrategy(fast_period=best.fast_period, slow_period=best.slow_period),
            instrument=self.instrument,
            starting_balance=self.starting_balance,
            risk_fraction=best.risk_fraction,
            stop_pips=best.stop_pips,
            spread=self.spread,
            train_fraction=train_fraction,
            walk_forward_steps=walk_forward_steps,
            slippage=self.slippage,
            financing_rate_per_day=self.financing_rate_per_day,
            quote_to_account_rate=self.quote_to_account_rate,
        )
        if save_path is not None:
            self._save_resume_state(Path(save_path), best, candidates, random_seed=random_seed)
        return HyperoptResult(best=best, candidates_tested=len(candidates), validation=validation)

    @staticmethod
    def _load_resume_state(path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    @staticmethod
    def _save_resume_state(
        path: Path,
        best: HyperoptCandidate,
        candidates: list[HyperoptCandidate],
        *,
        random_seed: int | None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "random_seed": random_seed,
            "best": {
                "fast_period": best.fast_period,
                "slow_period": best.slow_period,
                "stop_pips": str(best.stop_pips),
                "risk_fraction": str(best.risk_fraction),
                "objective": str(best.objective),
            },
            "candidates": [
                {
                    "fast_period": candidate.fast_period,
                    "slow_period": candidate.slow_period,
                    "stop_pips": str(candidate.stop_pips),
                    "risk_fraction": str(candidate.risk_fraction),
                    "objective": str(candidate.objective),
                }
                for candidate in candidates
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _resolve_objective(
        result: BacktestResult,
        drawdown: Decimal,
        *,
        objective_fn: Callable[[BacktestResult, Decimal], Decimal | float | int] | None,
        loss_function: Callable[[BacktestResult, Decimal], Decimal | float | int] | None,
    ) -> Decimal:
        if loss_function is not None:
            value = loss_function(result, drawdown)
            return Decimal(str(value))
        if objective_fn is not None:
            value = objective_fn(result, drawdown)
            return Decimal(str(value))
        return ForexHyperopt._default_objective(result, drawdown)

    @staticmethod
    def _default_objective(result: BacktestResult, drawdown: Decimal) -> Decimal:
        return result.net_pl - drawdown * Decimal("0.5") - result.total_costs * Decimal("0.1")

    @staticmethod
    def _max_drawdown(result: BacktestResult) -> Decimal:
        peak = result.starting_balance
        balance = peak
        max_drawdown = Decimal("0")
        for trade in result.trades:
            balance += trade.net_pl
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
        return max_drawdown
