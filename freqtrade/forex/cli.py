"""Standalone read-only CLI for OANDA Practice connectivity checks."""

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

from freqtrade.forex.config import OandaSettings, load_forex_config, save_forex_config
from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.ai_hyperopt import (
    DEFAULT_HYPEROPT_LOSS,
    HYPEROPT_LOSS_FUNCTIONS,
    compute_hyperopt_objective,
    run_ai_hyperopt,
)
from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline
from freqtrade.forex.health import OandaHealthCheck, OandaHealthReport
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.ledger import PaperLedger, PaperPerformance
from freqtrade.forex.execution import ExecutionMode, OandaExecutionGateway
from freqtrade.forex.paper import DryRunSession
from freqtrade.forex.practice_runs import PracticeRunRecord, PracticeRunRecorder
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.runner import DryRunPortfolioWorker, DryRunWorker, WorkerConfig
from freqtrade.forex.strategy_loop import DryRunStrategyLoop, EmaCrossStrategy, Signal
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    freqtrade_timeframe,
    load_strategy,
    strategy_informative_candle_count,
    strategy_informative_timeframes,
)
from freqtrade.strategy.interface import IStrategy
from freqtrade.forex.oanda import OandaClient
from freqtrade.timeframe import timeframe_to_seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m freqtrade.forex")
    subparsers = parser.add_subparsers(dest="command", required=True)
    health = subparsers.add_parser("health", help="Run a read-only OANDA connectivity check")
    health.add_argument(
        "--instruments",
        default=None,
        help="Comma-separated instruments; defaults to OANDA_INSTRUMENTS",
    )
    report = subparsers.add_parser("paper-report", help="Show persisted paper trades and P/L")
    report.add_argument(
        "--ledger",
        default="user_data/oanda/paper.sqlite",
        help="SQLite paper ledger path",
    )
    backup = subparsers.add_parser("paper-backup", help="Create a consistent SQLite paper ledger backup")
    backup.add_argument("--ledger", default="user_data/oanda/paper.sqlite")
    backup.add_argument("--destination", required=True)
    setup = subparsers.add_parser(
        "setup",
        help="Create the non-interactive forex user directory and config skeleton",
    )
    setup.add_argument("--userdir", default="user_data")
    setup.add_argument("--config", default="user_data/config.json")
    setup.add_argument("--environment", choices=("practice", "live"), default="practice")
    setup.add_argument("--execution-mode", choices=("dry_run", "practice"), default="dry_run")
    setup.add_argument("--instruments", default="EUR_USD,GBP_USD")
    setup.add_argument("--risk-fraction", default="0.01")
    setup.add_argument("--force", action="store_true", help="Replace an existing config file")
    show_timeframes = subparsers.add_parser(
        "show-timeframes",
        help="Show configured and approved strategy timeframes for each pair",
    )
    show_timeframes.add_argument("--pair", default=None, help="Show one pair only")
    show_timeframes.add_argument("--config", default=None, help="Forex config path")
    dry_run = subparsers.add_parser("dry-run", help="Run the live-price paper strategy safely")
    dry_run.add_argument("--pair", default=None, help="Run one pair; defaults to all pairs in setup")
    dry_run.add_argument("--timeframe", default=None)
    dry_run.add_argument("--strategy", default=None, help="Override the pair's configured strategy class")
    dry_run.add_argument("--steps", type=int, default=1, help="Number of steps; default: 1")
    dry_run.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    dry_run.add_argument("--ledger", default="user_data/oanda/paper.sqlite")
    practice = subparsers.add_parser(
        "practice",
        help="Run an OANDA Practice preflight or submit one protected market order",
    )
    practice.add_argument(
        "--instruments",
        default=None,
        help="Comma-separated instruments; defaults to OANDA_INSTRUMENTS",
    )
    practice.add_argument("--pair", default="EUR/USD")
    practice.add_argument("--units", type=int, default=None)
    practice.add_argument("--stop-loss-price", default=None)
    practice.add_argument("--take-profit-price", default=None)
    practice.add_argument("--simulated-fill-price", default=None)
    practice.add_argument("--client-order-id", default=None)
    practice_report = subparsers.add_parser("practice-report", help="Show recorded Practice session results")
    practice_report.add_argument(
        "--runs",
        default="user_data/oanda/practice-runs.jsonl",
        help="JSONL Practice run record path",
    )
    practice_run = subparsers.add_parser(
        "practice-run",
        help="Run opt-in, multi-session Practice health checks and record results",
    )
    practice_run.add_argument("--sessions", default="london,new-york")
    practice_run.add_argument("--steps", type=int, default=1)
    practice_run.add_argument("--interval-seconds", type=float, default=300.0)
    practice_run.add_argument("--runs", default="user_data/oanda/practice-runs.jsonl")
    practice_run.add_argument("--instruments", default=None)
    backtest = subparsers.add_parser(
        "backtest", help="Run a read-only AI or EMA historical forex backtest"
    )
    backtest.add_argument("--pair", default="EUR/USD")
    backtest.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
    backtest.add_argument("--count", type=int, default=500)
    backtest.add_argument("--start", default=None, help="UTC ISO start for cached historical data")
    backtest.add_argument("--end", default=None, help="UTC ISO end for cached historical data")
    backtest.add_argument("--data-cache", default=None, help="JSON cache for raw and normalized candles")
    backtest.add_argument(
        "--refresh-data", action="store_true", help="Fetch from OANDA even when cache exists"
    )
    backtest.add_argument(
        "--strategy", default="ai", help="Built-in ai/ema or a Freqtrade strategy class"
    )
    backtest.add_argument(
        "--freqaimodel",
        choices=("ForexAIStrategyBaseline", "LightGBMRegressor", "LightGBMClassifier"),
        default=None,
        help="Reuse or train a FreqAI model for the selected strategy",
    )
    backtest.add_argument(
        "--ai-model", default=None, help="Load optimized AI parameters from a hyperopt JSON file"
    )
    backtest.add_argument("--model-dir", default="user_data/hyperopt_results")
    backtest.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--financing-rate-per-day", type=Decimal, default=Decimal("0"))
    hyperopt = subparsers.add_parser("hyperopt", help="Optimize AI strategy parameters read-only")
    hyperopt.add_argument("--pair", default="EUR/USD")
    hyperopt.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
    hyperopt.add_argument("--count", type=int, default=500)
    hyperopt.add_argument("--start", default=None, help="UTC ISO start for cached historical data")
    hyperopt.add_argument("--end", default=None, help="UTC ISO end for cached historical data")
    hyperopt.add_argument("--data-cache", default=None, help="JSON cache for raw and normalized candles")
    hyperopt.add_argument(
        "--refresh-data", action="store_true", help="Fetch from OANDA even when cache exists"
    )
    hyperopt.add_argument(
        "--epochs", type=int, default=30, help="Maximum AI parameter combinations to evaluate"
    )
    hyperopt.add_argument(
        "--freqaimodel",
        choices=("ForexAIStrategyBaseline", "LightGBMRegressor", "LightGBMClassifier"),
        default=None,
        help="AI model to optimize; LightGBM models save a trained booster",
    )
    hyperopt.add_argument(
        "--strategy",
        default=None,
        help="Custom Freqtrade strategy class; optimize its declared parameters",
    )
    hyperopt.add_argument(
        "--hyperopt-loss", choices=HYPEROPT_LOSS_FUNCTIONS, default=DEFAULT_HYPEROPT_LOSS
    )
    hyperopt.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    hyperopt.add_argument("--model-dir", default="user_data/hyperopt_results")
    download = subparsers.add_parser(
        "download-data", help="Download and cache OANDA historical candles"
    )
    download.add_argument("--pair", default="EUR/USD")
    download.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
    download.add_argument("--count", type=int, default=500)
    download.add_argument("--start", default=None)
    download.add_argument("--end", default=None)
    download.add_argument("--data-cache", default="user_data/data/oanda/candles.json")
    download.add_argument(
        "--refresh-data", action="store_true", help="Replace matching cached candles"
    )
    clear_cache = subparsers.add_parser(
        "cache-clear", help="Clear all or selected cached OANDA candles"
    )
    clear_cache.add_argument("--pair", default=None)
    clear_cache.add_argument("--timeframe", default=None, choices=("5m", "1h"))
    clear_cache.add_argument("--data-cache", default="user_data/data/oanda/candles.json")
    hyperopt.add_argument("--slippage", type=Decimal, default=Decimal("0"))
    hyperopt.add_argument("--financing-rate-per-day", type=Decimal, default=Decimal("0"))
    return parser


def run_setup(args: argparse.Namespace) -> int:
    userdir = Path(args.userdir)
    config_path = Path(args.config)
    if config_path.exists() and not args.force:
        raise ValueError(f"config already exists: {config_path}; use --force to replace it")
    userdir.mkdir(parents=True, exist_ok=True)
    for directory in ("data/oanda", "logs", "strategies", "backtest_results", "hyperopt_results"):
        (userdir / directory).mkdir(parents=True, exist_ok=True)
    instruments = [item.strip() for item in args.instruments.split(",") if item.strip()]
    if not instruments:
        raise ValueError("--instruments must contain at least one instrument")
    if args.environment == "live":
        raise ValueError("initial setup only supports the Practice environment; configure Live after review")
    payload = {
        "schema_version": 1,
        "exchange": {
            "name": "oanda",
            "oanda_environment": args.environment,
            "oanda_execution_mode": args.execution_mode,
            "oanda_token": "",
            "account_id": "",
            "pair_whitelist": instruments,
            "oanda_risk_fraction": args.risk_fraction,
            "oanda_transaction_cursor_path": f"{args.userdir}/oanda/transaction_cursor.json",
        },
        "timeframe": "5m",
        "setup": {"configured": False, "credentials_source": "ui"},
    }
    saved_path = save_forex_config(payload, config_path)
    print(json.dumps({
        "status": "ready",
        "userdir": str(userdir),
        "config": str(saved_path),
        "next": "start the API and open /setup to enter OANDA credentials",
    }, indent=2))
    return 0


async def run_health(settings: OandaSettings) -> OandaHealthReport:
    async with OandaClient(
        settings.token,
        settings.account_id,
        environment=settings.environment,
    ) as client:
        return await OandaHealthCheck(client).run(settings.instruments)


