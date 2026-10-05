"""Standalone read-only CLI for OANDA Practice connectivity checks."""

import argparse
import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from collections.abc import Sequence

import pandas as pd
from rich.console import Console
from rich.table import Table

from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.config import OandaSettings, load_forex_config, save_forex_config
from freqtrade.forex.execution import ExecutionMode, OandaExecutionGateway
from freqtrade.forex.health import OandaHealthCheck, OandaHealthReport
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.ledger import PaperLedger, PaperPerformance
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.paper import DryRunSession
from freqtrade.forex.practice_runs import PracticeRunRecord, PracticeRunRecorder
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.runner import DryRunPortfolioWorker, DryRunWorker, WorkerConfig
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    freqtrade_timeframe,
    load_strategy,
    strategy_informative_candle_count,
)
from freqtrade.forex.strategy_hyperopt import (
    DEFAULT_HYPEROPT_LOSS,
    HYPEROPT_LOSS_FUNCTIONS,
    run_strategy_hyperopt,
)
from freqtrade.forex.strategy_loop import DryRunStrategyLoop, EmaCrossStrategy
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
        "backtest", help="Run a read-only historical forex strategy backtest"
    )
    backtest.add_argument("--pair", default="EUR/USD")
    backtest.add_argument(
        "--timeframe", default="5m", choices=("1m", "5m", "15m", "1h")
    )
    backtest.add_argument("--count", type=int, default=500)
    backtest.add_argument("--start", default=None, help="UTC ISO start for cached historical data")
    backtest.add_argument("--end", default=None, help="UTC ISO end for cached historical data")
    backtest.add_argument("--data-cache", default=None, help="JSON cache for raw and normalized candles")
    backtest.add_argument(
        "--refresh-data", action="store_true", help="Fetch from OANDA even when cache exists"
    )
    backtest.add_argument(
        "--strategy",
        default="ForexEmaStrategy",
        help="Freqtrade strategy class to backtest (or 'ema' for the built-in EMA strategy)",
    )
    backtest.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--financing-rate-per-day", type=Decimal, default=Decimal("0"))
    hyperopt = subparsers.add_parser(
        "hyperopt", help="Optimize a Freqtrade strategy's declared parameters"
    )
    hyperopt.add_argument("--pair", default="EUR/USD")
    hyperopt.add_argument(
        "--timeframe", default="5m", choices=("1m", "5m", "15m", "1h")
    )
    hyperopt.add_argument("--count", type=int, default=500)
    hyperopt.add_argument("--start", default=None, help="UTC ISO start for cached historical data")
    hyperopt.add_argument("--end", default=None, help="UTC ISO end for cached historical data")
    hyperopt.add_argument("--data-cache", default=None, help="JSON cache for raw and normalized candles")
    hyperopt.add_argument(
        "--refresh-data", action="store_true", help="Fetch from OANDA even when cache exists"
    )
    hyperopt.add_argument(
        "--epochs",
        type=int,
        default=30,
        help="Maximum strategy parameter combinations to evaluate",
    )
    hyperopt.add_argument(
        "--strategy",
        default="ForexEmaStrategy",
        help="Freqtrade strategy class whose declared parameters should be optimized",
    )
    hyperopt.add_argument(
        "--hyperopt-loss", choices=HYPEROPT_LOSS_FUNCTIONS, default=DEFAULT_HYPEROPT_LOSS
    )
    hyperopt.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    hyperopt.add_argument("--results-dir", default="user_data/hyperopt_results")
    download = subparsers.add_parser(
        "download-data", help="Download and cache OANDA historical candles"
    )
    download.add_argument("--pair", default="EUR/USD")
    download.add_argument(
        "--timeframe", default="5m", choices=("1m", "5m", "1h")
    )
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
    clear_cache.add_argument(
        "--timeframe", default=None, choices=("1m", "5m", "1h")
    )
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
                f"{pair} has no approved strategy/timeframe. Approve a revision in /hyperopt first."
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
                args.pair, args.timeframe, start=args.start, end=args.end,
                store=store, refresh=args.refresh_data,
            )
        else:
            frame = await provider.fetch_latest(
                args.pair, args.timeframe, count=args.count,
                store=store, refresh=args.refresh_data,
            )
        price_instruments = (
            (instrument_name, "GBP_USD") if account.currency != "USD" else (instrument_name,)
        )
        prices = await client.get_prices(price_instruments)
        price_map = {price.instrument: price for price in prices}
        if not metadata or instrument_name not in price_map:
            raise ValueError(f"missing metadata or price for {instrument_name}")
        conversion = Decimal("1")
        if account.currency != "USD":
            gbp_usd = price_map.get("GBP_USD")
            if gbp_usd is None:
                raise ValueError("GBP_USD price is required for GBP account backtest conversion")
            conversion = Decimal("1") / gbp_usd.midpoint

        if args.strategy == "ema":
            strategy = EmaCrossStrategy()
            backtest_strategy = strategy
        else:
            timeframe = freqtrade_timeframe(args.timeframe)
            strategy = load_strategy(args.strategy, timeframe, args.pair)
            informative_candles = await _fetch_strategy_informative_candles(
                provider,
                strategy_class=args.strategy,
                pair=args.pair,
                timeframe=args.timeframe,
                base_candle_count=len(frame),
                start=args.start,
                end=args.end,
                store=store,
                refresh=args.refresh_data,
            )
            backtest_strategy = FreqtradeStrategyAdapter(
                strategy, args.pair, informative_candles
            )
            backtest_strategy.prepare_backtest(frame)

        result = ForexBacktester(
            backtest_strategy,
            metadata[0],
            starting_balance=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            stop_pips=args.stop_pips,
            spread=price_map[instrument_name].spread,
            slippage=args.slippage,
            financing_rate_per_day=args.financing_rate_per_day,
            quote_to_account_rate=conversion,
        ).run(frame, detail_candles=frame)

    summary = _backtest_summary(
        result,
        frame,
        instrument=instrument_name,
        timeframe=args.timeframe,
        strategy=args.strategy,
        account_currency=account.currency,
    )
    _print_backtest_summary(summary)
    print(json.dumps({
        "instrument": instrument_name,
        "timeframe": args.timeframe,
        "strategy": args.strategy,
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
    if args.count < 40:
        raise ValueError("--count must be at least 40 for strategy hyperopt")
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
        price_instruments = (
            (instrument_name, "GBP_USD") if account.currency != "USD" else (instrument_name,)
        )
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
        informative_candles = await _fetch_strategy_informative_candles(
            provider,
            strategy_class=args.strategy,
            pair=args.pair,
            timeframe=args.timeframe,
            base_candle_count=len(frame),
            start=args.start,
            end=args.end,
            store=store,
            refresh=args.refresh_data,
        )
        strategy_timeframe = freqtrade_timeframe(args.timeframe)

        def show_strategy_epoch(
            done: int, total: int, candidate: dict[str, object]
        ) -> None:
            print(
                f"Epoch {done}/{total} | objective={candidate['objective']} "
                f"| validation_pl={candidate['validationNetPl']} "
                f"| params={candidate['parameters']} "
                f"| minimal_roi={candidate.get('minimal_roi') or {}}",
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
        report_path = Path(args.results_dir) / (
            f"{instrument_name}_{args.timeframe}_{args.strategy}_hyperopt.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({
                "strategy": args.strategy,
                "pair": args.pair.upper(),
                "timeframe": args.timeframe,
                "hyperopt_loss": args.hyperopt_loss,
                "candidates_tested": len(candidates),
                "best": candidates[0],
                "best_minimal_roi": candidates[0].get("minimal_roi") or {},
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
        "candidates_tested": len(candidates),
        "best_parameters": candidates[0]["parameters"],
        "best_minimal_roi": candidates[0].get("minimal_roi") or {},
        "report_path": str(report_path),
    }, indent=2, default=str))
    return 0

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
