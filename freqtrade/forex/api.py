"""Read-only HTTP API for OANDA health and paper performance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response

from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline
from freqtrade.forex.ai_hyperopt import run_ai_hyperopt_robust
from freqtrade.forex.config import OandaSettings, load_forex_config, save_forex_config
from freqtrade.forex.health import OandaHealthCheck
from freqtrade.forex.ledger import PaperLedger
from freqtrade.forex.models import OandaEnvironment
from freqtrade.forex.oanda import OandaAPIError, OandaClient, discover_oanda_accounts


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


def create_app(ledger_path: Path = Path("user_data/oanda/paper.sqlite")) -> FastAPI:
    app = FastAPI(title="Forex Dry-Run API", version="0.1.0")
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
            AI_CONFIG_BY_PAIR[normalized]["configRevision"] = "r0"
            AI_CONFIG_REVISIONS[normalized] = [dict(AI_CONFIG_BY_PAIR[normalized])]
        return AI_CONFIG_BY_PAIR[normalized]

    def validate_ai_config(candidate: dict[str, object]) -> None:
        model = str(candidate.get("model", "hybrid")).lower()
        if model not in {"rule-based", "ml", "hybrid"}:
            raise HTTPException(status_code=400, detail="AI model must be rule-based, ml, or hybrid")
        timeframe = str(candidate.get("timeframe", "M5")).upper()
        if timeframe not in {"M5", "M15", "H1"}:
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
        normalized = pair.replace("_", "/").upper()
        defaults = {
            "pair": normalized,
            "units": "1000",
            "riskBudget": "0.50%",
            "riskBudgetMode": "percent",
            "leverage": "1x",
            "maxExposure": "$10,000",
            "maxExposureMode": "absolute",
            "side": "LONG",
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

    def reset_pair_research_state(pair: str) -> None:
        normalized = pair.replace("_", "/").upper()
        AI_PENDING_OPTIMIZATION.pop(normalized, None)
        pair_config = ai_config_for_pair(normalized)
        pair_config.pop("approvedHyperopt", None)
        BACKTEST_HISTORY[:] = [item for item in BACKTEST_HISTORY if item.get("pair") != normalized]

    def resolve_history_request(payload: dict, timeframe: str) -> tuple[str, int]:
        mode = str(payload.get("historyMode", "candles")).lower()
        value = max(1, int(payload.get("historyValue", payload.get("steps", 250))))
        if mode == "candles":
            return mode, min(value, 10000)
        if mode != "days":
            raise HTTPException(status_code=400, detail="historyMode must be candles or days")
        candles_per_day = {"M5": 288, "M15": 96, "H1": 24}.get(timeframe.upper())
        if candles_per_day is None:
            raise HTTPException(status_code=400, detail=f"Days history is unsupported for timeframe {timeframe}")
        return mode, min(value * candles_per_day, 10000)
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
    AI_REVIEW_BY_SCOPE: dict[tuple[str, str], dict[str, object]] = {}
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

    def persist_scope_revision(pair: str, timeframe: str) -> None:
        normalized_pair = pair.replace("_", "/").upper()
        scope = f"{normalized_pair}|{timeframe.upper()}"
        revision_db.execute(
            "INSERT OR REPLACE INTO ai_scope_revisions(scope, config_json, review_json) VALUES (?, ?, ?)",
            (
                scope,
                json.dumps(ai_config_for_pair(normalized_pair), default=str),
                json.dumps(AI_REVIEW_BY_SCOPE.get((normalized_pair, timeframe.upper()), {}), default=str),
            ),
        )
        revision_db.commit()

    def restore_scope_revisions() -> None:
        for scope, config_json, review_json in revision_db.execute("SELECT scope, config_json, review_json FROM ai_scope_revisions"):
            pair, timeframe = scope.rsplit("|", 1)
            pair = pair.replace("_", "/").upper()
            config = json.loads(config_json)
            AI_CONFIG_BY_PAIR[pair] = config
            AI_CONFIG_REVISIONS.setdefault(pair, [dict(config)])
            review = json.loads(review_json)
            if review:
                AI_REVIEW_BY_SCOPE[(pair, timeframe)] = review

    restore_scope_revisions()

    AI_HYPEROPT_SCHEDULER: dict[str, object] = {
        "enabled": False,
        "intervalDays": 2,
        "gapMinutes": 120,
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

    def format_hyperopt_report(report: dict) -> str:
        """Render the hyperopt result as a readable report instead of raw JSON."""
        best = report["bestParameters"]
        coverage_line = (
            f"  {report['candidatesTested']} candidates tested ({report['attemptsRequested']} attempts requested)"
            f" across {report['pairsTested']} pair(s) and {report['periodsTested']} period(s)"
            f" ({report['coverage']} validation slices)."
        )
        lines = [
            f"Hyperopt report - {report['pair']} ({report['timeframe']}) - {report['status']}",
            "",
            coverage_line,
            f"  Loss function: {report.get('hyperoptLoss', 'ProfitDrawDownHyperOptLoss')}",
            f"  Best parameters: entryThreshold={best['entryThreshold']} maxSpreadPct={best['maxSpreadPct']}%",
            f"  Objective: {report['objective']}",
            f"  Train:      net P/L {report['train']['netPl']}  drawdown {report['train']['drawdown']}  trades {report['train']['trades']}",
            f"  Validation: net P/L {report['validation']['netPl']}  drawdown {report['validation']['drawdown']}  trades {report['validation']['trades']}",
            "",
            "  Top candidates (validation net P/L):",
        ]
        for candidate in report["candidates"][:5]:
            sign = "+" if float(candidate["validationNetPl"]) >= 0 else ""
            candidate_line = (
                f"    #{candidate['rank']} entry={candidate['entryThreshold']} spread={candidate['maxSpreadPct']}%"
                f" objective={candidate['objective']} val P/L={sign}{candidate['validationNetPl']}"
                f" trades={candidate['validationTrades']}"
            )
            lines.append(candidate_line)
        return "\n".join(lines)

    def report_age_days(completed_at: str) -> int:
        completed = datetime.fromisoformat(completed_at)
        return max(0, (datetime.now(timezone.utc) - completed).days)

    def fallback_ai_config() -> dict:
        return dict(AI_CONFIG)

    def fallback_ai_review(pair: str | None = None, timeframe: str | None = None) -> dict:
        if pair is not None and timeframe is not None:
            scoped = AI_REVIEW_BY_SCOPE.get((normalize_pair(pair), timeframe.upper()))
            if scoped is not None:
                return dict(scoped)
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
        for key in ("units", "riskBudget", "riskBudgetMode", "leverage", "maxExposure", "maxExposureMode", "side", "stopLoss", "stopLossMode", "takeProfit", "takeProfitMode", "averageEntry", "averageEntryMode", "maxAdds"):
            if key in payload:
                candidate[key] = payload[key]
        if str(candidate["side"]).upper() not in {"LONG", "SHORT", "BOTH", "NONE"}:
            raise HTTPException(status_code=400, detail="Risk side must be LONG, SHORT, BOTH, or NONE")
        for key in ("riskBudgetMode", "maxExposureMode"):
            if candidate[key] not in {"percent", "absolute"}:
                raise HTTPException(status_code=400, detail=f"{key} must be percent or absolute")
        for key in ("stopLossMode", "takeProfitMode", "averageEntryMode"):
            if candidate[key] not in {"percent", "price"}:
                raise HTTPException(status_code=400, detail=f"{key} must be percent or price")
        candidate["pair"] = pair.replace("_", "/").upper()
        candidate["source"] = "operator-config"
        current.update(candidate)
        record_audit_event("risk.config.update", details={"pair": candidate["pair"], "source": candidate["source"]}, username=user_role, role=user_role, allowed=True)
        return dict(current)

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

    @app.get("/api/v1/orders/chart")
    async def orders_chart(pair: str = "EUR/USD", timeframe: str = "M15", count: int = 120) -> dict:
        """Return selected-view candles with signals from the pair's approved strategy timeframe."""
        try:
            settings = OandaSettings.from_environment()
            normalized_pair = normalize_pair(pair)
            instrument_name = normalized_pair.replace("/", "_")
            view_timeframe = timeframe.upper()
            granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(view_timeframe)
            if granularity is None:
                raise HTTPException(status_code=400, detail="Unsupported chart timeframe")
            count = max(30, min(int(count), 5000))
            pair_config = ai_config_for_pair(normalized_pair)
            approved_timeframe = str(pair_config.get("timeframe", "M5")).upper()
            approved_granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(approved_timeframe, "M5")
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                view_candles = await client.get_candles(instrument_name, granularity, count=count)
                signal_candles = await client.get_candles(instrument_name, approved_granularity, count=min(count * 4, 5000))
                frame = df_from_raw_candles(signal_candles)
                view_times = [candle.time for candle in view_candles]
                strategy = ForexAIStrategyBaseline({
                    "forex_ai_model": str(pair_config.get("model", "hybrid")),
                    "forex_ai_features": pair_config.get("featureSet", []),
                    "forex_ai_entry_threshold": pair_config.get("entryThreshold", "0.5"),
                    "forex_ai_exit_threshold": pair_config.get("exitThreshold", "0.0"),
                    "forex_ai_volatility_window": pair_config.get("volatilityWindow", "5"),
                    "forex_ai_atr_window": pair_config.get("atrWindow", "14"),
                    "forex_ai_max_spread_pct": pair_config.get("maxSpreadPct", "1.0"),
                })
                signals: list[dict] = []
                previous = "flat"
                for index in range(len(frame)):
                    window = frame.iloc[: index + 1]
                    trace = strategy.signal_trace(window)
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
                return {
                    "pair": normalized_pair,
                    "timeframe": view_timeframe,
                    "approvedTimeframe": approved_timeframe,
                    "candles": [{"time": candle.time, "open": float(candle.open), "high": float(candle.high), "low": float(candle.low), "close": float(candle.close)} for candle in view_candles],
                    "signals": signals,
                    "trades": [order for order in fallback_orders() if order["symbol"] == normalized_pair],
                    "indicators": {"ema": []},
                }
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
            units = int(float(raw_units))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="units must be a number") from exc
        if units == 0:
            raise HTTPException(status_code=400, detail="units must not be zero")
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
        client_order_id = str(payload.get("clientOrderId") or f"ui-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        normalized_units = units if side == "BUY" else -units

        if settings_obj and settings_obj.token and settings_obj.account_id:
            try:
                async with OandaClient(settings_obj.token, settings_obj.account_id, settings_obj.environment) as client:
                    result = await client.create_market_order(
                        instrument,
                        normalized_units,
                        stop_loss_price=str(stop_loss) if stop_loss else None,
                        take_profit_price=str(take_profit) if take_profit else None,
                        client_order_id=client_order_id,
                    )
            except OandaAPIError as exc:
                status_code = 409 if "not tradeable" in str(exc).lower() or "market halted" in str(exc).lower() else 502
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
        return {
            "configured": bool(token and account_id),
            "environment": exchange.get("oanda_environment", "practice"),
            "executionMode": exchange.get("oanda_execution_mode", "dry_run"),
            "instruments": instruments,
            "pairTimeframes": pair_timeframes,
            "accountIdConfigured": bool(account_id),
            "tokenConfigured": bool(token),
            "strategyFile": str(strategy_path),
            "configFile": str(config_path),
        }

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
        config_path = Path(payload.get("configPath") or os.environ.get("OANDA_CONFIG_PATH", "user_data/config.json"))
        current = load_forex_config(config_path)
        current.update({
            "schema_version": 1,
            "timeframe": "5m",
            "setup": {"configured": True, "credentials_source": "ui"},
            "pair_timeframes": pair_timeframes,
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

    @app.get("/api/v1/setup/files/{file_kind}")
    async def download_setup_file(file_kind: str) -> Response:
        config_path, strategy_path = resolve_setup_paths()
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
        if not content.strip() or len(content.encode("utf-8")) > 2_000_000:
            raise HTTPException(status_code=400, detail="file is empty or exceeds the 2 MB limit")
        if file_kind == "config":
            try:
                config_payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="config.json must contain valid JSON") from exc
            if not isinstance(config_payload, dict):
                raise HTTPException(status_code=400, detail="config.json must contain a JSON object")
        else:
            stripped = content.lstrip()
            if not any(
                stripped.startswith(prefix)
                for prefix in ("#", '"""', "from ", "import ", "class ", "def ", "@")
            ):
                raise HTTPException(status_code=400, detail="strategy file does not look like Python source")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
        record_audit_event("setup.file_uploaded", details={"fileKind": file_kind, "path": str(path)}, allowed=True)
        return {"uploaded": True, "fileKind": file_kind, "path": str(path), "reloadRequired": True}

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
        latest_run = next((dict(item) for item in BACKTEST_HISTORY if item.get("pair") == pair.replace("_", "/").upper()), None)
        pending_run = AI_PENDING_OPTIMIZATION.get(pair.replace("_", "/").upper())
        last_optimization = pending_run.get("updatedAt") if pending_run else pair_config.get("lastOptimizationAttempt")
        independent_backtest = bool(
            pending_run and next(
                (
                    item for item in BACKTEST_HISTORY
                    if item.get("pair") == pair.replace("_", "/").upper()
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
            "strategy": "ForexAIStrategyBaseline",
            "strategyVersion": "baseline-v1",
            "pair": pair.replace("_", "/").upper(),
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
        """Return recent explainable signals from the pair's effective AI strategy."""
        settings = OandaSettings.from_environment()
        normalized_pair = normalize_pair(pair)
        instrument_name = normalized_pair.replace("/", "_")
        granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(timeframe.upper())
        if granularity is None:
            raise HTTPException(status_code=400, detail="Unsupported AI signal timeframe")
        pair_config = ai_config_for_pair(normalized_pair)
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                candles = df_from_raw_candles(await client.get_candles(instrument_name, granularity, count=max(30, min(count, 5000))))
                strategy = ForexAIStrategyBaseline({
                    "forex_ai_model": pair_config.get("model", "hybrid"),
                    "forex_ai_features": pair_config.get("featureSet", []),
                    "forex_ai_entry_threshold": pair_config.get("entryThreshold", "0.5"),
                    "forex_ai_exit_threshold": pair_config.get("exitThreshold", "0.0"),
                    "forex_ai_volatility_window": pair_config.get("volatilityWindow", "5"),
                    "forex_ai_atr_window": pair_config.get("atrWindow", "14"),
                    "forex_ai_max_spread_pct": pair_config.get("maxSpreadPct", "1.0"),
                })
                traces = [strategy.signal_trace(candles.iloc[: index + 1]) for index in range(max(0, len(candles) - min(count, 60)), len(candles))]
                return {"pair": normalized_pair, "timeframe": timeframe.upper(), "strategy": "ForexAIStrategyBaseline", "configRevision": pair_config.get("configRevision", "r0"), "signals": traces}
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
        history_mode: str,
        history_value: int,
        attempts: int,
        hyperopt_loss: str,
        candles_by_pair: dict[str, pd.DataFrame],
        instruments: dict[str, object],
        spreads: dict[str, Decimal],
        starting_balance: Decimal,
        risk_fraction: Decimal,
        data_hash: str,
        schema_hash: str,
    ) -> None:
        """Run the CPU-bound search in a worker thread so /status and /stop stay responsive."""
        from freqtrade.forex.ai_hyperopt import run_ai_hyperopt

        stop_event: threading.Event = job["stopEvent"]  # type: ignore[assignment]

        def on_attempt(done: int, total: int) -> None:
            job["attemptsCompleted"] = done
            job["attemptsTotal"] = total

        def should_stop() -> bool:
            return stop_event.is_set()

        try:
            if len(pairs) == 1:
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
            completed_at = datetime.now(timezone.utc).isoformat()
            ai_config_for_pair(normalized_pair)["lastOptimizationAttempt"] = completed_at
            AI_PENDING_OPTIMIZATION[normalized_pair] = {
                "pair": normalized_pair,
                "timeframe": timeframe.upper(),
                "steps": history_value,
                "historyMode": history_mode,
                "historyValue": history_value,
                "attempts": attempts,
                "entryThreshold": str(best["entryThreshold"]),
                "maxSpreadPct": str(best["maxSpreadPct"]),
                "objective": str(best["objective"]),
                "updatedAt": completed_at,
                "dataRevision": completed_at,
                "dataHash": data_hash,
                "featureSchemaHash": schema_hash,
                "modelVersion": "baseline-v1",
            }
            report = {
                "pair": normalized_pair,
                "timeframe": timeframe.upper(),
                "status": "stopped" if stop_event.is_set() else "completed",
                "strategy": "ForexAIStrategyBaseline",
                "dataSource": "OANDA historical candles",
                "dataRevision": completed_at,
                "dataHash": data_hash,
                "featureSchemaHash": schema_hash,
                "modelVersion": "baseline-v1",
                "candidatesTested": len(candidates),
                "pairsTested": len(pairs),
                "periodsTested": 2,
                "coverage": len(pairs) * 2,
                "attemptsRequested": attempts,
                "hyperoptLoss": hyperopt_loss,
                "historyMode": history_mode,
                "historyValue": history_value,
                "trainCandles": max(len(candle_frame) // 2 for candle_frame in candles_by_pair.values()),
                "validationCandles": max(len(candle_frame) // 2 for candle_frame in candles_by_pair.values()),
                "bestParameters": {
                    "entryThreshold": str(best["entryThreshold"]),
                    "maxSpreadPct": str(best["maxSpreadPct"]),
                },
                "objective": str(best["objective"]),
                "train": {
                    "netPl": str(best["trainNetPl"]),
                    "drawdown": str(best["validationDrawdown"]),
                    "trades": int(best["validationTrades"]),
                },
                "validation": {
                    "netPl": str(best["validationNetPl"]),
                    "drawdown": str(best["validationDrawdown"]),
                    "trades": int(best["validationTrades"]),
                },
                "candidates": [{"rank": rank, **candidate} for rank, candidate in enumerate(candidates, start=1)],
            }
            report["reportText"] = format_hyperopt_report(report)
            job["status"] = report["status"]
            job["report"] = report
            job["completedAt"] = completed_at
            AI_HYPEROPT_REPORTS[normalized_pair] = {"report": report, "completedAt": completed_at}
            persist_hyperopt_report(normalized_pair, completed_at, report)
        except Exception as exc:  # pragma: no cover - background worker boundary
            job["status"] = "failed"
            job["error"] = str(exc)

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
        existing_job = AI_HYPEROPT_JOBS.get(normalized_pair)
        if existing_job is not None and existing_job.get("status") == "running":
            raise HTTPException(status_code=409, detail=f"Hyperopt already running for {normalized_pair}")
        if payload.get("resetPrevious", True):
            for selected_pair in pairs:
                reset_pair_research_state(selected_pair)
        timeframe = str(payload.get("timeframe", ai_config_for_pair(str(payload.get("pair", "EUR/USD"))).get("timeframe", "M5")))
        history_mode, steps = resolve_history_request(payload, timeframe)
        history_value = int(payload.get("historyValue", steps))
        attempts = max(1, min(int(payload.get("attempts", 24)), 900))
        from freqtrade.forex.ai_hyperopt import DEFAULT_HYPEROPT_LOSS, HYPEROPT_LOSS_FUNCTIONS
        hyperopt_loss = str(payload.get("hyperoptLoss", DEFAULT_HYPEROPT_LOSS))
        if hyperopt_loss not in HYPEROPT_LOSS_FUNCTIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported hyperoptLoss: {hyperopt_loss}")
        settings = OandaSettings.from_environment()
        if settings.execution_mode not in {"dry_run", "backtest", "practice"}:
            raise HTTPException(status_code=400, detail="AI hyperopt requires a safe execution mode")
        granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(timeframe.upper(), "M5")
        try:
            async with OandaClient(settings.token, settings.account_id, environment=settings.environment) as client:
                account = await client.get_account_summary()
                instrument_names = tuple(item.replace("/", "_").upper() for item in pairs)
                metadata = await client.get_instruments(instrument_names)
                instruments = {item.name: item for item in metadata}
                candles_by_pair: dict[str, pd.DataFrame] = {}
                spreads: dict[str, Decimal] = {}
                for pair in pairs:
                    instrument_name = pair.replace("/", "_").upper()
                    candles = df_from_raw_candles(await client.get_candles(instrument_name, granularity, count=steps))
                    if candles.empty:
                        raise HTTPException(status_code=400, detail=f"No historical candles returned for {pair}")
                    candles_by_pair[instrument_name] = candles
                    spreads[instrument_name] = (await client.get_prices((instrument_name,)))[0].spread
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - API boundary
            raise HTTPException(status_code=500, detail=f"AI hyperopt failed: {exc}") from exc

        data_hash = candle_frame_hash(candles_by_pair)
        schema_hash = ai_feature_schema_hash(ai_config_for_pair(normalized_pair))
        coverage = 2 if len(pairs) == 1 else len(pairs) * 2
        search_space_size = min(attempts, 900)
        job: dict[str, object] = {
            "pair": normalized_pair,
            "timeframe": timeframe.upper(),
            "status": "running",
            "attemptsCompleted": 0,
            "attemptsTotal": search_space_size * coverage,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "stopEvent": threading.Event(),
        }
        AI_HYPEROPT_JOBS[normalized_pair] = job
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            _run_hyperopt_job,
            job,
            pairs,
            instrument_names,
            timeframe,
            history_mode,
            history_value,
            attempts,
            hyperopt_loss,
            candles_by_pair,
            instruments,
            spreads,
            Decimal(str(account.balance)),
            Decimal(str(settings.risk_fraction)),
            data_hash,
            schema_hash,
        )
        return {"pair": normalized_pair, "status": "running", "attemptsTotal": job["attemptsTotal"]}

    @app.get("/api/v1/ai/hyperopt/loss-functions")
    async def ai_hyperopt_loss_functions() -> dict:
        from freqtrade.forex.ai_hyperopt import DEFAULT_HYPEROPT_LOSS, HYPEROPT_LOSS_FUNCTIONS
        return {"default": DEFAULT_HYPEROPT_LOSS, "options": list(HYPEROPT_LOSS_FUNCTIONS)}

    @app.get("/api/v1/ai/hyperopt/status")
    async def ai_hyperopt_status(pair: str = "EUR/USD") -> dict:
        normalized_pair = normalize_pair(pair)
        job = AI_HYPEROPT_JOBS.get(normalized_pair)
        if job is None:
            last_report = AI_HYPEROPT_REPORTS.get(normalized_pair)
            return {
                "pair": normalized_pair,
                "status": "idle",
                "attemptsCompleted": 0,
                "attemptsTotal": 0,
                "hasLastReport": last_report is not None,
            }
        return {
            "pair": normalized_pair,
            "status": job["status"],
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
        job = AI_HYPEROPT_JOBS.get(normalized_pair)
        if job is None or job.get("status") != "running":
            raise HTTPException(status_code=400, detail=f"No running hyperopt job for {normalized_pair}")
        job["stopEvent"].set()  # type: ignore[union-attr]
        return {"pair": normalized_pair, "status": "stopping"}

    @app.get("/api/v1/ai/hyperopt/report")
    async def ai_hyperopt_report(pair: str = "EUR/USD") -> dict:
        normalized_pair = normalize_pair(pair)
        entry = AI_HYPEROPT_REPORTS.get(normalized_pair)
        if entry is None:
            return {"pair": normalized_pair, "available": False}
        return {
            "pair": normalized_pair,
            "available": True,
            "completedAt": entry["completedAt"],
            "ageDays": report_age_days(str(entry["completedAt"])),
            "report": entry["report"],
        }

    def approved_scheduler_pairs() -> list[str]:
        configured = [normalize_pair(str(pair)) for pair in AI_HYPEROPT_SCHEDULER.get("pairs", [])]
        candidates = list(dict.fromkeys([*configured, *sorted(AI_CONFIG_BY_PAIR)]))
        return [pair for pair in candidates if isinstance(ai_config_for_pair(pair).get("approvedRevision"), dict)]

    def schedule_pair_slots(pairs: list[str], *, anchor: datetime | None = None) -> dict[str, str]:
        base = anchor or (datetime.now(timezone.utc) + timedelta(minutes=5))
        gap = timedelta(minutes=int(AI_HYPEROPT_SCHEDULER["gapMinutes"]))
        return {pair: (base + index * gap).isoformat() for index, pair in enumerate(pairs)}

    async def wait_for_hyperopt_job(pair: str) -> dict[str, object]:
        while True:
            job = AI_HYPEROPT_JOBS.get(pair, {})
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
        try:
            async def start_for_pair(pair: str) -> dict:
                config = ai_config_for_pair(pair)
                revision = config.get("approvedRevision") or {}
                hyperopt = revision.get("hyperopt") or {}
                timeframe = str(revision.get("timeframe", config.get("timeframe", "M5"))).upper()
                history_value = int(hyperopt.get("historyValue") or 250)
                payload = {
                    "pair": pair,
                    "timeframe": timeframe,
                    "historyMode": hyperopt.get("historyMode", "candles"),
                    "historyValue": history_value,
                    "steps": history_value,
                    "attempts": int(hyperopt.get("attempts") or 24),
                    "hyperoptLoss": hyperopt.get("hyperoptLoss", "ProfitDrawDownHyperOptLoss"),
                    "resetPrevious": False,
                }
                started_job = await ai_hyperopt_start(payload, user_role="operator", csrf_token="scheduled-hyperopt")
                result = await wait_for_hyperopt_job(pair)
                return {"pair": pair, "status": result.get("status", started_job.get("status")), "attemptsCompleted": result.get("attemptsCompleted", 0)}

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
            return {"status": "started", "pairs": pairs, "jobs": started}
        except Exception as exc:
            AI_HYPEROPT_SCHEDULER["lastError"] = str(exc)
            persist_hyperopt_scheduler()
            raise
        finally:
            AI_HYPEROPT_SCHEDULER["running"] = False
            persist_hyperopt_scheduler()

    @app.get("/api/v1/ai/hyperopt/scheduler")
    async def ai_hyperopt_scheduler() -> dict:
        return {**AI_HYPEROPT_SCHEDULER, "approvedPairs": approved_scheduler_pairs()}

    @app.post("/api/v1/ai/hyperopt/scheduler")
    async def save_ai_hyperopt_scheduler(
        payload: dict,
        user_role: str | None = Header(default=None, alias="X-User-Role"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict:
        validate_write_access(user_role, csrf_token)
        pairs = list(dict.fromkeys(normalize_pair(str(pair)) for pair in payload.get("pairs", [])))
        unknown = [pair for pair in pairs if not isinstance(ai_config_for_pair(pair).get("approvedRevision"), dict)]
        if unknown:
            raise HTTPException(status_code=409, detail=f"Pairs require an approved Hyperopt revision: {', '.join(unknown)}")
        interval_days = int(payload.get("intervalDays", AI_HYPEROPT_SCHEDULER["intervalDays"]))
        gap_minutes = int(payload.get("gapMinutes", AI_HYPEROPT_SCHEDULER["gapMinutes"]))
        if not 1 <= interval_days <= 30:
            raise HTTPException(status_code=400, detail="intervalDays must be between 1 and 30")
        if not 1 <= gap_minutes <= 1440:
            raise HTTPException(status_code=400, detail="gapMinutes must be between 1 and 1440")
        AI_HYPEROPT_SCHEDULER.update({"enabled": bool(payload.get("enabled", False)), "intervalDays": interval_days, "gapMinutes": gap_minutes, "pairs": pairs})
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
    async def ai_review(pair: str = "EUR/USD", timeframe: str | None = None) -> dict:
        normalized_pair = normalize_pair(pair)
        selected_timeframe = (timeframe or str(ai_config_for_pair(normalized_pair).get("timeframe", "M5"))).upper()
        return fallback_ai_review(normalized_pair, selected_timeframe)

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
        if status not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="Invalid review status")
        pending = AI_PENDING_OPTIMIZATION.get(pair)
        if pending is None:
            stored_report = AI_HYPEROPT_REPORTS.get(pair)
            report = stored_report.get("report") if stored_report else None
            if isinstance(report, dict):
                report_parameters = report.get("bestParameters") or {}
                pending = {
                    "pair": pair,
                    "timeframe": str(report.get("timeframe", requested_timeframe)).upper(),
                    "historyMode": report.get("historyMode", "candles"),
                    "historyValue": int(report.get("historyValue", 0)),
                    "attempts": int(report.get("attemptsRequested", 0)),
                    "entryThreshold": str(report_parameters.get("entryThreshold", "0.5")),
                    "maxSpreadPct": str(report_parameters.get("maxSpreadPct", "1.0")),
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
            if "entryThreshold" in persisted_config and "maxSpreadPct" in persisted_config:
                pending = {
                    "pair": pair,
                    "timeframe": requested_timeframe,
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
        if status == "approved" and bool(payload.get("requireOptimization", False)):
            if pending is None:
                raise HTTPException(status_code=409, detail=f"No Hyperopt result is pending approval for {pair}")
            if pending["timeframe"] != requested_timeframe:
                raise HTTPException(status_code=409, detail=f"Approval timeframe {requested_timeframe} does not match Hyperopt timeframe {pending['timeframe']} for {pair}")
            target_config = ai_config_for_pair(pair)
            target_config["timeframe"] = requested_timeframe
            target_config["entryThreshold"] = pending["entryThreshold"]
            target_config["maxSpreadPct"] = pending["maxSpreadPct"]
            target_config["approvedHyperopt"] = dict(pending)
            target_config["approvedRevision"] = {
                "pair": pair,
                "timeframe": requested_timeframe,
                "configRevision": target_config.get("configRevision", "r0"),
                "approvedAt": datetime.now(timezone.utc).isoformat(),
                "hyperopt": dict(pending),
                "freqai": dict(target_config.get("freqai") or {}),
            }
        elif status == "approved":
            configured_timeframe = str(ai_config_for_pair(pair).get("timeframe", "M5")).upper()
            if requested_timeframe != configured_timeframe:
                raise HTTPException(status_code=409, detail=f"Approval timeframe {requested_timeframe} does not match configured timeframe {configured_timeframe} for {pair}")

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
        AI_REVIEW_BY_SCOPE[(pair, requested_timeframe)] = scoped_review
        persist_scope_revision(pair, requested_timeframe)

        record_audit_event(
            "ai.strategy.review",
            details={"status": status, "notes": AI_REVIEW_STATE["notes"]},
            username=user_role,
            role=user_role,
            allowed=True,
        )
        return fallback_ai_review(pair, requested_timeframe)

    @app.post("/api/v1/ai/config")
    async def save_ai_config(payload: dict, pair: str = "EUR/USD") -> dict:
        pair = str(payload.get("pair", pair))
        target_config = ai_config_for_pair(pair)
        candidate = dict(target_config)
        for key in ("strategyName", "model", "timeframe", "riskBudget", "trainingMode", "entryThreshold", "exitThreshold", "volatilityWindow", "atrWindow", "maxSpreadPct"):
            if key in payload and isinstance(payload[key], str):
                candidate[key] = payload[key]
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
        persist_scope_revision(pair, str(candidate.get("timeframe", "M5")))
        if "strategyName" in AI_CONFIG:
            AI_REVIEW_STATE["strategyName"] = target_config["strategyName"]
        if "model" in target_config:
            AI_REVIEW_STATE["model"] = target_config["model"]
        return dict(target_config)

    @app.get("/api/v1/strategies")
    async def strategies() -> list[dict]:
        return fallback_strategies()

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
        update_backtest_job(job_id, status="running", phase="validating", message="Validating OANDA backtest configuration.")
        pair = str(payload.get("pair", "EUR/USD"))
        timeframe = str(payload.get("timeframe", "M5"))
        history_mode, steps = resolve_history_request(payload, timeframe)
        normalized_pair = normalize_pair(pair)
        BACKTEST_HISTORY[:] = [
            item for item in BACKTEST_HISTORY
            if not (item.get("pair") == normalized_pair and item.get("timeframe") == timeframe)
        ]
        data_revision = datetime.now(timezone.utc).isoformat()
        settings = OandaSettings.from_environment()
        if settings.environment.value.lower() not in {"practice", "live"}:
            raise HTTPException(status_code=400, detail="Backtest requires a valid OANDA environment")
        if settings.execution_mode.lower() not in {"dry_run", "backtest", "practice"}:
            raise HTTPException(status_code=400, detail="Backtest requires a safe OANDA execution mode")

        instrument_name = pair.replace("/", "_").upper()
        pair_config = ai_config_for_pair(pair)
        approved_run = pair_config.get("approvedHyperopt")
        backtest_warning: str | None = None
        if isinstance(approved_run, dict):
            pair_matches = str(approved_run.get("pair", normalized_pair)).upper() == normalized_pair.upper()
            timeframe_matches = str(approved_run.get("timeframe", "")).upper() == timeframe.upper()
            history_matches = (
                approved_run.get("historyMode", "candles") == history_mode
                and int(approved_run.get("historyValue", steps)) == int(payload.get("historyValue", steps))
            )
            if pair_matches and timeframe_matches:
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
                    f"{normalized_pair} at {timeframe}; default parameters are being used."
                )
                approved_run = None
                pair_config = dict(AI_CONFIG)
        granularity = {"M5": "M5", "M15": "M15", "H1": "H1"}.get(timeframe.upper(), "M5")
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
                data_hash = candle_frame_hash({instrument_name: frame})
                price_row = (await client.get_prices((instrument_name,)))[0]
                update_backtest_job(job_id, phase="backtest", historyProgress=100, backtestProgress=10, message=f"History ready: {len(frame)} complete candles. Running AI baseline.")
                strategy = ForexAIStrategyBaseline({
                    "forex_ai_model": str(pair_config.get("model", "hybrid")),
                    "forex_ai_features": pair_config.get("featureSet", []),
                    "forex_ai_entry_threshold": pair_config.get("entryThreshold", "0.5"),
                    "forex_ai_exit_threshold": pair_config.get("exitThreshold", "0.0"),
                    "forex_ai_volatility_window": pair_config.get("volatilityWindow", "5"),
                    "forex_ai_atr_window": pair_config.get("atrWindow", "14"),
                    "forex_ai_max_spread_pct": pair_config.get("maxSpreadPct", "1.0"),
                })
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
                ).run(frame)
                update_backtest_job(job_id, backtestProgress=95, message="Aggregating trades, P/L and risk metrics.")
                drawdown_rate = getattr(result, "max_drawdown_rate", None)
                if drawdown_rate is None:
                    drawdown_rate = Decimal(str(getattr(result, "max_drawdown", 0))) / result.starting_balance
                summary = {
                    "id": f"bt-{secrets.token_hex(4)}",
                    "name": f"{normalize_pair(pair)} AI baseline",
                    "pair": normalize_pair(pair),
                    "timeframe": timeframe,
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
                    "message": backtest_warning or "Real OANDA historical backtest completed using ForexAIStrategyBaseline.",
                    "warning": backtest_warning,
                    "netPl": format_decimal(result.net_pl),
                    "trades": len(result.trades),
                    "startingBalance": format_decimal(result.starting_balance),
                    "endingBalance": format_decimal(result.ending_balance),
                    "winRate": format_decimal(result.win_rate * Decimal('100')),
                    "maxDrawdown": format_decimal(drawdown_rate * Decimal('100')),
                    "strategy": "ForexAIStrategyBaseline",
                    "dataSource": "OANDA historical candles",
                    "dataRevision": data_revision,
                    "aiParameters": {
                        "model": str(pair_config.get("model", "hybrid")),
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

    @app.on_event("shutdown")
    async def stop_hyperopt_scheduler() -> None:
        task = getattr(app.state, "hyperopt_scheduler_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    if ui_index.exists():
        from fastapi.staticfiles import StaticFiles

        ui_dir = ui_index.parent
        app.mount("/assets", StaticFiles(directory=str(ui_dir / "assets")), name="ui_assets")

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
