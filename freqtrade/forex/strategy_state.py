"""Restart-safe strategy state that is independent from broker positions."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from freqtrade.forex.strategy_loop import Signal


@dataclass
class ForexStrategyState:
    strategy_name: str
    timeframe: str
    last_candle_time: str | None = None
    last_signal: Signal = Signal.FLAT
    processed_candles: int = 0
    indicators: dict[str, str] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.strategy_name.strip():
            raise ValueError("strategy_name must not be empty")
        if not self.timeframe.strip():
            raise ValueError("timeframe must not be empty")
        if self.processed_candles < 0:
            raise ValueError("processed_candles must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_name": self.strategy_name,
            "timeframe": self.timeframe,
            "last_candle_time": self.last_candle_time,
            "last_signal": self.last_signal.value,
            "processed_candles": self.processed_candles,
            "indicators": dict(self.indicators),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ForexStrategyState":
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported strategy state schema version")
        try:
            return cls(
                strategy_name=str(payload["strategy_name"]),
                timeframe=str(payload["timeframe"]),
                last_candle_time=payload.get("last_candle_time"),
                last_signal=Signal(payload.get("last_signal", Signal.FLAT.value)),
                processed_candles=int(payload.get("processed_candles", 0)),
                indicators={str(key): str(value) for key, value in payload.get("indicators", {}).items()},
                schema_version=1,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid forex strategy state") from exc


class ForexStrategyStateStore:
    """Persist strategy metadata atomically without persisting broker positions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, state: ForexStrategyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), sort_keys=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)

    def load(self) -> ForexStrategyState | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid persisted forex strategy state") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid persisted forex strategy state")
        return ForexStrategyState.from_dict(payload)
