"""Standalone read-only CLI for OANDA Practice connectivity checks."""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from freqtrade.forex.config import OandaSettings, save_forex_config
from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.health import OandaHealthCheck, OandaHealthReport
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.hyperopt import ForexHyperopt
from freqtrade.forex.ledger import PaperLedger, PaperPerformance
from freqtrade.forex.execution import ExecutionMode, OandaExecutionGateway
from freqtrade.forex.paper import DryRunSession
from freqtrade.forex.practice_runs import PracticeRunRecord, PracticeRunRecorder
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.runner import DryRunWorker, WorkerConfig
from freqtrade.forex.strategy_loop import DryRunStrategyLoop, EmaCrossStrategy
from freqtrade.forex.oanda import OandaClient


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
    dry_run = subparsers.add_parser("dry-run", help="Run the live-price paper strategy safely")
    dry_run.add_argument("--pair", default="EUR/USD")
    dry_run.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
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
    backtest = subparsers.add_parser("backtest", help="Run a read-only historical forex backtest")
    backtest.add_argument("--pair", default="EUR/USD")
    backtest.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
    backtest.add_argument("--count", type=int, default=500)
    backtest.add_argument("--start", default=None, help="UTC ISO start for cached historical data")
    backtest.add_argument("--end", default=None, help="UTC ISO end for cached historical data")
    backtest.add_argument("--data-cache", default=None, help="JSON cache for raw and normalized candles")
    backtest.add_argument("--stop-pips", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--financing-rate-per-day", type=Decimal, default=Decimal("0"))
    hyperopt = subparsers.add_parser("hyperopt", help="Optimize EMA and stop parameters read-only")
    hyperopt.add_argument("--pair", default="EUR/USD")
    hyperopt.add_argument("--timeframe", default="5m", choices=("5m", "1h"))
    hyperopt.add_argument("--count", type=int, default=500)
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
    instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments((instrument_name,))
        if not metadata:
            raise ValueError(f"instrument not available: {instrument_name}")
        account = await client.get_account_summary()
        prices = await client.get_prices((instrument_name, "GBP_USD"))
        price_map = {price.instrument: price for price in prices}
        conversion = Decimal("1")
        if account.currency != "USD":
            gbp_usd = price_map.get("GBP_USD")
            if gbp_usd is None:
                raise ValueError("GBP_USD price is required for GBP account risk conversion")
            conversion = Decimal("1") / gbp_usd.midpoint
        ledger = PaperLedger(Path(args.ledger))
        session = DryRunSession(client, OandaExecutionGateway(settings, ExecutionMode.DRY_RUN), (instrument_name,), ledger=ledger)
        loop = DryRunStrategyLoop(
            OandaMarketDataProvider(client, settings),
            session,
            EmaCrossStrategy(),
            metadata[0],
            account_equity=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            stop_pips=args.stop_pips,
            quote_to_account_rate=conversion,
        )
        interval = 300.0 if args.timeframe == "5m" else 3600.0
        worker = DryRunWorker(
            loop,
            WorkerConfig(pair=args.pair, timeframe=args.timeframe, interval_seconds=interval),
            on_result=lambda result: print(json.dumps(result.as_dict(), default=str)),
        )
        await worker.run(max_steps=args.steps)
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


async def run_backtest(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.count < 30:
        raise ValueError("--count must be at least 30")
    instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
    granularity = {"5m": "M5", "1h": "H1"}[args.timeframe]
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments((instrument_name,))
        account = await client.get_account_summary()
        provider = OandaMarketDataProvider(client, settings)
        if (args.start is None) != (args.end is None):
            raise ValueError("--start and --end must be provided together")
        if args.start and args.end:
            frame = await provider.fetch_historical(
                args.pair,
                args.timeframe,
                start=args.start,
                end=args.end,
                store=HistoricalCandleStore(Path(args.data_cache)) if args.data_cache else None,
            )
        else:
            candles = await client.get_candles(instrument_name, granularity, count=args.count)
            frame = OandaMarketDataProvider.candles_to_dataframe(candles)
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
        result = ForexBacktester(
            EmaCrossStrategy(),
            metadata[0],
            starting_balance=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            stop_pips=args.stop_pips,
            spread=price_map[instrument_name].spread,
            slippage=args.slippage,
            financing_rate_per_day=args.financing_rate_per_day,
            quote_to_account_rate=conversion,
        ).run(frame)
    print(json.dumps({
        "instrument": instrument_name,
        "timeframe": args.timeframe,
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
    }, indent=2))
    return 0


async def run_hyperopt(settings: OandaSettings, args: argparse.Namespace) -> int:
    if args.count < 30:
        raise ValueError("--count must be at least 30")
    instrument_name = OandaMarketDataProvider.to_oanda_instrument(args.pair)
    granularity = {"5m": "M5", "1h": "H1"}[args.timeframe]
    async with OandaClient(
        settings.token, settings.account_id, environment=settings.environment
    ) as client:
        metadata = await client.get_instruments((instrument_name,))
        account = await client.get_account_summary()
        candles = await client.get_candles(instrument_name, granularity, count=args.count)
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
        result = ForexHyperopt(
            metadata[0],
            starting_balance=account.balance,
            risk_fraction=Decimal(settings.risk_fraction),
            spread=price_map[instrument_name].spread,
            slippage=args.slippage,
            financing_rate_per_day=args.financing_rate_per_day,
            quote_to_account_rate=conversion,
        ).run(OandaMarketDataProvider.candles_to_dataframe(candles))
    best = result.best
    print(json.dumps({
        "instrument": instrument_name,
        "timeframe": args.timeframe,
        "candles": len(candles),
        "candidates_tested": result.candidates_tested,
        "best_fast_period": best.fast_period,
        "best_slow_period": best.slow_period,
        "best_stop_pips": str(best.stop_pips),
        "ending_balance": str(best.result.ending_balance),
        "net_pl": str(best.result.net_pl),
        "max_drawdown": str(best.max_drawdown),
        "objective": str(best.objective),
        "trades": len(best.result.trades),
        "win_rate": str(best.result.win_rate),
    }, indent=2))
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
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        return run_setup(args)
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
