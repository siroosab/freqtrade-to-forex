"""Validated configuration for the OANDA forex adapter."""

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from os import environ
from pathlib import Path

from freqtrade.enums import RunMode
from freqtrade.forex.models import OandaEnvironment

VALID_EXECUTION_MODES = {"backtest", "hyperopt", "dry_run", "practice", "live"}
DEFAULT_CONFIG_PATH = Path("user_data/config.json")
NATIVE_RUNMODE_EXECUTION_MODES = {
    RunMode.BACKTEST: "backtest",
    RunMode.HYPEROPT: "hyperopt",
    RunMode.DRY_RUN: "dry_run",
    RunMode.LIVE: "live",
}


def execution_mode_for_native_runmode(runmode: RunMode | str) -> str:
    """Map native Freqtrade runmodes to the FX execution contract."""
    try:
        normalized = RunMode(runmode)
    except ValueError as exc:
        raise ValueError(f"unsupported native forex runmode: {runmode}") from exc
    try:
        return NATIVE_RUNMODE_EXECUTION_MODES[normalized]
    except KeyError as exc:
        raise ValueError(
            "Practice is standalone and cannot be selected through native runmode"
        ) from exc


def validate_native_forex_config(config: dict, runmode: RunMode | str) -> "OandaSettings":
    """Build FX settings for native Freqtrade commands with explicit runmode."""
    settings = OandaSettings.from_freqtrade_config(config)
    normalized = RunMode(runmode)
    if normalized in {
        RunMode.UTIL_EXCHANGE,
        RunMode.UTIL_NO_EXCHANGE,
        RunMode.PLOT,
        RunMode.WEBSERVER,
        RunMode.OTHER,
    }:
        return settings

    execution_mode = execution_mode_for_native_runmode(normalized)
    if settings.execution_mode not in {execution_mode, "dry_run"}:
        raise ValueError(
            f"native runmode {execution_mode} conflicts with OANDA execution mode {settings.execution_mode}"
        )
    return OandaSettings(
        token=settings.token,
        account_id=settings.account_id,
        environment=settings.environment,
        instruments=settings.instruments,
        timeframes=settings.timeframes,
        risk_fraction=settings.risk_fraction,
        execution_mode=execution_mode,
        transaction_cursor_path=settings.transaction_cursor_path,
    )


@dataclass(frozen=True)
class OandaSettings:
    token: str
    account_id: str
    environment: OandaEnvironment = OandaEnvironment.PRACTICE
    instruments: tuple[str, ...] = ("EUR_USD", "GBP_USD")
    timeframes: tuple[str, ...] = ("5m", "1h")
    risk_fraction: str = "0.01"
    execution_mode: str = "dry_run"
    transaction_cursor_path: str = "user_data/oanda/transaction_cursor.json"

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("OANDA token is required")
        if not self.account_id:
            raise ValueError("OANDA account ID is required")
        if self.environment is OandaEnvironment.LIVE and environ.get("OANDA_LIVE_CONFIRM") != "1":
            raise ValueError("live OANDA mode requires OANDA_LIVE_CONFIRM=1")
        if self.execution_mode not in VALID_EXECUTION_MODES:
            raise ValueError(f"unsupported OANDA execution mode: {self.execution_mode}")

    @classmethod
    def from_environment(cls) -> "OandaSettings":
        persisted = load_forex_config()
        exchange = persisted.get("exchange", {})
        environment = OandaEnvironment(
            environ.get("OANDA_ENVIRONMENT", exchange.get("oanda_environment", "practice"))
        )
        instruments = tuple(
            item.strip()
            for item in environ.get("OANDA_INSTRUMENTS", "EUR_USD,GBP_USD").split(",")
            if item.strip()
        )
        timeframes = tuple(
            item.strip()
            for item in environ.get("OANDA_TIMEFRAMES", "5m,1h").split(",")
            if item.strip()
        )
        return cls(
            token=environ.get("OANDA_TOKEN", exchange.get("oanda_token", "")),
            account_id=environ.get("OANDA_ACCOUNT_ID", exchange.get("account_id", "")),
            environment=environment,
            instruments=instruments,
            timeframes=timeframes,
            risk_fraction=environ.get("OANDA_RISK_FRACTION", str(exchange.get("oanda_risk_fraction", "0.01"))),
            execution_mode=environ.get("OANDA_EXECUTION_MODE", exchange.get("oanda_execution_mode", "dry_run")),
            transaction_cursor_path=environ.get(
                "OANDA_TRANSACTION_CURSOR_PATH", "user_data/oanda/transaction_cursor.json"
            ),
        )

    @classmethod
    def from_freqtrade_config(cls, config: dict) -> "OandaSettings":
        exchange = config.get("exchange", {})
        environment = OandaEnvironment(
            exchange.get("oanda_environment", environ.get("OANDA_ENVIRONMENT", "practice"))
        )
        return cls(
            token=exchange.get("oanda_token")
            or exchange.get("api_key")
            or environ.get("OANDA_TOKEN", ""),
            account_id=exchange.get("account_id") or environ.get("OANDA_ACCOUNT_ID", ""),
            environment=environment,
            instruments=tuple(exchange.get("pair_whitelist", ("EUR_USD", "GBP_USD"))),
            timeframes=tuple(config.get("timeframe", "5m").split(",")),
            risk_fraction=str(exchange.get("oanda_risk_fraction", "0.01")),
            execution_mode=exchange.get("oanda_execution_mode", "dry_run"),
            transaction_cursor_path=exchange.get(
                "oanda_transaction_cursor_path", "user_data/oanda/transaction_cursor.json"
            ),
        )


def load_forex_config(path: Path | None = None) -> dict:
    config_path = path or Path(environ.get("OANDA_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_forex_config(payload: dict, path: Path | None = None) -> Path:
    config_path = path or Path(environ.get("OANDA_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = config_path.with_suffix(f"{config_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary_path, config_path)
    with suppress(PermissionError):
        os.chmod(config_path, 0o600)
    return config_path
