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

    def load_latest(
        self, instrument: str, timeframe: str, *, count: int
    ) -> list[OandaCandle] | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        ranges = payload.get("ranges", {})
        exact_record = ranges.get(self._latest_key(instrument, timeframe, count))
        candles_by_time: dict[str, OandaCandle] = {}
        complete_ranges: list[list[OandaCandle]] = []
        for cached in ranges.values():
            if (
                cached.get("instrument", "").upper() != instrument.upper()
                or cached.get("timeframe") != timeframe
            ):
                continue
            cached_candles: list[OandaCandle] = []
            for item in cached.get("raw", []):
                candle = OandaCandle.from_payload(item)
                candles_by_time[candle.time] = candle
                cached_candles.append(candle)
            if len(cached_candles) >= count:
                complete_ranges.append(cached_candles)
        if complete_ranges:
            latest_range = max(
                complete_ranges,
                key=lambda candles: max(candle.time for candle in candles),
            )
            return sorted(latest_range, key=lambda candle: candle.time)[-count:]
        if len(candles_by_time) < count:
            if exact_record is None:
                return None
            return [OandaCandle.from_payload(item) for item in exact_record["raw"]]
        return sorted(candles_by_time.values(), key=lambda candle: candle.time)[-count:]

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

    def save_latest(
        self,
        instrument: str,
        timeframe: str,
        *,
        count: int,
        candles: list[OandaCandle],
        normalized: list[dict[str, Any]],
    ) -> None:
        payload: dict[str, Any] = {"version": 1, "ranges": {}}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("version", 1)
        payload.setdefault("ranges", {})
        payload["ranges"][self._latest_key(instrument, timeframe, count)] = {
            "instrument": instrument.upper(),
            "timeframe": timeframe,
            "count": count,
            "raw": [self._raw_payload(candle) for candle in candles],
            "normalized": normalized,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def clear(self, *, instrument: str | None = None, timeframe: str | None = None) -> int:
        if not self.path.exists():
            return 0
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        ranges = payload.get("ranges", {})
        matching = {
            key: record
            for key, record in ranges.items()
            if (instrument is None or record.get("instrument", "").upper() == instrument.upper())
            and (timeframe is None or record.get("timeframe") == timeframe)
        }
        for key in matching:
            del ranges[key]
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return len(matching)

    def inventory(self, *, instrument: str, timeframe: str) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "instrument": instrument.upper(),
                "timeframe": timeframe,
                "cachedRanges": 0,
                "candles": 0,
                "from": None,
                "to": None,
                "ranges": [],
            }
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        matching = (
            (key, record)
            for key, record in payload.get("ranges", {}).items()
            if record.get("instrument", "").upper() == instrument.upper()
            and record.get("timeframe") == timeframe
        )
        timestamps: set[datetime] = set()
        ranges: list[dict[str, Any]] = []
        for key, record in matching:
            raw = record.get("raw", [])
            candle_times = {
                datetime.fromisoformat(item["time"].replace("Z", "+00:00")).astimezone(UTC)
                for item in raw
            }
            timestamps.update(candle_times)
            ranges.append(
                {
                    "key": key,
                    "kind": "range" if "start" in record else "latest",
                    "requestedStart": record.get("start"),
                    "requestedEnd": record.get("end"),
                    "candles": len(candle_times),
                    "from": min(candle_times).isoformat() if candle_times else None,
                    "to": max(candle_times).isoformat() if candle_times else None,
                }
            )
        ranges.sort(key=lambda item: (item["from"] or "", item["key"]))
        ordered = sorted(timestamps)
        return {
            "instrument": instrument.upper(),
            "timeframe": timeframe,
            "cachedRanges": len(ranges),
            "candles": len(timestamps),
            "from": ordered[0].isoformat() if ordered else None,
            "to": ordered[-1].isoformat() if ordered else None,
            "ranges": ranges,
        }

    @staticmethod
    def _key(instrument: str, timeframe: str, start: str, end: str) -> str:
        return f"{instrument.upper()}|{timeframe}|{start}|{end}"

    @staticmethod
    def _latest_key(instrument: str, timeframe: str, count: int) -> str:
        return f"{instrument.upper()}|{timeframe}|latest:{count}"

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
