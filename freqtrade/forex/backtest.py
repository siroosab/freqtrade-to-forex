"""Deterministic candle backtest for the first OANDA forex strategy slice."""

import inspect
import re
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

import pandas as pd

from freqtrade.forex.margin import MarginSnapshot, margin_snapshot
from freqtrade.forex.models import ForexQuoteRate, OandaInstrument
from freqtrade.forex.risk import quote_to_account_rate
from freqtrade.forex.strategy_loop import CandleStrategy, Signal


@dataclass(frozen=True)
class BacktestTrade:
    instrument: str
    direction: Signal
    units: int
    entry_time: object
    exit_time: object
    entry_price: Decimal
    exit_price: Decimal
    gross_pl: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    financing_cost: Decimal
    net_pl: Decimal
    account_currency: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "direction": self.direction.value,
            "units": self.units,
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price),
            "gross_pl": str(self.gross_pl),
            "spread_cost": str(self.spread_cost),
            "slippage_cost": str(self.slippage_cost),
            "financing_cost": str(self.financing_cost),
            "net_pl": str(self.net_pl),
            "account_currency": self.account_currency,
        }


@dataclass(frozen=True)
class EquityPoint:
    timestamp: object
    balance: Decimal


@dataclass(frozen=True)
class BacktestPeriodSummary:
    period: str
    trades: int
    net_pl: Decimal
    wins: int
    losses: int
    draws: int


@dataclass(frozen=True)
class BacktestResult:
    starting_balance: Decimal
    ending_balance: Decimal
    trades: tuple[BacktestTrade, ...]
    account_currency: str | None = None
    margin_snapshot: MarginSnapshot | None = None

    @property
    def net_pl(self) -> Decimal:
        return self.ending_balance - self.starting_balance

    @property
    def total_costs(self) -> Decimal:
        return sum(
            (trade.spread_cost + trade.slippage_cost + trade.financing_cost for trade in self.trades),
            Decimal("0"),
        )
    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        winners = sum(trade.net_pl > 0 for trade in self.trades)
        return Decimal(winners) / Decimal(len(self.trades))

    @property
    def trade_output(self) -> tuple[dict[str, object], ...]:
        return tuple(trade.as_dict() for trade in self.trades)

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]:
        balance = self.starting_balance
        points = [EquityPoint(timestamp=None, balance=balance)]
        for trade in self._chronological_trades:
            balance += trade.net_pl
            points.append(EquityPoint(timestamp=trade.exit_time, balance=balance))
        return tuple(points)

    @property
    def max_drawdown(self) -> Decimal:
        peak = self.starting_balance
        drawdown = Decimal("0")
        for point in self.equity_curve:
            peak = max(peak, point.balance)
            drawdown = max(drawdown, peak - point.balance)
        return drawdown

    @property
    def max_drawdown_rate(self) -> Decimal:
        peak = self.starting_balance
        drawdown_rate = Decimal("0")
        for point in self.equity_curve:
            peak = max(peak, point.balance)
            if peak > 0:
                drawdown_rate = max(drawdown_rate, (peak - point.balance) / peak)
        return drawdown_rate

    @property
    def daily_breakdown(self) -> tuple[BacktestPeriodSummary, ...]:
        return self._breakdown("day")

    @property
    def weekly_breakdown(self) -> tuple[BacktestPeriodSummary, ...]:
        return self._breakdown("week")

    @property
    def monthly_breakdown(self) -> tuple[BacktestPeriodSummary, ...]:
        return self._breakdown("month")

    @property
    def _chronological_trades(self) -> tuple[BacktestTrade, ...]:
        return tuple(sorted(self.trades, key=lambda trade: self._utc_timestamp(trade.exit_time)))

    def _breakdown(self, period: str) -> tuple[BacktestPeriodSummary, ...]:
        grouped: dict[str, list[BacktestTrade]] = {}
        for trade in self._chronological_trades:
            timestamp = self._utc_timestamp(trade.exit_time)
            if period == "day":
                key = timestamp.date().isoformat()
            elif period == "week":
                iso = timestamp.isocalendar()
                key = f"{iso.year}-W{iso.week:02d}"
            elif period == "month":
                key = f"{timestamp.year:04d}-{timestamp.month:02d}"
            else:
                raise ValueError(f"unsupported breakdown period: {period}")
            grouped.setdefault(key, []).append(trade)
        return tuple(
            BacktestPeriodSummary(
                period=key,
                trades=len(period_trades),
                net_pl=sum((trade.net_pl for trade in period_trades), Decimal("0")),
                wins=sum(trade.net_pl > 0 for trade in period_trades),
                losses=sum(trade.net_pl < 0 for trade in period_trades),
                draws=sum(trade.net_pl == 0 for trade in period_trades),
            )
            for key, period_trades in grouped.items()
        )
    @staticmethod
    def _utc_timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize(UTC)
        return timestamp.tz_convert(UTC)


