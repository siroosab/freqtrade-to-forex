"""Persistent raw and normalized candle storage for reproducible backtests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from freqtrade.forex.models import OandaCandle


class HistoricalCandleStore:
    """Store normalized candle ranges with the original broker payload values."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(
        self,
        instrument: str,
        timeframe: str,
        *,
        start: str,
        end: str,
    ) -> list[OandaCandle] | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        key = self._key(instrument, timeframe, start, end)
        record = payload.get("ranges", {}).get(key)
        if record is None:
            return None
        return [OandaCandle.from_payload(item) for item in record["raw"]]

    def save(
        self,
        instrument: str,
        timeframe: str,
        *,
        start: str,
        end: str,
        candles: list[OandaCandle],
        normalized: list[dict[str, Any]],
    ) -> None:
        payload: dict[str, Any] = {"version": 1, "ranges": {}}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("version", 1)
        payload.setdefault("ranges", {})
        payload["ranges"][self._key(instrument, timeframe, start, end)] = {
            "instrument": instrument,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "raw": [self._raw_payload(candle) for candle in candles],
            "normalized": normalized,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _key(instrument: str, timeframe: str, start: str, end: str) -> str:
        return f"{instrument.upper()}|{timeframe}|{start}|{end}"

    @staticmethod
    def _raw_payload(candle: OandaCandle) -> dict[str, Any]:
        return {
            "time": candle.time,
            "complete": candle.complete,
            "mid": {
                "o": str(candle.open),
                "h": str(candle.high),
                "l": str(candle.low),
                "c": str(candle.close),
            },
            "volume": candle.volume,
        }


def normalized_candle_payload(candles: list[OandaCandle]) -> list[dict[str, Any]]:
    return [
        {
            "date": datetime.fromisoformat(candle.time.replace("Z", "+00:00"))
            .astimezone(UTC)
            .isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": candle.volume,
        }
        for candle in candles
    ]