async def run_dry_run(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.steps < 1:
        raise ValueError("--steps must be at least 1")
    if settings.execution_mode != ExecutionMode.DRY_RUN:
        raise ValueError("set OANDA_EXECUTION_MODE=dry_run before starting")
    instrument_names = (
        (OandaMarketDataProvider.to_oanda_instrument(args.pair),)
        if args.pair
        else tuple(settings.instruments)
    )
    if not instrument_names:
        raise ValueError("setup must configure at least one instrument")
    selected_scopes: dict[str, tuple[str, str, dict[str, object]]] = {}
    for instrument_name in instrument_names:
        pair = instrument_name.replace("_", "/")
        approved = settings.pair_approved_revisions.get(instrument_name)
        if not isinstance(approved, dict):
            raise ValueError(
                f"{pair} has no approved strategy/timeframe. Approve a revision in /ai Review first."
            )
        timeframe = str(approved.get("timeframe", ""))
        strategy_class = str(approved.get("strategyClass", ""))
        configured_timeframe = settings.pair_timeframes.get(instrument_name)
        configured_strategy = settings.pair_strategies.get(instrument_name)
        if (
            not timeframe
            or not strategy_class
            or not configured_timeframe
            or not configured_strategy
            or freqtrade_timeframe(timeframe)
            != freqtrade_timeframe(configured_timeframe)
            or strategy_class != configured_strategy
            or approved.get("pair") != pair.upper()
        ):
            raise ValueError(
                f"{pair} has inconsistent approved and configured strategy/timeframe settings. "
                "Review and approve the current pair configuration again."
            )
        timeframe = configured_timeframe
        if args.timeframe and freqtrade_timeframe(args.timeframe) != freqtrade_timeframe(timeframe):
            raise ValueError(f"--timeframe cannot override the approved timeframe {timeframe} for {pair}")
        if args.strategy and args.strategy != strategy_class:
            raise ValueError(f"--strategy cannot override the approved strategy {strategy_class} for {pair}")
        hyperopt = approved.get("hyperopt")
        hyperopt = hyperopt if isinstance(hyperopt, dict) else {}
        parameters = hyperopt.get(
            "parameters", hyperopt.get("bestParameters", {})
        )
        selected_scopes[instrument_name] = (
            strategy_class,
            timeframe,
            parameters if isinstance(parameters, dict) else {},
        )
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments(instrument_names)
        metadata_by_name = {item.name: item for item in metadata}
        missing = set(instrument_names) - set(metadata_by_name)
        if missing:
            raise ValueError(f"instruments not available: {', '.join(sorted(missing))}")
        account = await client.get_account_summary()
        price_instruments = tuple(dict.fromkeys((*instrument_names, "GBP_USD")))
        prices = await client.get_prices(price_instruments)
        price_map = {price.instrument: price for price in prices}
        conversion = Decimal("1")
        if account.currency != "USD":
            gbp_usd = price_map.get("GBP_USD")
            if gbp_usd is None:
                raise ValueError("GBP_USD price is required for GBP account risk conversion")
            conversion = Decimal("1") / gbp_usd.midpoint
        ledger = PaperLedger(Path(args.ledger))
        session = DryRunSession(client, OandaExecutionGateway(settings, ExecutionMode.DRY_RUN), instrument_names, ledger=ledger)
        provider = OandaMarketDataProvider(client, settings)
        workers: list[DryRunWorker] = []
        for instrument_name in instrument_names:
            pair = instrument_name.replace("_", "/")
            strategy_class, timeframe, parameters = selected_scopes[instrument_name]
            strategy = load_strategy(
                strategy_class,
                freqtrade_timeframe(timeframe),
                pair,
                parameter_values=parameters,
            )
            adapter = FreqtradeStrategyAdapter(strategy, pair)
            loop = DryRunStrategyLoop(
                provider,
                session,
                adapter,
                metadata_by_name[instrument_name],
                account_equity=account.balance,
                risk_fraction=Decimal(settings.risk_fraction),
                stop_pips=args.stop_pips,
                quote_to_account_rate=conversion,
            )
            workers.append(DryRunWorker(
                loop,
                WorkerConfig(pair=pair, timeframe=timeframe, interval_seconds=float(timeframe_to_seconds(freqtrade_timeframe(timeframe)))),
                on_result=lambda result: print(json.dumps(result.as_dict(), default=str)),
            ))
        await DryRunPortfolioWorker(tuple(workers)).run(max_steps=args.steps)
    return 0


def run_show_timeframes(args: argparse.Namespace) -> int:
    config = load_forex_config(Path(args.config) if args.config else None)
    pair_timeframes = dict(config.get("pair_timeframes") or {})
    pair_strategies = dict(config.get("pair_strategies") or {})
    approved_revisions = dict(config.get("pair_approved_revisions") or {})
    configured_pairs = {
        str(item).strip().upper().replace("/", "_")
        for item in dict(config.get("exchange", {})).get("pair_whitelist", ())
    }
    pairs = configured_pairs | set(pair_timeframes) | set(pair_strategies)
    if args.pair:
        pairs = {OandaMarketDataProvider.to_oanda_instrument(args.pair)}
    result = []
    for instrument_name in sorted(pairs):
        revision = approved_revisions.get(instrument_name)
        revision = revision if isinstance(revision, dict) else {}
        configured_timeframe = pair_timeframes.get(instrument_name)
        configured_strategy = pair_strategies.get(instrument_name)
        approved_timeframe = revision.get("timeframe")
        approved_strategy = revision.get("strategyClass")
        approved = bool(
            approved_timeframe
            and approved_strategy
            and configured_timeframe
            and configured_strategy
            and freqtrade_timeframe(str(approved_timeframe))
            == freqtrade_timeframe(str(configured_timeframe))
            and approved_strategy == configured_strategy
            and revision.get("pair") == instrument_name.replace("_", "/")
        )
        result.append({
            "pair": instrument_name.replace("_", "/"),
            "timeframe": configured_timeframe,
            "strategy": configured_strategy,
            "approved": approved,
        })
    print(json.dumps(result, indent=2))
    return 0


async def run_practice(settings: OandaSettings, args: argparse.Namespace) -> int:
    if settings.execution_mode != ExecutionMode.PRACTICE:
        raise ValueError("set OANDA_EXECUTION_MODE=practice before starting")
    if settings.environment.value != "practice":
        raise ValueError("practice command requires OANDA_ENVIRONMENT=practice")
    if args.instruments:
        instruments = tuple(item.strip() for item in args.instruments.split(",") if item.strip())
        settings = replace(settings, instruments=instruments)
    order_values = (
        args.units,
        args.stop_loss_price,
        args.take_profit_price,
        args.simulated_fill_price,
        args.client_order_id,
    )
    if any(value is not None for value in order_values):
        if any(value is None for value in order_values):
            raise ValueError(
                "practice orders require --units, --stop-loss-price, --take-profit-price, "
                "--simulated-fill-price, and --client-order-id"
            )
        if args.units == 0:
            raise ValueError("--units cannot be zero")
        instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
        async with OandaClient(
            settings.token, settings.account_id, environment=settings.environment
        ) as client:
            gateway = OandaExecutionGateway(settings, ExecutionMode.PRACTICE, client=client)
            result = await gateway.submit_market_order_and_reconcile(
                instrument_name,
                args.units,
                simulated_fill_price=args.simulated_fill_price,
                stop_loss_price=args.stop_loss_price,
                take_profit_price=args.take_profit_price,
                client_order_id=args.client_order_id,
            )
        print(json.dumps(result.as_dict(), default=str, indent=2))
        return 0
    report = await run_health(settings)
    print(report_to_json(report))
    return 0


async def run_practice_run(settings: OandaSettings, args: argparse.Namespace) -> int:
    if settings.execution_mode != ExecutionMode.PRACTICE:
        raise ValueError("set OANDA_EXECUTION_MODE=practice before starting")
    if settings.environment.value != "practice":
        raise ValueError("practice-run requires OANDA_ENVIRONMENT=practice")
    if args.steps < 1:
        raise ValueError("--steps must be at least 1")
    if args.interval_seconds < 0:
        raise ValueError("--interval-seconds must be non-negative")
    sessions = tuple(item.strip() for item in args.sessions.split(",") if item.strip())
    if not sessions:
        raise ValueError("--sessions must contain at least one session")
    if args.instruments:
        instruments = tuple(item.strip() for item in args.instruments.split(",") if item.strip())
        settings = replace(settings, instruments=instruments)

    recorder = PracticeRunRecorder(Path(args.runs))
    for session in sessions:
        started = datetime.now(UTC)
        status = "completed"
        notes = ""
        order_count = 0
        try:
            for step in range(args.steps):
                report = await run_health(settings)
                if not report.healthy:
                    raise RuntimeError("Practice health check returned unhealthy")
                if step + 1 < args.steps:
                    await asyncio.sleep(args.interval_seconds)
        except Exception as exc:
            status = "failed"
            notes = str(exc)
        ended = datetime.now(UTC)
        recorder.append(
            PracticeRunRecord(
                run_id=f"{session}-{started.strftime('%Y%m%dT%H%M%SZ')}",
                session=session,
                started_at=started.isoformat().replace("+00:00", "Z"),
                ended_at=ended.isoformat().replace("+00:00", "Z"),
                instruments=settings.instruments,
                status=status,
                order_count=order_count,
                notes=notes,
            )
        )
        if status == "failed":
            return 1
    return 0


def _backtest_summary(
    result,
    candles: pd.DataFrame,
    *,
    instrument: str,
    timeframe: str,
    strategy: str,
    freqaimodel: str | None,
    account_currency: str | None = None,
) -> dict[str, object]:
    trades = result.trades
    wins = sum(trade.net_pl > 0 for trade in trades)
    losses = sum(trade.net_pl < 0 for trade in trades)
    draws = len(trades) - wins - losses
    gross_profit = sum((trade.net_pl for trade in trades if trade.net_pl > 0), Decimal("0"))
    gross_loss = abs(sum((trade.net_pl for trade in trades if trade.net_pl < 0), Decimal("0")))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    average_profit = result.net_pl / len(trades) if trades else Decimal("0")
    durations = [
        (pd.Timestamp(trade.exit_time) - pd.Timestamp(trade.entry_time)).total_seconds() / 60
        for trade in trades
    ]
    average_duration_minutes = sum(durations) / len(durations) if durations else 0.0
    first_candle = pd.Timestamp(candles["date"].iloc[0]) if not candles.empty else None
    last_candle = pd.Timestamp(candles["date"].iloc[-1]) if not candles.empty else None
    account_currency = account_currency or result.account_currency or "account currency"
    summary: dict[str, object] = {
        "instrument": instrument,
        "timeframe": timeframe,
        "strategy": strategy,
        "freqaimodel": freqaimodel,
        "data_period": (
            f"{first_candle.isoformat()} - {last_candle.isoformat()}"
            if first_candle is not None and last_candle is not None else "n/a"
        ),
        "candles": len(candles),
        "trades": len(trades),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_rate": result.win_rate,
        "starting_balance": result.starting_balance,
        "ending_balance": result.ending_balance,
        "net_pl": result.net_pl,
        "average_pl_per_trade": average_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown": result.max_drawdown,
        "max_drawdown_rate": result.max_drawdown_rate,
        "total_costs": result.total_costs,
        "average_duration_minutes": average_duration_minutes,
        "account_currency": account_currency,
    }
    return summary


def _print_backtest_summary(summary: dict[str, object]) -> None:
    currency = str(summary["account_currency"])
    table = Table(
        title=(
            f"Backtest Summary | {summary['instrument']} | {summary['timeframe']} "
            f"| {summary['strategy']}"
        )
    )
    table.add_column("Metric", style="bold")
    table.add_column("Result", justify="right")
    rows = (
        ("Data period", summary["data_period"]),
        ("Candles", summary["candles"]),
        ("Trades", summary["trades"]),
        ("Wins / Draws / Losses", f"{summary['wins']} / {summary['draws']} / {summary['losses']}"),
        ("Win rate", f"{Decimal(str(summary['win_rate'])):.2%}"),
        (f"Starting balance ({currency})", f"{Decimal(str(summary['starting_balance'])):,.2f}"),
        (f"Ending balance ({currency})", f"{Decimal(str(summary['ending_balance'])):,.2f}"),
        (f"Net profit ({currency})", f"{Decimal(str(summary['net_pl'])):,.2f}"),
        (f"Average P/L per trade ({currency})", f"{Decimal(str(summary['average_pl_per_trade'])):,.2f}"),
        (f"Gross profit / loss ({currency})", f"{Decimal(str(summary['gross_profit'])):,.2f} / {Decimal(str(summary['gross_loss'])):,.2f}"),
        ("Profit factor", f"{Decimal(str(summary['profit_factor'])):.2f}" if summary["profit_factor"] is not None else "n/a"),
        (f"Max drawdown ({currency})", f"{Decimal(str(summary['max_drawdown'])):,.2f}"),
        ("Max drawdown rate", f"{Decimal(str(summary['max_drawdown_rate'])):.2%}"),
        (f"Total costs ({currency})", f"{Decimal(str(summary['total_costs'])):,.2f}"),
        ("Average trade duration", f"{float(summary['average_duration_minutes']):.1f} min"),
    )
    for label, value in rows:
        table.add_row(str(label), str(value))
    Console().print(table)


async def _fetch_strategy_informative_candles(
    provider: OandaMarketDataProvider,
    *,
    strategy_class: str,
    pair: str,
    timeframe: str,
    base_candle_count: int,
    start: str | None,
    end: str | None,
    store: HistoricalCandleStore,
    refresh: bool,
    config_overrides: dict[str, object] | None = None,
) -> dict[str, pd.DataFrame]:
    from freqtrade.forex.strategy_execution import strategy_informative_timeframes

    strategy = load_strategy(
        strategy_class,
        freqtrade_timeframe(timeframe),
        pair,
        config_overrides=config_overrides,
    )
    informative_candles: dict[str, pd.DataFrame] = {}
    for informative_timeframe in strategy_informative_timeframes(strategy, pair):
        informative_count = min(
            5000,
            strategy_informative_candle_count(
                strategy, informative_timeframe, base_candle_count
            ),
        )
        if start and end:
            informative_candles[informative_timeframe] = (
                await provider.fetch_historical(
                    pair,
                    informative_timeframe,
                    start=start,
                    end=end,
                    store=store,
                    refresh=refresh,
                )
            )
        else:
            informative_candles[informative_timeframe] = await provider.fetch_latest(
                pair,
                informative_timeframe,
                count=informative_count,
                store=store,
                refresh=refresh,
            )
        if informative_candles[informative_timeframe].empty:
            raise ValueError(
                f"No informative candles returned for {informative_timeframe}"
            )
    return informative_candles


async def run_backtest(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.count < 30:
        raise ValueError("--count must be at least 30")
    instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments((instrument_name,))
        account = await client.get_account_summary()
        provider = OandaMarketDataProvider(client, settings)
        if (args.start is None) != (args.end is None):
            raise ValueError("--start and --end must be provided together")
        store = HistoricalCandleStore(Path(args.data_cache or "user_data/data/oanda/candles.json"))
        if args.start and args.end:
            frame = await provider.fetch_historical(
                args.pair,
                args.timeframe,
                start=args.start,
                end=args.end,
                store=store,
                refresh=args.refresh_data,
            )
        else:
            frame = await provider.fetch_latest(
                args.pair, args.timeframe, count=args.count, store=store, refresh=args.refresh_data
            )
        price_instruments = (instrument_name, "GBP_USD") if account.currency != "USD" else (instrument_name,)
        prices = await client.get_prices(price_instruments)
        if not metadata or not prices:
            raise ValueError(f"missing metadata or price for {instrument_name}")
        price_map = {price.instrument: price for price in prices}
        conversion = Decimal("1")
        if account.currency != "USD":
            gbp_usd = price_map.get("GBP_USD")
            if gbp_usd is None:
                raise ValueError("GBP_USD price is required for GBP account backtest conversion")
            conversion = Decimal("1") / gbp_usd.midpoint
        if args.strategy not in {"ai", "ema", "ForexAIStrategyBaseline"}:
            if args.ai_model:
                raise ValueError(
                    "--ai-model is for the standalone baseline; use the matching "
                    "--model-dir FreqAI report for a custom strategy"
                )
            model_name = args.freqaimodel or "LightGBMRegressor"
            if model_name == "ForexAIStrategyBaseline":
                raise ValueError(
                    "custom FreqAI strategy backtests require LightGBMRegressor "
                    "or LightGBMClassifier"
                )
            freqai_config = _resolve_cli_freqai_config(args.timeframe)
            informative_candles = (
                await _fetch_strategy_informative_candles(
                    provider,
                    strategy_class=args.strategy,
                    pair=args.pair,
                    timeframe=args.timeframe,
                    base_candle_count=len(frame),
                    start=args.start,
                    end=args.end,
                    store=store,
                    refresh=args.refresh_data,
                    config_overrides={"freqai": freqai_config},
                )
                if args.strategy
                else {}
            )
            report_path, weights_path, report = _run_lightgbm_hyperopt(
                frame,
                instrument=metadata[0],
                pair=args.pair,
                timeframe=args.timeframe,
                model_name=model_name,
                epochs=1,
                model_dir=Path(args.model_dir),
                starting_balance=account.balance,
                risk_fraction=Decimal(settings.risk_fraction),
                spread=price_map[instrument_name].spread,
                slippage=args.slippage,
                financing_rate_per_day=args.financing_rate_per_day,
                quote_to_account_rate=conversion,
                stop_pips=args.stop_pips,
                hyperopt_loss=DEFAULT_HYPEROPT_LOSS,
                strategy_class=args.strategy,
                freqai_config=freqai_config,
                informative_candles=informative_candles,
                optimize_strategy=False,
            )
            result = report.pop("_backtest_result")
            summary = _backtest_summary(
                result,
                frame,
                instrument=instrument_name,
                timeframe=args.timeframe,
                strategy=args.strategy,
                freqaimodel=model_name,
                account_currency=account.currency,
            )
            _print_backtest_summary(summary)
            print(json.dumps({
                "instrument": instrument_name,
                "timeframe": args.timeframe,
                "strategy": args.strategy,
                "freqaimodel": model_name,
                "backtest_window": "out_of_sample",
                "ai_parameters": report["strategy_parameters"],
                "candles": len(frame),
                "starting_balance": str(result.starting_balance),
                "ending_balance": str(result.ending_balance),
                "net_pl": str(result.net_pl),
                "trades": len(result.trades),
                "win_rate": str(result.win_rate),
                "max_drawdown": str(result.max_drawdown),
                "total_costs": str(result.total_costs),
                "weights_path": str(weights_path),
                "report_path": str(report_path),
                "trades_detail": list(result.trade_output),
                "summary": summary,
            }, indent=2, default=str))
            return 0
        strategy = EmaCrossStrategy()
        ai_parameters: dict[str, object] = {}
        if args.strategy == "ai":
            if args.ai_model:
                ai_parameters = _load_ai_model(Path(args.ai_model), args.pair, args.timeframe)
            strategy = ForexAIStrategyBaseline({
                "forex_ai_entry_threshold": ai_parameters.get("entry_threshold", "0.5"),
                "forex_ai_max_spread_pct": ai_parameters.get("max_spread_pct", "1.0"),
            })
        result = ForexBacktester(
            strategy,
            metadata[0],
            starting_balance=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            stop_pips=args.stop_pips,
            spread=price_map[instrument_name].spread,
            slippage=args.slippage,
            financing_rate_per_day=args.financing_rate_per_day,
            quote_to_account_rate=conversion,
        ).run(frame)
    summary = _backtest_summary(
        result,
        frame,
        instrument=instrument_name,
        timeframe=args.timeframe,
        strategy=args.strategy,
        freqaimodel=None,
        account_currency=account.currency,
    )
    _print_backtest_summary(summary)
    print(json.dumps({
        "instrument": instrument_name,
        "timeframe": args.timeframe,
        "strategy": args.strategy,
        "ai_parameters": ai_parameters,
        "candles": len(frame),
        "starting_balance": str(result.starting_balance),
        "ending_balance": str(result.ending_balance),
        "net_pl": str(result.net_pl),
        "trades": len(result.trades),
        "win_rate": str(result.win_rate),
        "spread": str(price_map[instrument_name].spread),
        "slippage": str(args.slippage),
        "financing_rate_per_day": str(args.financing_rate_per_day),
        "total_costs": str(result.total_costs),
        "quote_to_account_rate": str(conversion),
        "trades_detail": list(result.trade_output),
        "equity_curve": [
            {"timestamp": str(point.timestamp), "balance": str(point.balance)}
            for point in result.equity_curve
        ],
        "max_drawdown": str(result.max_drawdown),
        "max_drawdown_rate": str(result.max_drawdown_rate),
        "daily_breakdown": [
            {
                "period": item.period,
                "trades": item.trades,
                "net_pl": str(item.net_pl),
                "wins": item.wins,
                "losses": item.losses,
                "draws": item.draws,
            }
            for item in result.daily_breakdown
        ],
        "weekly_breakdown": [
            {
                "period": item.period,
                "trades": item.trades,
                "net_pl": str(item.net_pl),
                "wins": item.wins,
                "losses": item.losses,
                "draws": item.draws,
            }
            for item in result.weekly_breakdown
        ],
        "monthly_breakdown": [
            {
                "period": item.period,
                "trades": item.trades,
                "net_pl": str(item.net_pl),
                "wins": item.wins,
                "losses": item.losses,
                "draws": item.draws,
            }
            for item in result.monthly_breakdown
        ],
        "summary": summary,
    }, indent=2, default=str))
    return 0


async def run_hyperopt(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.count < 30:
        raise ValueError("--count must be at least 30")
    if args.epochs < 1:
        raise ValueError("--epochs must be at least 1")
    instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments((instrument_name,))
        account = await client.get_account_summary()
        provider = OandaMarketDataProvider(client, settings)
        if (args.start is None) != (args.end is None):
            raise ValueError("--start and --end must be provided together")
        store = HistoricalCandleStore(Path(args.data_cache or "user_data/data/oanda/candles.json"))
        if args.start and args.end:
            frame = await provider.fetch_historical(
                args.pair, args.timeframe, start=args.start, end=args.end,
                store=store, refresh=args.refresh_data,
            )
        else:
            frame = await provider.fetch_latest(
                args.pair, args.timeframe, count=args.count,
                store=store, refresh=args.refresh_data,
            )
        price_instruments = (instrument_name, "GBP_USD") if account.currency != "USD" else (instrument_name,)
        prices = await client.get_prices(price_instruments)
        price_map = {price.instrument: price for price in prices}
        if not metadata or instrument_name not in price_map:
            raise ValueError(f"missing metadata or price for {instrument_name}")
        conversion = Decimal("1")
        if account.currency != "USD":
            gbp_usd = price_map.get("GBP_USD")
            if gbp_usd is None:
                raise ValueError("GBP_USD price is required for GBP account hyperopt conversion")
            conversion = Decimal("1") / gbp_usd.midpoint
        if args.strategy and not args.freqaimodel:
            from freqtrade.forex.ai_hyperopt import run_strategy_hyperopt

            strategy_timeframe = freqtrade_timeframe(args.timeframe)
            selected_strategy = load_strategy(args.strategy, strategy_timeframe, args.pair)
            informative_candles: dict[str, pd.DataFrame] = {}
            for informative_timeframe in strategy_informative_timeframes(
                selected_strategy, args.pair
            ):
                informative_count = min(
                    5000,
                    strategy_informative_candle_count(
                        selected_strategy, informative_timeframe, len(frame)
                    ),
                )
                if args.start and args.end:
                    informative_candles[informative_timeframe] = await provider.fetch_historical(
                        args.pair,
                        informative_timeframe,
                        start=args.start,
                        end=args.end,
                        store=store,
                        refresh=args.refresh_data,
                    )
                else:
                    informative_candles[informative_timeframe] = await provider.fetch_latest(
                        args.pair,
                        informative_timeframe,
                        count=informative_count,
                        store=store,
                        refresh=args.refresh_data,
                    )

            def show_strategy_epoch(
                done: int, total: int, candidate: dict[str, object]
            ) -> None:
                print(
                    f"Epoch {done}/{total} | objective={candidate['objective']} "
                    f"| validation_pl={candidate['validationNetPl']} "
                    f"| params={candidate['parameters']}",
                    flush=True,
                )

            candidates = run_strategy_hyperopt(
                frame,
                informative_candles,
                metadata[0],
                pair=args.pair,
                strategy_class=args.strategy,
                timeframe=strategy_timeframe,
                starting_balance=account.balance,
                risk_fraction=Decimal(settings.risk_fraction),
                spread=price_map[instrument_name].spread,
                stop_pips=args.stop_pips,
                slippage=args.slippage,
                financing_rate_per_day=args.financing_rate_per_day,
                quote_to_account_rate=conversion,
                max_attempts=args.epochs,
                hyperopt_loss=args.hyperopt_loss,
                on_candidate=show_strategy_epoch,
            )
            strategy_report_path = Path(args.model_dir) / (
                f"{instrument_name}_{args.timeframe}_{args.strategy}_hyperopt.json"
            )
            strategy_report_path.parent.mkdir(parents=True, exist_ok=True)
            strategy_report_path.write_text(
                json.dumps({
                    "strategy": args.strategy,
                    "pair": args.pair.upper(),
                    "timeframe": args.timeframe,
                    "hyperopt_loss": args.hyperopt_loss,
                    "candidates_tested": len(candidates),
                    "best": candidates[0],
                    "candidates": candidates,
                }, indent=2, default=str),
                encoding="utf-8",
            )
            print("FINAL STRATEGY HYPEROPT RESULTS", flush=True)
            print("Rank | Objective | Validation P/L | Drawdown | Parameters")
            print("-----+-----------+----------------+----------+-----------")
            for rank, candidate in enumerate(candidates[:10], start=1):
                print(
                    f"{rank:>4} | {candidate['objective']:>9} | "
                    f"{candidate['validationNetPl']:>14} | "
                    f"{candidate['validationDrawdown']:>8} | {candidate['parameters']}"
                )
            print(json.dumps({
                "strategy": args.strategy,
                "freqaimodel": None,
                "candidates_tested": len(candidates),
                "best_parameters": candidates[0]["parameters"],
                "report_path": str(strategy_report_path),
            }, indent=2, default=str))
            return 0

        selected_freqaimodel = args.freqaimodel or "LightGBMRegressor"
        if args.strategy and selected_freqaimodel == "ForexAIStrategyBaseline":
            raise ValueError(
                "custom FreqAI strategies require --freqaimodel LightGBMRegressor "
                "or --freqaimodel LightGBMClassifier"
            )
        if selected_freqaimodel != "ForexAIStrategyBaseline":
            freqai_config = _resolve_cli_freqai_config(args.timeframe)
            informative_candles = (
                await _fetch_strategy_informative_candles(
                    provider,
                    strategy_class=args.strategy,
                    pair=args.pair,
                    timeframe=args.timeframe,
                    base_candle_count=len(frame),
                    start=args.start,
                    end=args.end,
                    store=store,
                    refresh=args.refresh_data,
                    config_overrides={"freqai": freqai_config},
                )
                if args.strategy
                else {}
            )
            report_path, weights_path, report = _run_lightgbm_hyperopt(
                frame,
                instrument=metadata[0],
                pair=args.pair,
                timeframe=args.timeframe,
                model_name=selected_freqaimodel,
                epochs=args.epochs,
                model_dir=Path(args.model_dir),
                starting_balance=account.balance,
                risk_fraction=Decimal(settings.risk_fraction),
                spread=price_map[instrument_name].spread,
                slippage=args.slippage,
                financing_rate_per_day=args.financing_rate_per_day,
                quote_to_account_rate=conversion,
                stop_pips=args.stop_pips,
                hyperopt_loss=args.hyperopt_loss,
                strategy_class=args.strategy,
                freqai_config=freqai_config,
                informative_candles=informative_candles,
            )
            print(json.dumps({
                "instrument": instrument_name,
                "timeframe": args.timeframe,
                "freqaimodel": selected_freqaimodel,
                "candles": len(frame),
                "epochs": len(report["candidates"]),
                "best": report["best"],
                "model_path": str(report_path),
                "weights_path": str(weights_path),
            }, indent=2))
            return 0

        print("AI MODEL: ForexAIStrategyBaseline (deterministic; no ML training)", flush=True)
        print("STRATEGY HYPEROPT: optimizing entry_threshold and max_spread_pct", flush=True)
        best_so_far: Decimal | None = None

        def show_epoch(done: int, total: int, candidate) -> None:
            nonlocal best_so_far
            best_so_far = (
                candidate.objective
                if best_so_far is None
                else max(best_so_far, candidate.objective)
            )
            print(
                f"Epoch {done}/{total} | objective={candidate.objective} "
                f"| best={best_so_far} | entry={candidate.entry_threshold} "
                f"| max_spread={candidate.max_spread_pct}",
                flush=True,
            )

        result = run_ai_hyperopt(
            frame,
            metadata[0],
            starting_balance=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            spread=price_map[instrument_name].spread,
            slippage=args.slippage,
            financing_rate_per_day=args.financing_rate_per_day,
            quote_to_account_rate=conversion,
            max_attempts=args.epochs,
            hyperopt_loss=args.hyperopt_loss,
            on_candidate=show_epoch,
        )
    best = result.best
    print("FINAL STRATEGY HYPEROPT RESULTS", flush=True)
    print("Epoch | Entry threshold | Max spread | Objective | Validation P/L | Drawdown | Trades")
    print("------+-----------------+------------+-----------+----------------+----------+-------")
    for epoch, candidate in enumerate(result.candidates[:10], start=1):
        print(
            f"{epoch:>5} | {candidate.entry_threshold:>15} | "
            f"{candidate.max_spread_pct:>10} | {candidate.objective:>9} | "
            f"{candidate.validation_result.net_pl:>14} | "
            f"{candidate.validation_result.max_drawdown:>8} | "
            f"{len(candidate.validation_result.trades):>6}"
        )
    model_path = Path(args.model_dir) / f"{instrument_name}_{args.timeframe}_ai.json"
    model_payload = {
        "version": 1,
        "strategy": "ForexAIStrategyBaseline",
        "pair": OandaMarketDataProvider.to_freqtrade_pair(instrument_name),
        "timeframe": args.timeframe,
        "hyperopt_loss": args.hyperopt_loss,
        "candidates_tested": result.candidates_tested,
        "parameters": {
            "entry_threshold": str(best.entry_threshold),
            "max_spread_pct": str(best.max_spread_pct),
        },
        "objective": str(best.objective),
        "saved_at": datetime.now(UTC).isoformat(),
        "candidates": [
            {
                "entry_threshold": str(candidate.entry_threshold),
                "max_spread_pct": str(candidate.max_spread_pct),
                "objective": str(candidate.objective),
                "validation_net_pl": str(candidate.validation_result.net_pl),
                "validation_drawdown": str(candidate.validation_result.max_drawdown),
            }
            for candidate in result.candidates
        ],
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = model_path.with_suffix(f"{model_path.suffix}.tmp")
    temporary_path.write_text(json.dumps(model_payload, indent=2), encoding="utf-8")
    temporary_path.replace(model_path)
    print(json.dumps({
        "instrument": instrument_name,
        "timeframe": args.timeframe,
        "candles": len(frame),
        "candidates_tested": result.candidates_tested,
        "entry_threshold": str(best.entry_threshold),
        "max_spread_pct": str(best.max_spread_pct),
        "ending_balance": str(best.validation_result.ending_balance),
        "net_pl": str(best.validation_result.net_pl),
        "max_drawdown": str(best.validation_result.max_drawdown),
        "objective": str(best.objective),
        "trades": len(best.validation_result.trades),
        "win_rate": str(best.validation_result.win_rate),
        "model_path": str(model_path),
    }, indent=2))
    return 0


def _run_lightgbm_hyperopt(
    candles,
    *,
    instrument,
    pair: str,
    timeframe: str,
    model_name: str,
    epochs: int,
    model_dir: Path,
    starting_balance: Decimal,
    risk_fraction: Decimal,
    spread: Decimal,
    slippage: Decimal,
    financing_rate_per_day: Decimal,
    quote_to_account_rate: Decimal,
    stop_pips: Decimal,
    hyperopt_loss: str,
    strategy_class: str | None = None,
    freqai_config: dict[str, object] | None = None,
    informative_candles: dict[str, pd.DataFrame] | None = None,
    optimize_strategy: bool = True,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    try:
        from freqtrade.forex.ai_dataset import build_forex_ai_dataset
        from freqtrade.forex.ai_lgbm import (
            LightGBMDirectionClassifier,
            LightGBMFutureReturnModel,
        )
        from lightgbm import Booster
    except ImportError as exc:
        raise ValueError(
            "LightGBM FreqAI models require the freqai dependencies; install the project's freqai extra"
        ) from exc

    timeframe_code = {"5m": "M5", "1h": "H1"}[timeframe]
    strategy_features = None
    target_column = None
    selected_strategy = None
    informative_candles = informative_candles or {}
    if strategy_class is not None:
        if freqai_config is None:
            raise ValueError("FreqAI settings are required when a strategy class is selected")
        selected_strategy = load_strategy(
            strategy_class,
            freqtrade_timeframe(timeframe),
            pair,
            config_overrides={"freqai": freqai_config},
        )
        selected_strategy.freqai_info = freqai_config
        required_timeframes = set(
            strategy_informative_timeframes(selected_strategy, pair)
        )
        missing_timeframes = required_timeframes - set(informative_candles)
        if missing_timeframes:
            raise ValueError(
                "Missing informative candles for: "
                + ", ".join(sorted(missing_timeframes))
            )
        strategy_features, target_column = _build_freqai_strategy_features(
            candles,
            selected_strategy,
            pair=pair,
            timeframe=timeframe,
            freqai_config=freqai_config,
        )
        _validate_freqai_strategy_consumption(selected_strategy, target_column)
    freqai_settings = dict(freqai_config or {})
    split_settings = dict(freqai_settings.get("data_split_parameters", {}))
    unsupported_split_settings = set(split_settings) - {"test_size", "shuffle", "random_state"}
    if unsupported_split_settings:
        raise ValueError(
            "Unsupported FreqAI data_split_parameters: "
            + ", ".join(sorted(unsupported_split_settings))
        )
    if split_settings.get("shuffle", False):
        raise ValueError("Forex FreqAI requires chronological splits; shuffle must be false")
    validation_fraction = Decimal(str(split_settings.get("test_size", 0.2)))
    if not Decimal("0") < validation_fraction < Decimal("0.4"):
        raise ValueError("freqai.data_split_parameters.test_size must be greater than 0 and below 0.4")
    train_period_days = freqai_settings.get("train_period_days")
    backtest_period_days = freqai_settings.get("backtest_period_days")
    if (train_period_days is None) != (backtest_period_days is None):
        raise ValueError("freqai.train_period_days and backtest_period_days must be configured together")
    dataset, manifest = build_forex_ai_dataset(
        candles,
        pair=pair,
        timeframe=timeframe_code,
        strategy_features=strategy_features,
        target_column=target_column,
        label_period=int(
            dict(freqai_config or {}).get("feature_parameters", {}).get(
                "label_period_candles", 2
            )
        ),
        indicator_periods=tuple(
            int(period)
            for period in dict(freqai_config or {}).get("feature_parameters", {}).get(
                "indicator_periods_candles", (5, 14)
            )
        ),
        include_shifted_candles=int(
            dict(freqai_config or {}).get("feature_parameters", {}).get(
                "include_shifted_candles", 0
            )
        ),
        train_period_days=train_period_days,
        backtest_period_days=backtest_period_days,
        validation_fraction=validation_fraction,
    )
    ai_identifier = str(dict(freqai_config or {}).get("identifier", "forex-cli-default"))
    model_training_parameters = dict(
        dict(freqai_config or {}).get("model_training_parameters", {})
    )
    training_parameters_hash = hashlib.sha256(
        json.dumps(model_training_parameters, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    training_context = {
        "strategy": strategy_class,
        "target_column": target_column or "label",
        "train_rows": manifest.train_rows,
        "validation_rows": manifest.validation_rows,
        "train_range": manifest.train_range,
        "identifier": ai_identifier,
        "feature_parameters": dict(freqai_settings.get("feature_parameters", {})),
        "data_split_parameters": split_settings,
        "train_period_days": train_period_days,
        "backtest_period_days": backtest_period_days,
    }
    training_context_hash = hashlib.sha256(
        json.dumps(training_context, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    supported_model_parameters = {
        "seed", "n_estimators", "learning_rate", "weight_factor",
        "di_threshold", "neutral_band", "num_leaves", "max_depth",
        "min_child_samples", "min_child_weight", "subsample",
        "colsample_bytree", "reg_alpha", "reg_lambda", "min_split_gain",
        "max_bin", "n_jobs", "subsample_freq", "reg_sqrt", "boosting_type",
    }
    unsupported_model_parameters = set(model_training_parameters) - supported_model_parameters
    if unsupported_model_parameters:
        raise ValueError(
            "Unsupported LightGBM model_training_parameters: "
            + ", ".join(sorted(unsupported_model_parameters))
        )
    model_dir.mkdir(parents=True, exist_ok=True)
    identifier_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", ai_identifier).strip("._-")
    stem = f"{pair.replace('/', '_')}_{timeframe}_{identifier_slug}_{model_name}"
    weights_path = model_dir / f"{stem}.txt"
    cache_metadata_path = model_dir / f"{stem}.model.json"
    prediction_cache_path = model_dir / f"{stem}.predictions.json"
    report_path = model_dir / f"{stem}.json"
    cache_metadata: dict[str, object] = {}
    booster = None
    model_reused = False
    model_evaluation: dict[str, object] = {}
    model_classes: list[str] = []
    cache_is_valid = False
    if cache_metadata_path.is_file() and weights_path.is_file():
        cache_metadata = json.loads(cache_metadata_path.read_text(encoding="utf-8"))
        cache_is_valid = (
            cache_metadata.get("training_data_hash") == manifest.training_data_hash
            and cache_metadata.get("feature_schema_hash") == manifest.feature_schema_hash
            and cache_metadata.get("freqaimodel") == model_name
            and cache_metadata.get("identifier") == ai_identifier
            and cache_metadata.get("model_training_parameters_hash") == training_parameters_hash
            and cache_metadata.get("training_context_hash") == training_context_hash
        )
    if cache_is_valid:
        model_reused = True
        if on_progress is not None:
            on_progress("model_reuse", 0, epochs)
        print(
            f"AI TRAINING: reusing cached {model_name} "
            f"(data_hash={manifest.training_data_hash})",
            flush=True,
        )
        booster = Booster(model_file=str(weights_path))
        model_classes = [str(item) for item in cache_metadata.get("classes", [])]
        model_evaluation = dict(cache_metadata.get("evaluation", {}))
    else:
        if on_progress is not None:
            on_progress("model_training", 0, epochs)
        print(
            f"AI TRAINING: fitting {model_name} once on "
            f"{manifest.train_rows} training rows; validation={manifest.validation_rows}, "
            f"out_of_sample={manifest.out_of_sample_rows}",
            flush=True,
        )
        model_options = {
            "seed": int(model_training_parameters.get(
                "seed", split_settings.get("random_state", 7)
            )),
            "estimators": int(model_training_parameters.get("n_estimators", 200)),
            "weight_factor": float(model_training_parameters.get("weight_factor", 0.0)),
            "di_threshold": float(model_training_parameters.get("di_threshold", 0.0)),
        }
        lightgbm_options = {
            key: value
            for key, value in model_training_parameters.items()
            if key in supported_model_parameters - {
                "seed", "n_estimators", "learning_rate", "weight_factor",
                "di_threshold", "neutral_band",
            }
        }
        if model_name == "LightGBMRegressor":
            model = LightGBMFutureReturnModel(
                **model_options,
                learning_rate=float(model_training_parameters.get("learning_rate", 0.05)),
                model_parameters=lightgbm_options,
            )
        else:
            model = LightGBMDirectionClassifier(
                **model_options,
                neutral_band=float(model_training_parameters.get("neutral_band", 0.0001)),
                learning_rate=float(model_training_parameters.get("learning_rate", 0.05)),
                model_parameters=lightgbm_options,
            )
        trained_result = model.fit_and_evaluate(dataset, manifest)
        evaluation = (
            trained_result.revision.evaluation
            if model_name == "LightGBMRegressor"
            else trained_result
        )
        booster = model.model.booster_
        if model_name == "LightGBMClassifier":
            model_classes = [str(item) for item in model.model.classes_]
        booster.save_model(str(weights_path))
        model_evaluation = {
            "train": {
                key: str(value) for key, value in evaluation.train_metrics.items()
            } if model_name == "LightGBMRegressor" else {},
            "validation": {
                key: str(value) for key, value in evaluation.validation_metrics.items()
            },
            "out_of_sample": {
                key: str(value) for key, value in evaluation.out_of_sample_metrics.items()
            },
            "accepted": evaluation.accepted,
            "rejection_reasons": list(evaluation.rejection_reasons),
        }
        cache_metadata = {
            "version": 1,
            "freqaimodel": model_name,
            "identifier": ai_identifier,
            "model_training_parameters_hash": training_parameters_hash,
            "training_context_hash": training_context_hash,
            "training_context": training_context,
            "pair": pair.upper(),
            "timeframe": timeframe,
            "training_data_hash": manifest.training_data_hash,
            "feature_schema_hash": manifest.feature_schema_hash,
            "feature_columns": list(manifest.feature_columns),
            "model_training_parameters": model_training_parameters,
            "data_split_parameters": split_settings,
            "classes": model_classes,
            "evaluation": model_evaluation,
            "weights_file": weights_path.name,
            "predictions_file": prediction_cache_path.name,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        cache_temp_path = cache_metadata_path.with_suffix(".json.tmp")
        cache_temp_path.write_text(json.dumps(cache_metadata, indent=2), encoding="utf-8")
        cache_temp_path.replace(cache_metadata_path)
        print(
            f"AI TRAINING COMPLETE: accepted={evaluation.accepted}, "
            f"weights={weights_path}",
            flush=True,
        )

    if booster is None:
        raise ValueError(f"{model_name} model could not be loaded or trained")
    model_artifact_hash = hashlib.sha256(weights_path.read_bytes()).hexdigest()
    cached_predictions: list[object] | None = None
    if prediction_cache_path.is_file():
        prediction_payload = json.loads(prediction_cache_path.read_text(encoding="utf-8"))
        if (
            prediction_payload.get("training_data_hash") == manifest.training_data_hash
            and prediction_payload.get("feature_schema_hash") == manifest.feature_schema_hash
            and prediction_payload.get("model_artifact_hash") == model_artifact_hash
            and prediction_payload.get("identifier") == ai_identifier
        ):
            cached_predictions = prediction_payload.get("predictions")
    if cached_predictions is None:
        all_predictions = booster.predict(dataset.loc[:, manifest.feature_columns])
        cached_predictions = np.asarray(all_predictions).tolist()
        prediction_payload = {
            "identifier": ai_identifier,
            "training_data_hash": manifest.training_data_hash,
            "feature_schema_hash": manifest.feature_schema_hash,
            "model_artifact_hash": model_artifact_hash,
            "predictions": cached_predictions,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        prediction_temp_path = prediction_cache_path.with_suffix(".json.tmp")
        prediction_temp_path.write_text(
            json.dumps(prediction_payload, indent=2), encoding="utf-8"
        )
        prediction_temp_path.replace(prediction_cache_path)
        print(f"AI PREDICTIONS: cached to {prediction_cache_path}", flush=True)
    else:
        print(f"AI PREDICTIONS: reusing cached {prediction_cache_path}", flush=True)
    all_predictions = np.asarray(cached_predictions)
    validation_end = manifest.train_rows + manifest.validation_rows
    validation = dataset.iloc[manifest.train_rows:validation_end]
    validation_dates = pd.to_datetime(validation["date"], utc=True)
    candle_frame = candles.copy()
    candle_frame["date"] = pd.to_datetime(candle_frame["date"], utc=True)
    validation_candles = candle_frame[
        candle_frame["date"].isin(validation_dates)
    ].reset_index(drop=True)
    if len(validation_candles) < 40 and selected_strategy is not None:
        raise ValueError(
            "FreqAI strategy Hyperopt needs at least 40 validation candles; "
            "increase --count or adjust freqai.train_period_days/backtest_period_days"
        )
    raw_predictions = all_predictions[manifest.train_rows:validation_end]
    classifier = model_name == "LightGBMClassifier"
    prediction_rows: dict[int, tuple[object, float]] = {}
    if classifier:
        if raw_predictions.ndim == 1 and len(model_classes) == 2:
            for date, positive_probability in zip(
                validation["date"], raw_predictions, strict=True
            ):
                positive_probability = float(positive_probability)
                class_index = int(positive_probability >= 0.5)
                confidence = max(positive_probability, 1.0 - positive_probability)
                timestamp = int(pd.Timestamp(date).value)
                prediction_rows[timestamp] = (model_classes[class_index], confidence)
        elif raw_predictions.ndim == 1 and len(model_classes) == 1:
            for date in validation["date"]:
                prediction_rows[int(pd.Timestamp(date).value)] = (model_classes[0], 1.0)
        else:
            if len(model_classes) != raw_predictions.shape[1]:
                raise ValueError("cached classifier class labels do not match model output")
            for date, probabilities in zip(validation["date"], raw_predictions, strict=True):
                class_index = int(probabilities.argmax())
                timestamp = int(pd.Timestamp(date).value)
                prediction_rows[timestamp] = (model_classes[class_index], float(probabilities[class_index]))
    else:
        for date, prediction in zip(validation["date"], raw_predictions, strict=True):
            prediction_rows[int(pd.Timestamp(date).value)] = (float(prediction), 1.0)

    if selected_strategy is not None:
        oos_start_index = manifest.train_rows + manifest.validation_rows
        oos_start_time = (
            pd.Timestamp(dataset.iloc[oos_start_index]["date"])
            if oos_start_index < len(dataset)
            else pd.Timestamp.max.tz_localize("UTC")
        )
        if classifier:
            class_names = model_classes
            strategy_predictions = {}
            if all_predictions.ndim == 1:
                for date, probability in zip(dataset["date"], all_predictions, strict=True):
                    class_index = int(float(probability) >= 0.5)
                    strategy_predictions[int(pd.Timestamp(date).value)] = {
                        target_column: class_names[class_index],
                        "do_predict": int(
                            optimize_strategy
                            or pd.Timestamp(date) >= oos_start_time
                        ),
                    }
            else:
                for date, probabilities in zip(dataset["date"], all_predictions, strict=True):
                    class_index = int(probabilities.argmax())
                    strategy_predictions[int(pd.Timestamp(date).value)] = {
                        target_column: class_names[class_index],
                        "do_predict": int(
                            optimize_strategy
                            or pd.Timestamp(date) >= oos_start_time
                        ),
                    }
        else:
            strategy_predictions = {
                int(pd.Timestamp(date).value): {
                    target_column: float(prediction),
                    "do_predict": int(
                        optimize_strategy or pd.Timestamp(date) >= oos_start_time
                    ),
                }
                for date, prediction in zip(dataset["date"], all_predictions, strict=True)
            }
        from freqtrade.forex.ai_hyperopt import run_strategy_hyperopt

        if on_progress is not None:
            on_progress("strategy_hyperopt" if optimize_strategy else "backtest_oos", 0, epochs if optimize_strategy else 1)
        if not optimize_strategy:
            parameter_report_path = model_dir / f"{stem}.json"
            strategy_parameters: dict[str, object] = {}
            if parameter_report_path.is_file():
                parameter_report = json.loads(
                    parameter_report_path.read_text(encoding="utf-8")
                )
                if (
                    parameter_report.get("strategy") == strategy_class
                    and parameter_report.get("freqaimodel") == model_name
                    and parameter_report.get("training_context_hash") == training_context_hash
                ):
                    strategy_parameters = dict(
                        parameter_report.get("strategy_parameters", {})
                    )
            backtest_strategy = load_strategy(
                strategy_class,
                freqtrade_timeframe(timeframe),
                pair,
                parameter_values=strategy_parameters,
                config_overrides={"freqai": freqai_config},
            )
            backtest_strategy.freqai_info = freqai_config
            from freqtrade.forex.strategy_execution import (
                CachedFreqAIPredictions,
                FreqtradeStrategyAdapter,
            )

            backtest_strategy.freqai = CachedFreqAIPredictions(strategy_predictions)
            backtest_result = ForexBacktester(
                FreqtradeStrategyAdapter(
                    backtest_strategy, pair, informative_candles
                ),
                instrument,
                starting_balance=starting_balance,
                risk_fraction=risk_fraction,
                stop_pips=stop_pips,
                spread=spread,
                slippage=slippage,
                financing_rate_per_day=financing_rate_per_day,
                quote_to_account_rate=quote_to_account_rate,
            ).run(candles)
            backtest_report_path = model_dir / f"{stem}.backtest.json"
            backtest_report: dict[str, object] = {
                "version": 1,
                "identifier": ai_identifier,
                "freqaimodel": model_name,
                "strategy": strategy_class,
                "model_reused": model_reused,
                "strategy_parameters": strategy_parameters,
                "pair": pair.upper(),
                "timeframe": timeframe,
                "training_context_hash": training_context_hash,
                "model_training": model_evaluation,
                "backtest_window": "out_of_sample",
                "backtest_start": str(oos_start_time),
                "starting_balance": str(backtest_result.starting_balance),
                "ending_balance": str(backtest_result.ending_balance),
                "net_pl": str(backtest_result.net_pl),
                "trades": len(backtest_result.trades),
                "win_rate": str(backtest_result.win_rate),
                "max_drawdown": str(backtest_result.max_drawdown),
                "total_costs": str(backtest_result.total_costs),
                "weights_file": weights_path.name,
                "predictions_file": prediction_cache_path.name,
                "saved_at": datetime.now(UTC).isoformat(),
            }
            temporary_path = backtest_report_path.with_suffix(".json.tmp")
            temporary_path.write_text(
                json.dumps(backtest_report, indent=2, default=str), encoding="utf-8"
            )
            temporary_path.replace(backtest_report_path)
            backtest_report["_backtest_result"] = backtest_result
            if on_progress is not None:
                on_progress("completed", 1, 1)
            return backtest_report_path, weights_path, backtest_report

        print(
            f"STRATEGY HYPEROPT: {strategy_class} on {len(validation_candles)} "
            f"validation candles; optimizing declared strategy parameters",
            flush=True,
        )

        def report_strategy_epoch(
            done: int, total: int, candidate: dict[str, object]
        ) -> None:
            if on_progress is not None:
                on_progress("strategy_hyperopt", done, total)
            print(
                f"Epoch {done}/{total} | objective={candidate['objective']} "
                f"| validation_pl={candidate['validationNetPl']} "
                f"| params={candidate['parameters']}",
                flush=True,
            )

        strategy_candidates = run_strategy_hyperopt(
            validation_candles,
            informative_candles,
            instrument,
            pair=pair,
            strategy_class=strategy_class,
            timeframe=freqtrade_timeframe(timeframe),
            starting_balance=starting_balance,
            risk_fraction=risk_fraction,
            spread=spread,
            stop_pips=stop_pips,
            slippage=slippage,
            financing_rate_per_day=financing_rate_per_day,
            quote_to_account_rate=quote_to_account_rate,
            max_attempts=epochs,
            hyperopt_loss=hyperopt_loss,
            on_candidate=report_strategy_epoch,
            freqai_config=freqai_config,
            freqai_predictions=strategy_predictions,
            freqai_target_column=target_column,
        )
        print("FINAL STRATEGY HYPEROPT RESULTS", flush=True)
        print("Rank | Objective | Validation P/L | Drawdown | Parameters")
        print("-----+-----------+----------------+----------+-----------")
        for rank, candidate in enumerate(strategy_candidates[:10], start=1):
            print(
                f"{rank:>4} | {candidate['objective']:>9} | "
                f"{candidate['validationNetPl']:>14} | "
                f"{candidate['validationDrawdown']:>8} | {candidate['parameters']}"
            )
        report_path = model_dir / f"{stem}.json"
        report: dict[str, object] = {
            "version": 1,
            "identifier": ai_identifier,
            "freqaimodel": model_name,
            "strategy": strategy_class,
            "pair": pair.upper(),
            "timeframe": timeframe,
            "data_hash": manifest.training_data_hash,
            "feature_schema_hash": manifest.feature_schema_hash,
            "feature_columns": list(manifest.feature_columns),
            "target_column": target_column,
            "model_training_parameters": model_training_parameters,
            "data_split_parameters": split_settings,
            "training_context": training_context,
            "training_context_hash": training_context_hash,
            "model_training": model_evaluation,
            "strategy_parameters": strategy_candidates[0]["parameters"],
            "hyperopt_loss": hyperopt_loss,
            "best": strategy_candidates[0],
            "candidates": strategy_candidates,
            "weights_file": weights_path.name,
            "predictions_file": prediction_cache_path.name,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        temporary_path = report_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        temporary_path.replace(report_path)
        return report_path, weights_path, report

    if len(validation_candles) < 4:
        raise ValueError("AI strategy hyperopt requires at least 4 aligned validation candles")

    if classifier:
        entry_thresholds = [
            Decimal(value)
            for value in (
                "0.35", "0.40", "0.45", "0.50", "0.55", "0.60",
                "0.65", "0.70", "0.75", "0.80", "0.85", "0.90",
            )
        ]
    else:
        absolute_predictions = pd.Series(
            sorted(abs(float(value[0])) for value in prediction_rows.values())
        )
        entry_thresholds = [
            Decimal("0"),
            *[
                Decimal(str(float(absolute_predictions.quantile(quantile))))
                for quantile in (
                    0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4,
                    0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8,
                    0.85, 0.9, 0.95,
                )
            ],
        ]
        entry_thresholds = sorted(set(entry_thresholds))
    spread_values = pd.to_numeric(validation["spread_pct"], errors="coerce").fillna(0.0)
    max_spreads = sorted({
        Decimal(str(float(spread_values.quantile(quantile))))
        for quantile in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    } | {Decimal("1")})
    search_space = [
        (threshold, max_spread)
        for threshold in entry_thresholds
        for max_spread in max_spreads
    ]
    if len(search_space) > epochs:
        indexes = sorted({round(index * (len(search_space) - 1) / (epochs - 1)) for index in range(epochs)}) if epochs > 1 else [len(search_space) // 2]
        search_space = [search_space[index] for index in indexes]

    print(
        f"STRATEGY HYPEROPT: {len(search_space)} candidates; "
        f"parameters=entry_threshold,max_spread_pct; split=validation",
        flush=True,
    )

    class PredictionStrategy:
        def __init__(self, entry_threshold: Decimal, max_spread_pct: Decimal) -> None:
            self.entry_threshold = entry_threshold
            self.max_spread_pct = max_spread_pct

        def signal(self, window):
            candle = window.iloc[-1]
            close = Decimal(str(candle["close"]))
            spread_pct = (
                Decimal(str(candle["high"])) - Decimal(str(candle["low"]))
            ) / close if close else Decimal("0")
            if spread_pct > self.max_spread_pct:
                return Signal.FLAT
            timestamp = int(pd.Timestamp(candle["date"]).value)
            prediction = prediction_rows.get(timestamp)
            if prediction is None:
                return Signal.FLAT
            predicted_value, confidence = prediction
            if classifier:
                if Decimal(str(confidence)) < self.entry_threshold:
                    return Signal.FLAT
                if predicted_value == "long":
                    return Signal.LONG
                if predicted_value == "short":
                    return Signal.SHORT
                return Signal.FLAT
            numeric_prediction = Decimal(str(predicted_value))
            if numeric_prediction > self.entry_threshold:
                return Signal.LONG
            if numeric_prediction < -self.entry_threshold:
                return Signal.SHORT
            return Signal.FLAT

    candidates: list[dict[str, object]] = []
    best_score: Decimal | None = None
    best_row: dict[str, object] | None = None
    backtest_settings = {
        "starting_balance": starting_balance,
        "risk_fraction": risk_fraction,
        "stop_pips": stop_pips,
        "spread": spread,
        "slippage": slippage,
        "financing_rate_per_day": financing_rate_per_day,
        "quote_to_account_rate": quote_to_account_rate,
    }
    print("Epoch | Entry threshold | Max spread | Objective | Validation P/L | Drawdown | Trades")
    print("------+-----------------+------------+-----------+----------------+----------+-------")
    for epoch, (entry_threshold, max_spread) in enumerate(search_space, start=1):
        strategy = PredictionStrategy(entry_threshold, max_spread)
        backtest_result = ForexBacktester(strategy, instrument, **backtest_settings).run(
            validation_candles
        )
        objective = compute_hyperopt_objective(backtest_result, hyperopt_loss)
        row = {
            "epoch": epoch,
            "entry_threshold": str(entry_threshold),
            "max_spread_pct": str(max_spread),
            "objective": str(objective),
            "validation_net_pl": str(backtest_result.net_pl),
            "validation_drawdown": str(backtest_result.max_drawdown),
            "validation_trades": len(backtest_result.trades),
            "win_rate": str(backtest_result.win_rate),
        }
        candidates.append(row)
        if best_score is None or objective > best_score:
            best_score = objective
            best_row = row
        print(
            f"{epoch:>5}/{len(search_space):<3} | {entry_threshold:>15} | {max_spread:>10} | "
            f"{objective:>9} | {backtest_result.net_pl:>14} | "
            f"{backtest_result.max_drawdown:>8} | {len(backtest_result.trades):>6}",
            flush=True,
        )
    if best_row is None:
        raise ValueError(f"{model_name} strategy hyperopt produced no candidates")
    candidates.sort(key=lambda row: Decimal(str(row["objective"])), reverse=True)
    print("FINAL STRATEGY HYPEROPT RESULTS", flush=True)
    print("Rank | Entry threshold | Max spread | Objective | Validation P/L | Drawdown | Trades")
    print("-----+-----------------+------------+-----------+----------------+----------+-------")
    for rank, row in enumerate(candidates[:10], start=1):
        print(
            f"{rank:>4} | {row['entry_threshold']:>15} | {row['max_spread_pct']:>10} | "
            f"{row['objective']:>9} | {row['validation_net_pl']:>14} | "
            f"{row['validation_drawdown']:>8} | {row['validation_trades']:>6}"
        )

    report: dict[str, object] = {
        "version": 1,
        "freqaimodel": model_name,
        "pair": pair.upper(),
        "timeframe": timeframe,
        "data_hash": manifest.training_data_hash,
        "feature_schema_hash": manifest.feature_schema_hash,
        "feature_columns": list(manifest.feature_columns),
        "model_training": model_evaluation,
        "strategy_parameters": {
            "entry_threshold": best_row["entry_threshold"],
            "max_spread_pct": best_row["max_spread_pct"],
        },
        "hyperopt_loss": hyperopt_loss,
        "best": best_row,
        "candidates": candidates,
        "weights_file": weights_path.name,
        "saved_at": datetime.now(UTC).isoformat(),
    }
    temporary_path = report_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary_path.replace(report_path)
    return report_path, weights_path, report


async def run_download_data(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.count < 1:
        raise ValueError("--count must be at least 1")
    if (args.start is None) != (args.end is None):
        raise ValueError("--start and --end must be provided together")
    store = HistoricalCandleStore(Path(args.data_cache))
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        provider = OandaMarketDataProvider(client, settings)
        if args.start and args.end:
            frame = await provider.fetch_historical(
                args.pair, args.timeframe, start=args.start, end=args.end,
                store=store, refresh=args.refresh_data,
            )
        else:
            frame = await provider.fetch_latest(
                args.pair, args.timeframe, count=args.count,
                store=store, refresh=args.refresh_data,
            )
    print(json.dumps({
        "pair": args.pair.upper(), "timeframe": args.timeframe,
        "candles": len(frame), "cache": str(store.path),
    }, indent=2))
    return 0


def run_cache_clear(args: argparse.Namespace) -> int:
    instrument = OandaMarketDataProvider.to_oanda_instrument(args.pair) if args.pair else None
    removed = HistoricalCandleStore(Path(args.data_cache)).clear(
        instrument=instrument, timeframe=args.timeframe
    )
    print(json.dumps({"removed_ranges": removed, "cache": args.data_cache}, indent=2))
    return 0


def _load_ai_model(path: Path, pair: str, timeframe: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model_pair = OandaMarketDataProvider.to_freqtrade_pair(str(payload.get("pair", "")))
    requested_pair = OandaMarketDataProvider.to_freqtrade_pair(pair)
    if model_pair != requested_pair or payload.get("timeframe") != timeframe:
        raise ValueError("AI model pair/timeframe does not match this backtest")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict) or not {
        "entry_threshold", "max_spread_pct"
    }.issubset(parameters):
        raise ValueError("AI model file is missing optimized parameters")
    return parameters


def _resolve_cli_freqai_config(
    timeframe: str, config_source: dict[str, object] | None = None
) -> dict[str, object]:
    source = config_source if config_source is not None else load_forex_config()
    stored = source.get("freqai", source)
    stored = stored if isinstance(stored, dict) else {}
    feature_source = stored.get("feature_parameters", stored.get("featureParameters", {}))
    feature_source = feature_source if isinstance(feature_source, dict) else {}
    supported_feature_keys = {
        "include_timeframes", "includeTimeframes",
        "include_corr_pairlist", "includeCorrPairlist",
        "label_period_candles", "labelPeriodCandles",
        "include_shifted_candles", "includeShiftedCandles",
        "indicator_periods_candles", "indicatorPeriodsCandles",
        "DI_threshold", "diThreshold", "weight_factor", "weightFactor",
        "principal_component_analysis", "principalComponentAnalysis",
    }
    unsupported_feature_settings = {
        key: value
        for key, value in feature_source.items()
        if key not in supported_feature_keys and value not in (None, False, 0, [], {})
    }
    if unsupported_feature_settings:
        raise ValueError(
            "Unsupported active FreqAI feature_parameters: "
            + ", ".join(sorted(unsupported_feature_settings))
        )
    model_parameters = stored.get("model_training_parameters", {})
    model_parameters = model_parameters if isinstance(model_parameters, dict) else {}
    split_parameters = stored.get("data_split_parameters", {})
    split_parameters = split_parameters if isinstance(split_parameters, dict) else {}
    feature_parameters: dict[str, object] = {
        "include_timeframes": feature_source.get(
            "include_timeframes", feature_source.get("includeTimeframes", [timeframe])
        ),
        "include_corr_pairlist": feature_source.get(
            "include_corr_pairlist", feature_source.get("includeCorrPairlist", [])
        ),
        "label_period_candles": feature_source.get(
            "label_period_candles", feature_source.get("labelPeriodCandles", 2)
        ),
        "include_shifted_candles": feature_source.get(
            "include_shifted_candles", feature_source.get("includeShiftedCandles", 0)
        ),
        "indicator_periods_candles": feature_source.get(
            "indicator_periods_candles", feature_source.get("indicatorPeriodsCandles", [5, 14])
        ),
        "DI_threshold": feature_source.get(
            "DI_threshold", feature_source.get("diThreshold", 0.0)
        ),
        "weight_factor": feature_source.get(
            "weight_factor", feature_source.get("weightFactor", 0.0)
        ),
        "principal_component_analysis": feature_source.get(
            "principal_component_analysis", feature_source.get("principalComponentAnalysis", False)
        ),
    }
    train_period_days = stored.get("train_period_days", stored.get("trainPeriodDays"))
    backtest_period_days = stored.get(
        "backtest_period_days", stored.get("backtestPeriodDays")
    )
    if train_period_days is not None:
        train_period_days = int(train_period_days)
    if backtest_period_days is not None:
        backtest_period_days = int(backtest_period_days)
    model_parameters = dict(model_parameters)
    model_parameters.setdefault("weight_factor", feature_parameters["weight_factor"])
    model_parameters.setdefault("di_threshold", feature_parameters["DI_threshold"])
    return {
        "enabled": True,
        "identifier": str(stored.get("identifier", "forex-cli-default")),
        "train_period_days": train_period_days,
        "backtest_period_days": backtest_period_days,
        "feature_parameters": feature_parameters,
        "data_split_parameters": split_parameters,
        "model_training_parameters": model_parameters,
    }


def _validate_freqai_strategy_consumption(strategy: IStrategy, target_column: str) -> None:
    import inspect

    source_by_method: dict[str, str] = {}
    for method_name in (
        "populate_indicators", "populate_entry_trend", "populate_exit_trend"
    ):
        try:
            source_by_method[method_name] = inspect.getsource(
                getattr(type(strategy), method_name)
            )
        except (OSError, TypeError):
            source_by_method[method_name] = ""
    if "freqai.start(" not in source_by_method["populate_indicators"]:
        raise ValueError(
            f"{type(strategy).__name__}.populate_indicators must call self.freqai.start()"
        )
    signal_source = (
        source_by_method["populate_entry_trend"]
        + source_by_method["populate_exit_trend"]
    )
    if target_column not in signal_source:
        raise ValueError(
            f"{type(strategy).__name__} must use FreqAI target {target_column!r} "
            "in entry or exit logic so model predictions affect Hyperopt"
        )


def _build_freqai_strategy_features(
    candles: pd.DataFrame,
    strategy: IStrategy,
    *,
    pair: str,
    timeframe: str,
    freqai_config: dict[str, object],
) -> tuple[pd.DataFrame, str]:
    feature_parameters = dict(freqai_config["feature_parameters"])
    configured_timeframes = feature_parameters.get("include_timeframes", [timeframe])
    if any(
        freqtrade_timeframe(str(configured)) != freqtrade_timeframe(timeframe)
        for configured in configured_timeframes
    ):
        raise ValueError(
            "Forex CLI FreqAI currently supports only the base timeframe in include_timeframes"
        )
    if feature_parameters.get("include_corr_pairlist"):
        raise ValueError("Forex CLI FreqAI does not support include_corr_pairlist yet")
    if feature_parameters.get("principal_component_analysis"):
        raise ValueError("Forex CLI FreqAI does not support principal_component_analysis yet")

    frame = candles.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    metadata = {"pair": pair, "tf": timeframe}
    feature_columns: set[str] = set()
    strategy_type = type(strategy)
    for period in feature_parameters["indicator_periods_candles"]:
        method = getattr(strategy, "feature_engineering_expand_all", None)
        base_method = getattr(IStrategy, "feature_engineering_expand_all", None)
        if callable(method) and getattr(strategy_type, "feature_engineering_expand_all", None) is not base_method:
            frame = method(frame, period=int(period), metadata=metadata)
            feature_columns.update(
                column for column in frame.columns
                if isinstance(column, str) and column.startswith("%")
            )
    for method_name in ("feature_engineering_expand_basic", "feature_engineering_standard"):
        method = getattr(strategy, method_name, None)
        base_method = getattr(IStrategy, method_name, None)
        if callable(method) and getattr(strategy_type, method_name, None) is not base_method:
            frame = method(frame, metadata=metadata)
            feature_columns.update(
                column for column in frame.columns
                if isinstance(column, str) and column.startswith("%")
            )
    if not feature_columns:
        raise ValueError(
            f"{type(strategy).__name__} must define FreqAI %-prefixed feature engineering methods"
        )

    shift_count = int(feature_parameters.get("include_shifted_candles", 0))
    original_features = sorted(feature_columns)
    for lag in range(1, shift_count + 1):
        for column in original_features:
            shifted_name = f"{column}_shift-{lag}"
            frame[shifted_name] = frame[column].shift(lag)
            feature_columns.add(shifted_name)

    target_method = getattr(strategy, "set_freqai_targets", None)
    base_target_method = getattr(IStrategy, "set_freqai_targets", None)
    if not callable(target_method) or getattr(strategy_type, "set_freqai_targets", None) is base_target_method:
        raise ValueError(f"{type(strategy).__name__} must implement set_freqai_targets()")
    frame = target_method(frame, metadata=metadata)
    targets = [column for column in frame if isinstance(column, str) and column.startswith("&")]
    if len(targets) != 1:
        raise ValueError("Forex CLI currently supports exactly one FreqAI & target column")
    if len(feature_columns) == 0:
        raise ValueError("FreqAI strategy generated no usable features")
    return frame[["date", *sorted(feature_columns), targets[0]]], targets[0]


def report_to_json(report: OandaHealthReport) -> str:
    return json.dumps(
        {
            "healthy": report.healthy,
            "account_id": report.account.account_id,
            "currency": report.account.currency,
            "balance": str(report.account.balance),
            "nav": str(report.account.nav),
            "margin_available": str(report.account.margin_available),
            "instruments": [instrument.name for instrument in report.instruments],
            "prices": [
                {
                    "instrument": price.instrument,
                    "bid": str(price.bid),
                    "ask": str(price.ask),
                    "spread": str(price.spread),
                }
                for price in report.prices
            ],
        },
        indent=2,
    )


def paper_report_to_json(ledger: PaperLedger) -> str:
    performance: PaperPerformance = ledger.performance()
    return json.dumps(
        {
            "performance": {
                "realized_pl": str(performance.realized_pl),
                "unrealized_pl": str(performance.unrealized_pl),
                "closed_trades": performance.closed_trades,
                "open_trades": performance.open_trades,
            },
            "trades": [
                {
                    "trade_id": trade.trade_id,
                    "instrument": trade.instrument,
                    "units": trade.units,
                    "entry_price": str(trade.entry_price),
                    "exit_price": str(trade.exit_price) if trade.exit_price is not None else None,
                    "realized_pl": str(trade.realized_pl) if trade.realized_pl is not None else None,
                    "unrealized_pl": str(trade.unrealized_pl)
                    if trade.unrealized_pl is not None
                    else None,
                    "status": trade.status,
                }
                for trade in ledger.all_trades()
            ],
        },
        indent=2,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        return run_setup(args)
    if args.command == "show-timeframes":
        return run_show_timeframes(args)
    if args.command == "dry-run":
        return asyncio.run(run_dry_run(OandaSettings.from_environment(), args))
    if args.command == "practice":
        return asyncio.run(run_practice(OandaSettings.from_environment(), args))
    if args.command == "practice-run":
        return asyncio.run(run_practice_run(OandaSettings.from_environment(), args))
    if args.command == "practice-report":
        records = PracticeRunRecorder(Path(args.runs)).records()
        print(json.dumps([record.to_dict() for record in records], indent=2))
        return 0
    if args.command == "backtest":
        return asyncio.run(run_backtest(OandaSettings.from_environment(), args))
    if args.command == "hyperopt":
        return asyncio.run(run_hyperopt(OandaSettings.from_environment(), args))
    if args.command == "download-data":
        return asyncio.run(run_download_data(OandaSettings.from_environment(), args))
    if args.command == "cache-clear":
        return run_cache_clear(args)
    if args.command == "paper-report":
        print(paper_report_to_json(PaperLedger(Path(args.ledger))))
        return 0
    if args.command == "paper-backup":
        destination = PaperLedger(Path(args.ledger)).backup_to(Path(args.destination))
        print(json.dumps({"source": args.ledger, "backup": str(destination)}, indent=2))
        return 0
    if args.command != "health":
        return 2
    settings = OandaSettings.from_environment()
    if args.instruments:
        instruments = tuple(item.strip() for item in args.instruments.split(",") if item.strip())
        settings = replace(settings, instruments=instruments)
    report = asyncio.run(run_health(settings))
    print(report_to_json(report))
    return 0
