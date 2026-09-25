"""Native-core contracts for the OANDA forex adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from freqtrade.forex.models import ForexMarketSession, OandaInstrument, OandaPrice
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.state import OandaAccountState
from freqtrade.forex.native_protections import ForexNativeProtectionBridge, ForexProtectionLimits


@dataclass(frozen=True)
class ForexWalletSnapshot:
    currency: str
    balance: Decimal
    equity: Decimal
    free_margin: Decimal


@dataclass(frozen=True)
class ForexPricing:
    instrument: str
    bid: Decimal
    ask: Decimal
    timestamp: str

    def entry_price(self, units: int) -> Decimal:
        if units == 0:
            raise ValueError("units cannot be zero")
        return self.ask if units > 0 else self.bid

    def exit_price(self, units: int) -> Decimal:
        if units == 0:
            raise ValueError("units cannot be zero")
        return self.bid if units > 0 else self.ask


class OandaNativeCoreBridge:
    """Bridge OANDA data, pairlist, wallet, and bid/ask pricing to core callers."""

    def __init__(self, instruments: tuple[str, ...]) -> None:
        normalized = tuple(OandaMarketDataProvider.to_freqtrade_pair(item) for item in instruments)
        if not normalized:
            raise ValueError("at least one forex instrument is required")
        self._pairlist = normalized
        self._dataframes: dict[tuple[str, str], pd.DataFrame] = {}
        self._pricing: dict[str, ForexPricing] = {}
        self._wallet: ForexWalletSnapshot | None = None

    def whitelist(self) -> tuple[str, ...]:
        return self._pairlist

    def set_pair_dataframe(self, pair: str, timeframe: str, dataframe: pd.DataFrame) -> None:
        if pair not in self._pairlist:
            raise ValueError(f"pair is not in the OANDA whitelist: {pair}")
        self._dataframes[(pair, timeframe)] = dataframe.copy()

    def get_pair_dataframe(self, pair: str, timeframe: str) -> pd.DataFrame:
        if pair not in self._pairlist:
            raise ValueError(f"pair is not in the OANDA whitelist: {pair}")
        return self._dataframes.get((pair, timeframe), pd.DataFrame()).copy()

    def update_wallet(self, account: OandaAccountState) -> None:
        self._wallet = ForexWalletSnapshot(
            currency=account.currency,
            balance=account.balance,
            equity=account.nav,
            free_margin=account.margin_available,
        )

    @property
    def wallet(self) -> ForexWalletSnapshot:
        if self._wallet is None:
            raise RuntimeError("OANDA account snapshot has not been loaded")
        return self._wallet

    def update_price(self, price: OandaPrice) -> None:
        self._pricing[price.instrument] = ForexPricing(
            instrument=price.instrument,
            bid=price.bid,
            ask=price.ask,
            timestamp=price.time,
        )

    def pricing(self, pair: str) -> ForexPricing:
        instrument = OandaMarketDataProvider.to_oanda_instrument(pair)
        try:
            return self._pricing[instrument]
        except KeyError as exc:
            raise RuntimeError(f"OANDA price has not been loaded for {pair}") from exc

    def instrument(self, metadata: OandaInstrument) -> str:
        pair = OandaMarketDataProvider.to_freqtrade_pair(metadata.name)
        if pair not in self._pairlist:
            raise ValueError(f"instrument is not in the OANDA whitelist: {metadata.name}")
        return pair


def attach_native_forex_adapters(config: dict) -> dict:
    """Attach shared core and protection bridges to an OANDA native config."""
    if config.get("exchange", {}).get("name", "").lower() != "oanda":
        return config
    exchange = config["exchange"]
    instruments = tuple(exchange.get("pair_whitelist", ("EUR_USD",)))
    config["forex_core_bridge"] = OandaNativeCoreBridge(instruments)
    exchange["pair_whitelist"] = list(config["forex_core_bridge"].whitelist())
    config["forex_protection_bridge"] = ForexNativeProtectionBridge(
        ForexProtectionLimits(
            max_spread=_decimal_option(exchange.get("oanda_max_spread")),
            min_free_margin=_decimal_option(exchange.get("oanda_min_free_margin")),
            min_margin_level=_decimal_option(exchange.get("oanda_min_margin_level")),
            allowed_sessions=_parse_allowed_sessions(exchange.get("oanda_allowed_sessions")),
            max_financing_cost=_decimal_option(exchange.get("oanda_max_financing_cost")),
        )
    )
    config["forex_pairlist"] = config["forex_core_bridge"].whitelist()
    return config


def _parse_allowed_sessions(value: object) -> tuple:
    if value is None:
        return ()
    if isinstance(value, tuple):
        items = value
    elif isinstance(value, list):
        items = tuple(value)
    else:
        return ()

    sessions: list[ForexMarketSession] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("session") or "default")
        open_utc = str(item.get("open_utc") or item.get("open") or "00:00")
        close_utc = str(item.get("close_utc") or item.get("close") or "23:59")
        sessions.append(ForexMarketSession(name=name, open_utc=open_utc, close_utc=close_utc))
    return tuple(sessions)


def _decimal_option(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
