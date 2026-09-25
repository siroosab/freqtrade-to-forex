"""OANDA market-data bridge for Freqtrade-compatible OHLCV data."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from freqtrade.forex.config import OandaSettings
from freqtrade.forex.historical import HistoricalCandleStore, normalized_candle_payload
from freqtrade.forex.models import OandaCandle
from freqtrade.forex.oanda import OandaClient


OANDA_GRANULARITIES = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D1",
    "1w": "W1",
    "1mo": "M1",
}


class OandaMarketDataProvider:
    """Fetch OANDA candles and return the DataFrame shape expected by Freqtrade."""

    def __init__(self, client: OandaClient, settings: OandaSettings) -> None:
        self.client = client
        self.settings = settings

    @staticmethod
    def to_oanda_instrument(pair: str) -> str:
        return pair.replace("/", "_").upper()

    @staticmethod
    def to_oanda_granularity(timeframe: str) -> str:
        try:
            return OANDA_GRANULARITIES[timeframe]
        except KeyError as exc:
            raise ValueError(f"Unsupported OANDA timeframe: {timeframe}") from exc

    @staticmethod
    def to_freqtrade_pair(instrument: str) -> str:
        return instrument.replace("_", "/").upper()

    @staticmethod
    def filter_incomplete_candles(candles: list[OandaCandle]) -> list[OandaCandle]:
        return [candle for candle in candles if candle.complete]

    @staticmethod
    def deduplicate_candles(candles: list[OandaCandle]) -> list[OandaCandle]:
        by_time: dict[str, OandaCandle] = {}
        for candle in candles:
            by_time[candle.time] = candle
        return sorted(by_time.values(), key=lambda candle: candle.time)

    @staticmethod
    def _timeframe_delta(timeframe: str) -> timedelta:
        mapping = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
            "1w": timedelta(weeks=1),
        }
        try:
            return mapping[timeframe]
        except KeyError as exc:
            raise ValueError(f"Unsupported OANDA timeframe: {timeframe}") from exc

    @staticmethod
    def detect_gaps(candles: list[OandaCandle], *, timeframe: str) -> list[str]:
        if not candles:
            return []

        step = OandaMarketDataProvider._timeframe_delta(timeframe)
        ordered = sorted(candles, key=lambda candle: candle.time)
        missing_times: list[str] = []
        for previous, current in zip(ordered, ordered[1:]):
            previous_dt = datetime.fromisoformat(previous.time.replace("Z", "+00:00")).astimezone(UTC)
            current_dt = datetime.fromisoformat(current.time.replace("Z", "+00:00")).astimezone(UTC)
            delta = current_dt - previous_dt
            if delta <= step:
                continue
            slots = int(delta.total_seconds() // step.total_seconds())
            for offset in range(1, slots):
                missing_dt = previous_dt + offset * step
                missing_times.append(missing_dt.isoformat().replace("+00:00", "Z"))
        return missing_times

    async def fetch_ohlcv(self, pair: str, timeframe: str, *, count: int = 500) -> pd.DataFrame:
        granularity = self.to_oanda_granularity(timeframe)

        candles = await self.client.get_candles(
            self.to_oanda_instrument(pair), granularity, count=count
        )
        candles = self.filter_incomplete_candles(candles)
        candles = self.deduplicate_candles(candles)
        return self.candles_to_dataframe(candles)

    async def fetch_historical(
        self,
        pair: str,
        timeframe: str,
        *,
        start: str,
        end: str,
        store: HistoricalCandleStore | None = None,
    ) -> pd.DataFrame:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC)
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(UTC)
        if start_dt >= end_dt:
            raise ValueError("historical start must be before end")
        instrument = self.to_oanda_instrument(pair)
        if store is not None:
            cached = store.load(instrument, timeframe, start=start, end=end)
            if cached is not None:
                return self.candles_to_dataframe(cached)
        candles = await self.client.get_candles(
            instrument,
            self.to_oanda_granularity(timeframe),
            from_time=start,
            to_time=end,
        )
        candles = self.filter_incomplete_candles(self.deduplicate_candles(candles))
        candles = [
            candle
            for candle in candles
            if start_dt <= datetime.fromisoformat(candle.time.replace("Z", "+00:00")).astimezone(UTC) < end_dt
        ]
        if store is not None:
            store.save(
                instrument,
                timeframe,
                start=start,
                end=end,
                candles=candles,
                normalized=normalized_candle_payload(candles),
            )
        return self.candles_to_dataframe(candles)

    @staticmethod
    def candles_to_dataframe(candles: list[OandaCandle]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for candle in candles:
            timestamp = datetime.fromisoformat(candle.time.replace("Z", "+00:00"))
            rows.append(
                {
                    "date": timestamp.astimezone(UTC),
                    "open": float(candle.open),
                    "high": float(candle.high),
                    "low": float(candle.low),
                    "close": float(candle.close),
                    "volume": candle.volume,
                }
            )
        return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