def validate_backtest_split(
    candles: pd.DataFrame,
    *,
    strategy: CandleStrategy,
    instrument: OandaInstrument,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    stop_pips: Decimal,
    spread: Decimal | dict[object, Decimal],
    train_fraction: Decimal = Decimal("0.7"),
    walk_forward_steps: int = 2,
    slippage: Decimal = Decimal("0"),
    financing_rate_per_day: Decimal = Decimal("0"),
    quote_to_account_rate: Decimal = Decimal("1"),
    take_profit_pips: Decimal | None = None,
    max_open_positions: int | None = None,
    account_currency: str | None = None,
    rates: tuple[ForexQuoteRate, ...] = (),
    fill_ratio: Decimal = Decimal("1"),
) -> dict[str, object]:
    if len(candles) < 4:
        raise ValueError("at least 4 candles are required for validation split")
    train_fraction = Decimal(str(train_fraction))
    if not Decimal("0") < train_fraction < Decimal("1"):
        raise ValueError("train_fraction must be between 0 and 1")
    if walk_forward_steps < 2:
        raise ValueError(
            "walk-forward and out-of-sample validation are mandatory: walk_forward_steps must be at least 2"
        )

    split_index = max(2, min(len(candles) - 2, int(len(candles) * float(train_fraction))))
    train_window = candles.iloc[:split_index]
    test_window = candles.iloc[split_index:]

    backtester = ForexBacktester(
        strategy,
        instrument,
        starting_balance=starting_balance,
        risk_fraction=risk_fraction,
        stop_pips=stop_pips,
        spread=spread,
        slippage=slippage,
        financing_rate_per_day=financing_rate_per_day,
        quote_to_account_rate=quote_to_account_rate,
        take_profit_pips=take_profit_pips,
        max_open_positions=max_open_positions,
        account_currency=account_currency,
        rates=rates,
        fill_ratio=fill_ratio,
    )

    train_result = backtester.run(train_window)
    test_result = backtester.run(test_window)

    wf_windows: list[dict[str, object]] = []
    if walk_forward_steps > 1:
        remaining = list(range(len(candles)))
        step_size = max(1, (len(remaining) - split_index) // walk_forward_steps)
        for step in range(walk_forward_steps):
            start = split_index + step * step_size
            end = min(len(candles), start + step_size)
            if start >= end:
                continue
            window = candles.iloc[start:end]
            wf_windows.append(
                {
                    "window_start": window.iloc[0]["date"],
                    "window_end": window.iloc[-1]["date"],
                    "result": backtester.run(window),
                }
            )
    return {
        "train": {
            "window_start": train_window.iloc[0]["date"],
            "window_end": train_window.iloc[-1]["date"],
            "result": train_result,
        },
        "test": {
            "window_start": test_window.iloc[0]["date"],
            "window_end": test_window.iloc[-1]["date"],
            "result": test_result,
        },
        "walk_forward": wf_windows,
    }


class ForexBacktester:
    def __init__(
        self,
        strategy: CandleStrategy,
        instrument: OandaInstrument,
        *,
        starting_balance: Decimal,
        risk_fraction: Decimal,
        stop_pips: Decimal,
        spread: Decimal | dict[object, Decimal],
        slippage: Decimal = Decimal("0"),
        financing_rate_per_day: Decimal = Decimal("0"),
        quote_to_account_rate: Decimal = Decimal("1"),
        take_profit_pips: Decimal | None = None,
        max_open_positions: int | None = None,
        account_currency: str | None = None,
        rates: tuple[ForexQuoteRate, ...] = (),
        fill_ratio: Decimal = Decimal("1"),
    ) -> None:
        if starting_balance <= 0 or not Decimal("0") < risk_fraction < Decimal("1"):
            raise ValueError("starting balance must be positive and risk fraction must be between 0 and 1")
        spread_values = [Decimal(value) for value in (spread.values() if isinstance(spread, dict) else [spread])]
        if (
            stop_pips <= 0
            or any(value < 0 for value in spread_values)
            or slippage < 0
            or financing_rate_per_day < 0
            or (take_profit_pips is not None and take_profit_pips <= 0)
            or not Decimal("0") < fill_ratio <= Decimal("1")
        ):
            raise ValueError("stop, spread, slippage, fill ratio, and financing rate must be valid")
        if quote_to_account_rate <= 0:
            raise ValueError("conversion rate must be positive")
        if max_open_positions is not None and max_open_positions < 1:
            raise ValueError("max_open_positions must be at least 1 when set")
        self.strategy = strategy
        self.instrument = instrument
        self.starting_balance = starting_balance
        self.risk_fraction = risk_fraction
        self.stop_pips = stop_pips
        self.spread = spread
        self.slippage = slippage
        self.financing_rate_per_day = financing_rate_per_day
        self.quote_to_account_rate = quote_to_account_rate
        self.take_profit_pips = take_profit_pips
        self.max_open_positions = max_open_positions
        self.account_currency = account_currency
        self.rates = rates
        self.fill_ratio = fill_ratio

    def run(
        self,
        candles: pd.DataFrame | dict[str, pd.DataFrame],
        *,
        detail_candles: pd.DataFrame | dict[str, pd.DataFrame] | None = None,
        strategies: dict[str, CandleStrategy] | None = None,
        instruments: dict[str, OandaInstrument] | None = None,
    ) -> BacktestResult:
        if isinstance(candles, dict):
            for strategy in (strategies or {}).values():
                self._validate_strategy_contract(strategy)
            return self._run_portfolio(
                candles,
                detail_candles=detail_candles,
                strategies=strategies,
                instruments=instruments,
            )

        self._validate_strategy_contract(self.strategy)
        return self._run_single(candles, detail_candles=detail_candles)

    @staticmethod
    def _validate_strategy_contract(strategy: CandleStrategy) -> None:
        sources: list[str] = []
        for candidate in (
            strategy,
            getattr(strategy, "signal", None),
            getattr(type(strategy), "signal", None),
        ):
            if candidate is None:
                continue
            try:
                sources.append(inspect.getsource(candidate))
            except (OSError, TypeError):
                continue

        if not sources:
            return

        for source in sources:
            normalized = "\n".join(source.lower().split())
            if re.search(r"self\.signal\s*\(", normalized):
                raise ValueError("strategy recursion detected: signal must not call itself recursively")
            if re.search(r"shift\s*\(\s*-\d+", normalized) or re.search(
                r"shift\s*\(\s*periods\s*=\s*-\d+", normalized
            ):
                raise ValueError("strategy appears to use lookahead or future data in signal()")

    def _run_portfolio(
        self,
        candles_by_symbol: dict[str, pd.DataFrame],
        *,
        detail_candles: pd.DataFrame | dict[str, pd.DataFrame] | None,
        strategies: dict[str, CandleStrategy] | None,
        instruments: dict[str, OandaInstrument] | None,
    ) -> BacktestResult:
        balance = self.starting_balance
        trades: list[BacktestTrade] = []
        portfolio_open_symbols: set[str] = set()

        for symbol, candles in candles_by_symbol.items():
            strategy = (strategies or {}).get(symbol, self.strategy)
            instrument = (instruments or {}).get(symbol, self.instrument)
            detail = None if not isinstance(detail_candles, dict) else detail_candles.get(symbol)
            if detail is None and isinstance(detail_candles, pd.DataFrame):
                detail = detail_candles

            balance, symbol_trades, portfolio_open_symbols = self._run_single_for_symbol(
                candles,
                strategy=strategy,
                instrument=instrument,
                balance=balance,
                detail_candles=detail,
                open_symbols=portfolio_open_symbols,
                portfolio_mode=True,
            )
            trades.extend(symbol_trades)

        return self._finalize_result(balance, tuple(trades))

    def _run_single(
        self,
        candles: pd.DataFrame,
        *,
        detail_candles: pd.DataFrame | None = None,
    ) -> BacktestResult:
        balance, trades, _ = self._run_single_for_symbol(
            candles,
            strategy=self.strategy,
            instrument=self.instrument,
            balance=self.starting_balance,
            detail_candles=detail_candles,
            open_symbols=set(),
            portfolio_mode=False,
        )
        return self._finalize_result(balance, tuple(trades))

    def _run_single_for_symbol(
        self,
        candles: pd.DataFrame,
        *,
        strategy: CandleStrategy,
        instrument: OandaInstrument,
        balance: Decimal,
        detail_candles: pd.DataFrame | None,
        open_symbols: set[str],
        portfolio_mode: bool,
    ) -> tuple[Decimal, list[BacktestTrade], set[str]]:
        required = {"date", "open", "high", "low", "close"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"backtest candles missing columns: {', '.join(sorted(missing))}")
        if detail_candles is not None and not {"date", "high", "low"}.issubset(detail_candles.columns):
            raise ValueError("detail candles require date, high, and low columns")

        trades: list[BacktestTrade] = []
        position: tuple[Signal, int, Decimal, object, object] | None = None
        for index in range(len(candles)):
            window = candles.iloc[: index + 1]
            signal = strategy.signal(window)
            candle = candles.iloc[index]
            intrabar_closed = False
            if position and detail_candles is not None and index > 0:
                trigger = self._intrabar_trigger(
                    position,
                    detail_candles,
                    start=position[4],
                    end=candle["date"],
                    instrument=instrument,
                )
                if trigger is not None:
                    trade = self._close_at_trigger(position, trigger, instrument=instrument)
                    trades.append(trade)
                    balance += trade.net_pl
                    position = None
                    intrabar_closed = True
                    if not portfolio_mode:
                        open_symbols.discard(instrument.name)
            if position and self._opposite(position[0], signal):
                trade = self._close(position, candle, instrument=instrument)
                trades.append(trade)
                balance += trade.net_pl
                position = None
                if not portfolio_mode:
                    open_symbols.discard(instrument.name)
            if (
                not intrabar_closed
                and position is None
                and signal is not Signal.FLAT
                and index + 1 < len(candles)
                and (self.max_open_positions is None or len(open_symbols) < self.max_open_positions)
            ):
                entry_time = candles.iloc[index + 1]["date"]
                entry_spread = self._spread_for(entry_time)
                entry = Decimal(str(candles.iloc[index + 1]["open"]))
                entry = entry + entry_spread / 2 if signal is Signal.LONG else entry - entry_spread / 2
                stop_distance = self.stop_pips * instrument.pip_size
                raw_units = balance * self.risk_fraction / (stop_distance * self.quote_to_account_rate)
                units = int(raw_units)
                if units > 0:
                    requested_units = units if signal is Signal.LONG else -units
                    filled_units = self._filled_units(requested_units, entry_time)
                    if filled_units != 0:
                        position = (
                            signal,
                            filled_units,
                            entry,
                            candle["date"],
                            entry_time,
                        )
                        open_symbols.add(instrument.name)
                        if detail_candles is not None and index + 2 < len(candles):
                            trigger = self._intrabar_trigger(
                                position,
                                detail_candles,
                                start=position[4],
                                end=candles.iloc[index + 2]["date"],
                                instrument=instrument,
                            )
                            if trigger is not None:
                                trade = self._close_at_trigger(position, trigger, instrument=instrument)
                                trades.append(trade)
                                balance += trade.net_pl
                                position = None
                                if not portfolio_mode:
                                    open_symbols.discard(instrument.name)
        if position:
            trigger = None
            if detail_candles is not None:
                trigger = self._intrabar_trigger(
                    position,
                    detail_candles,
                    start=position[4],
                    end=pd.Timestamp.max.tz_localize("UTC"),
                    instrument=instrument,
                )
            trade = self._close_at_trigger(position, trigger, instrument=instrument) if trigger is not None else self._close(
                position, candles.iloc[-1], instrument=instrument
            )
            trades.append(trade)
            balance += trade.net_pl
            if not portfolio_mode:
                open_symbols.discard(instrument.name)
        return balance, trades, open_symbols

    def _finalize_result(self, balance: Decimal, trades: tuple[BacktestTrade, ...]) -> BacktestResult:
        converted_trades = tuple(self._convert_trade_to_account_currency(trade) for trade in trades)
        if self.account_currency is None or not self.rates:
            ending_balance = balance
        else:
            ending_balance = self.starting_balance + sum((trade.net_pl for trade in converted_trades), Decimal("0"))
        margin = self._margin_snapshot_for(ending_balance, converted_trades)
        return BacktestResult(
            starting_balance=self.starting_balance,
            ending_balance=ending_balance,
            trades=converted_trades,
            account_currency=self.account_currency,
            margin_snapshot=margin,
        )

    def _convert_trade_to_account_currency(self, trade: BacktestTrade) -> BacktestTrade:
        if self.account_currency is None or not self.rates:
            return trade
        instrument = OandaInstrument(
            name=trade.instrument,
            display_name=trade.instrument.replace("_", "/"),
            pip_location=-4,
            display_precision=5,
            trade_units_precision=0,
            minimum_trade_size=Decimal("1"),
            base_currency=trade.instrument.split("_")[0],
            quote_currency=trade.instrument.split("_")[1],
        )
        if self.account_currency.upper() == instrument.quote_currency.upper():
            return BacktestTrade(
                instrument=trade.instrument,
                direction=trade.direction,
                units=trade.units,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                gross_pl=trade.gross_pl,
                spread_cost=trade.spread_cost,
                slippage_cost=trade.slippage_cost,
                financing_cost=trade.financing_cost,
                net_pl=trade.net_pl,
                account_currency=self.account_currency,
            )
        conversion = quote_to_account_rate(
            instrument=instrument,
            account_currency=self.account_currency,
            rates=self.rates,
        )
        return BacktestTrade(
            instrument=trade.instrument,
            direction=trade.direction,
            units=trade.units,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            gross_pl=trade.gross_pl * conversion,
            spread_cost=trade.spread_cost * conversion,
            slippage_cost=trade.slippage_cost * conversion,
            financing_cost=trade.financing_cost * conversion,
            net_pl=trade.net_pl * conversion,
            account_currency=self.account_currency,
        )

    def _margin_snapshot_for(self, balance: Decimal, trades: tuple[BacktestTrade, ...]) -> MarginSnapshot | None:
        if self.account_currency is None:
            return None
        return margin_snapshot(
            equity=balance,
            positions=(),
            leverage=Decimal("1"),
        )

    def _close(
        self,
        position: tuple[Signal, int, Decimal, object, object],
        candle: pd.Series,
        *,
        instrument: OandaInstrument,
    ) -> BacktestTrade:
        direction, units, entry, entry_time, _ = position
        entry_spread = self._spread_for(entry_time)
        spread = self._spread_for(candle["date"])
        raw_exit = Decimal(str(candle["close"]))
        exit_price = raw_exit - spread / 2 if direction is Signal.LONG else raw_exit + spread / 2
        exit_price += -self.slippage if direction is Signal.LONG else self.slippage
        entry_price = entry + self.slippage if direction is Signal.LONG else entry - self.slippage
        price_pl = (exit_price - entry_price) * units
        spread_cost = entry_spread * abs(units)
        slippage_cost = self.slippage * Decimal(2) * abs(units)
        financing_cost = self._financing_cost(entry_time, candle["date"], entry, units)
        gross = price_pl + spread_cost + slippage_cost
        net_pl = price_pl - financing_cost
        return BacktestTrade(
            instrument=instrument.name,
            direction=direction,
            units=units,
            entry_time=entry_time,
            exit_time=candle["date"],
            entry_price=entry_price,
            exit_price=exit_price,
            gross_pl=gross,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            financing_cost=financing_cost,
            net_pl=net_pl,
            account_currency=self.account_currency,
        )

    def _filled_units(self, units: int, at_time: object) -> int:
        if self.fill_ratio == Decimal("1"):
            return units
        filled_abs = int((Decimal(abs(units)) * self.fill_ratio).to_integral_value())
        if filled_abs == 0:
            return 0
        return filled_abs if units > 0 else -filled_abs

    def _spread_for(self, timestamp: object) -> Decimal:
        if not isinstance(self.spread, dict):
            return self.spread
        values = tuple(self.spread.values())
        if not values:
            raise ValueError("no spread values configured")
        ts = str(timestamp)
        if ts in self.spread:
            return Decimal(self.spread[ts])
        normalized = ts.replace("+00:00", "Z")
        if normalized in self.spread:
            return Decimal(self.spread[normalized])
        return values[-1]

    def _close_at_trigger(
        self,
        position: tuple[Signal, int, Decimal, object, object],
        trigger: tuple[object, Decimal],
        *,
        instrument: OandaInstrument,
    ) -> BacktestTrade:
        trigger_time, actual_exit = trigger
        spread = self._spread_for(trigger_time)
        raw_exit = actual_exit + spread / 2 + self.slippage
        if position[0] is Signal.SHORT:
            raw_exit = actual_exit - spread / 2 - self.slippage
        candle = pd.Series({"date": trigger_time, "close": raw_exit})
        return self._close(position, candle, instrument=instrument)

    def _intrabar_trigger(
        self,
        position: tuple[Signal, int, Decimal, object, object],
        detail_candles: pd.DataFrame,
        *,
        start: object,
        end: object,
        instrument: OandaInstrument,
    ) -> tuple[object, Decimal] | None:
        direction, _, entry, _, _ = position
        stop_distance = self.stop_pips * instrument.pip_size
        stop = entry - stop_distance if direction is Signal.LONG else entry + stop_distance
        target = None
        if self.take_profit_pips is not None:
            target_distance = self.take_profit_pips * instrument.pip_size
            target = entry + target_distance if direction is Signal.LONG else entry - target_distance
        start_time = self._utc_timestamp(start)
        end_time = self._utc_timestamp(end)
        for _, detail in detail_candles.sort_values("date").iterrows():
            timestamp = self._utc_timestamp(detail["date"])
            if not start_time <= timestamp < end_time:
                continue
            high = Decimal(str(detail["high"]))
            low = Decimal(str(detail["low"]))
            if direction is Signal.LONG:
                if low <= stop:
                    return detail["date"], stop
                if target is not None and high >= target:
                    return detail["date"], target
            else:
                if high >= stop:
                    return detail["date"], stop
                if target is not None and low <= target:
                    return detail["date"], target
        return None

    @staticmethod
    def _utc_timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")

    def _financing_cost(self, entry_time: object, exit_time: object, entry: Decimal, units: int) -> Decimal:
        try:
            days = max((pd.Timestamp(exit_time) - pd.Timestamp(entry_time)).total_seconds() / 86400, 0)
        except (TypeError, ValueError):
            days = 0
        direction = "short" if units < 0 else "long"
        notional = entry * abs(units) * self.quote_to_account_rate
        cost = notional * self.financing_rate_per_day * Decimal(str(days))
        if direction == "short":
            return -cost
        return cost

    @staticmethod
    def _opposite(current: Signal, new: Signal) -> bool:
        return (current is Signal.LONG and new is Signal.SHORT) or (
            current is Signal.SHORT and new is Signal.LONG
        )

