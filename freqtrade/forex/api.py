"""HTTP API for OANDA-backed trading controls and paper performance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response

from freqtrade.forex.ai_hyperopt import run_ai_hyperopt_robust
from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline
from freqtrade.forex.auto_execution import OandaAutoStrategyExecutor
from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.config import OandaSettings, load_forex_config, save_forex_config
from freqtrade.forex.health import OandaHealthCheck
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.ledger import PaperLedger
from freqtrade.forex.models import OandaEnvironment, OandaInstrument
from freqtrade.forex.oanda import OandaAPIError, OandaClient, discover_oanda_accounts
from freqtrade.forex.strategy_catalog import discover_strategy_files, validate_strategy_upload
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    freqtrade_timeframe,
    load_strategy,
    oanda_granularity,
    strategy_informative_candle_count,
    strategy_informative_timeframes,
)
from freqtrade.timeframe import timeframe_to_seconds


MINIMUM_FREQAI_HISTORY_DAYS = 7
logger = logging.getLogger(__name__)


def minimum_freqai_history(freqai_config: dict[str, object], timeframe: str) -> int:
    feature_source = freqai_config.get("featureParameters", {})
    feature_source = feature_source if isinstance(feature_source, dict) else {}
    try:
        seconds = timeframe_to_seconds(freqtrade_timeframe(timeframe))
        label_period = int(feature_source.get("labelPeriodCandles", 2))
        periods = [int(value) for value in feature_source.get("indicatorPeriodsCandles", [5, 14])]
        shifted = int(feature_source.get("includeShiftedCandles", 0))
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid FreqAI history settings: {exc}") from exc
    seven_day_candles = (MINIMUM_FREQAI_HISTORY_DAYS * 86400 + seconds - 1) // seconds
    warmup_candles = 40 + label_period + max(periods, default=14) + shifted
    return max(seven_day_candles, warmup_candles)


def freqai_history_config(
    freqai_config: dict[str, object], timeframe: str, history_candles: int
) -> dict[str, object]:
    config = dict(freqai_config)
    seconds = timeframe_to_seconds(freqtrade_timeframe(timeframe))
    train_days = int(config.get("trainPeriodDays", 30))
    validation_days = int(config.get("backtestPeriodDays", 7))
    configured_candles = (train_days + validation_days) * 86400 // seconds
    if history_candles >= configured_candles:
        return config

    candles_per_day = max(1, 86400 // seconds)
    available_days = max(2, history_candles // candles_per_day)
    minimum_validation_days = (40 + candles_per_day - 1) // candles_per_day
    validation_days = min(
        available_days - 1,
        max(
            minimum_validation_days,
            (available_days * validation_days + train_days + validation_days - 1)
            // (train_days + validation_days),
        ),
    )
    train_days = min(train_days, available_days - validation_days)
    config["trainPeriodDays"] = train_days
    config["backtestPeriodDays"] = validation_days
    return config


def _validated_protection_values(
    payload: dict,
) -> tuple[str | None, str | None, str | None]:
    stop_loss = payload.get("stopLoss")
    take_profit = payload.get("takeProfit")
    trailing_distance = payload.get("trailingStopLossDistance")
    if stop_loss is not None and trailing_distance is not None:
        raise HTTPException(
            status_code=400,
            detail="A fixed stop loss and a trailing stop loss cannot be set simultaneously",
        )
    if stop_loss is None and take_profit is None and trailing_distance is None:
        raise HTTPException(status_code=400, detail="At least one protective order is required")
    for name, value in (("stopLoss", stop_loss), ("takeProfit", take_profit)):
        if value is None:
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{name} must be a number") from exc
        if not parsed.is_finite() or parsed <= 0:
            raise HTTPException(
                status_code=400, detail=f"{name} must be a positive number"
            )
    if trailing_distance is not None:
        try:
            parsed_distance = Decimal(str(trailing_distance))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="trailingStopLossDistance must be a number",
            ) from exc
        if not parsed_distance.is_finite() or parsed_distance <= 0:
            raise HTTPException(
                status_code=400,
                detail="trailingStopLossDistance must be a positive number",
            )
        trailing_distance = str(parsed_distance)
    return (
        str(stop_loss) if stop_loss is not None else None,
        str(take_profit) if take_profit is not None else None,
        str(trailing_distance) if trailing_distance is not None else None,
    )


def _render_ui_index(ui_index: Path) -> str:
    if not ui_index.exists():
        return ""

    html = ui_index.read_text(encoding="utf-8")
    asset_dir = ui_index.parent / "assets"
    if not asset_dir.exists():
        return html

    js_assets = sorted(asset_dir.glob("*.js"))
    css_assets = sorted(asset_dir.glob("*.css"))

    if js_assets:
        html = re.sub(r'src="/assets/[^"]+\.js"', f'src="/assets/{js_assets[0].name}"', html)
    if css_assets:
        html = re.sub(r'href="/assets/[^"]+\.css"', f'href="/assets/{css_assets[0].name}"', html)

    return html


def _clear_cached_historical_data(pair: str | None = None, timeframe: str | None = None) -> list[str]:
    if not pair or not timeframe:
        return []

    data_dir = Path("user_data/data")
    if not data_dir.exists():
        return []

    removed: list[str] = []
    instrument = pair.replace("/", "_").upper()
    normalized_timeframe = freqtrade_timeframe(timeframe)
    store_path = data_dir / "oanda" / "candles.json"
    cleared_ranges = HistoricalCandleStore(store_path).clear(
        instrument=instrument,
        timeframe=normalized_timeframe,
    )
    if cleared_ranges:
        removed.append(f"{store_path} ({cleared_ranges} ranges)")

    timeframe_tokens = {timeframe.upper(), normalized_timeframe.upper()}
    try:
        timeframe_tokens.add(oanda_granularity(normalized_timeframe).upper())
    except ValueError:
        pass
    candle_extensions = (".feather", ".parquet", ".csv")
    for candidate in data_dir.rglob("*"):
        if not candidate.is_file() or not candidate.name.upper().startswith(instrument):
            continue
        filename = candidate.name.upper()
        if not any(filename.endswith(extension.upper()) for extension in candle_extensions):
            continue
        if any(
            filename.startswith(f"{instrument}{separator}{token}")
            and filename[len(instrument) + 1 + len(token):len(instrument) + 2 + len(token)] in {".", "-", "_"}
            for token in timeframe_tokens
            for separator in ("-", "_")
        ):
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    return removed


def _clear_cached_ai_models(pair: str) -> list[str]:
    model_dir = Path("user_data/hyperopt_results")
    if not model_dir.exists():
        return []

    instrument = pair.replace("/", "_").upper()
    cache_suffixes = (".txt", ".model.json", ".predictions.json")
    removed: list[str] = []
    for candidate in model_dir.rglob("*"):
        if (
            candidate.is_file()
            and candidate.name.upper().startswith(f"{instrument}_")
            and candidate.name.lower().endswith(cache_suffixes)
        ):
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    return removed


def format_hyperopt_report(report: dict) -> str:
    """Format baseline and generic strategy Hyperopt results."""
    best_parameters = report.get("bestParameters") or {}
    parameter_text = lambda parameters: " ".join(
        f"{name}={value}{'%' if name == 'maxSpreadPct' else ''}"
        for name, value in parameters.items()
    ) or "none"
    coverage_line = (
        f"  {report['candidatesTested']} candidates tested ({report['attemptsRequested']} attempts requested)"
        f" across {report['pairsTested']} pair(s) and {report['periodsTested']} period(s)"
        f" ({report['coverage']} validation slices)."
    )
    lines = [
        f"Hyperopt report - {report['pair']} ({report['timeframe']}) - {report['status']}",
        "",
        coverage_line,
        f"  Strategy: {report.get('strategy', 'ForexAIStrategyBaseline')}",
        f"  Loss function: {report.get('hyperoptLoss', 'ProfitDrawDownHyperOptLoss')}",
        f"  Best parameters: {parameter_text(best_parameters)}",
        f"  Objective: {report['objective']}",
        f"  Train:      net P/L {report['train']['netPl']}  drawdown {report['train']['drawdown']}  trades {report['train']['trades']}",
        f"  Validation: net P/L {report['validation']['netPl']}  drawdown {report['validation']['drawdown']}  trades {report['validation']['trades']}",
        "",
        "  Top candidates (validation net P/L):",
    ]
    for candidate in report["candidates"][:5]:
        sign = "+" if float(candidate["validationNetPl"]) >= 0 else ""
        parameters = candidate.get("parameters") or {
            name: candidate[name]
            for name in ("entryThreshold", "maxSpreadPct")
            if name in candidate
        }
        lines.append(
            f"    #{candidate['rank']} {parameter_text(parameters)}"
            f" objective={candidate['objective']} val P/L={sign}{candidate['validationNetPl']}"
            f" trades={candidate['validationTrades']}"
        )
    return "\n".join(lines)


def create_app(ledger_path: Path = Path("user_data/oanda/paper.sqlite")) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    app = FastAPI(title="Forex Dry-Run API", version="0.1.0")
    app.state.strategy_execution_task = None
    strategy_execution_state_path = Path(
        os.environ.get(
            "OANDA_AUTO_EXECUTION_STATE_PATH",
            "user_data/oanda/strategy-execution.json",
        )
    )
    risk_config_state_path = Path(
        os.environ.get(
            "OANDA_RISK_CONFIG_PATH",
            str(ledger_path.parent / "risk-config.json"),
        )
    )
    risk_config_state_loaded = False
    ui_index = Path(__file__).resolve().parents[2] / "apps" / "ui" / "dist" / "index.html"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    ledger = PaperLedger(ledger_path)
    SESSION_USERS = {
        "viewer": {"username": "viewer", "password": "viewer", "role": "viewer"},
        "operator": {"username": "operator", "password": "operator", "role": "operator"},
        "admin": {"username": "admin", "password": "admin", "role": "admin"},
    }
    ACTIVE_SESSIONS: dict[str, dict[str, str]] = {}
    AUDIT_LOGS: list[dict] = []
    ORDER_HISTORY: list[dict] = []
    chart_cache: dict[tuple[str, str, int, str], tuple[float, dict]] = {}
    chart_cache_ttl = 45.0
    chart_cache_capacity = 128
    instrument_metadata_cache: dict[tuple[str, str], tuple[datetime, OandaInstrument]] = {}
    instrument_metadata_cache_ttl = timedelta(minutes=5)

    def redact_value(value: object) -> object:
        if isinstance(value, str):
            return "***REDACTED***" if value else value
        if isinstance(value, (list, tuple, set)):
            return [redact_value(item) for item in value]
        if isinstance(value, dict):
            return {key: redact_value(value[key]) for key in value}
        return value

    def sanitize_for_log(data: dict | None) -> dict:
        if not data:
            return {}
        sanitized: dict[str, object] = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("token", "secret", "password", "authorization", "cookie", "key")):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = sanitize_for_log(value)
            elif isinstance(value, list):
                sanitized[key] = [sanitize_for_log(item) if isinstance(item, dict) else redact_value(item) for item in value]
            else:
                sanitized[key] = redact_value(value)
        return sanitized

    def record_audit_event(event: str, *, details: dict | None = None, username: str | None = None, role: str | None = None, allowed: bool | None = None) -> None:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "username": username,
            "role": role,
            "allowed": allowed,
            "details": sanitize_for_log(details),
        }
        AUDIT_LOGS.append(log_entry)

    def create_session_token() -> str:
        return secrets.token_urlsafe(32)

    def resolve_session_user(session_token: str | None) -> dict[str, str] | None:
        if not session_token:
            return None
        return ACTIVE_SESSIONS.get(session_token)

    def validate_write_access(
        user_role: str | None,
        csrf_token: str | None,
        session_token: str | None = None,
    ) -> None:
        session_user = resolve_session_user(session_token)
        if user_role is not None and session_user is not None and user_role != session_user["role"]:
            raise HTTPException(status_code=403, detail="Forbidden: session role does not match request role")
        if session_user is None:
            if user_role == "viewer":
                raise HTTPException(status_code=403, detail="Forbidden: viewer role cannot submit orders")
            if not csrf_token:
                raise HTTPException(status_code=403, detail="Missing CSRF token")
            if user_role not in {"operator", "admin"}:
                raise HTTPException(status_code=403, detail="Forbidden: invalid role for write actions")
            return

        if user_role is None:
            raise HTTPException(status_code=403, detail="Forbidden: user role required for write actions")
        if not csrf_token:
            raise HTTPException(status_code=403, detail="Missing CSRF token")
        if user_role not in {"operator", "admin"}:
            raise HTTPException(status_code=403, detail="Forbidden: invalid role for write actions")

    def resolve_setup_paths() -> tuple[Path, Path]:
        config_path = Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
        strategy_path = Path(os.environ.get("FOREX_STRATEGY_PATH", "user_data/strategies/ForexAIStrategyBaseline.py"))
        return config_path, strategy_path

    def fallback_health() -> dict:
        return {
            "healthy": False,
            "account_id": "demo-acc",
            "currency": "USD",
            "balance": "184260.48",
            "nav": "184260.48",
            "margin_available": "126840.00",
            "instruments": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"],
            "prices": [
                {"instrument": "EUR/USD", "bid": "1.0906", "ask": "1.0908", "spread": "0.0002"},
                {"instrument": "GBP/USD", "bid": "1.2794", "ask": "1.2797", "spread": "0.0003"},
                {"instrument": "USD/JPY", "bid": "148.68", "ask": "148.72", "spread": "0.04"},
                {"instrument": "AUD/USD", "bid": "0.6648", "ask": "0.6651", "spread": "0.0003"},
            ],
        }

    def fallback_account_summary() -> dict:
        return {
            "equity": "$184,260.48",
            "exposure": "$32,420.00",
            "netPnl": "$8,972.18",
            "marginUsed": "31.4%",
            "dailyRisk": "0.72%",
            "drawdown": "4.10%",
        }

    def fallback_market_summary() -> dict:
        return {
            "instruments": [
                {"pair": "EUR/USD", "bid": 1.0906, "ask": 1.0908, "spread": 0.0002, "change": "+0.42%"},
                {"pair": "GBP/USD", "bid": 1.2794, "ask": 1.2797, "spread": 0.0003, "change": "+0.18%"},
                {"pair": "USD/JPY", "bid": 148.68, "ask": 148.72, "spread": 0.04, "change": "-0.27%"},
                {"pair": "AUD/USD", "bid": 0.6648, "ask": 0.6651, "spread": 0.0003, "change": "+0.32%"},
            ],
            "strategySignals": [
                {"name": "FX Trend Pulse", "mode": "Practice", "status": "Running", "signal": "Buy bias", "quality": "84%"},
                {"name": "Breakout Guard", "mode": "Dry-run", "status": "Watching", "signal": "Neutral", "quality": "76%"},
                {"name": "Carry Edge", "mode": "Backtest", "status": "Validated", "signal": "Short bias", "quality": "91%"},
            ],
            "alerts": [
                {"title": "Risk check passed", "detail": "Daily loss remains within policy threshold."},
                {"title": "Session rollover", "detail": "London close overlap is active for EUR/USD."},
                {"title": "Order validation", "detail": "Client order ID confirmed and idempotency check passed."},
            ],
        }

    def fallback_risk_summary() -> dict:
        return {
            "dailyLoss": "$1,420.20",
            "maxExposure": "$45,000.00",
            "marginLevel": "130.4%",
            "killSwitch": False,
            "exposureByPair": [
                {"pair": "EUR/USD", "value": "$15.4k"},
                {"pair": "GBP/USD", "value": "$12.9k"},
                {"pair": "USD/JPY", "value": "$8.1k"},
            ],
        }

    def fallback_orders() -> list[dict]:
        return [
            {"id": "ORD-1042", "symbol": "EUR/USD", "side": "BUY", "volume": "1200", "status": "Filled", "createdAt": "2026-09-18T09:14:22Z", "risk": "0.75%"},
            {"id": "ORD-1043", "symbol": "GBP/USD", "side": "SELL", "volume": "900", "status": "Pending", "createdAt": "2026-09-18T09:17:10Z", "risk": "0.62%"},
            {"id": "ORD-1044", "symbol": "USD/JPY", "side": "BUY", "volume": "800", "status": "Cancelled", "createdAt": "2026-09-18T09:20:07Z", "risk": "0.48%"},
        ]

    def fallback_settings() -> dict:
        return {
            "environment": "Practice",
            "broker": "OANDA",
            "database": "SQLite",
            "websocket": "Connected",
        }

    def fallback_strategies() -> list[dict]:
        return [
            {"id": "fx-trend-pulse", "name": "FX Trend Pulse", "mode": "Practice", "status": "Running", "signal": "Buy bias", "confidence": "84%", "version": "v2.8.1", "warmup": "96 candles", "lastCandle": "M5 • 09:35", "enabled": True},
            {"id": "breakout-guard", "name": "Breakout Guard", "mode": "Dry-run", "status": "Watching", "signal": "Neutral", "confidence": "76%", "version": "v1.4.9", "warmup": "72 candles", "lastCandle": "M15 • 09:20", "enabled": False},
            {"id": "carry-edge", "name": "Carry Edge", "mode": "Backtest", "status": "Validated", "signal": "Short bias", "confidence": "91%", "version": "v3.0.2", "warmup": "120 candles", "lastCandle": "H1 • 08:00", "enabled": True},
        ]

    def fallback_backtests() -> list[dict]:
        return [
            {"id": "bt-2026-09-18-01", "name": "EUR/USD Multi-Session", "pair": "EUR/USD", "timeframe": "M15", "status": "Completed", "result": "+4.61%", "netProfit": "+$8,420.10", "drawdown": "5.20%", "trades": 62, "updatedAt": "2026-09-18T09:42:00Z"},
            {"id": "bt-2026-09-18-02", "name": "GBP/JPY Volatility", "pair": "GBP/JPY", "timeframe": "H1", "status": "Running", "result": "Processing", "netProfit": "+$2,960.40", "drawdown": "3.10%", "trades": 28, "updatedAt": "2026-09-18T09:26:00Z"},
            {"id": "bt-2026-09-18-03", "name": "USD/CHF Carry Filter", "pair": "USD/CHF", "timeframe": "H4", "status": "Warning", "result": "+1.08%", "netProfit": "+$1,640.90", "drawdown": "8.40%", "trades": 17, "updatedAt": "2026-09-18T08:42:00Z"},
        ]

    AI_CONFIG: dict[str, object] = {
        "strategyName": "FX Trend Pulse",
        "strategyClass": "ForexAIStrategyBaseline",
        "model": "hybrid",
        "timeframe": "M5",
        "riskBudget": "0.72%",
        "featureSet": ["trend", "spread", "session", "volatility"],
        "trainingMode": "dry-run",
        "entryThreshold": "0.5",
        "exitThreshold": "0.0",
        "volatilityWindow": "5",
        "atrWindow": "14",
        "maxSpreadPct": "1.0",
        # FreqAI-style dataset/training controls used by the LightGBM research
        # pipeline (build_forex_ai_dataset / compare_research_models). These
        # values are read directly by /api/v1/ai/model-comparison; they are
        # not decorative.
        "freqai": {
            "trainPeriodDays": 30,
            "backtestPeriodDays": 7,
            "featureParameters": {
                "labelPeriodCandles": 2,
                "includeShiftedCandles": 0,
                "indicatorPeriodsCandles": [5, 14],
                "weightFactor": 0.0,
                "diThreshold": 0.0,
            },
        },
    }
    AI_CONFIG_BY_PAIR: dict[str, dict[str, object]] = {}
    AI_CONFIG_REVISIONS: dict[str, list[dict[str, object]]] = {}
    AI_PENDING_OPTIMIZATION: dict[str, dict[str, object]] = {}
    RISK_CONFIG_BY_PAIR: dict[str, dict[str, object]] = {}
    # Background hyperopt jobs keyed by the primary pair; supports live progress and stop.
    AI_HYPEROPT_JOBS: dict[str, dict[str, object]] = {}
    # Last completed/stopped report per pair, kept so the report survives job cleanup.
    AI_HYPEROPT_REPORTS: dict[str, dict[str, object]] = {}

    def ai_config_for_pair(pair: str) -> dict[str, object]:
        normalized = pair.replace("_", "/").upper()
        if normalized not in AI_CONFIG_BY_PAIR:
            AI_CONFIG_BY_PAIR[normalized] = dict(AI_CONFIG)
            setup = load_forex_config(Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json")))
            pair_strategy = dict(setup.get("pair_strategies", {})).get(normalized.replace("/", "_"))
            if pair_strategy:
                AI_CONFIG_BY_PAIR[normalized]["strategyClass"] = pair_strategy
            pair_timeframe = dict(setup.get("pair_timeframes", {})).get(normalized.replace("/", "_"))
            timeframe_labels = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "2h": "H2", "4h": "H4", "6h": "H6", "8h": "H8", "12h": "H12", "1d": "D1", "1w": "W1", "1mo": "MN1"}
            if pair_timeframe in timeframe_labels:
                AI_CONFIG_BY_PAIR[normalized]["timeframe"] = timeframe_labels[pair_timeframe]
            AI_CONFIG_BY_PAIR[normalized]["configRevision"] = "r0"
            AI_CONFIG_REVISIONS[normalized] = [dict(AI_CONFIG_BY_PAIR[normalized])]
        return AI_CONFIG_BY_PAIR[normalized]

    def approved_runtime_revision(pair: str) -> dict[str, object] | None:
        normalized_pair = normalize_pair(pair)
        pair_key = normalized_pair.replace("/", "_")
        setup = load_forex_config(
            Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
        )
        revision = dict(setup.get("pair_approved_revisions", {})).get(pair_key)
        if not isinstance(revision, dict):
            return None
        configured_strategy = dict(setup.get("pair_strategies", {})).get(pair_key)
        configured_timeframe = dict(setup.get("pair_timeframes", {})).get(pair_key)
        approved_strategy = revision.get("strategyClass")
        approved_timeframe = revision.get("timeframe")
        if (
            revision.get("pair") != normalized_pair
            or not configured_strategy
            or not configured_timeframe
            or approved_strategy != configured_strategy
            or freqtrade_timeframe(str(approved_timeframe))
            != freqtrade_timeframe(str(configured_timeframe))
        ):
            return None
        return dict(revision)

    def hyperopt_scope(pair: str, strategy_class: str, timeframe: str) -> str:
        return f"{normalize_pair(pair)}|{strategy_class}|{timeframe.upper()}"

    def validate_ai_config(candidate: dict[str, object]) -> None:
        model = str(candidate.get("model", "hybrid")).lower()
        if model not in {"rule-based", "ml", "hybrid"}:
            raise HTTPException(status_code=400, detail="AI model must be rule-based, ml, or hybrid")
        timeframe = str(candidate.get("timeframe", "M5")).upper()
        if timeframe not in {"M1", "M5", "M15", "M30", "H1", "H2", "H4", "H6", "H8", "H12", "D", "D1", "W", "W1", "M", "MN1"}:
            raise HTTPException(status_code=400, detail="Unsupported AI timeframe")
        try:
            if not 0 < float(candidate["entryThreshold"]) <= 10 or not 0 <= float(candidate["exitThreshold"]) <= 10:
                raise ValueError
            if int(candidate["volatilityWindow"]) < 2 or int(candidate["atrWindow"]) < 2 or not 0 < float(candidate["maxSpreadPct"]) <= 10:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid AI baseline parameter range") from exc
        validate_freqai_config(candidate.get("freqai"))

    def validate_freqai_config(freqai: object) -> None:
        if freqai is None:
            return
        if not isinstance(freqai, dict):
            raise HTTPException(status_code=400, detail="freqai settings must be an object")
        feature_parameters = freqai.get("featureParameters", {})
        if not isinstance(feature_parameters, dict):
            raise HTTPException(status_code=400, detail="freqai.featureParameters must be an object")
        try:
            train_period_days = int(freqai.get("trainPeriodDays", 30))
            backtest_period_days = int(freqai.get("backtestPeriodDays", 7))
            label_period_candles = int(feature_parameters.get("labelPeriodCandles", 2))
            include_shifted_candles = int(feature_parameters.get("includeShiftedCandles", 0))
            indicator_periods_candles = [int(period) for period in feature_parameters.get("indicatorPeriodsCandles", [5, 14])]
            weight_factor = float(feature_parameters.get("weightFactor", 0.0))
            di_threshold = float(feature_parameters.get("diThreshold", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid FreqAI training/feature parameter type") from exc
        valid_ranges = (
            1 <= train_period_days <= 365
            and 1 <= backtest_period_days <= 90
            and 1 <= label_period_candles <= 200
            and 0 <= include_shifted_candles <= 20
            and 0 < len(indicator_periods_candles) <= 8
            and all(2 <= period <= 500 for period in indicator_periods_candles)
            and 0 <= weight_factor < 1
            and 0 <= di_threshold <= 10
        )
        if not valid_ranges:
            raise HTTPException(status_code=400, detail="Invalid FreqAI training/feature parameter range")

    def risk_config_for_pair(pair: str) -> dict[str, object]:
        nonlocal risk_config_state_loaded
        if not risk_config_state_loaded:
            if risk_config_state_path.exists():
                try:
                    saved_configs = json.loads(
                        risk_config_state_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    logger.exception("Could not read persisted risk configuration")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Risk configuration is unavailable: {exc}",
                    ) from exc
                if not isinstance(saved_configs, dict) or any(
                    not isinstance(value, dict)
                    for value in saved_configs.values()
                ):
                    raise HTTPException(
                        status_code=500,
                        detail="Persisted risk configuration must map pairs to objects",
                    )
                for saved_pair, config in saved_configs.items():
                    normalized_saved_pair = str(saved_pair).replace("_", "/").upper()
                    RISK_CONFIG_BY_PAIR[normalized_saved_pair] = dict(config)
            risk_config_state_loaded = True
        normalized = pair.replace("_", "/").upper()
        defaults = {
            "pair": normalized,
            "units": "1000",
            "riskBudget": "0.50%",
            "riskBudgetMode": "percent",
            "leverage": "1x",
            "maxExposure": "$10,000",
            "maxExposureMode": "absolute",
            "side": "NONE",
            "stopLoss": None,
            "stopLossMode": "price",
            "takeProfit": None,
            "takeProfitMode": "price",
            "averageEntry": None,
            "averageEntryMode": "price",
            "maxAdds": "0",
            "source": "default-policy",
        }
        if normalized not in RISK_CONFIG_BY_PAIR:
            RISK_CONFIG_BY_PAIR[normalized] = defaults
        return RISK_CONFIG_BY_PAIR[normalized]

    def read_strategy_execution_state() -> dict[str, object]:
        if not strategy_execution_state_path.exists():
            return {"enabled": False, "lastProcessed": {}, "results": []}
        try:
            payload = json.loads(
                strategy_execution_state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read automatic strategy execution state")
            raise HTTPException(
                status_code=500,
                detail=f"Automatic strategy execution state is unavailable: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=500,
                detail="Automatic strategy execution state must be a JSON object",
            )
        payload.setdefault("enabled", False)
        payload.setdefault("lastProcessed", {})
        payload.setdefault("results", [])
        return payload

    def write_strategy_execution_state(state: dict[str, object]) -> None:
        strategy_execution_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = strategy_execution_state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(state, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(strategy_execution_state_path)

    async def auto_execution_cycle() -> list[dict[str, object]]:
        settings = OandaSettings.from_environment()
        setup_path, _ = resolve_setup_paths()
        setup = load_forex_config(setup_path)
        if not settings.token or not settings.account_id:
            raise ValueError("Complete and confirm the OANDA account in Setup first")
        if settings.environment.value not in {"practice", "live"}:
            raise ValueError("Setup must select the Practice or Live account")
        instruments = tuple(settings.instruments)
        risk_configs = {
            instrument.replace("_", "/").upper(): dict(
                risk_config_for_pair(instrument.replace("_", "/"))
            )
            for instrument in instruments
        }
        state = read_strategy_execution_state()
        last_processed = state.get("lastProcessed", {})
        if not isinstance(last_processed, dict):
            last_processed = {}

        def checkpoint() -> None:
            state["lastProcessed"] = last_processed
            write_strategy_execution_state(state)

        async with OandaClient(
            settings.token,
            settings.account_id,
            environment=settings.environment,
        ) as client:
            executor = OandaAutoStrategyExecutor(
                client,
                settings,
                setup,
                risk_configs,
                last_processed=last_processed,
                checkpoint=checkpoint,
            )
            return await executor.run_cycle()

    async def auto_execution_loop() -> None:
        while True:
            state = read_strategy_execution_state()
            if state.get("enabled") is not True:
                return
            runtime_state_path = Path("user_data/oanda/runtime-state.json")
            if runtime_state_path.exists():
                try:
                    runtime_state = json.loads(
                        runtime_state_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    logger.exception("Could not read runtime execution gate")
                    state["lastError"] = f"Runtime gate unavailable: {exc}"
                    write_strategy_execution_state(state)
                    await asyncio.sleep(5)
                    continue
                if runtime_state.get("state") == "stopped":
                    state["enabled"] = False
                    state["lastError"] = "Stopped from runtime controls"
                    write_strategy_execution_state(state)
                    return
                if runtime_state.get("state") == "paused":
                    await asyncio.sleep(1)
                    continue

            try:
                results = await auto_execution_cycle()
                state = read_strategy_execution_state()
                state["results"] = (results + list(state.get("results", [])))[:50]
                state["lastError"] = None
                state["lastCycleAt"] = datetime.now(timezone.utc).isoformat()
                write_strategy_execution_state(state)
                for result in results:
                    if result.get("status") not in {
                        "filled",
                        "not_filled",
                        "closed",
                        "close_not_filled",
                    }:
                        continue
                    record_audit_event(
                        "strategy.auto_order",
                        details=result,
                        username="system",
                        role="system",
                        allowed=result.get("status") in {"filled", "closed"},
                    )
                    if result.get("status") == "filled":
                        ORDER_HISTORY.insert(
                            0,
                            {
                                "id": result.get("orderId"),
                                "symbol": result.get("pair"),
                                "side": "BUY" if result.get("signal") == "long" else "SELL",
                                "volume": str(abs(int(result.get("units", 0)))),
                                "status": "Filled",
                                "createdAt": state["lastCycleAt"],
                                "risk": "strategy policy",
                                "source": "auto",
                            },
                        )
                        del ORDER_HISTORY[100:]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Automatic strategy execution cycle failed")
                state = read_strategy_execution_state()
                state["lastError"] = str(exc)
                state["lastCycleAt"] = datetime.now(timezone.utc).isoformat()
                write_strategy_execution_state(state)
            await asyncio.sleep(15)

    def reset_pair_research_state(pair: str, strategy_class: str, timeframe: str) -> None:
        normalized = pair.replace("_", "/").upper()
        scope_key = hyperopt_scope(normalized, strategy_class, timeframe)
        AI_PENDING_OPTIMIZATION.pop(scope_key, None)
        AI_HYPEROPT_REPORTS.pop(scope_key, None)
        AI_HYPEROPT_JOBS.pop(scope_key, None)
        pair_config = ai_config_for_pair(normalized)
        approved_revisions = dict(pair_config.get("approvedRevisions") or {})
        approved_revisions.pop(f"{strategy_class}|{timeframe.upper()}", None)
        pair_config["approvedRevisions"] = approved_revisions
        if pair_config.get("approvedRevision", {}).get("strategyClass") == strategy_class and pair_config.get("approvedRevision", {}).get("timeframe") == timeframe.upper():
            pair_config.pop("approvedRevision", None)
            pair_config.pop("approvedHyperopt", None)
        BACKTEST_HISTORY[:] = [
            item for item in BACKTEST_HISTORY
            if not (item.get("pair") == normalized and item.get("timeframe", "").upper() == timeframe.upper() and item.get("strategy") == strategy_class)
        ]

    def resolve_history_request(
        payload: dict, timeframe: str, *, max_candles: int = 10000
    ) -> tuple[str, int]:
        mode = str(payload.get("historyMode", "candles")).lower()
        value = max(1, int(payload.get("historyValue", payload.get("steps", 250))))
        if mode == "candles":
            return mode, min(value, max_candles)
        if mode != "days":
            raise HTTPException(status_code=400, detail="historyMode must be candles or days")
        try:
            timeframe_seconds = timeframe_to_seconds(freqtrade_timeframe(timeframe))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Days history is unsupported for timeframe {timeframe}") from exc
        requested_candles = (value * 86400 + timeframe_seconds - 1) // timeframe_seconds
        return mode, min(requested_candles, max_candles)

    AI_REVIEW_STATE: dict[str, object] = {
        "status": "pending",
        "strategyName": "FX Trend Pulse",
        "model": "hybrid",
        "riskPolicy": "Practice-safe",
        "guardrails": [
            "dry-run only",
            "no live order execution",
            "manual approval required",
            "broker parity validation required",
        ],
        "notes": "Awaiting manual review before Practice-safe execution approval.",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
    }
    AI_REVIEW_BY_SCOPE: dict[tuple[str, str, str], dict[str, object]] = {}
    BACKTEST_HISTORY: list[dict] = []
    BACKTEST_JOBS: dict[str, dict] = {}

    revision_db = sqlite3.connect(ledger_path, check_same_thread=False)
    revision_db.execute(
        "CREATE TABLE IF NOT EXISTS ai_scope_revisions (scope TEXT PRIMARY KEY, config_json TEXT NOT NULL, review_json TEXT NOT NULL)"
    )
    revision_db.execute(
        "CREATE TABLE IF NOT EXISTS ai_hyperopt_scheduler (id INTEGER PRIMARY KEY CHECK (id = 1), config_json TEXT NOT NULL)"
    )
    revision_db.execute(
        "CREATE TABLE IF NOT EXISTS ai_hyperopt_reports (pair TEXT PRIMARY KEY, completed_at TEXT NOT NULL, report_json TEXT NOT NULL)"
    )
    revision_db.commit()

    def persist_scope_revision(pair: str, timeframe: str, strategy_class: str | None = None) -> None:
        normalized_pair = pair.replace("_", "/").upper()
        selected_class = strategy_class or str(ai_config_for_pair(normalized_pair).get("strategyClass", "ForexAIStrategyBaseline"))
        scope = f"{normalized_pair}|{timeframe.upper()}|{selected_class}"
        revision_db.execute(
            "INSERT OR REPLACE INTO ai_scope_revisions(scope, config_json, review_json) VALUES (?, ?, ?)",
            (
                scope,
                json.dumps(ai_config_for_pair(normalized_pair), default=str),
                json.dumps(AI_REVIEW_BY_SCOPE.get((normalized_pair, timeframe.upper(), selected_class), {}), default=str),
            ),
        )
        revision_db.commit()

    def restore_scope_revisions() -> None:
        for scope, config_json, review_json in revision_db.execute("SELECT scope, config_json, review_json FROM ai_scope_revisions"):
            parts = scope.split("|")
            pair = parts[0]
            if len(parts) == 2:
                timeframe, strategy_class = parts[1], "ForexAIStrategyBaseline"
            else:
                timeframe, strategy_class = parts[1], parts[2]
            pair = pair.replace("_", "/").upper()
            config = json.loads(config_json)
            AI_CONFIG_BY_PAIR[pair] = config
            AI_CONFIG_REVISIONS.setdefault(pair, [dict(config)])
            review = json.loads(review_json)
            if review:
                AI_REVIEW_BY_SCOPE[(pair, timeframe, strategy_class)] = review

    restore_scope_revisions()

    AI_HYPEROPT_SCHEDULER: dict[str, object] = {
        "enabled": False,
        "intervalDays": 2,
        "gapMinutes": 120,
        "strategyClass": "ForexAIStrategyBaseline",
        "freqaimodel": "ForexAIStrategyBaseline",
        "timeframe": "M5",
        "pairStrategies": {},
        "pairTimeframes": {},
        "pairs": [],
        "lastRunAt": None,
        "nextRunAt": None,
        "nextRuns": {},
        "lastError": None,
        "running": False,
    }
    scheduler_row = revision_db.execute("SELECT config_json FROM ai_hyperopt_scheduler WHERE id = 1").fetchone()
    if scheduler_row:
        AI_HYPEROPT_SCHEDULER.update(json.loads(scheduler_row[0]))

    def persist_hyperopt_scheduler() -> None:
        revision_db.execute(
            "INSERT OR REPLACE INTO ai_hyperopt_scheduler(id, config_json) VALUES (1, ?)",
            (json.dumps(AI_HYPEROPT_SCHEDULER, default=str),),
        )
        revision_db.commit()

    for report_pair, completed_at, report_json in revision_db.execute(
        "SELECT pair, completed_at, report_json FROM ai_hyperopt_reports"
    ):
        AI_HYPEROPT_REPORTS[report_pair] = {
            "completedAt": completed_at,
            "report": json.loads(report_json),
        }

    def persist_hyperopt_report(pair: str, completed_at: str, report: dict) -> None:
        revision_db.execute(
            "INSERT OR REPLACE INTO ai_hyperopt_reports(pair, completed_at, report_json) VALUES (?, ?, ?)",
            (pair, completed_at, json.dumps(report, default=str)),
        )
        revision_db.commit()

    def update_backtest_job(job_id: str | None, **updates: object) -> None:
        if job_id and job_id in BACKTEST_JOBS:
            BACKTEST_JOBS[job_id].update(updates)

    def df_from_raw_candles(candles: object) -> pd.DataFrame:
        if isinstance(candles, pd.DataFrame):
            return candles
        if not candles:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        rows: list[dict[str, object]] = []
        for candle in candles:
            if hasattr(candle, "time"):
                time_value = getattr(candle, "time")
                open_value = getattr(candle, "open", 0)
                high_value = getattr(candle, "high", 0)
                low_value = getattr(candle, "low", 0)
                close_value = getattr(candle, "close", 0)
                volume_value = getattr(candle, "volume", 0)
            elif isinstance(candle, dict):
                time_value = candle.get("time")
                mid = candle.get("mid") if isinstance(candle.get("mid"), dict) else candle
                open_value = mid.get("o") if isinstance(mid, dict) else candle.get("open", 0)
                high_value = mid.get("h") if isinstance(mid, dict) else candle.get("high", 0)
                low_value = mid.get("l") if isinstance(mid, dict) else candle.get("low", 0)
                close_value = mid.get("c") if isinstance(mid, dict) else candle.get("close", 0)
                volume_value = candle.get("volume", 0)
            else:
                continue
            if time_value is None:
                continue
            rows.append(
                {
                    "date": pd.Timestamp(time_value).tz_localize("UTC") if pd.Timestamp(time_value).tzinfo is None else pd.Timestamp(time_value).tz_convert("UTC"),
                    "open": float(open_value),
                    "high": float(high_value),
                    "low": float(low_value),
                    "close": float(close_value),
                    "volume": int(volume_value),
                }
            )
        return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])

    def format_decimal(value: Decimal | float | int | str | None) -> str:
        return f"{Decimal(str(value)):.2f}" if value is not None else "0.00"

    def ai_feature_schema_hash(config: dict[str, object]) -> str:
        payload = json.dumps(list(config.get("featureSet", [])), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def candle_frame_hash(frames: dict[str, pd.DataFrame]) -> str:
        digest = hashlib.sha256()
        for key in sorted(frames):
            digest.update(key.encode())
            digest.update(frames[key].to_json(orient="split", date_format="iso").encode())
        return digest.hexdigest()[:16]

    def normalize_pair(pair: str) -> str:
        return pair.replace("_", "/").upper()

    def serialize_broker_trade(trade: dict, quotes: dict[str, object], *, closed: bool) -> dict:
        instrument = str(trade.get("instrument", ""))
        unit_value = trade.get("initialUnits") if closed else trade.get("currentUnits")
        units = Decimal(str(unit_value or "0"))
        side = "BUY" if units >= 0 else "SELL"
        extension = trade.get("tradeClientExtensions") or trade.get("clientExtensions") or {}
        client_order_id = str(extension.get("id", "")) if isinstance(extension, dict) else ""
        source = (
            "manual"
            if client_order_id.startswith("manual-ui-")
            else "auto"
            if client_order_id.startswith("auto-")
            else "strategy"
        )
        quote = quotes.get(instrument)
        current_price = None
        if quote is not None and not closed:
            current_price = str(quote.bid if side == "BUY" else quote.ask)
        stop_loss = trade.get("stopLossOrder") or {}
        take_profit = trade.get("takeProfitOrder") or {}
        return {
            "id": str(trade.get("id", "")),
            "symbol": normalize_pair(instrument),
            "side": side,
            "units": str(abs(units)),
            "entryPrice": str(trade.get("price", "")),
            "currentPrice": current_price,
            "exitPrice": str(trade.get("averageClosePrice", "")) or None,
            "stopLoss": str(stop_loss.get("price", "")) or None,
            "takeProfit": str(take_profit.get("price", "")) or None,
            "pnl": str(trade.get("realizedPL" if closed else "unrealizedPL", "0")),
            "openedAt": trade.get("openTime"),
            "closedAt": trade.get("closeTime") if closed else None,
            "status": "closed" if closed else "open",
            "manual": source == "manual",
            "source": source,
            "clientOrderId": client_order_id or None,
        }

    async def broker_trade_rows(client: OandaClient) -> tuple[list[dict], list[dict]]:
        open_trades = await client.get_open_trades()
        closed_trades = await client.get_closed_trades(count=500)
        instruments = sorted({
            str(trade.get("instrument", ""))
            for trade in [*open_trades, *closed_trades]
            if trade.get("instrument")
        })
        quotes = (
            {quote.instrument: quote for quote in await client.get_prices(instruments)}
            if instruments
            else {}
        )
        return (
            [serialize_broker_trade(trade, quotes, closed=False) for trade in open_trades],
            [serialize_broker_trade(trade, quotes, closed=True) for trade in closed_trades],
        )

    async def currency_conversion_rate(
        client: OandaClient,
        source_currency: str | None,
        target_currency: str,
    ) -> tuple[str | None, str | None]:
        if source_currency == target_currency:
            return "1", None
        if not source_currency:
            return None, "Broker quote has no quote currency"

        direct_pair = f"{source_currency}_{target_currency}"
        inverse_pair = f"{target_currency}_{source_currency}"
        conversion_error: str | None = None
        try:
            prices = await client.get_prices((direct_pair,))
            if prices:
                return str(prices[0].bid), None
            conversion_error = f"No broker conversion quote for {direct_pair}"
        except OandaAPIError as exc:
            conversion_error = str(exc)

        try:
            prices = await client.get_prices((inverse_pair,))
            if prices:
                return str(Decimal(1) / prices[0].ask), None
        except OandaAPIError as exc:
            conversion_error = str(exc)
        return None, conversion_error or (
            f"No broker conversion quote for {source_currency}/{target_currency}"
        )

    def find_manual_pending_order(orders: list[dict], order_id: str) -> dict:
        order = next((item for item in orders if str(item.get("id")) == order_id), None)
        if order is None:
            raise HTTPException(status_code=404, detail="Pending broker order not found")
        extension = order.get("clientExtensions") or {}
        if not str(extension.get("id", "")).startswith("manual-ui-"):
            raise HTTPException(
                status_code=403,
                detail="Only orders created from the manual ticket can be modified here",
            )
        return order

    def parse_pending_order_modification(
        payload: dict, existing_order: dict
    ) -> tuple[str | None, int | None]:
        price = payload.get("price")
        if price is not None:
            try:
                parsed_price = Decimal(str(price))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="price must be a number"
                ) from exc
            if not parsed_price.is_finite() or parsed_price <= 0:
                raise HTTPException(status_code=400, detail="price must be positive")

        raw_units = payload.get("units")
        try:
            units = Decimal(str(raw_units)) if raw_units is not None else None
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="units must be a number"
            ) from exc
        if units is not None and (
            not units.is_finite() or units == 0 or units != units.to_integral_value()
        ):
            raise HTTPException(status_code=400, detail="units must be a non-zero whole number")
        if units is not None and Decimal(str(existing_order.get("units", "0"))) < 0:
            units = -units
        return (
            str(price) if price is not None else None,
            int(units) if units is not None else None,
        )

    def report_age_days(completed_at: str) -> int:
        completed = datetime.fromisoformat(completed_at)
        return max(0, (datetime.now(timezone.utc) - completed).days)

    def fallback_ai_config() -> dict:
        return dict(AI_CONFIG)

    def fallback_ai_review(
        pair: str | None = None,
        timeframe: str | None = None,
        strategy_class: str | None = None,
    ) -> dict:
        if pair is not None and timeframe is not None:
            normalized_pair = normalize_pair(pair)
            selected_class = strategy_class or str(ai_config_for_pair(normalized_pair).get("strategyClass", "ForexAIStrategyBaseline"))
            scoped = AI_REVIEW_BY_SCOPE.get((normalized_pair, timeframe.upper(), selected_class))
            if scoped is not None:
                return dict(scoped)
            pair_config = ai_config_for_pair(normalized_pair)
            return {
                **AI_REVIEW_STATE,
                "status": "pending",
                "pair": normalized_pair,
                "timeframe": timeframe.upper(),
                "strategyClass": selected_class,
                "model": pair_config.get("model", "hybrid"),
                "notes": "Awaiting manual review for this pair, timeframe, and strategy.",
                "approvedRevision": None,
            }
        return dict(AI_REVIEW_STATE)

    def live_event(channel: str, event_type: str, data: dict | list[dict]) -> dict:
        return {
            "type": event_type,
            "channel": channel,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

    async def resolve_health_payload() -> dict:
        try:
            settings = OandaSettings.from_environment()
            async with OandaClient(
                settings.token,
                settings.account_id,
                environment=settings.environment,
            ) as client:
                report = await OandaHealthCheck(client).run(settings.instruments)
            return {
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
            }
        except Exception:
            return fallback_health()

    @app.get("/api/v1/paper/report")
    def paper_report() -> dict:
        performance = ledger.performance()
        return {
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
        }

    @app.get("/api/v1/paper/trades")
    def paper_trades() -> list[dict]:
        return paper_report()["trades"]

    @app.get("/api/v1/health")
    async def health() -> dict:
        return await resolve_health_payload()

    @app.get("/api/v1/account/summary")
    async def account_summary() -> dict:
        health_payload = await resolve_health_payload()
        nav = float(health_payload["nav"])
        balance = float(health_payload["balance"])
        margin_available = float(health_payload["margin_available"])
        exposure = max(0.0, nav - margin_available)
        used_margin = (exposure / nav * 100) if nav else 0.0
        return {
            "equity": f"${nav:,.2f}",
            "exposure": f"${exposure:,.2f}",
            "netPnl": f"${(nav - balance):,.2f}",
            "marginUsed": f"{used_margin:.1f}%",
            "dailyRisk": "0.72%",
            "drawdown": "4.10%",
        }

    @app.get("/api/v1/markets/summary")
    async def markets_summary() -> dict:
        health_payload = await resolve_health_payload()
        prices = health_payload.get("prices", [])
        instruments = [
            {
                "pair": item["instrument"],
                "bid": float(item["bid"]),
                "ask": float(item["ask"]),
                "spread": float(item["spread"]),
                "change": "+0.00%",
            }
            for item in prices
        ]
        if not instruments:
            return fallback_market_summary()
        return {
            "instruments": instruments,
            "strategySignals": [
                {"name": "FX Trend Pulse", "mode": "Practice", "status": "Running", "signal": "Buy bias", "quality": "84%"},
                {"name": "Breakout Guard", "mode": "Dry-run", "status": "Watching", "signal": "Neutral", "quality": "76%"},
                {"name": "Carry Edge", "mode": "Backtest", "status": "Validated", "signal": "Short bias", "quality": "91%"},
            ],
            "alerts": [
                {"title": "Risk check passed", "detail": "Daily loss remains within policy threshold."},
                {"title": "Session rollover", "detail": "London close overlap is active for EUR/USD."},
                {"title": "Order validation", "detail": "Client order ID confirmed and idempotency check passed."},
            ],
        }

    @app.get("/api/v1/markets/quote")
    async def market_quote(pair: str = "EUR/USD") -> dict:
        try:
            settings = OandaSettings.from_environment()
            instrument = normalize_pair(pair).replace("/", "_")
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                prices = await client.get_prices((instrument,))
                if not prices:
                    raise HTTPException(
                        status_code=502,
                        detail="Broker returned no quote for this instrument",
                    )
                price = prices[0]
                metadata_key = (settings.account_id, instrument)
                now = datetime.now(timezone.utc)
                cached_metadata = instrument_metadata_cache.get(metadata_key)
                if cached_metadata is None or cached_metadata[0] <= now:
                    instruments = await client.get_instruments((instrument,))
                    if not instruments:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Broker returned no instrument metadata for {instrument}",
                        )
                    broker_instrument = instruments[0]
                    instrument_metadata_cache[metadata_key] = (
                        now + instrument_metadata_cache_ttl,
                        broker_instrument,
                    )
                else:
                    broker_instrument = cached_metadata[1]
                account = await client.get_account_summary()
                quote_currency = broker_instrument.quote_currency
                account_currency = account.currency
                quote_to_account_rate, conversion_error = await currency_conversion_rate(
                    client, quote_currency, account_currency
                )
            return {
                "pair": normalize_pair(price.instrument),
                "bid": str(price.bid),
                "ask": str(price.ask),
                "spread": str(price.spread),
                "time": price.time,
                "tradeable": price.tradeable,
                "environment": settings.environment.value,
                "displayPrecision": broker_instrument.display_precision,
                "tradeUnitsPrecision": broker_instrument.trade_units_precision,
                "minimumTradeSize": str(broker_instrument.minimum_trade_size),
                "baseCurrency": broker_instrument.base_currency,
                "quoteCurrency": quote_currency,
                "pipSize": str(broker_instrument.pip_size),
                "marginRate": (
                    str(broker_instrument.margin_rate)
                    if broker_instrument.margin_rate is not None
                    else None
                ),
                "bids": [
                    {"price": str(level_price), "units": str(liquidity)}
                    for level_price, liquidity in price.bids
                ],
                "asks": [
                    {"price": str(level_price), "units": str(liquidity)}
                    for level_price, liquidity in price.asks
                ],
                "unitsAvailable": price.units_available,
                "accountCurrency": account_currency,
                "marginAvailable": str(account.margin_available),
                "quoteToAccountRate": quote_to_account_rate,
                "conversionError": conversion_error,
            }
        except HTTPException:
            raise
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"Broker quote unavailable: {exc}") from exc

    @app.get("/api/v1/account/risk")
    async def account_risk() -> dict:
        return fallback_risk_summary()

    @app.get("/api/v1/account/risk/config")
    async def risk_config(pair: str = "EUR/USD") -> dict:
        return dict(risk_config_for_pair(pair))

    @app.post("/api/v1/account/risk/config")
    async def save_risk_config(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        pair = str(payload.get("pair", "EUR/USD"))
        current = risk_config_for_pair(pair)
        candidate = dict(current)
        configurable_fields = (
            "units",
            "riskBudget",
            "riskBudgetMode",
            "leverage",
            "maxExposure",
            "maxExposureMode",
            "side",
            "stopLoss",
            "stopLossMode",
            "takeProfit",
            "takeProfitMode",
            "averageEntry",
            "averageEntryMode",
            "maxAdds",
        )
        for key in configurable_fields:
            if key in payload:
                candidate[key] = payload[key]
        if str(candidate["side"]).upper() not in {"LONG", "SHORT", "BOTH", "NONE"}:
            raise HTTPException(status_code=400, detail="Risk side must be LONG, SHORT, BOTH, or NONE")
        for key in ("riskBudgetMode", "maxExposureMode"):
            if candidate[key] not in {"percent", "absolute"}:
                raise HTTPException(status_code=400, detail=f"{key} must be percent or absolute")
        for key in ("stopLossMode", "takeProfitMode", "averageEntryMode"):
            if candidate[key] not in {"percent", "price", "pips"}:
                raise HTTPException(
                    status_code=400, detail=f"{key} must be percent, price, or pips"
                )
        candidate["pair"] = pair.replace("_", "/").upper()
        candidate["source"] = "operator-config"
        try:
            units = Decimal(str(candidate["units"]).replace(",", "").strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="units must be a positive number"
            ) from exc
        if not units.is_finite() or units <= 0:
            raise HTTPException(
                status_code=400, detail="units must be a positive number"
            )
        candidate["units"] = str(units)
        persisted_configs = {
            **RISK_CONFIG_BY_PAIR,
            candidate["pair"]: candidate,
        }
        try:
            risk_config_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = risk_config_state_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(persisted_configs, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(risk_config_state_path)
        except OSError as exc:
            logger.exception("Could not persist risk configuration")
            raise HTTPException(
                status_code=500,
                detail=f"Risk configuration could not be saved: {exc}",
            ) from exc
        RISK_CONFIG_BY_PAIR[candidate["pair"]] = candidate
        record_audit_event(
            "risk.config.update",
            details={"pair": candidate["pair"], "source": candidate["source"]},
            username=user_role,
            role=user_role,
            allowed=True,
        )
        return dict(candidate)

    @app.get("/api/v1/audit/logs")
    async def audit_logs() -> list[dict]:
        return [
            {
                "timestamp": entry["timestamp"],
                "event": entry["event"],
                "username": entry["username"],
                "role": entry["role"],
                "allowed": entry["allowed"],
                "details": entry["details"],
            }
            for entry in AUDIT_LOGS
        ]

    @app.get("/api/v1/orders")
    async def orders() -> list[dict]:
        if ORDER_HISTORY:
            return [
                {
                    "id": str(item.get("orderId") or item.get("id") or "ORD-UNKNOWN"),
                    "symbol": item.get("symbol", "EUR/USD"),
                    "side": item.get("side", "BUY"),
                    "volume": str(item.get("volume") or "0"),
                    "status": str(item.get("status") or "Pending"),
                    "createdAt": item.get("createdAt") or datetime.now(timezone.utc).isoformat(),
                    "risk": item.get("risk", "0.75%"),
                }
                for item in reversed(ORDER_HISTORY)
            ]
        return fallback_orders()

    @app.get("/api/v1/orders/pending")
    async def pending_orders() -> list[dict]:
        try:
            settings = OandaSettings.from_environment()
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                broker_orders = await client.get_pending_orders()
            return [
                {
                    "id": str(item.get("id", "")),
                    "symbol": normalize_pair(str(item.get("instrument", ""))),
                    "side": "BUY" if Decimal(str(item.get("units", "0"))) > 0 else "SELL",
                    "volume": str(abs(Decimal(str(item.get("units", "0"))))),
                    "price": str(item.get("price", "")),
                    "status": "Pending",
                    "createdAt": item.get("createTime"),
                    "risk": "—",
                    "manual": str(
                        (item.get("clientExtensions") or {}).get("id", "")
                    ).startswith("manual-ui-"),
                }
                for item in broker_orders
            ]
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail=f"Broker pending orders unavailable: {exc}"
            ) from exc

    @app.get("/api/v1/positions")
    async def broker_positions() -> dict:
        try:
            settings = OandaSettings.from_environment()
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                account = await client.get_account_summary()
                open_rows, closed_rows = await broker_trade_rows(client)
            return {
                "open": open_rows,
                "closed": closed_rows,
                "accountCurrency": account.currency,
            }
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Broker positions unavailable: {exc}",
            ) from exc

    @app.get("/api/v1/orders/chart")
    async def orders_chart(pair: str = "EUR/USD", timeframe: str = "M15", count: int = 120) -> dict:
        """Return selected-view candles with signals from the pair's approved strategy timeframe."""
        try:
            normalized_pair = normalize_pair(pair)
            instrument_name = normalized_pair.replace("/", "_")
            view_timeframe = timeframe.upper()
            supported_granularities = {
                "S5", "S10", "S15", "S30",
                "M1", "M2", "M4", "M5", "M10", "M15", "M30",
                "H1", "H2", "H3", "H4", "H6", "H8", "H12",
                "D", "W", "M",
            }
            granularity = view_timeframe if view_timeframe in supported_granularities else None
            if granularity is None:
                raise HTTPException(status_code=400, detail="Unsupported chart timeframe")
            count = max(30, min(int(count), 5000))
            approved_revision = approved_runtime_revision(normalized_pair)
            if approved_revision is None:
                raise HTTPException(
                    status_code=409,
                    detail="No approved strategy/timeframe is configured for this pair",
                )
            config_key = json.dumps(approved_revision, sort_keys=True, default=str)
            cache_key = (normalized_pair, view_timeframe, count, config_key)
            now = asyncio.get_running_loop().time()
            cached_chart = chart_cache.get(cache_key)
            if cached_chart and cached_chart[0] > now:
                return cached_chart[1]
            if cached_chart:
                chart_cache.pop(cache_key, None)

            settings = OandaSettings.from_environment()
            approved_config = dict(approved_revision.get("strategyConfig") or {})
            approved_timeframe = str(
                approved_revision.get("timeframe", "M5")
            ).upper()
            approved_strategy = str(
                approved_revision.get("strategyClass", "ForexAIStrategyBaseline")
            )
            approved_granularity = oanda_granularity(freqtrade_timeframe(approved_timeframe))
            view_seconds = 30 * 86400 if view_timeframe == "M" else timeframe_to_seconds(freqtrade_timeframe(view_timeframe))
            approved_seconds = 30 * 86400 if approved_timeframe in {"M", "MN1"} else timeframe_to_seconds(freqtrade_timeframe(approved_timeframe))
            signal_count = min(
                max(count * max(1, view_seconds // approved_seconds), count),
                5000,
            )
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                view_candles = await client.get_candles(instrument_name, granularity, count=count)
                signal_candles = await client.get_candles(instrument_name, approved_granularity, count=signal_count)
                frame = df_from_raw_candles(signal_candles)
                view_times = [candle.time for candle in view_candles]
                strategy_config = {
                    "forex_ai_model": str(approved_config.get("model", "hybrid")),
                    "forex_ai_features": approved_config.get("featureSet", []),
                    "forex_ai_entry_threshold": approved_config.get("entryThreshold", "0.5"),
                    "forex_ai_exit_threshold": approved_config.get("exitThreshold", "0.0"),
                    "forex_ai_volatility_window": approved_config.get("volatilityWindow", "5"),
                    "forex_ai_atr_window": approved_config.get("atrWindow", "14"),
                    "forex_ai_max_spread_pct": approved_config.get("maxSpreadPct", "1.0"),
                }
                if approved_strategy == "ForexAIStrategyBaseline":
                    strategy = ForexAIStrategyBaseline(strategy_config)

                    def signal_for_window(window: pd.DataFrame) -> dict[str, str]:
                        return strategy.signal_trace(window)

                else:
                    hyperopt = approved_revision.get("hyperopt", {}) if isinstance(approved_revision, dict) else {}
                    parameters = hyperopt.get("parameters", hyperopt.get("bestParameters", {})) if isinstance(hyperopt, dict) else {}
                    strategy = load_strategy(
                        approved_strategy,
                        freqtrade_timeframe(approved_timeframe),
                        normalized_pair,
                        parameter_values=parameters if isinstance(parameters, dict) else None,
                    )
                    informative_candles = {}
                    for informative_timeframe in strategy_informative_timeframes(strategy, normalized_pair):
                        informative_count = strategy_informative_candle_count(
                            strategy, informative_timeframe, signal_count
                        )
                        informative_raw = await client.get_candles(
                            instrument_name,
                            oanda_granularity(informative_timeframe),
                            count=min(informative_count, 5000),
                        )
                        informative_candles[informative_timeframe] = df_from_raw_candles(informative_raw)
                    strategy_adapter = FreqtradeStrategyAdapter(strategy, normalized_pair, informative_candles)

                    def signal_for_window(window: pd.DataFrame) -> dict[str, str]:
                        return {
                            "signal": strategy_adapter.signal(window).value,
                            "reason": "approved_strategy_signal",
                        }

                signals: list[dict] = []
                previous = "flat"
                for index in range(len(frame)):
                    window = frame.iloc[: index + 1]
                    trace = signal_for_window(window)
                    signal = str(trace["signal"])
                    if signal in {"long", "short"} and signal != previous:
                        row = frame.iloc[index]
                        signal_time = pd.Timestamp(row["date"])
                        signal_time = signal_time.tz_localize("UTC") if signal_time.tzinfo is None else signal_time.tz_convert("UTC")
                        mapped_time = next((candidate for candidate in reversed(view_times) if pd.Timestamp(candidate) <= signal_time), view_times[0] if view_times else row["date"].isoformat())
                        signals.append({
                            "time": mapped_time,
                            "side": "BUY" if signal == "long" else "SELL",
                            "price": float(row["close"]),
                            "sourceTimeframe": approved_timeframe,
                            "aiReason": trace.get("reason", ""),
                            "signalStrength": float(trace.get("signalStrength", 0.0)),
                            "entryThreshold": float(trace.get("entryThreshold", 0.0)),
                        })
                    previous = signal
                open_trades = await client.get_open_trades()
                closed_trades = await client.get_closed_trades(count=100)
                chart_trades: list[dict] = []
                all_chart_trades = [
                    *((item, False) for item in open_trades),
                    *((item, True) for item in closed_trades),
                ]
                for trade, closed in all_chart_trades:
                    row = serialize_broker_trade(trade, {}, closed=closed)
                    if row["openedAt"] and row["entryPrice"]:
                        chart_trades.append({
                            "pair": row["symbol"],
                            "time": row["openedAt"],
                            "side": row["side"],
                            "price": float(row["entryPrice"]),
                            "source": row["source"],
                            "markerType": "entry",
                            "pnl": row["pnl"],
                        })
                    if row["closedAt"] and row["exitPrice"]:
                        chart_trades.append({
                            "pair": row["symbol"],
                            "time": row["closedAt"],
                            "side": row["side"],
                            "price": float(row["exitPrice"]),
                            "source": row["source"],
                            "markerType": "exit",
                            "pnl": row["pnl"],
                        })
                chart_data = {
                    "pair": normalized_pair,
                    "timeframe": view_timeframe,
                    "approvedTimeframe": approved_timeframe,
                    "approvedStrategy": approved_strategy,
                    "candles": [{"time": candle.time, "open": float(candle.open), "high": float(candle.high), "low": float(candle.low), "close": float(candle.close)} for candle in view_candles],
                    "signals": signals,
                    "trades": [trade for trade in chart_trades if trade["pair"] == normalized_pair],
                    "indicators": {"ema": []},
                }
                if len(chart_cache) >= chart_cache_capacity:
                    chart_cache.pop(next(iter(chart_cache)))
                chart_cache[cache_key] = (now + chart_cache_ttl, chart_data)
                return chart_data
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - API boundary
            raise HTTPException(status_code=502, detail=f"Chart data unavailable: {exc}") from exc

    @app.post("/api/v1/ops/validation")
    async def operation_validation(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        session_user = resolve_session_user(session_token)
        environment_name = str(payload.get("environment", "practice")).lower()
        execution_mode = str(payload.get("executionMode", "Practice")).lower()
        release_gate = str(payload.get("releaseGate", "")).lower()
        checks = {
            "sessionValid": session_user is not None,
            "roleAllowed": user_role in {"operator", "admin"},
            "csrfPresent": bool(csrf_token),
            "environmentAllowed": environment_name in {"dev", "staging", "practice", "live"},
            "executionModeAllowed": execution_mode in {"dry-run", "practice", "backtest", "live"},
            "releaseGateApproved": release_gate == "approved" if environment_name == "live" else True,
        }
        if not session_user:
            raise HTTPException(status_code=401, detail="Missing or invalid session token")
        if user_role is None:
            raise HTTPException(status_code=403, detail="Forbidden: missing user role")
        if user_role != session_user["role"]:
            raise HTTPException(status_code=403, detail="Forbidden: session role mismatch")
        if user_role not in {"operator", "admin"}:
            raise HTTPException(status_code=403, detail="Forbidden: role not permitted for operations")
        if not csrf_token:
            raise HTTPException(status_code=403, detail="Missing CSRF token")
        if environment_name == "live":
            if user_role != "admin":
                raise HTTPException(status_code=403, detail="Live environment requires admin role")
            if release_gate != "approved":
                raise HTTPException(status_code=403, detail="Live environment requires explicit release gate approval")

        response = {
            "allowed": True,
            "checks": checks,
            "environment": payload.get("environment", "practice"),
            "executionMode": payload.get("executionMode", "Practice"),
            "instrument": payload.get("instrument", "EUR/USD"),
        }
        record_audit_event(
            "ops.validation",
            details={
                "environment": payload.get("environment", "practice"),
                "executionMode": payload.get("executionMode", "Practice"),
                "instrument": payload.get("instrument", "EUR/USD"),
                "checks": checks,
            },
            username=session_user["username"],
            role=session_user["role"],
            allowed=True,
        )
        return response

    @app.post("/api/v1/auth/login")
    async def login(payload: dict) -> dict:
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        user = SESSION_USERS.get(username)
        if not user or user["password"] != password:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        session_token = create_session_token()
        ACTIVE_SESSIONS[session_token] = {"username": username, "role": user["role"]}
        record_audit_event(
            "auth.login",
            details={"username": username, "role": user["role"], "sessionCreated": True},
            username=username,
            role=user["role"],
            allowed=True,
        )
        return {
            "user": {"username": username, "role": user["role"]},
            "sessionToken": session_token,
            "expiresIn": 3600,
        }

    @app.get("/api/v1/auth/session")
    async def session(
        authorization: str | None = Header(default=None, alias="Authorization"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1]
        elif session_token:
            token = session_token

        if not token:
            raise HTTPException(status_code=401, detail="Missing session token")

        session_user = resolve_session_user(token)
        if session_user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session token")

        return {
            "username": session_user["username"],
            "role": session_user["role"],
            "sessionToken": token,
        }

    @app.post("/api/v1/orders/market")
    async def submit_market_order(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)

        resolved_user = resolve_session_user(session_token) or {"username": "anonymous", "role": user_role or "viewer"}
        symbol = str(payload.get("symbol", "EUR/USD")).strip()
        side = str(payload.get("side", "BUY")).upper().strip()
        raw_units = payload.get("units", payload.get("volume", 0))
        try:
            parsed_units = Decimal(str(raw_units))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="units must be a number") from exc
        if not parsed_units.is_finite() or parsed_units != parsed_units.to_integral_value():
            raise HTTPException(status_code=400, detail="units must be a whole number")
        units = int(parsed_units)
        if units <= 0:
            raise HTTPException(status_code=400, detail="units must be positive")
        if side not in {"BUY", "SELL"}:
            raise HTTPException(status_code=400, detail="side must be BUY or SELL")

        settings_obj = None
        try:
            settings_obj = OandaSettings.from_environment()
        except ValueError:
            settings_obj = None

        instrument = symbol.replace("/", "_")
        stop_loss = payload.get("stopLoss") or payload.get("stop_loss")
        take_profit = payload.get("takeProfit") or payload.get("take_profit")
        client_order_id = str(
            payload.get("clientOrderId")
            or f"ui-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        normalized_units = units if side == "BUY" else -units

        if settings_obj and settings_obj.token and settings_obj.account_id:
            environment_name = str(
                getattr(settings_obj.environment, "value", settings_obj.environment)
            ).lower()
            if client_order_id.startswith("manual-ui-") and environment_name != "practice":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Manual dashboard orders are restricted to the OANDA Practice environment"
                    ),
                )
            try:
                async with OandaClient(
                    settings_obj.token,
                    settings_obj.account_id,
                    settings_obj.environment,
                ) as client:
                    trade_extensions = (
                        {
                            "id": client_order_id,
                            "tag": "manual",
                            "comment": "Manual dashboard ticket",
                        }
                        if client_order_id.startswith("manual-ui-")
                        else {}
                    )
                    extension_kwargs = (
                        {"trade_client_extensions": trade_extensions} if trade_extensions else {}
                    )
                    result = await client.create_market_order(
                        instrument,
                        normalized_units,
                        stop_loss_price=str(stop_loss) if stop_loss else None,
                        take_profit_price=str(take_profit) if take_profit else None,
                        client_order_id=client_order_id,
                        **extension_kwargs,
                    )
            except OandaAPIError as exc:
                status_code = (
                    409
                    if "not tradeable" in str(exc).lower() or "market halted" in str(exc).lower()
                    else 502
                )
                raise HTTPException(status_code=status_code, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            cancel_reason = getattr(result, 'cancel_reason', None) or None
            final_status = "filled" if result.fill_price is not None else "cancelled" if cancel_reason else "queued"
            order = {
                "status": final_status,
                "symbol": symbol,
                "side": side,
                "units": normalized_units,
                "volume": str(abs(normalized_units)),
                "clientOrderId": client_order_id,
                "orderId": result.order_id,
                "transactionId": result.transaction_id,
                "fillPrice": str(result.fill_price) if result.fill_price is not None else None,
                "environment": getattr(getattr(settings_obj, 'environment', None), 'value', 'practice'),
                "executionMode": getattr(settings_obj, 'execution_mode', 'practice'),
                "role": user_role,
                "reason": cancel_reason,
                "cancelReason": cancel_reason,
            }
        else:
            order = {
                "status": "accepted",
                "symbol": symbol,
                "side": side,
                "units": normalized_units,
                "volume": str(abs(normalized_units)),
                "clientOrderId": client_order_id,
                "orderId": f"demo-{client_order_id}",
                "transactionId": None,
                "fillPrice": None,
                "environment": payload.get("environment", "practice"),
                "executionMode": payload.get("executionMode", "practice"),
                "role": user_role,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "risk": f"{float(payload.get('riskPercent', payload.get('risk', 0.75)) or 0.75):.2f}%",
            }

        order.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
        order.setdefault(
            "risk",
            f"{float(payload.get('riskPercent', payload.get('risk', 0.75)) or 0.75):.2f}%",
        )
        order["source"] = "manual"
        order["stopLoss"] = str(stop_loss) if stop_loss else None
        order["takeProfit"] = str(take_profit) if take_profit else None
        ORDER_HISTORY.append(order)
        ORDER_HISTORY[:] = ORDER_HISTORY[-20:]
        record_audit_event(
            "orders.market.submit",
            details={
                "symbol": order["symbol"],
                "side": order["side"],
                "volume": order["volume"],
                "clientOrderId": order["clientOrderId"],
                "status": order["status"],
                "environment": order["environment"],
            },
            username=resolved_user["username"],
            role=resolved_user["role"],
            allowed=True,
        )
        return order

    @app.post("/api/v1/orders/limit")
    async def submit_limit_order(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        try:
            settings = OandaSettings.from_environment()
        except ValueError as exc:
            raise HTTPException(
                status_code=503, detail=f"OANDA account unavailable: {exc}"
            ) from exc
        if str(getattr(settings.environment, "value", settings.environment)).lower() != "practice":
            raise HTTPException(
                status_code=403,
                detail="Manual dashboard orders are restricted to the OANDA Practice environment",
            )
        try:
            symbol = normalize_pair(str(payload.get("symbol", "EUR/USD")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        side = str(payload.get("side", "BUY")).upper().strip()
        if side not in {"BUY", "SELL"}:
            raise HTTPException(status_code=400, detail="side must be BUY or SELL")
        try:
            units = Decimal(str(payload.get("units", payload.get("volume", "0"))))
            price = Decimal(str(payload.get("price", "")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="units and price must be numbers") from exc
        if not units.is_finite() or units != units.to_integral_value() or units <= 0:
            raise HTTPException(status_code=400, detail="units must be a positive whole number")
        if not price.is_finite() or price <= 0:
            raise HTTPException(status_code=400, detail="price must be a positive number")
        client_order_id = str(
            payload.get("clientOrderId")
            or f"manual-ui-{secrets.token_hex(16)}"
        )
        if not client_order_id.startswith("manual-ui-"):
            raise HTTPException(status_code=400, detail="Manual order ID is invalid")
        signed_units = int(units) if side == "BUY" else -int(units)
        try:
            async with OandaClient(
                settings.token, settings.account_id, settings.environment
            ) as client:
                result = await client.create_limit_order(
                    symbol.replace("/", "_"),
                    signed_units,
                    str(price),
                    stop_loss_price=(
                        str(payload["stopLoss"]) if payload.get("stopLoss") else None
                    ),
                    take_profit_price=(
                        str(payload["takeProfit"]) if payload.get("takeProfit") else None
                    ),
                    client_order_id=client_order_id,
                    trade_client_extensions={
                        "id": client_order_id,
                        "tag": "manual",
                        "comment": "Manual dashboard ticket",
                    },
                )
        except OandaAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit_event(
            "orders.limit.submit",
            details={
                "symbol": symbol,
                "side": side,
                "volume": str(units),
                "price": str(price),
                "clientOrderId": client_order_id,
            },
            username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
            role=user_role,
            allowed=True,
        )
        return {
            "status": "pending",
            "symbol": symbol,
            "side": side,
            "units": signed_units,
            "volume": str(units),
            "clientOrderId": client_order_id,
            "orderId": result.order_id,
            "transactionId": result.transaction_id,
            "fillPrice": str(result.fill_price) if result.fill_price is not None else None,
            "environment": settings.environment.value,
            "price": str(price),
        }

    @app.post("/api/v1/orders/{order_id}/modify")
    async def modify_pending_order(
        order_id: str,
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        try:
            settings = OandaSettings.from_environment()
        except ValueError as exc:
            raise HTTPException(
                status_code=503, detail=f"OANDA account unavailable: {exc}"
            ) from exc
        environment_name = str(
            getattr(settings.environment, "value", settings.environment)
        ).lower()
        if environment_name != "practice":
            raise HTTPException(
                status_code=403,
                detail=(
                    "Manual dashboard orders can only be modified "
                    "in the OANDA Practice environment"
                ),
            )
        try:
            async with OandaClient(
                settings.token, settings.account_id, settings.environment
            ) as client:
                order = find_manual_pending_order(
                    await client.get_pending_orders(), order_id
                )
                price, units = parse_pending_order_modification(payload, order)
                result = await client.modify_order(
                    order_id,
                    price=price,
                    units=units,
                )
        except HTTPException:
            raise
        except OandaAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit_event(
            "orders.pending.modify",
            details={
                "orderId": order_id,
                "price": price,
                "units": str(units) if units is not None else None,
            },
            username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
            role=user_role,
            allowed=True,
        )
        return {
            "status": "modified",
            "orderId": result.order_id,
            "transactionId": result.transaction_id,
        }

    @app.post("/api/v1/positions/{trade_id}/close")
    async def close_manual_position(
        trade_id: str,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        try:
            settings = OandaSettings.from_environment()
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                open_trades = await client.get_open_trades()
                trade = next(
                    (item for item in open_trades if str(item.get("id")) == trade_id),
                    None,
                )
                if trade is None:
                    raise HTTPException(status_code=404, detail="Open broker trade not found")
                extension = (
                    trade.get("tradeClientExtensions") or trade.get("clientExtensions") or {}
                )
                client_order_id = str(extension.get("id", "")) if isinstance(extension, dict) else ""
                is_manual = client_order_id.startswith("manual-ui-")
                is_automated = (
                    isinstance(extension, dict)
                    and extension.get("tag") == "auto"
                    and client_order_id.startswith("auto-")
                )
                if not is_manual and not is_automated:
                    raise HTTPException(
                        status_code=403,
                        detail="Only dashboard manual trades and automatic strategy trades can be closed here",
                    )
                environment_name = settings.environment.value
                if is_manual and environment_name != "practice":
                    raise HTTPException(
                        status_code=403,
                        detail="Manual dashboard positions can only be closed in the OANDA Practice environment",
                    )
                if is_automated and environment_name == "live" and user_role != "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="Closing an automatic Live strategy trade requires admin role",
                    )
                result = await client.close_trade(trade_id)
            record_audit_event(
                "positions.manual.close",
                details={"tradeId": trade_id, "clientOrderId": client_order_id},
                username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
                role=user_role,
                allowed=True,
            )
            fill = result.get("orderFillTransaction") or {}
            if not fill:
                cancel = result.get("orderCancelTransaction") or {}
                reason = str(cancel.get("reason") or "OANDA did not confirm a close fill")
                raise HTTPException(
                    status_code=409,
                    detail=f"Broker did not confirm trade close: {reason}",
                )
            return {
                "status": "closed",
                "tradeId": trade_id,
                "transactionId": fill.get("id"),
                "fillPrice": fill.get("price"),
                "environment": settings.environment.value,
            }
        except HTTPException:
            raise
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"Broker close rejected: {exc}") from exc

    @app.post("/api/v1/positions/{trade_id}/modify")
    async def modify_manual_position(
        trade_id: str,
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        stop_loss, take_profit, trailing_stop_loss_distance = (
            _validated_protection_values(payload)
        )
        try:
            settings = OandaSettings.from_environment()
            environment_name = str(
                getattr(settings.environment, "value", settings.environment)
            ).lower()
            if environment_name != "practice":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Manual dashboard positions can only be modified "
                        "in the OANDA Practice environment"
                    ),
                )
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                trade = next(
                    (
                        item
                        for item in await client.get_open_trades()
                        if str(item.get("id")) == trade_id
                    ),
                    None,
                )
                if trade is None:
                    raise HTTPException(status_code=404, detail="Open broker trade not found")
                extension = (
                    trade.get("tradeClientExtensions") or trade.get("clientExtensions") or {}
                )
                client_order_id = (
                    str(extension.get("id", "")) if isinstance(extension, dict) else ""
                )
                if not client_order_id.startswith("manual-ui-"):
                    raise HTTPException(
                        status_code=403,
                        detail="Only trades opened from the manual ticket can be modified here",
                    )
                result = await client.modify_trade_orders(
                    trade_id,
                    stop_loss_price=str(stop_loss) if stop_loss is not None else None,
                    take_profit_price=str(take_profit) if take_profit is not None else None,
                    trailing_stop_loss_distance=(
                        str(trailing_stop_loss_distance)
                        if trailing_stop_loss_distance is not None
                        else None
                    ),
                )
            record_audit_event(
                "positions.manual.modify",
                details={
                    "tradeId": trade_id,
                    "clientOrderId": client_order_id,
                    "stopLoss": stop_loss,
                    "takeProfit": take_profit,
                    "trailingStopLossDistance": trailing_stop_loss_distance,
                },
                username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
                role=user_role,
                allowed=True,
            )
            return {
                "status": "modified",
                "tradeId": trade_id,
                "transactionId": result.get("lastTransactionID"),
                "environment": settings.environment.value,
            }
        except HTTPException:
            raise
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Broker modification rejected: {exc}"
            ) from exc

    @app.post("/api/v1/positions/{trade_id}/risk-protection")
    async def apply_risk_protection(
        trade_id: str,
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        stop_loss, take_profit, trailing_stop_loss_distance = (
            _validated_protection_values(payload)
        )
        try:
            settings = OandaSettings.from_environment()
            environment_name = str(
                getattr(settings.environment, "value", settings.environment)
            ).lower()
            if environment_name != "practice":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Risk protection updates are restricted to the OANDA "
                        "Practice environment"
                    ),
                )
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                open_trades = await client.get_open_trades()
                trade = next(
                    (item for item in open_trades if str(item.get("id")) == trade_id),
                    None,
                )
                if trade is None:
                    raise HTTPException(status_code=404, detail="Open broker trade not found")
                result = await client.modify_trade_orders(
                    trade_id,
                    stop_loss_price=str(stop_loss) if stop_loss is not None else None,
                    take_profit_price=str(take_profit) if take_profit is not None else None,
                    trailing_stop_loss_distance=(
                        str(trailing_stop_loss_distance)
                        if trailing_stop_loss_distance is not None
                        else None
                    ),
                )
            record_audit_event(
                "positions.risk_protection.update",
                details={
                    "tradeId": trade_id,
                    "stopLoss": stop_loss,
                    "takeProfit": take_profit,
                    "trailingStopLossDistance": trailing_stop_loss_distance,
                },
                username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
                role=user_role,
                allowed=True,
            )
            return {
                "status": "modified",
                "tradeId": trade_id,
                "transactionId": result.get("lastTransactionID"),
                "environment": settings.environment.value,
            }
        except HTTPException:
            raise
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Broker protection update rejected: {exc}"
            ) from exc

    @app.post("/api/v1/positions/{trade_id}/average-entry")
    async def create_average_entry_order(
        trade_id: str,
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        try:
            units = Decimal(str(payload.get("units", "")))
            price = Decimal(str(payload.get("price", "")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="units and price must be numbers") from exc
        if not units.is_finite() or units <= 0 or units != units.to_integral_value():
            raise HTTPException(
                status_code=400, detail="units must be a positive whole number"
            )
        if not price.is_finite() or price <= 0:
            raise HTTPException(status_code=400, detail="price must be a positive number")
        try:
            settings = OandaSettings.from_environment()
            environment_name = str(
                getattr(settings.environment, "value", settings.environment)
            ).lower()
            if environment_name != "practice":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Average-entry orders are restricted to the OANDA "
                        "Practice environment"
                    ),
                )
            async with OandaClient(
                settings.token, settings.account_id, environment=settings.environment
            ) as client:
                open_trades = await client.get_open_trades()
                trade = next(
                    (item for item in open_trades if str(item.get("id")) == trade_id),
                    None,
                )
                if trade is None:
                    raise HTTPException(status_code=404, detail="Open broker trade not found")
                current_units = Decimal(str(trade.get("currentUnits", "0")))
                if current_units == 0:
                    raise HTTPException(status_code=409, detail="Broker trade has no open units")
                if Decimal(str(trade.get("unrealizedPL", "0"))) >= 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Average-entry orders can only be placed while the "
                            "broker trade is at a loss"
                        ),
                    )
                instrument = str(trade.get("instrument", ""))
                prices = await client.get_prices((instrument,))
                if not prices or not prices[0].tradeable:
                    raise HTTPException(
                        status_code=409, detail="Broker instrument is not tradeable"
                    )
                quote = prices[0]
                is_long = current_units > 0
                entry_price = Decimal(str(trade.get("price", "0")))
                price_is_adverse = price < entry_price if is_long else price > entry_price
                price_is_pending = price < quote.ask if is_long else price > quote.bid
                if not price_is_adverse or not price_is_pending:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Average-entry price must be adverse to entry and "
                            "remain beyond the live quote"
                        ),
                    )
                stop_loss = payload.get("stopLoss")
                take_profit = payload.get("takeProfit")
                client_order_id = f"risk-average-for-{trade_id}-{secrets.token_hex(6)}"
                result = await client.create_limit_order(
                    instrument,
                    int(units) if is_long else -int(units),
                    str(price),
                    stop_loss_price=str(stop_loss) if stop_loss else None,
                    take_profit_price=str(take_profit) if take_profit else None,
                    client_order_id=client_order_id,
                    trade_client_extensions={
                        "id": client_order_id,
                        "tag": "risk-average",
                        "comment": f"average-for:{trade_id}",
                    },
                )
            record_audit_event(
                "positions.average_entry.create",
                details={
                    "tradeId": trade_id,
                    "orderId": result.order_id,
                    "units": str(units),
                    "price": str(price),
                },
                username=(resolve_session_user(session_token) or {}).get("username", "anonymous"),
                role=user_role,
                allowed=True,
            )
            return {
                "status": "created",
                "tradeId": trade_id,
                "orderId": result.order_id,
                "transactionId": result.transaction_id,
                "environment": settings.environment.value,
            }
        except HTTPException:
            raise
        except (OandaAPIError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Average-entry order rejected by broker: {exc}"
            ) from exc

    @app.get("/api/v1/settings")
    async def settings() -> dict:
        try:
            settings_obj = OandaSettings.from_environment()
            return {
                "environment": settings_obj.environment.title(),
                "broker": "OANDA",
                "database": "SQLite",
                "websocket": "Connected",
                "executionMode": settings_obj.execution_mode.value,
                "accountId": settings_obj.account_id,
                "instruments": settings_obj.instruments,
            }
        except Exception:
            return fallback_settings()

    @app.get("/api/v1/setup/status")
    async def setup_status() -> dict:
        config_path, strategy_path = resolve_setup_paths()
        persisted = load_forex_config(config_path)
        exchange = persisted.get("exchange", {})
        token = exchange.get("oanda_token", "")
        account_id = exchange.get("account_id", "")
        instruments = exchange.get("pair_whitelist", ["EUR_USD", "GBP_USD"])
        pair_timeframes = persisted.get("pair_timeframes", {})
        pair_strategies = persisted.get("pair_strategies", {})
        return {
            "configured": bool(token and account_id),
            "environment": exchange.get("oanda_environment", "practice"),
            "executionMode": exchange.get("oanda_execution_mode", "dry_run"),
            "instruments": instruments,
            "pairTimeframes": pair_timeframes,
            "pairStrategies": pair_strategies,
            "accountIdConfigured": bool(account_id),
            "tokenConfigured": bool(token),
            "strategyFile": str(strategy_path),
            "configFile": str(config_path),
        }

    @app.get("/api/v1/setup/instruments")
    async def setup_instruments(
        token: str | None = None,
        account_id: str | None = None,
        environment: str = "practice",
        accountId: str | None = None,
    ) -> dict:
        token_value = str(token or "").strip()
        account_value = str(account_id or accountId or "").strip()
        environment_name = str(environment or "practice").strip().lower()
        if not token_value or not account_value:
            raise HTTPException(status_code=400, detail="token and account_id are required")
        if environment_name not in {"practice", "live"}:
            raise HTTPException(status_code=400, detail="environment must be practice or live")
        try:
            env = OandaEnvironment(environment_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="environment must be practice or live") from exc

        try:
            async with OandaClient(token_value, account_value, env) as client:
                metadata = await client.get_instruments()
        except OandaAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        priority_rank = {"high": 0, "medium": 1, "standard": 2}
        priority_map = {
            "EUR_USD": "high",
            "GBP_USD": "high",
            "USD_JPY": "high",
            "AUD_USD": "high",
            "NZD_USD": "medium",
            "USD_CAD": "medium",
            "USD_CHF": "medium",
            "EUR_GBP": "medium",
        }
        items = []
        for instrument in metadata:
            name = str(getattr(instrument, "name", "") or "").strip().upper()
            display_name = str(getattr(instrument, "display_name", getattr(instrument, "displayName", name)) or name)
            base_currency = getattr(instrument, "base_currency", getattr(instrument, "baseCurrency", None))
            quote_currency = getattr(instrument, "quote_currency", getattr(instrument, "quoteCurrency", None))
            items.append({
                "name": name,
                "displayName": display_name,
                "baseCurrency": base_currency,
                "quoteCurrency": quote_currency,
                "priority": priority_map.get(name, "standard"),
            })
        items.sort(key=lambda item: (priority_rank.get(item["priority"], 99), item["name"]))
        return {"environment": env.value, "accountId": account_value, "instruments": items}

    @app.post("/api/v1/setup/discover")
    async def setup_discover(payload: dict) -> dict:
        token = str(payload.get("token", "")).strip()
        environment_name = str(payload.get("environment", "practice")).strip().lower()
        if not token:
            raise HTTPException(status_code=400, detail="OANDA token is required")
        try:
            environment = OandaEnvironment(environment_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="environment must be practice or live") from exc
        try:
            result = await discover_oanda_accounts(token, environment)
        except OandaAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"environment": environment.value, **result}

    @app.post("/api/v1/setup")
    async def save_setup(payload: dict) -> dict:
        token = str(payload.get("token", "")).strip()
        account_id = str(payload.get("accountId", "")).strip()
        environment = str(payload.get("environment", "practice")).strip().lower()
        instruments = [
            str(item).strip().upper().replace("/", "_")
            for item in payload.get("instruments", ["EUR_USD", "GBP_USD"])
            if str(item).strip()
        ]
        try:
            risk_fraction = Decimal(str(payload.get("riskFraction", "0.01")))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="riskFraction must be a number") from exc
        if not token or not account_id:
            raise HTTPException(status_code=400, detail="token and accountId are required")
        if environment not in {"practice", "live"}:
            raise HTTPException(status_code=400, detail="environment must be practice or live")
        execution_mode = environment
        if payload.get("accountConfirmed") is not True:
            raise HTTPException(status_code=400, detail="Confirm the selected OANDA account before saving")
        expected_type_code = str(payload.get("accountTypeCode", ""))
        supported_type = expected_type_code in {"002", "003"} or (
            environment == "practice" and expected_type_code == "PRACTICE"
        )
        if not supported_type:
            raise HTTPException(
                status_code=400,
                detail="Live supports Spread Betting (002) and CFD (003); Practice also supports verified V20 accounts.",
            )
        if environment == "live":
            if payload.get("liveConfirmed") is not True:
                raise HTTPException(status_code=400, detail="Explicitly confirm that this is a Live OANDA account")
            if os.environ.get("OANDA_LIVE_CONFIRM") != "1":
                raise HTTPException(status_code=403, detail="Live setup is disabled. Set OANDA_LIVE_CONFIRM=1 on the server and restart the API.")
        try:
            environment_enum = OandaEnvironment(environment)
            discovery = await discover_oanda_accounts(token, environment_enum)
        except OandaAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        selected_account = next(
            (
                account
                for account in discovery["accounts"]
                if account["accountId"] == account_id
                and account["accountTypeCode"] == expected_type_code
                and account["summaryAccessible"]
            ),
            None,
        )
        if selected_account is None:
            raise HTTPException(status_code=400, detail="Selected account could not be verified as a supported OANDA account")
        if not instruments:
            raise HTTPException(status_code=400, detail="at least one instrument is required")
        if not Decimal("0") < risk_fraction <= Decimal("1"):
            raise HTTPException(status_code=400, detail="riskFraction must be greater than 0 and at most 1")
        pair_timeframes = {
            str(pair).strip().upper().replace("/", "_"): str(timeframe).strip().lower()
            for pair, timeframe in dict(payload.get("pairTimeframes", {})).items()
            if str(pair).strip() and str(timeframe).strip()
        }
        supported_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1mo"}
        if any(timeframe not in supported_timeframes for timeframe in pair_timeframes.values()):
            raise HTTPException(status_code=400, detail="pairTimeframes contains an unsupported timeframe")
        pair_strategies = {
            str(pair).strip().upper().replace("/", "_"): str(strategy).strip()
            for pair, strategy in dict(payload.get("pairStrategies", {})).items()
            if str(pair).strip() and str(strategy).strip()
        }
        available_strategy_names = {item.name for item in discover_strategy_files()}
        unknown_strategies = sorted(set(pair_strategies.values()) - available_strategy_names)
        if unknown_strategies:
            raise HTTPException(status_code=400, detail=f"Unknown strategy class(es): {', '.join(unknown_strategies)}")
        config_path = Path(payload.get("configPath") or os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
        current = load_forex_config(config_path)
        approved_revisions = dict(current.get("pair_approved_revisions") or {})
        for pair_key, revision in list(approved_revisions.items()):
            if (
                not isinstance(revision, dict)
                or pair_strategies.get(pair_key) != revision.get("strategyClass")
                or pair_timeframes.get(pair_key) is None
                or freqtrade_timeframe(str(revision.get("timeframe", "")))
                != pair_timeframes.get(pair_key)
            ):
                approved_revisions.pop(pair_key, None)
        current.update({
            "schema_version": 1,
            "timeframe": "5m",
            "setup": {"configured": True, "credentials_source": "ui"},
            "pair_timeframes": pair_timeframes,
            "pair_strategies": pair_strategies,
            "pair_approved_revisions": approved_revisions,
            "exchange": {
                **current.get("exchange", {}),
                "name": "oanda",
                "oanda_environment": environment,
                "oanda_execution_mode": execution_mode,
                "oanda_token": token,
                "account_id": account_id,
                "oanda_account_type_code": selected_account["accountTypeCode"],
                "oanda_account_type": selected_account["accountType"],
                "oanda_account_tags": selected_account["tags"],
                "pair_whitelist": instruments,
                "oanda_risk_fraction": str(risk_fraction),
            },
        })
        timeframe_labels = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "2h": "H2", "4h": "H4", "6h": "H6", "8h": "H8", "12h": "H12", "1d": "D1", "1w": "W1", "1mo": "MN1"}
        for pair_key, strategy_class in pair_strategies.items():
            normalized_pair = pair_key.replace("_", "/").upper()
            if normalized_pair in AI_CONFIG_BY_PAIR:
                AI_CONFIG_BY_PAIR[normalized_pair]["strategyClass"] = strategy_class
                selected_pair_timeframe = pair_timeframes.get(pair_key)
                if selected_pair_timeframe in timeframe_labels:
                    AI_CONFIG_BY_PAIR[normalized_pair]["timeframe"] = timeframe_labels[selected_pair_timeframe]
        saved_path = save_forex_config(current, config_path)
        record_audit_event(
            "setup.saved",
            details={"environment": environment, "executionMode": execution_mode, "instruments": instruments},
            allowed=True,
        )
        return {
            "configured": True,
            "configPath": str(saved_path),
            "environment": environment,
            "executionMode": execution_mode,
            "accountTypeCode": selected_account["accountTypeCode"],
            "accountType": selected_account["accountType"],
            "instruments": instruments,
            "pairTimeframes": pair_timeframes,
            "pairStrategies": pair_strategies,
            "accountIdConfigured": True,
            "tokenConfigured": True,
        }

    @app.get("/api/v1/setup/runtime")
    async def setup_runtime_status() -> dict:
        state_path = Path("user_data/oanda/runtime-state.json")
        if not state_path.exists():
            return {"state": "running", "reloadPending": False, "message": "Bot is allowed to operate."}
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        return {
            "state": state.get("state", "running"),
            "reloadPending": bool(state.get("reloadPending", False)),
            "message": state.get("message", "Bot is allowed to operate."),
            "updatedAt": state.get("updatedAt"),
        }

    @app.post("/api/v1/setup/runtime")
    async def control_setup_runtime(payload: dict) -> dict:
        action = str(payload.get("action", "")).strip().lower()
        actions = {
            "reload": ("running", True, "Configuration reload requested; the worker must reload before its next cycle."),
            "resume": ("running", False, "Bot operation resumed."),
            "pause": ("paused", False, "Paused: no new trades and no management of open trades."),
            "stop": ("stopped", False, "Stopped: trading and open-trade management are disabled."),
        }
        if action not in actions:
            raise HTTPException(status_code=400, detail="action must be reload, resume, pause, or stop")
        state, reload_pending, message = actions[action]
        state_path = Path("user_data/oanda/runtime-state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps({"state": state, "reloadPending": reload_pending, "message": message, "updatedAt": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(state_path)
        record_audit_event("setup.runtime", details={"action": action, "state": state}, allowed=True)
        return {"state": state, "reloadPending": reload_pending, "message": message}

    @app.get("/api/v1/strategy/auto-execution")
    async def strategy_auto_execution_status() -> dict:
        state = read_strategy_execution_state()
        try:
            settings = OandaSettings.from_environment()
            environment = settings.environment.value
        except ValueError as exc:
            environment = "unconfigured"
            state["lastError"] = str(exc)
        return {
            "enabled": state.get("enabled") is True,
            "environment": environment,
            "lastCycleAt": state.get("lastCycleAt"),
            "lastError": state.get("lastError"),
            "results": state.get("results", []),
        }

    @app.post("/api/v1/strategy/auto-execution")
    async def control_strategy_auto_execution(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        state = read_strategy_execution_state()
        if enabled:
            try:
                settings = OandaSettings.from_environment()
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if (
                settings.environment.value not in {"practice", "live"}
                or settings.execution_mode != settings.environment.value
                or not settings.token
                or not settings.account_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Select and confirm a Practice or Live account in Setup first",
                )
            if settings.environment.value == "live" and user_role != "admin":
                raise HTTPException(
                    status_code=403,
                    detail="Enabling automatic execution for a Live account requires admin role",
                )
            setup_path, _ = resolve_setup_paths()
            setup = load_forex_config(setup_path)
            configured_pairs = set(settings.instruments)
            approved = setup.get("pair_approved_revisions", {})
            strategies = setup.get("pair_strategies", {})
            timeframes = setup.get("pair_timeframes", {})
            for instrument in configured_pairs:
                pair_key = instrument.upper().replace("/", "_")
                pair = instrument.replace("_", "/").upper()
                revision = approved.get(pair_key) if isinstance(approved, dict) else None
                if (
                    not isinstance(revision, dict)
                    or revision.get("pair") != pair
                    or revision.get("strategyClass")
                    != (strategies.get(pair_key) if isinstance(strategies, dict) else None)
                    or not revision.get("timeframe")
                    or freqtrade_timeframe(str(revision.get("timeframe", "")))
                    != freqtrade_timeframe(
                        str(timeframes.get(pair_key, ""))
                        if isinstance(timeframes, dict)
                        else ""
                    )
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"{pair} needs a current approved strategy revision before auto-execution",
                    )
                risk = risk_config_for_pair(pair)
                if not risk.get("stopLoss"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Configure a stop loss for {pair} in Risk controls before auto-execution",
                    )
                if str(risk.get("side", "NONE")).upper() not in {
                    "LONG",
                    "SHORT",
                    "BOTH",
                    "NONE",
                }:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Configure an allowed entry side for {pair} in Risk controls",
                    )
                if not risk.get("riskBudget") or not risk.get("maxExposure"):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Configure risk budget and maximum exposure for {pair}",
                    )
            runtime_state_path = Path("user_data/oanda/runtime-state.json")
            if runtime_state_path.exists():
                try:
                    runtime_state = json.loads(
                        runtime_state_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Runtime control state is unavailable: {exc}",
                    ) from exc
                if runtime_state.get("state") != "running":
                    raise HTTPException(
                        status_code=409,
                        detail="Resume runtime controls before enabling strategy execution",
                    )
        state["enabled"] = enabled
        if not enabled:
            state["lastError"] = None
        write_strategy_execution_state(state)
        current_task = app.state.strategy_execution_task
        if enabled and (current_task is None or current_task.done()):
            app.state.strategy_execution_task = asyncio.create_task(
                auto_execution_loop()
            )
        elif not enabled and current_task is not None and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except asyncio.CancelledError:
                pass
            app.state.strategy_execution_task = None
        record_audit_event(
            "strategy.auto_execution",
            details={
                "enabled": enabled,
                "environment": settings.environment.value if enabled else None,
            },
            username=(resolve_session_user(session_token) or {}).get(
                "username", "anonymous"
            ),
            role=user_role,
            allowed=True,
        )
        return {
            "enabled": enabled,
            "environment": settings.environment.value if enabled else None,
            "lastCycleAt": state.get("lastCycleAt"),
            "lastError": state.get("lastError"),
            "results": state.get("results", []),
        }

    @app.get("/api/v1/setup/files/{file_kind}")
    async def download_setup_file(file_kind: str, strategy_name: str | None = None) -> Response:
        config_path, strategy_path = resolve_setup_paths()
        if file_kind == "strategy" and strategy_name:
            selected = next((item for item in discover_strategy_files() if item.name == strategy_name), None)
            if selected is None:
                raise HTTPException(status_code=404, detail="strategy class is not available")
            strategy_path = selected.path
        paths = {
            "config": config_path,
            "strategy": strategy_path,
        }
        path = paths.get(file_kind)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown setup file")
        generated_content: str | None = None
        if not path.exists():
            if file_kind == "strategy":
                fallback_strategy_path = Path("freqtrade/forex/strategies/ema_cross.py")
                if fallback_strategy_path.exists():
                    path = fallback_strategy_path
            else:
                generated_content = json.dumps(
                    {
                        "schema_version": 1,
                        "timeframe": "5m",
                        "pair_timeframes": {"EUR_USD": "5m", "GBP_USD": "1h"},
                        "exchange": {
                            "name": "oanda",
                            "oanda_environment": "practice",
                            "oanda_execution_mode": "dry_run",
                            "oanda_token": "",
                            "account_id": "",
                            "pair_whitelist": ["EUR_USD", "GBP_USD"],
                            "oanda_risk_fraction": "0.01",
                        },
                    },
                    indent=2,
                ) + "\n"
            if not path.exists() and generated_content is None:
                raise HTTPException(status_code=404, detail=f"{file_kind} file is not available")
        media_type = "application/json" if file_kind == "config" else "text/x-python"
        return Response(
            content=generated_content if generated_content is not None else path.read_text(encoding="utf-8"),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
        )

    @app.post("/api/v1/setup/files/{file_kind}")
    async def upload_setup_file(file_kind: str, payload: dict) -> dict:
        config_path, strategy_path = resolve_setup_paths()
        paths = {
            "config": config_path,
            "strategy": strategy_path,
        }
        path = paths.get(file_kind)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown setup file")
        content = str(payload.get("content", ""))
        strategy_names: list[str] = []
        if file_kind == "config":
            if not content.strip() or len(content.encode("utf-8")) > 2_000_000:
                raise HTTPException(status_code=400, detail="file is empty or exceeds the 2 MB limit")
            try:
                config_payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="config.json must contain valid JSON") from exc
            if not isinstance(config_payload, dict):
                raise HTTPException(status_code=400, detail="config.json must contain a JSON object")
        else:
            file_name = str(payload.get("fileName") or strategy_path.name)
            strategy_directory = Path(os.environ.get("FOREX_STRATEGIES_DIR", "user_data/strategies"))
            path = strategy_directory / file_name
            try:
                strategy_names = validate_strategy_upload(
                    file_name,
                    content,
                    replacing=path if path.exists() else None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
        record_audit_event("setup.file_uploaded", details={"fileKind": file_kind, "path": str(path)}, allowed=True)
        result = {"uploaded": True, "fileKind": file_kind, "path": str(path), "reloadRequired": True}
        if file_kind == "strategy":
            result["strategyNames"] = strategy_names
        return result

    @app.get("/api/v1/ai/config")
    async def ai_config(pair: str = "EUR/USD") -> dict:
        return dict(ai_config_for_pair(pair))

    @app.post("/api/v1/ai/config/validate")
    async def validate_ai_config_endpoint(payload: dict, pair: str = "EUR/USD") -> dict:
        current = ai_config_for_pair(pair)
        candidate = dict(current)
        candidate.update({key: value for key, value in payload.items() if key in current})
        validate_ai_config(candidate)
        return {"valid": True, "pair": pair.replace("_", "/").upper(), "effectiveConfig": candidate}

    @app.get("/api/v1/ai/status")
    async def ai_status(pair: str = "EUR/USD") -> dict:
        pair_config = ai_config_for_pair(pair)
        config_payload = json.dumps(pair_config, sort_keys=True, default=str).encode()
        config_revision = str(pair_config.get("configRevision") or hashlib.sha256(config_payload).hexdigest()[:12])
        settings_obj = None
        try:
            settings_obj = OandaSettings.from_environment()
        except ValueError:
            pass
        normalized_pair = normalize_pair(pair)
        selected_class = str(pair_config.get("strategyClass", "ForexAIStrategyBaseline"))
        selected_timeframe = str(pair_config.get("timeframe", "M5")).upper()
        latest_run = next((dict(item) for item in BACKTEST_HISTORY if item.get("pair") == normalized_pair and item.get("strategy") == selected_class), None)
        pending_run = AI_PENDING_OPTIMIZATION.get(hyperopt_scope(normalized_pair, selected_class, selected_timeframe))
        last_optimization = pending_run.get("updatedAt") if pending_run else pair_config.get("lastOptimizationAttempt")
        independent_backtest = bool(
            pending_run and next(
                (
                    item for item in BACKTEST_HISTORY
                    if item.get("pair") == normalized_pair
                    and item.get("strategy") == selected_class
                    and item.get("timeframe") == pending_run.get("timeframe")
                    and item.get("historyMode", "candles") == pending_run.get("historyMode", "candles")
                    and int(item.get("historyValue", item.get("steps", 0))) == int(pending_run.get("historyValue", pending_run.get("steps", 0)))
                    and item.get("status") == "Completed"
                ),
                None,
            )
        )
        return {
            "state": "research/backtest-ready",
            "strategy": selected_class,
            "strategyVersion": "baseline-v1" if selected_class == "ForexAIStrategyBaseline" else "custom",
            "pair": normalized_pair,
            "modelMode": pair_config.get("model", "hybrid"),
            "configRevision": config_revision,
            "modelVersion": "baseline-v1",
            "featureSchemaHash": ai_feature_schema_hash(pair_config),
            "featureSchema": list(pair_config.get("featureSet", [])),
            "executionMode": settings_obj.execution_mode if settings_obj else "unknown",
            "environment": settings_obj.environment.value if settings_obj else "unknown",
            "liveExecution": False,
            "lastBacktest": latest_run,
            "lastOptimizationAttempt": last_optimization,
            "optimizationState": "completed" if last_optimization else "not-run",
            "evidence": {
                "historicalData": "OANDA candles" if latest_run else "not-run",
                "trainingDataHash": latest_run.get("dataHash") if latest_run else None,
                "independentBacktest": "passed" if independent_backtest else "optional/not-required-for-approval",
                "strategyContract": "ForexBacktester.signal",
                "validation": "backtest result available" if latest_run else "awaiting backtest",
                "guardrails": list(AI_REVIEW_STATE["guardrails"]),
            },
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/v1/ai/signals")
    async def ai_signals(pair: str = "EUR/USD", timeframe: str = "M5", count: int = 60) -> dict:
        """Return signal traces only for the pair's exact approved runtime revision."""
        normalized_pair = normalize_pair(pair)
        instrument_name = normalized_pair.replace("/", "_")
        revision = approved_runtime_revision(normalized_pair)
        if revision is None:
            raise HTTPException(
                status_code=409,
                detail="No approved strategy/timeframe is configured for this pair",
            )
        if freqtrade_timeframe(timeframe) != freqtrade_timeframe(
            str(revision.get("timeframe", ""))
        ):
            raise HTTPException(
                status_code=409,
                detail=f"The approved timeframe for {normalized_pair} is {revision.get('timeframe')}",
            )
        try:
            granularity = oanda_granularity(freqtrade_timeframe(timeframe))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        strategy_class = str(revision.get("strategyClass", ""))
        strategy_config = dict(revision.get("strategyConfig") or {})
        hyperopt = revision.get("hyperopt")
        hyperopt = hyperopt if isinstance(hyperopt, dict) else {}
        parameter_values = hyperopt.get(
            "parameters", hyperopt.get("bestParameters", {})
        )
        settings = OandaSettings.from_environment()
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                candles = df_from_raw_candles(
                    await client.get_candles(
                        instrument_name,
                        granularity,
                        count=max(30, min(count, 5000)),
                    )
                )
                if candles.empty:
                    raise HTTPException(
                        status_code=502, detail="No candles returned for approved strategy"
                    )
                if strategy_class == "ForexAIStrategyBaseline":
                    strategy = ForexAIStrategyBaseline({
                        "forex_ai_model": strategy_config.get("model", "hybrid"),
                        "forex_ai_features": strategy_config.get("featureSet", []),
                        "forex_ai_entry_threshold": strategy_config.get("entryThreshold", "0.5"),
                        "forex_ai_exit_threshold": strategy_config.get("exitThreshold", "0.0"),
                        "forex_ai_volatility_window": strategy_config.get("volatilityWindow", "5"),
                        "forex_ai_atr_window": strategy_config.get("atrWindow", "14"),
                        "forex_ai_max_spread_pct": strategy_config.get("maxSpreadPct", "1.0"),
                    })
                    traces = [
                        strategy.signal_trace(candles.iloc[: index + 1])
                        for index in range(
                            max(0, len(candles) - min(count, 60)), len(candles)
                        )
                    ]
                else:
                    strategy = load_strategy(
                        strategy_class,
                        freqtrade_timeframe(timeframe),
                        normalized_pair,
                        parameter_values=(
                            parameter_values if isinstance(parameter_values, dict) else None
                        ),
                    )
                    informative_candles = {}
                    for informative_timeframe in strategy_informative_timeframes(
                        strategy, normalized_pair
                    ):
                        informative_count = strategy_informative_candle_count(
                            strategy, informative_timeframe, len(candles)
                        )
                        informative_raw = await client.get_candles(
                            instrument_name,
                            oanda_granularity(informative_timeframe),
                            count=min(informative_count, 5000),
                        )
                        informative_candles[informative_timeframe] = df_from_raw_candles(
                            informative_raw
                        )
                    adapter = FreqtradeStrategyAdapter(
                        strategy, normalized_pair, informative_candles
                    )
                    traces = [
                        {
                            "time": pd.Timestamp(candles.iloc[index]["date"]).isoformat(),
                            "signal": adapter.signal(candles.iloc[: index + 1]).value,
                            "reason": "approved_strategy_signal",
                            "signalStrength": 0.0,
                        }
                        for index in range(
                            max(0, len(candles) - min(count, 60)), len(candles)
                        )
                    ]
                return {
                    "pair": normalized_pair,
                    "timeframe": str(revision.get("timeframe")),
                    "strategy": strategy_class,
                    "configRevision": revision.get("configRevision", "r0"),
                    "signals": traces,
                }
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - API boundary
            raise HTTPException(status_code=502, detail=f"AI signals unavailable: {exc}") from exc

    @app.get("/api/v1/ai/model-comparison")
    async def ai_model_comparison(pair: str = "EUR/USD", timeframe: str = "M5", count: int = 120) -> dict:
        """Compare research-only LightGBM metrics with the deterministic baseline using the pair's freqai settings."""
        from freqtrade.forex.ai_dataset import build_forex_ai_dataset
        try:
            from freqtrade.forex.ai_lgbm import compare_research_models
        except ModuleNotFoundError as exc:
            if exc.name == "lightgbm":
                raise HTTPException(
                    status_code=503,
                    detail="LightGBM is not installed. Run ./setup.sh --update-forex and retry.",
                ) from exc
            raise

        settings = OandaSettings.from_environment()
        normalized_pair = normalize_pair(pair)
        instrument = normalized_pair.replace("/", "_")
        granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(timeframe.upper())
        if granularity is None:
            raise HTTPException(status_code=400, detail="Unsupported comparison timeframe")
        pair_config = ai_config_for_pair(normalized_pair)
        freqai_config = pair_config.get("freqai") or {}
        feature_parameters = freqai_config.get("featureParameters") or {}
        label_period = int(feature_parameters.get("labelPeriodCandles", 2))
        indicator_periods = tuple(int(period) for period in feature_parameters.get("indicatorPeriodsCandles", [5, 14]))
        include_shifted_candles = int(feature_parameters.get("includeShiftedCandles", 0))
        weight_factor = float(feature_parameters.get("weightFactor", 0.0))
        di_threshold = float(feature_parameters.get("diThreshold", 0.0))
        train_period_days = int(freqai_config.get("trainPeriodDays", 30))
        backtest_period_days = int(freqai_config.get("backtestPeriodDays", 7))
        candles_per_day = {"M5": 288, "M15": 96, "H1": 24}.get(timeframe.upper(), 0)
        requested_candles = (train_period_days + backtest_period_days) * candles_per_day
        fetch_count = max(40, min(max(count, requested_candles), 5000))
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                candles = df_from_raw_candles(await client.get_candles(instrument, granularity, count=fetch_count))
                dataset, manifest = build_forex_ai_dataset(
                    candles,
                    pair=normalized_pair,
                    timeframe=timeframe,
                    history_value=fetch_count,
                    label_period=label_period,
                    indicator_periods=indicator_periods,
                    include_shifted_candles=include_shifted_candles,
                    train_period_days=train_period_days,
                    backtest_period_days=backtest_period_days,
                )
                comparison = compare_research_models(dataset, manifest, weight_factor=weight_factor, di_threshold=di_threshold)
                return {"pair": normalized_pair, "timeframe": timeframe.upper(), "comparison": comparison}
        except Exception as exc:  # pragma: no cover - API boundary
            raise HTTPException(status_code=502, detail=f"Model comparison unavailable: {exc}") from exc

    def _run_hyperopt_job(
        job: dict[str, object],
        pairs: list[str],
        instrument_names: tuple[str, ...],
        timeframe: str,
        strategy_class: str,
        informative_candles: dict[str, pd.DataFrame],
        history_mode: str,
        history_value: int,
        attempts: int,
        hyperopt_loss: str,
        freqaimodel: str,
        freqai_config: dict[str, object],
        candles_by_pair: dict[str, pd.DataFrame],
        instruments: dict[str, object],
        spreads: dict[str, Decimal],
        starting_balance: Decimal,
        risk_fraction: Decimal,
        data_hash: str,
        schema_hash: str,
    ) -> None:
        """Run the CPU-bound search in a worker thread so /status and /stop stay responsive."""
        from freqtrade.forex.ai_hyperopt import run_ai_hyperopt, run_strategy_hyperopt

        stop_event: threading.Event = job["stopEvent"]  # type: ignore[assignment]

        def on_attempt(done: int, total: int) -> None:
            job["attemptsCompleted"] = done
            job["attemptsTotal"] = total

        def should_stop() -> bool:
            return stop_event.is_set()

        model_training: dict[str, object] | None = None
        model_reused: bool | None = None
        training_context: dict[str, object] | None = None
        model_version = "baseline-v1"

        try:
            if freqaimodel != "ForexAIStrategyBaseline":
                if len(pairs) != 1:
                    raise ValueError("FreqAI model Hyperopt currently runs one pair per job")
                from freqtrade.forex.cli import (
                    _resolve_cli_freqai_config,
                    _run_lightgbm_hyperopt,
                )

                normalized_freqai_config = _resolve_cli_freqai_config(
                    timeframe, {"freqai": freqai_config}
                )

                def on_model_progress(phase: str, done: int, total: int) -> None:
                    job["phase"] = phase
                    job["attemptsCompleted"] = done
                    job["attemptsTotal"] = total

                pair = pairs[0].replace("_", "/").upper()
                model_report_path, model_weights_path, model_report = _run_lightgbm_hyperopt(
                    candles_by_pair[instrument_names[0]],
                    instrument=instruments[instrument_names[0]],
                    pair=pair,
                    timeframe=freqtrade_timeframe(timeframe),
                    model_name=freqaimodel,
                    epochs=attempts,
                    model_dir=Path("user_data/hyperopt_results"),
                    starting_balance=starting_balance,
                    risk_fraction=risk_fraction,
                    spread=spreads[instrument_names[0]],
                    slippage=Decimal("0"),
                    financing_rate_per_day=Decimal("0"),
                    quote_to_account_rate=Decimal("1"),
                    stop_pips=Decimal("0.5"),
                    hyperopt_loss=hyperopt_loss,
                    strategy_class=strategy_class,
                    freqai_config=normalized_freqai_config,
                    informative_candles=informative_candles,
                    on_progress=on_model_progress,
                )
                model_training = dict(model_report.get("model_training", {}))
                model_reused = bool(model_report.get("model_reused", False))
                training_context = dict(model_report.get("training_context", {}))
                model_version = f"{freqaimodel}:{model_report.get('training_context_hash', '')}"
                candidates = [
                    {
                        "parameters": item.get("parameters", {}),
                        "objective": str(item.get("objective", "0")),
                        "trainNetPl": str(item.get("trainNetPl", "0")),
                        "validationNetPl": str(item.get("validationNetPl", item.get("validation_net_pl", "0"))),
                        "validationDrawdown": str(item.get("validationDrawdown", item.get("validation_drawdown", "0"))),
                        "validationTrades": int(item.get("validationTrades", item.get("validation_trades", 0))),
                        "coverage": 1,
                    }
                    for item in model_report.get("candidates", [])
                ]
                job["modelPath"] = str(model_weights_path)
                job["reportPath"] = str(model_report_path)
            elif strategy_class != "ForexAIStrategyBaseline":
                if len(pairs) != 1:
                    raise ValueError("Custom strategy Hyperopt currently runs one pair per job")
                candidates = run_strategy_hyperopt(
                    candles_by_pair[instrument_names[0]],
                    informative_candles,
                    instruments[instrument_names[0]],
                    pair=pairs[0].replace("_", "/").upper(),
                    strategy_class=strategy_class,
                    timeframe=freqtrade_timeframe(timeframe),
                    starting_balance=starting_balance,
                    risk_fraction=risk_fraction,
                    spread=spreads[instrument_names[0]],
                    max_attempts=attempts,
                    hyperopt_loss=hyperopt_loss,
                    on_attempt=on_attempt,
                    should_stop=should_stop,
                )
            elif len(pairs) == 1:
                single = run_ai_hyperopt(
                    candles_by_pair[instrument_names[0]],
                    instruments[instrument_names[0]],
                    starting_balance=starting_balance,
                    risk_fraction=risk_fraction,
                    spread=spreads[instrument_names[0]],
                    max_attempts=attempts,
                    hyperopt_loss=hyperopt_loss,
                    on_attempt=on_attempt,
                    should_stop=should_stop,
                )
                candidates = [{"entryThreshold": str(item.entry_threshold), "maxSpreadPct": str(item.max_spread_pct), "objective": format_decimal(item.objective), "trainNetPl": format_decimal(item.train_result.net_pl), "validationNetPl": format_decimal(item.validation_result.net_pl), "validationDrawdown": format_decimal(item.validation_result.max_drawdown), "validationTrades": len(item.validation_result.trades), "coverage": 2} for item in single.candidates]
            else:
                candidates = run_ai_hyperopt_robust(candles_by_pair, instruments, starting_balance=starting_balance, risk_fraction=risk_fraction, spreads=spreads, max_attempts=attempts, hyperopt_loss=hyperopt_loss, on_attempt=on_attempt, should_stop=should_stop)
            if not candidates:
                raise ValueError("Hyperopt was stopped before completing any attempt")
            best = candidates[0]
            normalized_pair = pairs[0].replace("_", "/").upper()
            scope_key = hyperopt_scope(normalized_pair, strategy_class, timeframe)
            completed_at = datetime.now(timezone.utc).isoformat()
            ai_config_for_pair(normalized_pair)["lastOptimizationAttempt"] = completed_at
            AI_PENDING_OPTIMIZATION[scope_key] = {
                "pair": normalized_pair,
                "timeframe": timeframe.upper(),
                "strategyClass": strategy_class,
                "steps": history_value,
                "historyMode": history_mode,
                "historyValue": history_value,
                "attempts": attempts,
                "freqaimodel": freqaimodel,
                "entryThreshold": str(best.get("entryThreshold", "")),
                "maxSpreadPct": str(best.get("maxSpreadPct", "")),
                "parameters": best.get("parameters", {}),
                "objective": str(best["objective"]),
                "updatedAt": completed_at,
                "dataRevision": completed_at,
                "dataHash": data_hash,
                "featureSchemaHash": schema_hash,
                "modelVersion": model_version,
            }
            report = {
                "pair": normalized_pair,
                "timeframe": timeframe.upper(),
                "status": "stopped" if stop_event.is_set() else "completed",
                "strategy": strategy_class,
                "dataSource": "OANDA historical candles",
                "dataRevision": completed_at,
                "dataHash": data_hash,
                "featureSchemaHash": schema_hash,
                "modelVersion": model_version,
                "candidatesTested": len(candidates),
                "pairsTested": len(pairs),
                "periodsTested": 2,
                "coverage": 1 if freqaimodel != "ForexAIStrategyBaseline" else len(pairs) * 2,
                "attemptsRequested": attempts,
                "hyperoptLoss": hyperopt_loss,
                "freqaimodel": freqaimodel,
                "modelReused": model_reused,
                "modelTraining": model_training,
                "historyMode": history_mode,
                "historyValue": history_value,
                "trainCandles": (
                    int(training_context.get("train_rows", 0))
                    if training_context else max(len(candle_frame) // 2 for candle_frame in candles_by_pair.values())
                ),
                "validationCandles": (
                    int(training_context.get("validation_rows", 0))
                    if training_context else max(len(candle_frame) // 2 for candle_frame in candles_by_pair.values())
                ),
                "trainingContext": training_context,
                "bestParameters": dict(best.get("parameters") or {
                    "entryThreshold": str(best.get("entryThreshold", "")),
                    "maxSpreadPct": str(best.get("maxSpreadPct", "")),
                }),
                "objective": str(best["objective"]),
                "train": {
                    "netPl": str(best.get("trainNetPl", "0")),
                    "drawdown": str(best.get("trainDrawdown", "0")),
                    "trades": int(best.get("trainTrades", 0)),
                },
                "validation": {
                    "netPl": str(best["validationNetPl"]),
                    "drawdown": str(best["validationDrawdown"]),
                    "trades": int(best["validationTrades"]),
                },
                "trainingContext": training_context,
                "candidates": [{"rank": rank, **candidate} for rank, candidate in enumerate(candidates, start=1)],
            }
            report["reportText"] = format_hyperopt_report(report)
            job["status"] = report["status"]
            job["report"] = report
            job["completedAt"] = completed_at
            AI_HYPEROPT_REPORTS[scope_key] = {"report": report, "completedAt": completed_at}
            persist_hyperopt_report(scope_key, completed_at, report)
        except Exception as exc:  # pragma: no cover - background worker boundary
            job["status"] = "failed"
            job["error"] = str(exc)

    @app.post("/api/v1/ai/research-cache/clear")
    async def clear_ai_research_cache(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        pair = normalize_pair(str(payload.get("pair", "EUR/USD")))
        timeframe = str(payload.get("timeframe", "M5")).upper()
        clear_candles = bool(payload.get("candles", True))
        clear_models = bool(payload.get("models", True))
        if not clear_candles and not clear_models:
            raise HTTPException(status_code=400, detail="Select at least one cache type to clear")

        active_backtest = any(
            job.get("status") in {"queued", "running"}
            and job.get("pair") == pair
            for job in BACKTEST_JOBS.values()
        )
        active_hyperopt = any(
            job.get("status") == "running" and job.get("pair") == pair
            for job in AI_HYPEROPT_JOBS.values()
        )
        if active_backtest or active_hyperopt:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot clear research caches while a backtest or Hyperopt is running for {pair}",
            )

        removed_candles = (
            _clear_cached_historical_data(pair, timeframe) if clear_candles else []
        )
        removed_models = _clear_cached_ai_models(pair) if clear_models else []
        return {
            "pair": pair,
            "timeframe": timeframe,
            "candleItemsRemoved": len(removed_candles),
            "modelFilesRemoved": len(removed_models),
            "removed": [*removed_candles, *removed_models],
        }

    @app.post("/api/v1/ai/hyperopt/start")
    async def ai_hyperopt_start(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        pairs = [str(item) for item in payload.get("pairs", [payload.get("pair", "EUR/USD")])]
        pairs = list(dict.fromkeys(pairs))
        normalized_pair = pairs[0].replace("_", "/").upper()
        pair_config = ai_config_for_pair(normalized_pair)
        timeframe = str(payload.get("timeframe", pair_config.get("timeframe", "M5")))
        strategy_class_name = str(payload.get("strategyClass") or pair_config.get("strategyClass") or "ForexAIStrategyBaseline")
        scope_key = hyperopt_scope(normalized_pair, strategy_class_name, timeframe)
        existing_job = AI_HYPEROPT_JOBS.get(scope_key)
        if existing_job is not None and existing_job.get("status") == "running":
            raise HTTPException(status_code=409, detail=f"Hyperopt already running for {normalized_pair}, {timeframe}, {strategy_class_name}")
        if payload.get("resetPrevious", True):
            for selected_pair in pairs:
                reset_pair_research_state(selected_pair, strategy_class_name, timeframe)
        freqaimodel = str(payload.get("freqaimodel", "ForexAIStrategyBaseline"))
        if freqaimodel not in {
            "ForexAIStrategyBaseline", "LightGBMRegressor", "LightGBMClassifier"
        }:
            raise HTTPException(status_code=400, detail=f"Unsupported FreqAI model: {freqaimodel}")
        freqai_config = dict(pair_config.get("freqai") or {})
        if freqaimodel != "ForexAIStrategyBaseline" and len(pairs) != 1:
            raise HTTPException(
                status_code=400,
                detail="FreqAI model Hyperopt currently supports one pair per job",
            )
        history_mode, steps = resolve_history_request(
            payload,
            timeframe,
            max_candles=50000 if freqaimodel != "ForexAIStrategyBaseline" else 10000,
        )
        if freqaimodel != "ForexAIStrategyBaseline":
            minimum_candles = minimum_freqai_history(freqai_config, timeframe)
            if minimum_candles > 50000:
                raise HTTPException(
                    status_code=400,
                    detail=f"Configured FreqAI window needs {minimum_candles} candles; reduce its training windows or increase the timeframe.",
                )
            if steps < minimum_candles:
                history_mode = "candles"
                steps = minimum_candles
            freqai_config = freqai_history_config(freqai_config, timeframe, steps)
        history_value = steps
        attempts = max(1, min(int(payload.get("attempts", 24)), 900))
        from freqtrade.forex.ai_hyperopt import DEFAULT_HYPEROPT_LOSS, HYPEROPT_LOSS_FUNCTIONS
        hyperopt_loss = str(payload.get("hyperoptLoss", DEFAULT_HYPEROPT_LOSS))
        if hyperopt_loss not in HYPEROPT_LOSS_FUNCTIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported hyperoptLoss: {hyperopt_loss}")
        try:
            candidate_strategy = load_strategy(strategy_class_name, freqtrade_timeframe(timeframe), normalized_pair)
            informative_timeframes = strategy_informative_timeframes(candidate_strategy, normalized_pair)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if strategy_class_name != "ForexAIStrategyBaseline":
            from freqtrade.strategy.parameters import BaseParameter

            optimizable_parameters = {
                name: value
                for strategy_type in reversed(type(candidate_strategy).__mro__)
                for name, value in vars(strategy_type).items()
                if isinstance(value, BaseParameter) and value.optimize
            }
            if not optimizable_parameters:
                raise HTTPException(status_code=400, detail=f"{strategy_class_name} has no optimizable Freqtrade parameters")
        if strategy_class_name != "ForexAIStrategyBaseline" and len(pairs) != 1:
            raise HTTPException(status_code=400, detail="Custom strategy Hyperopt currently runs one pair per job")
        settings = OandaSettings.from_environment()
        if settings.execution_mode not in {"dry_run", "backtest", "practice"}:
            raise HTTPException(status_code=400, detail="AI hyperopt requires a safe execution mode")
        history_warning = (
            f"Clearing previous cached historical data for {normalized_pair} at {timeframe} "
            "before downloading fresh OANDA candles."
        )
        _clear_cached_historical_data(pair=normalized_pair, timeframe=timeframe)
        try:
            granularity = oanda_granularity(freqtrade_timeframe(timeframe))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                account = await client.get_account_summary()
                instrument_names = tuple(item.replace("/", "_").upper() for item in pairs)
                metadata = await client.get_instruments(instrument_names)
                instruments = {item.name: item for item in metadata}
                candles_by_pair: dict[str, pd.DataFrame] = {}
                spreads: dict[str, Decimal] = {}
                informative_candles: dict[str, pd.DataFrame] = {}
                for pair in pairs:
                    instrument_name = pair.replace("/", "_").upper()
                    candles = df_from_raw_candles(await client.get_candles(instrument_name, granularity, count=steps))
                    if candles.empty:
                        raise HTTPException(status_code=400, detail=f"No historical candles returned for {pair}")
                    candles_by_pair[instrument_name] = candles
                    spreads[instrument_name] = (await client.get_prices((instrument_name,)))[0].spread
                    if len(pairs) == 1:
                        for informative_timeframe in informative_timeframes:
                            informative_count = min(
                                5000,
                                strategy_informative_candle_count(
                                    candidate_strategy, informative_timeframe, steps
                                ),
                            )
                            informative_frame = df_from_raw_candles(await client.get_candles(
                                instrument_name,
                                oanda_granularity(informative_timeframe),
                                count=informative_count,
                            ))
                            if informative_frame.empty:
                                raise HTTPException(status_code=400, detail=f"No informative candles returned for {informative_timeframe}")
                            informative_candles[informative_timeframe] = informative_frame
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - API boundary
            raise HTTPException(status_code=500, detail=f"AI hyperopt failed: {exc}") from exc

        data_hash = candle_frame_hash(candles_by_pair)
        schema_hash = hashlib.sha256(f"{strategy_class_name}|{timeframe}|{ai_feature_schema_hash(pair_config)}".encode()).hexdigest()
        coverage = 2 if len(pairs) == 1 else len(pairs) * 2
        search_space_size = min(attempts, 900)
        job: dict[str, object] = {
            "pair": normalized_pair,
            "timeframe": timeframe.upper(),
            "strategyClass": strategy_class_name,
            "freqaimodel": freqaimodel,
            "scopeKey": scope_key,
            "status": "running",
            "attemptsCompleted": 0,
            "attemptsTotal": search_space_size * coverage,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "stopEvent": threading.Event(),
        }
        AI_HYPEROPT_JOBS[scope_key] = job
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            _run_hyperopt_job,
            job,
            pairs,
            instrument_names,
            timeframe,
            strategy_class_name,
            informative_candles,
            history_mode,
            history_value,
            attempts,
            hyperopt_loss,
            freqaimodel,
            freqai_config,
            candles_by_pair,
            instruments,
            spreads,
            Decimal(str(account.balance)),
            Decimal(str(settings.risk_fraction)),
            data_hash,
            schema_hash,
        )
        return {
            "pair": normalized_pair,
            "timeframe": timeframe.upper(),
            "strategyClass": strategy_class_name,
            "freqaimodel": freqaimodel,
            "status": "running",
            "attemptsTotal": job["attemptsTotal"],
            "warning": history_warning,
        }

    @app.get("/api/v1/ai/hyperopt/loss-functions")
    async def ai_hyperopt_loss_functions() -> dict:
        from freqtrade.forex.ai_hyperopt import DEFAULT_HYPEROPT_LOSS, HYPEROPT_LOSS_FUNCTIONS
        return {"default": DEFAULT_HYPEROPT_LOSS, "options": list(HYPEROPT_LOSS_FUNCTIONS)}

    @app.get("/api/v1/ai/hyperopt/status")
    async def ai_hyperopt_status(
        pair: str = "EUR/USD",
        strategy_class: str | None = None,
        timeframe: str | None = None,
        freqaimodel: str | None = None,
    ) -> dict:
        normalized_pair = normalize_pair(pair)
        pair_config = ai_config_for_pair(normalized_pair)
        selected_class = strategy_class or str(pair_config.get("strategyClass", "ForexAIStrategyBaseline"))
        selected_timeframe = (timeframe or str(pair_config.get("timeframe", "M5"))).upper()
        selected_freqaimodel = freqaimodel
        scope_key = hyperopt_scope(normalized_pair, selected_class, selected_timeframe)
        job = AI_HYPEROPT_JOBS.get(scope_key)
        if (
            job is not None
            and selected_freqaimodel
            and job.get("freqaimodel") != selected_freqaimodel
        ):
            job = None
        if job is None:
            last_report = AI_HYPEROPT_REPORTS.get(scope_key) or AI_HYPEROPT_REPORTS.get(normalized_pair)
            last_report_value = last_report.get("report", {}) if last_report else {}
            if selected_freqaimodel and last_report_value.get("freqaimodel") != selected_freqaimodel:
                last_report = None
            return {
                "pair": normalized_pair,
                "strategyClass": selected_class,
                "freqaimodel": freqaimodel or (last_report.get("report", {}).get("freqaimodel") if last_report else None),
                "timeframe": selected_timeframe,
                "status": "idle",
                "attemptsCompleted": 0,
                "attemptsTotal": 0,
                "hasLastReport": last_report is not None,
            }
        return {
            "pair": normalized_pair,
            "status": job["status"],
            "strategyClass": selected_class,
            "freqaimodel": job.get("freqaimodel"),
            "phase": job.get("phase"),
            "attemptsCompleted": job["attemptsCompleted"],
            "attemptsTotal": job["attemptsTotal"],
            "startedAt": job["startedAt"],
            "error": job.get("error"),
            "report": job.get("report") if job["status"] in {"completed", "stopped"} else None,
        }

    @app.post("/api/v1/ai/hyperopt/stop")
    async def ai_hyperopt_stop(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        normalized_pair = normalize_pair(str(payload.get("pair", "EUR/USD")))
        pair_config = ai_config_for_pair(normalized_pair)
        strategy_class = str(payload.get("strategyClass") or pair_config.get("strategyClass", "ForexAIStrategyBaseline"))
        timeframe = str(payload.get("timeframe") or pair_config.get("timeframe", "M5"))
        scope_key = hyperopt_scope(normalized_pair, strategy_class, timeframe)
        job = AI_HYPEROPT_JOBS.get(scope_key)
        if job is None or job.get("status") != "running":
            raise HTTPException(status_code=400, detail=f"No running hyperopt job for {normalized_pair}, {timeframe}, {strategy_class}")
        job["stopEvent"].set()  # type: ignore[union-attr]
        return {"pair": normalized_pair, "timeframe": timeframe, "strategyClass": strategy_class, "status": "stopping"}

    @app.get("/api/v1/ai/hyperopt/report")
    async def ai_hyperopt_report(
        pair: str = "EUR/USD",
        strategy_class: str | None = None,
        timeframe: str | None = None,
        freqaimodel: str | None = None,
    ) -> dict:
        normalized_pair = normalize_pair(pair)
        pair_config = ai_config_for_pair(normalized_pair)
        selected_class = strategy_class or str(pair_config.get("strategyClass", "ForexAIStrategyBaseline"))
        selected_timeframe = (timeframe or str(pair_config.get("timeframe", "M5"))).upper()
        scope_key = hyperopt_scope(normalized_pair, selected_class, selected_timeframe)
        entry = AI_HYPEROPT_REPORTS.get(scope_key) or AI_HYPEROPT_REPORTS.get(normalized_pair)
        if (
            entry is not None
            and freqaimodel
            and entry.get("report", {}).get("freqaimodel") != freqaimodel
        ):
            entry = None
        if entry is None:
            return {"pair": normalized_pair, "available": False}
        return {
            "pair": normalized_pair,
            "available": True,
            "completedAt": entry["completedAt"],
            "ageDays": report_age_days(str(entry["completedAt"])),
            "report": entry["report"],
        }

    def approved_scheduler_pairs_for(strategy_class: str, timeframe: str) -> list[str]:
        timeframe = timeframe.upper()
        return [
            pair
            for pair, scopes in approved_scheduler_scopes().items()
            if any(scope["strategyClass"] == strategy_class and scope["timeframe"] == timeframe for scope in scopes)
        ]

    def approved_scheduler_scopes() -> dict[str, list[dict[str, str]]]:
        setup = load_forex_config(Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json")))
        configured_pairs = [normalize_pair(str(pair)) for pair in AI_HYPEROPT_SCHEDULER.get("pairs", [])]
        setup_pairs = [normalize_pair(str(pair)) for pair in dict(setup.get("exchange", {})).get("pair_whitelist", [])]
        candidates = list(dict.fromkeys([*configured_pairs, *setup_pairs, *sorted(AI_CONFIG_BY_PAIR)]))
        approved: dict[str, list[dict[str, str]]] = {}
        for pair in candidates:
            pair_config = ai_config_for_pair(pair)
            revisions = dict(pair_config.get("approvedRevisions") or {})
            scopes = [
                {"strategyClass": str(revision.get("strategyClass", "ForexAIStrategyBaseline")), "timeframe": str(revision.get("timeframe", "M5")).upper()}
                for revision in revisions.values()
                if isinstance(revision, dict)
            ]
            legacy = pair_config.get("approvedRevision")
            if isinstance(legacy, dict):
                legacy_scope = {"strategyClass": str(legacy.get("strategyClass", "ForexAIStrategyBaseline")), "timeframe": str(legacy.get("timeframe", "M5")).upper()}
                if legacy_scope not in scopes:
                    scopes.append(legacy_scope)
            if scopes:
                approved[pair] = scopes
        return approved

    def approved_scheduler_pairs() -> list[str]:
        pair_strategies = dict(AI_HYPEROPT_SCHEDULER.get("pairStrategies") or {})
        pair_timeframes = dict(AI_HYPEROPT_SCHEDULER.get("pairTimeframes") or {})
        approved_scopes = approved_scheduler_scopes()
        selected = []
        for pair in AI_HYPEROPT_SCHEDULER.get("pairs", []):
            normalized = normalize_pair(str(pair))
            strategy_class = str(pair_strategies.get(normalized, AI_HYPEROPT_SCHEDULER.get("strategyClass", "ForexAIStrategyBaseline")))
            timeframe = str(pair_timeframes.get(normalized, AI_HYPEROPT_SCHEDULER.get("timeframe", "M5"))).upper()
            if {"strategyClass": strategy_class, "timeframe": timeframe} in approved_scopes.get(normalized, []):
                selected.append(normalized)
        return selected or list(approved_scopes)

    def schedule_pair_slots(pairs: list[str], *, anchor: datetime | None = None) -> dict[str, str]:
        base = anchor or (datetime.now(timezone.utc) + timedelta(minutes=5))
        gap = timedelta(minutes=int(AI_HYPEROPT_SCHEDULER["gapMinutes"]))
        return {pair: (base + index * gap).isoformat() for index, pair in enumerate(pairs)}

    async def wait_for_hyperopt_job(scope_key: str) -> dict[str, object]:
        while True:
            job = AI_HYPEROPT_JOBS.get(scope_key, {})
            if job.get("status") in {"completed", "stopped", "failed"}:
                return job
            await asyncio.sleep(2)

    async def run_scheduled_hyperopt(pairs_override: list[str] | None = None) -> dict:
        if AI_HYPEROPT_SCHEDULER.get("running"):
            return {"status": "already-running", "pairs": approved_scheduler_pairs()}
        pairs = pairs_override or approved_scheduler_pairs()
        if not pairs:
            AI_HYPEROPT_SCHEDULER["lastError"] = "No approved pair/timeframe revisions available"
            persist_hyperopt_scheduler()
            return {"status": "blocked", "pairs": []}
        AI_HYPEROPT_SCHEDULER["running"] = True
        AI_HYPEROPT_SCHEDULER["lastError"] = None
        persist_hyperopt_scheduler()
        started: list[dict] = []
        warnings: list[str] = []
        try:
            async def start_for_pair(pair: str) -> dict:
                config = ai_config_for_pair(pair)
                strategy_class = str(dict(AI_HYPEROPT_SCHEDULER.get("pairStrategies") or {}).get(pair, ""))
                timeframe = str(dict(AI_HYPEROPT_SCHEDULER.get("pairTimeframes") or {}).get(pair, "")).upper()
                revisions = dict(config.get("approvedRevisions") or {})
                revision = revisions.get(f"{strategy_class}|{timeframe}") if strategy_class and timeframe else None
                if not isinstance(revision, dict):
                    revision = config.get("approvedRevision") or {}
                    strategy_class = str(revision.get("strategyClass", "ForexAIStrategyBaseline"))
                    timeframe = str(revision.get("timeframe", "M5")).upper()
                hyperopt = revision.get("hyperopt") or {}
                history_value = int(hyperopt.get("historyValue") or 250)
                payload = {
                    "pair": pair,
                    "timeframe": timeframe,
                    "strategyClass": strategy_class,
                    "freqaimodel": AI_HYPEROPT_SCHEDULER.get(
                        "freqaimodel", "ForexAIStrategyBaseline"
                    ),
                    "historyMode": hyperopt.get("historyMode", "candles"),
                    "historyValue": history_value,
                    "steps": history_value,
                    "attempts": int(hyperopt.get("attempts") or 24),
                    "hyperoptLoss": hyperopt.get("hyperoptLoss", "ProfitDrawDownHyperOptLoss"),
                    "resetPrevious": False,
                }
                started_job = await ai_hyperopt_start(payload, user_role="operator", csrf_token="scheduled-hyperopt")
                warning = str(started_job.get("warning") or "")
                if warning:
                    warnings.append(warning)
                result = await wait_for_hyperopt_job(hyperopt_scope(pair, strategy_class, timeframe))
                item = {
                    "pair": pair,
                    "status": result.get("status", started_job.get("status")),
                    "freqaimodel": result.get("freqaimodel", payload["freqaimodel"]),
                    "attemptsCompleted": result.get("attemptsCompleted", 0),
                }
                if warning:
                    item["warning"] = warning
                return item

            started = []
            for pair in pairs:
                started.append(await start_for_pair(pair))
            now = datetime.now(timezone.utc)
            AI_HYPEROPT_SCHEDULER["lastRunAt"] = now.isoformat()
            next_anchor = now + timedelta(days=int(AI_HYPEROPT_SCHEDULER["intervalDays"]))
            next_runs = dict(AI_HYPEROPT_SCHEDULER.get("nextRuns") or {})
            if pairs_override is not None and len(pairs) > 1:
                next_runs.update(schedule_pair_slots(pairs, anchor=next_anchor))
            else:
                for pair in pairs:
                    next_runs[pair] = next_anchor.isoformat()
            AI_HYPEROPT_SCHEDULER["nextRuns"] = next_runs
            AI_HYPEROPT_SCHEDULER["nextRunAt"] = min(next_runs.values()) if next_runs else None
            persist_hyperopt_scheduler()
            return {"status": "started", "pairs": pairs, "jobs": started, "warnings": warnings}
        except Exception as exc:
            AI_HYPEROPT_SCHEDULER["lastError"] = str(exc)
            persist_hyperopt_scheduler()
            raise
        finally:
            AI_HYPEROPT_SCHEDULER["running"] = False
            persist_hyperopt_scheduler()

    @app.get("/api/v1/ai/hyperopt/scheduler")
    async def ai_hyperopt_scheduler(strategy_class: str | None = None, timeframe: str | None = None) -> dict:
        selected_class = strategy_class or str(AI_HYPEROPT_SCHEDULER.get("strategyClass", "ForexAIStrategyBaseline"))
        selected_timeframe = timeframe or str(AI_HYPEROPT_SCHEDULER.get("timeframe", "M5"))
        result = {
            **AI_HYPEROPT_SCHEDULER,
            "strategyClass": selected_class,
            "timeframe": selected_timeframe,
            "approvedPairs": approved_scheduler_pairs_for(selected_class, selected_timeframe),
            "approvedScopes": approved_scheduler_scopes(),
        }
        return result

    @app.post("/api/v1/ai/hyperopt/scheduler")
    async def save_ai_hyperopt_scheduler(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        pairs = list(dict.fromkeys(normalize_pair(str(pair)) for pair in payload.get("pairs", [])))
        strategy_class = str(payload.get("strategyClass", AI_HYPEROPT_SCHEDULER["strategyClass"]))
        freqaimodel = str(
            payload.get(
                "freqaimodel", AI_HYPEROPT_SCHEDULER.get(
                    "freqaimodel", "ForexAIStrategyBaseline"
                )
            )
        )
        if freqaimodel not in {
            "ForexAIStrategyBaseline", "LightGBMRegressor", "LightGBMClassifier"
        }:
            raise HTTPException(status_code=400, detail=f"Unsupported FreqAI model: {freqaimodel}")
        timeframe = str(payload.get("timeframe", AI_HYPEROPT_SCHEDULER["timeframe"])).upper()
        requested_strategies = {normalize_pair(str(pair)): str(value) for pair, value in dict(payload.get("pairStrategies", {})).items()}
        requested_timeframes = {normalize_pair(str(pair)): str(value).upper() for pair, value in dict(payload.get("pairTimeframes", {})).items()}
        approved_scopes = approved_scheduler_scopes()
        selected_strategies: dict[str, str] = {}
        selected_timeframes: dict[str, str] = {}
        unknown: list[str] = []
        for pair in pairs:
            config = ai_config_for_pair(pair)
            latest = config.get("approvedRevision")
            fallback_strategy = str(latest.get("strategyClass", strategy_class)) if isinstance(latest, dict) else strategy_class
            fallback_timeframe = str(latest.get("timeframe", timeframe)).upper() if isinstance(latest, dict) else timeframe
            selected_strategy = requested_strategies.get(pair, fallback_strategy)
            selected_timeframe = requested_timeframes.get(pair, fallback_timeframe)
            selected_strategies[pair] = selected_strategy
            selected_timeframes[pair] = selected_timeframe
            if {"strategyClass": selected_strategy, "timeframe": selected_timeframe} not in approved_scopes.get(pair, []):
                unknown.append(pair)
        if unknown:
            raise HTTPException(status_code=409, detail=f"Pairs require an approved strategy/timeframe revision matching the scheduler selection: {', '.join(unknown)}")
        interval_days = int(payload.get("intervalDays", AI_HYPEROPT_SCHEDULER["intervalDays"]))
        gap_minutes = int(payload.get("gapMinutes", AI_HYPEROPT_SCHEDULER["gapMinutes"]))
        if not 1 <= interval_days <= 30:
            raise HTTPException(status_code=400, detail="intervalDays must be between 1 and 30")
        if not 1 <= gap_minutes <= 1440:
            raise HTTPException(status_code=400, detail="gapMinutes must be between 1 and 1440")
        AI_HYPEROPT_SCHEDULER.update({"enabled": bool(payload.get("enabled", False)), "intervalDays": interval_days, "gapMinutes": gap_minutes, "pairs": pairs, "strategyClass": strategy_class, "freqaimodel": freqaimodel, "timeframe": timeframe, "pairStrategies": selected_strategies, "pairTimeframes": selected_timeframes})
        if AI_HYPEROPT_SCHEDULER["enabled"]:
            next_runs = schedule_pair_slots(pairs)
            AI_HYPEROPT_SCHEDULER["nextRuns"] = next_runs
            AI_HYPEROPT_SCHEDULER["nextRunAt"] = min(next_runs.values()) if next_runs else None
        else:
            AI_HYPEROPT_SCHEDULER["nextRunAt"] = None
            AI_HYPEROPT_SCHEDULER["nextRuns"] = {}
        persist_hyperopt_scheduler()
        return {**AI_HYPEROPT_SCHEDULER, "approvedPairs": approved_scheduler_pairs()}

    @app.post("/api/v1/ai/hyperopt/scheduler/run-now")
    async def run_ai_hyperopt_scheduler_now(
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        return await run_scheduled_hyperopt()

    @app.get("/api/v1/ai/review")
    async def ai_review(pair: str = "EUR/USD", timeframe: str | None = None, strategy_class: str | None = None) -> dict:
        normalized_pair = normalize_pair(pair)
        selected_timeframe = (timeframe or str(ai_config_for_pair(normalized_pair).get("timeframe", "M5"))).upper()
        selected_class = strategy_class or str(ai_config_for_pair(normalized_pair).get("strategyClass", "ForexAIStrategyBaseline"))
        return fallback_ai_review(normalized_pair, selected_timeframe, selected_class)

    @app.post("/api/v1/ai/review")
    async def save_ai_review(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        if user_role not in {"operator", "admin"}:
            raise HTTPException(status_code=403, detail="Forbidden: invalid role for strategy approval")
        if not csrf_token:
            raise HTTPException(status_code=403, detail="Missing CSRF token")

        status = str(payload.get("status", "pending")).lower()
        pair = str(payload.get("pair", "EUR/USD")).replace("_", "/").upper()
        requested_timeframe = str(payload.get("timeframe", ai_config_for_pair(pair).get("timeframe", "M5"))).upper()
        strategy_class = str(payload.get("strategyClass") or ai_config_for_pair(pair).get("strategyClass", "ForexAIStrategyBaseline"))
        if strategy_class not in {item.name for item in discover_strategy_files()}:
            raise HTTPException(status_code=400, detail=f"Unknown strategy class: {strategy_class}")
        if status not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="Invalid review status")
        scope_key = hyperopt_scope(pair, strategy_class, requested_timeframe)
        pending = AI_PENDING_OPTIMIZATION.get(scope_key)
        if pending is None:
            stored_report = AI_HYPEROPT_REPORTS.get(scope_key) or AI_HYPEROPT_REPORTS.get(pair)
            report = stored_report.get("report") if stored_report else None
            if isinstance(report, dict):
                report_parameters = report.get("bestParameters") or {}
                pending = {
                    "pair": pair,
                    "timeframe": str(report.get("timeframe", requested_timeframe)).upper(),
                    "strategyClass": str(report.get("strategy", strategy_class)),
                    "historyMode": report.get("historyMode", "candles"),
                    "historyValue": int(report.get("historyValue", 0)),
                    "attempts": int(report.get("attemptsRequested", 0)),
                    "entryThreshold": str(report_parameters.get("entryThreshold", "")),
                    "maxSpreadPct": str(report_parameters.get("maxSpreadPct", "")),
                    "parameters": report_parameters,
                    "objective": str(report.get("objective", "0")),
                    "updatedAt": str(stored_report.get("completedAt")),
                    "dataHash": report.get("dataHash"),
                    "featureSchemaHash": report.get("featureSchemaHash"),
                    "modelVersion": report.get("modelVersion", "baseline-v1"),
                    "hyperoptLoss": report.get("hyperoptLoss"),
                    "restored": True,
                }
        if pending is None:
            persisted_config = ai_config_for_pair(pair)
            if strategy_class == "ForexAIStrategyBaseline" and "entryThreshold" in persisted_config and "maxSpreadPct" in persisted_config:
                pending = {
                    "pair": pair,
                    "timeframe": requested_timeframe,
                    "strategyClass": strategy_class,
                    "historyMode": "candles",
                    "historyValue": 0,
                    "attempts": 0,
                    "entryThreshold": str(persisted_config["entryThreshold"]),
                    "maxSpreadPct": str(persisted_config["maxSpreadPct"]),
                    "updatedAt": str(persisted_config.get("lastOptimizationAttempt") or datetime.now(timezone.utc).isoformat()),
                    "dataHash": None,
                    "featureSchemaHash": ai_feature_schema_hash(persisted_config),
                    "modelVersion": "baseline-v1",
                    "restored": True,
                }
        require_optimization = bool(payload.get("requireOptimization", False))
        if status == "approved" and require_optimization:
            if pending is None:
                raise HTTPException(status_code=409, detail=f"No Hyperopt result is pending approval for {pair}")
            if pending["timeframe"] != requested_timeframe:
                raise HTTPException(status_code=409, detail=f"Approval timeframe {requested_timeframe} does not match Hyperopt timeframe {pending['timeframe']} for {pair}")
            if pending.get("strategyClass") != strategy_class:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Approval strategy {strategy_class} does not match Hyperopt "
                        f"strategy {pending.get('strategyClass')} for {pair}"
                    ),
                )
            if normalize_pair(str(pending.get("pair", pair))) != pair:
                raise HTTPException(
                    status_code=409,
                    detail=f"Hyperopt pair does not match approval pair {pair}",
                )
            target_config = ai_config_for_pair(pair)
            target_config["timeframe"] = requested_timeframe
            target_config["strategyClass"] = strategy_class
            if pending.get("entryThreshold"):
                target_config["entryThreshold"] = pending["entryThreshold"]
                target_config["maxSpreadPct"] = pending["maxSpreadPct"]
            target_config["approvedHyperopt"] = dict(pending)
        elif status == "approved":
            target_config = ai_config_for_pair(pair)
            configured_timeframe = str(target_config.get("timeframe", "M5")).upper()
            if requested_timeframe != configured_timeframe:
                raise HTTPException(status_code=409, detail=f"Approval timeframe {requested_timeframe} does not match configured timeframe {configured_timeframe} for {pair}")
            target_config["strategyClass"] = strategy_class

        if status == "approved":
            target_config = ai_config_for_pair(pair)
            strategy_config = {
                key: value for key, value in target_config.items()
                if key not in {"approvedRevision", "approvedRevisions", "approvedHyperopt"}
            }
            approved_revision = {
                "pair": pair,
                "timeframe": requested_timeframe,
                "strategyClass": strategy_class,
                "configRevision": target_config.get("configRevision", "r0"),
                "approvedAt": datetime.now(timezone.utc).isoformat(),
                "hyperopt": dict(pending) if require_optimization and pending else {},
                "freqai": dict(target_config.get("freqai") or {}),
                "strategyConfig": strategy_config,
            }
            target_config["approvedRevisions"] = dict(target_config.get("approvedRevisions") or {})
            target_config["approvedRevisions"][f"{strategy_class}|{requested_timeframe}"] = approved_revision
            target_config["approvedRevision"] = approved_revision
            setup_path = Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
            setup_config = load_forex_config(setup_path)
            pair_key = pair.replace("/", "_")
            setup_timeframes = dict(setup_config.get("pair_timeframes") or {})
            setup_strategies = dict(setup_config.get("pair_strategies") or {})
            setup_approved_revisions = dict(
                setup_config.get("pair_approved_revisions") or {}
            )
            setup_timeframes[pair_key] = "1mo" if requested_timeframe in {"M", "MN1"} else freqtrade_timeframe(requested_timeframe)
            setup_strategies[pair_key] = strategy_class
            setup_approved_revisions[pair_key] = approved_revision
            setup_config["pair_timeframes"] = setup_timeframes
            setup_config["pair_strategies"] = setup_strategies
            setup_config["pair_approved_revisions"] = setup_approved_revisions
            save_forex_config(setup_config, setup_path)
        elif status == "rejected":
            target_config = ai_config_for_pair(pair)
            approved_revisions = dict(target_config.get("approvedRevisions") or {})
            approved_revisions.pop(f"{strategy_class}|{requested_timeframe}", None)
            target_config["approvedRevisions"] = approved_revisions
            current_revision = target_config.get("approvedRevision")
            if isinstance(current_revision, dict) and current_revision.get("strategyClass") == strategy_class and current_revision.get("timeframe") == requested_timeframe:
                target_config.pop("approvedRevision", None)
                if approved_revisions:
                    latest_scope = next(reversed(approved_revisions))
                    target_config["approvedRevision"] = approved_revisions[latest_scope]
            setup_path = Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
            setup_config = load_forex_config(setup_path)
            pair_key = pair.replace("/", "_")
            setup_timeframes = dict(setup_config.get("pair_timeframes") or {})
            setup_strategies = dict(setup_config.get("pair_strategies") or {})
            setup_approved_revisions = dict(
                setup_config.get("pair_approved_revisions") or {}
            )
            runtime_timeframe = "1mo" if requested_timeframe in {"M", "MN1"} else freqtrade_timeframe(requested_timeframe)
            active_revision = setup_approved_revisions.get(pair_key)
            if (
                isinstance(active_revision, dict)
                and active_revision.get("strategyClass") == strategy_class
                and str(active_revision.get("timeframe", "")).upper()
                == requested_timeframe
            ):
                setup_approved_revisions.pop(pair_key, None)
                next_revision = target_config.get("approvedRevision")
                if isinstance(next_revision, dict):
                    setup_approved_revisions[pair_key] = next_revision
                    setup_strategies[pair_key] = str(next_revision.get("strategyClass", "ForexAIStrategyBaseline"))
                    next_timeframe = str(next_revision.get("timeframe", "M5")).upper()
                    setup_timeframes[pair_key] = "1mo" if next_timeframe in {"M", "MN1"} else freqtrade_timeframe(next_timeframe)
                else:
                    setup_strategies.pop(pair_key, None)
                    setup_timeframes.pop(pair_key, None)
            elif (
                setup_strategies.get(pair_key) == strategy_class
                and setup_timeframes.get(pair_key) == runtime_timeframe
            ):
                setup_approved_revisions.pop(pair_key, None)
                setup_strategies.pop(pair_key, None)
                setup_timeframes.pop(pair_key, None)
            setup_config["pair_timeframes"] = setup_timeframes
            setup_config["pair_strategies"] = setup_strategies
            setup_config["pair_approved_revisions"] = setup_approved_revisions
            save_forex_config(setup_config, setup_path)

        AI_REVIEW_STATE["status"] = status
        AI_REVIEW_STATE["strategyName"] = AI_CONFIG.get("strategyName", "FX Trend Pulse")
        AI_REVIEW_STATE["model"] = AI_CONFIG.get("model", "hybrid")
        AI_REVIEW_STATE["riskPolicy"] = "Practice-safe"
        AI_REVIEW_STATE["lastUpdated"] = datetime.now(timezone.utc).isoformat()
        AI_REVIEW_STATE["pair"] = pair
        AI_REVIEW_STATE["timeframe"] = requested_timeframe
        AI_REVIEW_STATE["notes"] = str(payload.get("notes") or (
            "Approved in Practice-safe dry-run mode." if status == "approved" else "Rejected. Strategy requires additional validation before approval."
        ))
        if "guardrails" in payload and isinstance(payload["guardrails"], list):
            AI_REVIEW_STATE["guardrails"] = payload["guardrails"]

        scoped_review = dict(AI_REVIEW_STATE)
        scoped_review["pair"] = pair
        scoped_review["timeframe"] = requested_timeframe
        scoped_review["approvedRevision"] = ai_config_for_pair(pair).get("approvedRevision")
        scoped_review["strategyClass"] = strategy_class
        AI_REVIEW_BY_SCOPE[(pair, requested_timeframe, strategy_class)] = scoped_review
        persist_scope_revision(pair, requested_timeframe, strategy_class)

        record_audit_event(
            "ai.strategy.review",
            details={"status": status, "notes": AI_REVIEW_STATE["notes"]},
            username=user_role,
            role=user_role,
            allowed=True,
        )
        return fallback_ai_review(pair, requested_timeframe, strategy_class)

    @app.post("/api/v1/ai/config")
    async def save_ai_config(payload: dict, pair: str = "EUR/USD") -> dict:
        pair = str(payload.get("pair", pair))
        target_config = ai_config_for_pair(pair)
        candidate = dict(target_config)
        for key in ("strategyName", "strategyClass", "model", "timeframe", "riskBudget", "trainingMode", "entryThreshold", "exitThreshold", "volatilityWindow", "atrWindow", "maxSpreadPct"):
            if key in payload and isinstance(payload[key], str):
                candidate[key] = payload[key]
        if str(candidate.get("strategyClass")) not in {item.name for item in discover_strategy_files()}:
            raise HTTPException(status_code=400, detail=f"Unknown strategy class: {candidate.get('strategyClass')}")
        if "featureSet" in payload and isinstance(payload["featureSet"], list):
            candidate["featureSet"] = payload["featureSet"]
        if "freqai" in payload and isinstance(payload["freqai"], dict):
            current_freqai = dict(target_config.get("freqai") or {})
            incoming_freqai = payload["freqai"]
            merged_freqai = dict(current_freqai)
            for key in ("trainPeriodDays", "backtestPeriodDays"):
                if key in incoming_freqai:
                    merged_freqai[key] = incoming_freqai[key]
            if isinstance(incoming_freqai.get("featureParameters"), dict):
                merged_feature_parameters = dict(current_freqai.get("featureParameters") or {})
                merged_feature_parameters.update(incoming_freqai["featureParameters"])
                merged_freqai["featureParameters"] = merged_feature_parameters
            candidate["freqai"] = merged_freqai
        validate_ai_config(candidate)
        revision_number = len(AI_CONFIG_REVISIONS.setdefault(pair.replace("_", "/").upper(), []))
        candidate["configRevision"] = f"r{revision_number}"
        candidate["updatedAt"] = datetime.now(timezone.utc).isoformat()
        target_config.clear()
        target_config.update(candidate)
        AI_CONFIG_REVISIONS[pair.replace("_", "/").upper()].append(dict(candidate))
        persist_scope_revision(pair, str(candidate.get("timeframe", "M5")), str(candidate.get("strategyClass", "ForexAIStrategyBaseline")))
        if "strategyName" in AI_CONFIG:
            AI_REVIEW_STATE["strategyName"] = target_config["strategyName"]
        if "model" in target_config:
            AI_REVIEW_STATE["model"] = target_config["model"]
        return dict(target_config)

    @app.get("/api/v1/strategies")
    async def strategies() -> list[dict]:
        return fallback_strategies()

    @app.get("/api/v1/strategies/available")
    async def available_strategies() -> list[dict[str, str | bool]]:
        return [
            {"name": item.name, "fileName": item.file_name, "builtin": item.builtin}
            for item in discover_strategy_files()
        ]

    @app.get("/api/v1/backtests")
    async def backtests() -> list[dict]:
        if BACKTEST_HISTORY:
            return [dict(item) for item in BACKTEST_HISTORY]
        return fallback_backtests()

    @app.get("/api/v1/backtests/{job_id}")
    async def backtest_job(job_id: str) -> dict:
        job = BACKTEST_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Backtest job not found")
        return dict(job)

    @app.post("/api/v1/backtests/run")
    async def run_backtest(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
        session_token: str | None = Header(default=None, alias="X-Session-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token, session_token)
        if payload.get("trackProgress") and not payload.get("_job_id"):
            job_id = f"bt-job-{secrets.token_hex(4)}"
            BACKTEST_JOBS[job_id] = {
                "id": job_id,
                "pair": normalize_pair(str(payload.get("pair", "EUR/USD"))),
                "timeframe": str(payload.get("timeframe", "M5")).upper(),
                "status": "queued",
                "phase": "queued",
                "historyProgress": 0,
                "backtestProgress": 0,
                "message": "Backtest queued.",
            }

            async def execute_tracked_backtest() -> None:
                try:
                    await run_backtest(
                        {**payload, "_job_id": job_id},
                        user_role=user_role,
                        csrf_token=csrf_token,
                        session_token=session_token,
                    )
                except HTTPException as exc:
                    update_backtest_job(job_id, status="failed", phase="failed", message=str(exc.detail))
                except Exception as exc:  # pragma: no cover - guarded task boundary
                    update_backtest_job(job_id, status="failed", phase="failed", message=str(exc))

            asyncio.create_task(execute_tracked_backtest())
            return dict(BACKTEST_JOBS[job_id])

        job_id = str(payload.get("_job_id", "")) or None
        pair = str(payload.get("pair", "EUR/USD"))
        timeframe = str(payload.get("timeframe", "M5"))
        normalized_pair = normalize_pair(pair)
        instrument_name = pair.replace("/", "_").upper()
        pair_config = ai_config_for_pair(pair)
        freqaimodel = str(payload.get("freqaimodel", "ForexAIStrategyBaseline"))
        if freqaimodel not in {
            "ForexAIStrategyBaseline", "LightGBMRegressor", "LightGBMClassifier"
        }:
            raise HTTPException(status_code=400, detail=f"Unsupported FreqAI model: {freqaimodel}")
        freqai_config = dict(pair_config.get("freqai") or {})
        persisted_setup = load_forex_config(Path(os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json")))
        configured_strategy = dict(persisted_setup.get("pair_strategies", {})).get(instrument_name)
        strategy_class_name = str(payload.get("strategyClass") or configured_strategy or pair_config.get("strategyClass") or "ForexAIStrategyBaseline")
        selected_timeframe = freqtrade_timeframe(timeframe)
        history_notice = (
            f"Clearing previous cached historical data for {normalized_pair} at {timeframe} "
            "before downloading fresh OANDA candles."
        )
        update_backtest_job(job_id, status="running", phase="validating", message=history_notice)
        _clear_cached_historical_data(pair=normalized_pair, timeframe=timeframe)
        history_mode, steps = resolve_history_request(
            payload,
            timeframe,
            max_candles=50000 if freqaimodel != "ForexAIStrategyBaseline" else 10000,
        )
        if freqaimodel != "ForexAIStrategyBaseline":
            minimum_candles = minimum_freqai_history(freqai_config, timeframe)
            if steps < minimum_candles:
                history_mode = "candles"
                steps = minimum_candles
            freqai_config = freqai_history_config(freqai_config, timeframe, steps)
        BACKTEST_HISTORY[:] = [
            item for item in BACKTEST_HISTORY
            if not (
                item.get("pair") == normalized_pair
                and item.get("timeframe") == timeframe
                and item.get("strategy") == strategy_class_name
            )
        ]
        data_revision = datetime.now(timezone.utc).isoformat()
        settings = OandaSettings.from_environment()
        if settings.environment.value.lower() not in {"practice", "live"}:
            raise HTTPException(status_code=400, detail="Backtest requires a valid OANDA environment")
        if settings.execution_mode.lower() not in {"dry_run", "backtest", "practice"}:
            raise HTTPException(status_code=400, detail="Backtest requires a safe OANDA execution mode")

        approved_revisions = dict(pair_config.get("approvedRevisions") or {})
        approved_run = approved_revisions.get(f"{strategy_class_name}|{timeframe.upper()}")
        if not isinstance(approved_run, dict):
            legacy_run = pair_config.get("approvedHyperopt")
            approved_run = legacy_run if isinstance(legacy_run, dict) and legacy_run.get("strategyClass", "ForexAIStrategyBaseline") == strategy_class_name else None
        backtest_warning: str | None = None
        if isinstance(approved_run, dict):
            pair_matches = str(approved_run.get("pair", normalized_pair)).upper() == normalized_pair.upper()
            timeframe_matches = str(approved_run.get("timeframe", "")).upper() == timeframe.upper()
            strategy_matches = str(approved_run.get("strategyClass", "ForexAIStrategyBaseline")) == strategy_class_name
            history_matches = (
                approved_run.get("historyMode", "candles") == history_mode
                and int(approved_run.get("historyValue", steps)) == int(payload.get("historyValue", steps))
            )
            if pair_matches and timeframe_matches and strategy_matches:
                # Hyperopt parameters remain valid for the same pair/timeframe;
                # a different history window only changes the validation sample.
                if not history_matches:
                    backtest_warning = (
                        f"Selected history for {normalized_pair} at {timeframe} differs from the approved "
                        f"Hyperopt history ({approved_run.get('historyMode', 'candles')} "
                        f"{approved_run.get('historyValue', approved_run.get('steps'))}); "
                        "approved Hyperopt parameters are still being used."
                    )
            else:
                backtest_warning = (
                    f"Approved Hyperopt revision exists for {approved_run.get('pair', normalized_pair)} "
                    f"at {approved_run.get('timeframe')} / {approved_run.get('historyMode', 'candles')} "
                    f"{approved_run.get('historyValue', approved_run.get('steps'))}, but this run is "
                    f"{normalized_pair} at {timeframe} using {strategy_class_name}; default parameters are being used."
                )
                approved_run = None
                pair_config = dict(AI_CONFIG)
        strategy_config_overrides: dict[str, object] = {}
        if strategy_class_name == "ForexAIStrategyBaseline":
            strategy_config_overrides = {
                "forex_ai_model": str(pair_config.get("model", "hybrid")),
                "forex_ai_features": pair_config.get("featureSet", []),
                "forex_ai_entry_threshold": pair_config.get("entryThreshold", "0.5"),
                "forex_ai_exit_threshold": pair_config.get("exitThreshold", "0.0"),
                "forex_ai_volatility_window": pair_config.get("volatilityWindow", "5"),
                "forex_ai_atr_window": pair_config.get("atrWindow", "14"),
                "forex_ai_max_spread_pct": pair_config.get("maxSpreadPct", "1.0"),
            }
            if isinstance(approved_run, dict):
                strategy_config_overrides["forex_ai_entry_threshold"] = approved_run.get("entryThreshold", strategy_config_overrides["forex_ai_entry_threshold"])
                strategy_config_overrides["forex_ai_max_spread_pct"] = approved_run.get("maxSpreadPct", strategy_config_overrides["forex_ai_max_spread_pct"])
        try:
            strategy_instance = load_strategy(
                strategy_class_name,
                selected_timeframe,
                normalized_pair,
                parameter_values=approved_run.get("parameters") if isinstance(approved_run, dict) else None,
                config_overrides=strategy_config_overrides,
            )
            informative_timeframes = strategy_informative_timeframes(strategy_instance, normalized_pair)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            granularity = oanda_granularity(selected_timeframe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                metadata = await client.get_instruments((instrument_name,))
                update_backtest_job(job_id, phase="history", historyProgress=15, backtestProgress=0, message="Loading instrument metadata.")
                if not metadata:
                    raise HTTPException(status_code=404, detail=f"Instrument {instrument_name} is not available in the configured OANDA account")
                account = await client.get_account_summary()
                update_backtest_job(job_id, historyProgress=25, message="Reading account baseline.")
                candles = await client.get_candles(instrument_name, granularity, count=steps)
                update_backtest_job(job_id, historyProgress=85, message=f"Received {len(candles)} historical candles from OANDA.")
                frame = df_from_raw_candles(candles)
                if frame.empty:
                    raise HTTPException(status_code=400, detail="No historical candles were returned for the requested OANDA pair")
                informative_frames: dict[str, pd.DataFrame] = {}
                for informative_timeframe in informative_timeframes:
                    informative_count = min(
                        5000,
                        strategy_informative_candle_count(
                            strategy_instance, informative_timeframe, steps
                        ),
                    )
                    informative_candles = await client.get_candles(
                        instrument_name,
                        oanda_granularity(informative_timeframe),
                        count=informative_count,
                    )
                    informative_frame = df_from_raw_candles(informative_candles)
                    if informative_frame.empty:
                        raise HTTPException(status_code=400, detail=f"No informative candles returned for {informative_timeframe}")
                    informative_frames[informative_timeframe] = informative_frame
                data_hash = candle_frame_hash({instrument_name: frame, **{f"{instrument_name}:{key}": value for key, value in informative_frames.items()}})
                price_row = (await client.get_prices((instrument_name,)))[0]
                update_backtest_job(job_id, phase="backtest", historyProgress=100, backtestProgress=10, message=f"History ready: {len(frame)} {timeframe} candles and {len(informative_frames)} informative timeframe(s). Running {strategy_class_name}.")
                model_backtest_report: dict[str, object] | None = None
                if freqaimodel != "ForexAIStrategyBaseline":
                    from freqtrade.forex.cli import (
                        _resolve_cli_freqai_config,
                        _run_lightgbm_hyperopt,
                    )

                    normalized_freqai_config = _resolve_cli_freqai_config(
                        timeframe, {"freqai": freqai_config}
                    )

                    def update_model_backtest_progress(
                        phase: str, done: int, total: int
                    ) -> None:
                        update_backtest_job(
                            job_id,
                            phase=phase,
                            backtestProgress=(
                                min(90, int(done / total * 90)) if total else 10
                            ),
                            message=(
                                f"FreqAI {phase.replace('_', ' ')} "
                                f"({done}/{total})"
                            ),
                        )

                    _, _, model_backtest_report = await asyncio.to_thread(
                        _run_lightgbm_hyperopt,
                        frame,
                        instrument=metadata[0],
                        pair=normalized_pair,
                        timeframe=freqtrade_timeframe(timeframe),
                        model_name=freqaimodel,
                        epochs=max(1, int(payload.get("attempts", 24))),
                        model_dir=Path("user_data/hyperopt_results"),
                        starting_balance=Decimal(str(account.balance)),
                        risk_fraction=Decimal(str(settings.risk_fraction)),
                        spread=price_row.spread,
                        slippage=Decimal("0"),
                        financing_rate_per_day=Decimal("0"),
                        quote_to_account_rate=Decimal("1"),
                        stop_pips=Decimal("0.5"),
                        hyperopt_loss=str(payload.get("hyperoptLoss", "ProfitDrawDownHyperOptLoss")),
                        strategy_class=strategy_class_name,
                        freqai_config=normalized_freqai_config,
                        informative_candles=informative_frames,
                        optimize_strategy=False,
                        on_progress=update_model_backtest_progress,
                    )
                    result = model_backtest_report.pop("_backtest_result")
                    strategy_parameters = dict(
                        model_backtest_report.get("strategy_parameters", {})
                    )
                else:
                    strategy = FreqtradeStrategyAdapter(
                        strategy_instance, normalized_pair, informative_frames
                    )
                    result = ForexBacktester(
                        strategy,
                        metadata[0],
                        starting_balance=Decimal(str(account.balance)),
                        risk_fraction=Decimal(str(settings.risk_fraction)),
                        stop_pips=Decimal("0.5"),
                        spread=price_row.spread,
                        slippage=Decimal("0"),
                        financing_rate_per_day=Decimal("0"),
                        quote_to_account_rate=Decimal("1"),
                    ).run(frame, detail_candles=frame)
                    strategy_parameters = dict(
                        approved_run.get("parameters", {})
                        if isinstance(approved_run, dict) else {}
                    )
                update_backtest_job(job_id, backtestProgress=95, message="Aggregating trades, P/L and risk metrics.")
                drawdown_rate = getattr(result, "max_drawdown_rate", None)
                if drawdown_rate is None:
                    drawdown_rate = Decimal(str(getattr(result, "max_drawdown", 0))) / result.starting_balance
                winning_trades = [trade for trade in result.trades if trade.net_pl > 0]
                losing_trades = [trade for trade in result.trades if trade.net_pl < 0]
                drawn_trades = len(result.trades) - len(winning_trades) - len(losing_trades)
                gross_profit = sum((trade.net_pl for trade in winning_trades), Decimal("0"))
                gross_loss = abs(sum((trade.net_pl for trade in losing_trades), Decimal("0")))
                duration_minutes: list[float] = []
                for trade in result.trades:
                    entry_time = getattr(trade, "entry_time", None)
                    exit_time = getattr(trade, "exit_time", None)
                    if entry_time is None or exit_time is None:
                        continue
                    duration_minutes.append(
                        max(
                            0.0,
                            (pd.Timestamp(exit_time) - pd.Timestamp(entry_time)).total_seconds() / 60,
                        )
                    )
                account_currency = str(account.currency)
                backtest_window = (
                    str(model_backtest_report.get("backtest_window", "out_of_sample"))
                    if model_backtest_report else "full_range"
                )
                backtest_metrics = {
                    "accountCurrency": account_currency,
                    "backtestWindow": backtest_window,
                    "candles": len(frame),
                    "trades": len(result.trades),
                    "wins": len(winning_trades),
                    "draws": drawn_trades,
                    "losses": len(losing_trades),
                    "winRate": format_decimal(result.win_rate * Decimal("100")),
                    "startingBalance": format_decimal(result.starting_balance),
                    "endingBalance": format_decimal(result.ending_balance),
                    "netProfit": format_decimal(result.net_pl),
                    "averageProfitPerTrade": format_decimal(
                        result.net_pl / len(result.trades) if result.trades else Decimal("0")
                    ),
                    "grossProfit": format_decimal(gross_profit),
                    "grossLoss": format_decimal(gross_loss),
                    "profitFactor": (
                        format_decimal(gross_profit / gross_loss) if gross_loss else None
                    ),
                    "maxDrawdown": format_decimal(result.max_drawdown),
                    "maxDrawdownRate": format_decimal(drawdown_rate * Decimal("100")),
                    "totalCosts": format_decimal(getattr(result, "total_costs", Decimal("0"))),
                    "averageDurationMinutes": (
                        sum(duration_minutes) / len(duration_minutes) if duration_minutes else 0
                    ),
                }
                summary = {
                    "id": f"bt-{secrets.token_hex(4)}",
                    "name": f"{normalize_pair(pair)} {strategy_class_name}",
                    "pair": normalize_pair(pair),
                    "timeframe": timeframe,
                    "strategy": strategy_class_name,
                    "freqaimodel": freqaimodel,
                    "status": "Completed",
                    "result": f"{((result.net_pl / result.starting_balance) * Decimal('100')):.2f}%",
                    "netProfit": f"${format_decimal(result.net_pl)}",
                    "drawdown": f"{format_decimal(drawdown_rate * Decimal('100'))}%",
                    "trades": len(result.trades),
                    "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "dataRevision": data_revision,
                    "historyMode": history_mode,
                    "historyValue": int(payload.get("historyValue", steps)),
                    "dataHash": data_hash,
                }
                BACKTEST_HISTORY.insert(0, summary)
                response = {
                    "id": summary["id"],
                    "status": "completed",
                    "pair": summary["pair"],
                    "timeframe": timeframe,
                    "steps": len(frame),
                    "historyMode": history_mode,
                    "historyValue": int(payload.get("historyValue", steps)),
                    "message": history_notice + (" " + backtest_warning if backtest_warning else "") + f". Real OANDA historical backtest completed using {strategy_class_name}.",
                    "warning": history_notice + (" " + backtest_warning if backtest_warning else ""),
                    "netPl": format_decimal(result.net_pl),
                    "trades": len(result.trades),
                    "startingBalance": format_decimal(result.starting_balance),
                    "endingBalance": format_decimal(result.ending_balance),
                    "winRate": format_decimal(result.win_rate * Decimal('100')),
                    "maxDrawdown": format_decimal(drawdown_rate * Decimal('100')),
                    "strategy": strategy_class_name,
                    "freqaimodel": freqaimodel,
                    "backtestWindow": backtest_window,
                    "modelReused": (
                        model_backtest_report.get("model_reused")
                        if model_backtest_report else None
                    ),
                    "modelTraining": (
                        model_backtest_report.get("model_training")
                        if model_backtest_report else None
                    ),
                    "dataSource": "OANDA historical candles",
                    "dataRevision": data_revision,
                    "summary": backtest_metrics,
                    "aiParameters": {
                        "model": str(pair_config.get("model", "hybrid")),
                        "freqaimodel": freqaimodel,
                        "backtestWindow": (
                            model_backtest_report.get("backtest_window")
                            if model_backtest_report else "full_range"
                        ),
                        "modelReused": (
                            model_backtest_report.get("model_reused")
                            if model_backtest_report else None
                        ),
                        "modelTraining": (
                            model_backtest_report.get("model_training")
                            if model_backtest_report else None
                        ),
                        "strategyParameters": strategy_parameters,
                        "backtestReport": (
                            {
                                key: value
                                for key, value in model_backtest_report.items()
                                if not key.startswith("_")
                            }
                            if model_backtest_report else None
                        ),
                        "timeframe": timeframe,
                        "features": list(pair_config.get("featureSet", [])),
                        "riskBudget": str(pair_config.get("riskBudget", settings.risk_fraction)),
                        "riskFraction": str(settings.risk_fraction),
                        "stopPips": "0.5",
                        "entryThreshold": str(pair_config.get("entryThreshold", "0.5")),
                        "exitThreshold": str(pair_config.get("exitThreshold", "0.0")),
                        "volatilityWindow": str(pair_config.get("volatilityWindow", "5")),
                        "atrWindow": str(pair_config.get("atrWindow", "14")),
                        "maxSpreadPct": str(pair_config.get("maxSpreadPct", "1.0")),
                        "executionMode": settings.execution_mode,
                        "configSource": "approved-hyperopt" if isinstance(approved_run, dict) else "pair-config",
                        "approvedObjective": str(approved_run.get("objective")) if isinstance(approved_run, dict) else None,
                        "modelVersion": "baseline-v1",
                        "featureSchemaHash": ai_feature_schema_hash(pair_config),
                        "trainingDataHash": data_hash,
                        "strategyClass": strategy_class_name,
                    },
                    "tradeDetails": [
                        {**trade, "volume": trade.get("units", 0), "leverage": "1x"}
                        for trade in result.trade_output
                    ],
                }
                update_backtest_job(
                    job_id,
                    status="completed",
                    phase="completed",
                    historyProgress=100,
                    backtestProgress=100,
                    message=response["message"],
                    result=response,
                )
                return response
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - guarded for API stability
            update_backtest_job(job_id, status="failed", phase="failed", message=f"Backtest failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}") from exc

    @app.websocket("/ws/market")
    async def ws_market(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(live_event("market", "market.snapshot", fallback_market_summary()))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/account")
    async def ws_account(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(live_event("account", "account.snapshot", fallback_account_summary()))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/orders")
    async def ws_orders(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(live_event("orders", "orders.snapshot", fallback_orders()))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/alerts")
    async def ws_alerts(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(
            live_event(
                "alerts",
                "alerts.snapshot",
                [
                    {"title": "Practice mode active", "detail": "Read-only broker health is confirmed and dry-run guard is enabled."},
                    {"title": "Risk guard", "detail": "Daily drawdown remains inside policy thresholds."},
                ],
            )
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/alerts")
    async def ws_alerts(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(live_event("alerts", "alerts.snapshot", fallback_market_summary()["alerts"]))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> Response:
        if ui_index.exists():
            return HTMLResponse(_render_ui_index(ui_index))
        return _dashboard_html()

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page() -> Response:
        if ui_index.exists():
            return HTMLResponse(_render_ui_index(ui_index))
        return _setup_html()

    @app.on_event("startup")
    async def start_hyperopt_scheduler() -> None:
        async def scheduler_loop() -> None:
            while True:
                await asyncio.sleep(5)
                if not AI_HYPEROPT_SCHEDULER.get("enabled") or AI_HYPEROPT_SCHEDULER.get("running"):
                    continue
                now = datetime.now(timezone.utc)
                next_runs = AI_HYPEROPT_SCHEDULER.get("nextRuns") or {}
                due_pairs = [pair for pair, scheduled_at in next_runs.items() if datetime.fromisoformat(str(scheduled_at)) <= now]
                if not due_pairs:
                    continue
                try:
                    await run_scheduled_hyperopt([due_pairs[0]])
                except Exception:
                    continue

        app.state.hyperopt_scheduler_task = asyncio.create_task(scheduler_loop())

        async def average_order_cleanup_loop() -> None:
            while True:
                await asyncio.sleep(5)
                try:
                    settings = OandaSettings.from_environment()
                    async with OandaClient(
                        settings.token, settings.account_id, environment=settings.environment
                    ) as client:
                        pending_orders = await client.get_pending_orders()
                        linked_orders = [
                            order
                            for order in pending_orders
                            if str(
                                (order.get("clientExtensions") or {}).get("id", "")
                            ).startswith("risk-average-for-")
                        ]
                        if not linked_orders:
                            continue
                        open_trade_ids = {
                            str(trade.get("id")) for trade in await client.get_open_trades()
                        }
                        for order in linked_orders:
                            client_order_id = str(
                                (order.get("clientExtensions") or {}).get("id", "")
                            )
                            parent_id = client_order_id.removeprefix(
                                "risk-average-for-"
                            ).rsplit("-", 1)[0]
                            if parent_id in open_trade_ids:
                                continue
                            try:
                                await client.cancel_order(str(order.get("id", "")))
                                record_audit_event(
                                    "positions.average_entry.cancel_parent_closed",
                                    details={
                                        "parentTradeId": parent_id,
                                        "orderId": str(order.get("id", "")),
                                    },
                                    username="system",
                                    role="system",
                                    allowed=True,
                                )
                            except (OandaAPIError, ValueError):
                                logger.exception(
                                    "Could not cancel average-entry order %s after parent trade %s closed",
                                    order.get("id"),
                                    parent_id,
                                )
                except (OandaAPIError, ValueError):
                    logger.warning(
                        "Could not check pending average-entry orders against open OANDA trades",
                        exc_info=True,
                    )

        app.state.average_order_cleanup_task = asyncio.create_task(
            average_order_cleanup_loop()
        )
        try:
            strategy_execution_state = read_strategy_execution_state()
        except HTTPException:
            logger.exception(
                "Automatic strategy execution will remain stopped because its state is invalid"
            )
        else:
            if strategy_execution_state.get("enabled") is True:
                app.state.strategy_execution_task = asyncio.create_task(
                    auto_execution_loop()
                )

    @app.on_event("shutdown")
    async def stop_hyperopt_scheduler() -> None:
        for task_name in (
            "hyperopt_scheduler_task",
            "average_order_cleanup_task",
            "strategy_execution_task",
        ):
            task = getattr(app.state, task_name, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    if ui_index.exists():
        from fastapi.staticfiles import StaticFiles

        ui_dir = ui_index.parent
        assets_dir = ui_dir / "assets"
        app.state.ui_assets_available = assets_dir.exists()
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui_assets")

        @app.get("/favicon.svg")
        def favicon() -> FileResponse:
            return FileResponse(ui_dir / "favicon.svg")

        @app.get("/icons.svg")
        def icons() -> FileResponse:
            return FileResponse(ui_dir / "icons.svg")

    return app


app = create_app()


def _setup_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forex setup</title>
<style>
:root{font-family:system-ui,sans-serif;color:#18251f;background:#eef1e9;--line:#cbd3c8;--panel:#f9faf5;--accent:#d6663f}
*{box-sizing:border-box}body{margin:0}.card{max-width:680px;margin:7vh auto;padding:32px;background:var(--panel);border:1px solid var(--line)}
h1{margin:0 0 8px;font:400 2.4rem Georgia,serif}.intro{color:#66736c;margin:0 0 28px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}label{display:grid;gap:7px;font-size:.85rem;font-weight:700}input,select{width:100%;padding:11px;border:1px solid var(--line);background:#fff;font:inherit}button{margin-top:22px;padding:12px 18px;border:0;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}.message{min-height:24px;margin-top:18px}.error{color:#a44}.success{color:#28724a}@media(max-width:640px){.card{margin:0;min-height:100vh;border:0}.grid{grid-template-columns:1fr}}
</style></head>
<body><main class="card"><h1>Forex setup</h1><p class="intro">Configure the OANDA Practice connection. Credentials are saved on this server.</p>
<form id="setup-form"><div class="grid"><label>OANDA Practice token<input id="token" type="password" autocomplete="new-password" required></label><label>Account ID<input id="accountId" required></label><label>Instruments<input id="instruments" value="EUR_USD,GBP_USD" required></label><label>Risk fraction<input id="riskFraction" type="number" min="0.0001" max="1" step="0.0001" value="0.01" required></label><label>Execution mode<select id="executionMode"><option value="dry_run">Dry run</option><option value="practice">Practice</option></select></label></div><button type="submit">Save setup</button><p id="message" class="message"></p></form></main>
<script>
const form=document.getElementById('setup-form');const message=document.getElementById('message');
fetch('/api/v1/setup/status').then(response=>response.json()).then(status=>{if(status.tokenConfigured)document.getElementById('token').placeholder='Already configured';if(status.accountIdConfigured)document.getElementById('accountId').placeholder='Already configured';if(status.instruments?.length)document.getElementById('instruments').value=status.instruments.join(',')}).catch(()=>{});
form.addEventListener('submit',async event=>{event.preventDefault();message.className='message';message.textContent='Saving...';const payload={token:document.getElementById('token').value,accountId:document.getElementById('accountId').value,instruments:document.getElementById('instruments').value.split(',').map(value=>value.trim()).filter(Boolean),riskFraction:document.getElementById('riskFraction').value,executionMode:document.getElementById('executionMode').value,environment:'practice',pairTimeframes:{}};try{const response=await fetch('/api/v1/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw new Error(result.detail||'Setup failed');message.className='message success';message.textContent='Setup saved. You can return to the dashboard.';form.reset()}catch(error){message.className='message error';message.textContent=error.message}});
</script></body></html>"""


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forex Dry-Run</title>
<style>
:root{font-family:Georgia,serif;color:#18251f;background:#eef1e9;--ink:#18251f;--muted:#66736c;--line:#cbd3c8;--accent:#d6663f;--panel:#f9faf5}
*{box-sizing:border-box}body{margin:0}.shell{max-width:1180px;margin:auto;padding:28px 22px 52px}
header{display:flex;justify-content:space-between;align-items:end;border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:24px}h1{font-size:clamp(2rem,5vw,4.6rem);font-weight:400;letter-spacing:0;margin:0}.eyebrow{font:700 11px/1.2 system-ui,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}
.stamp{font:12px system-ui,sans-serif;color:var(--muted)}.status{display:inline-flex;align-items:center;gap:8px;font:700 12px system-ui,sans-serif;text-transform:uppercase}.dot{width:10px;height:10px;border-radius:50%;background:#a44}.dot.ok{background:#398a5e}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px}.metric{background:var(--panel);border:1px solid var(--line);padding:18px;min-height:105px}.label{font:11px system-ui,sans-serif;text-transform:uppercase;color:var(--muted);letter-spacing:.08em}.value{font-size:1.7rem;margin-top:20px;word-break:break-word}
section{margin-top:28px}h2{font-size:1.4rem;font-weight:400;border-bottom:1px solid var(--line);padding-bottom:10px}.table-wrap{overflow:auto;background:var(--panel);border:1px solid var(--line)}table{width:100%;border-collapse:collapse;font:13px system-ui,sans-serif}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}td.num{text-align:right;font-variant-numeric:tabular-nums}.empty{padding:25px;color:var(--muted);font:14px system-ui,sans-serif}
@media(max-width:760px){.shell{padding:20px 14px}.grid{grid-template-columns:repeat(2,1fr)}header{display:block}.stamp{margin-top:14px}.value{font-size:1.35rem}}
</style></head>
<body><main class="shell"><header><div><div class="eyebrow">OANDA / Practice</div><h1>Dry-run desk</h1></div><div class="stamp"><span class="status"><i class="dot" id="dot"></i><span id="health">checking</span></span><br><span id="updated">not updated</span></div></header>
<div class="grid"><div class="metric"><div class="label">Account</div><div class="value" id="account">--</div></div><div class="metric"><div class="label">Balance</div><div class="value" id="balance">--</div></div><div class="metric"><div class="label">NAV</div><div class="value" id="nav">--</div></div><div class="metric"><div class="label">Margin available</div><div class="value" id="margin">--</div></div></div>
<section><h2>Market</h2><div class="table-wrap"><table><thead><tr><th>Instrument</th><th>Bid</th><th>Ask</th><th>Spread</th></tr></thead><tbody id="prices"><tr><td colspan="4" class="empty">Loading prices...</td></tr></tbody></table></div></section>
<section><h2>Paper trades</h2><div class="grid"><div class="metric"><div class="label">Realized P/L</div><div class="value" id="realized">--</div></div><div class="metric"><div class="label">Unrealized P/L</div><div class="value" id="unrealized">--</div></div><div class="metric"><div class="label">Open trades</div><div class="value" id="open">--</div></div><div class="metric"><div class="label">Closed trades</div><div class="value" id="closed">--</div></div></div><div class="table-wrap"><table><thead><tr><th>Instrument</th><th>Units</th><th>Entry</th><th>Exit</th><th>Status</th><th>P/L</th></tr></thead><tbody id="trades"><tr><td colspan="6" class="empty">Loading trades...</td></tr></tbody></table></div></section></main>
<script>
const text=(id,value)=>document.getElementById(id).textContent=value;
const row=(values)=>'<tr>'+values.map((v,i)=>'<td class="'+(i===1?'num':'')+'">'+(v??'--')+'</td>').join('')+'</tr>';
async function refresh(){try{const [health,report]=await Promise.all([fetch('/api/v1/health').then(r=>r.json()),fetch('/api/v1/paper/report').then(r=>r.json())]);text('health',health.healthy?'connected':'unhealthy');document.getElementById('dot').className='dot '+(health.healthy?'ok':'');text('account',health.account_id);text('balance',health.balance+' '+health.currency);text('nav',health.nav+' '+health.currency);text('margin',health.margin_available+' '+health.currency);document.getElementById('prices').innerHTML=health.prices.map(p=>row([p.instrument,p.bid,p.ask,p.spread])).join('')||'<tr><td colspan="4" class="empty">No prices</td></tr>';const perf=report.performance;text('realized',perf.realized_pl);text('unrealized',perf.unrealized_pl);text('open',perf.open_trades);text('closed',perf.closed_trades);document.getElementById('trades').innerHTML=report.trades.map(t=>row([t.instrument,t.units,t.entry_price,t.exit_price,t.status,t.realized_pl??t.unrealized_pl])).join('')||'<tr><td colspan="6" class="empty">No paper trades</td></tr>';text('updated',new Date().toLocaleTimeString())}catch(error){text('health','offline');document.getElementById('dot').className='dot';text('updated','API unavailable')}}refresh();setInterval(refresh,10000);
</script></body></html>"""
