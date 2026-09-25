"""Native Freqtrade exchange façade for the OANDA forex adapter."""

from __future__ import annotations

from typing import Any

from freqtrade.enums import RunMode
from freqtrade.forex.config import OandaSettings, validate_native_forex_config
from freqtrade.forex.native_core import attach_native_forex_adapters


class Oanda:
    """Resolve OANDA through native Freqtrade configuration without CCXT."""

    name = "OANDA"
    precisionMode = 4
    precision_mode_price = 4

    @staticmethod
    def _market(pair: str) -> dict[str, Any]:
        base, quote = pair.split("/", 1)
        return {
            "symbol": pair,
            "base": base,
            "quote": quote,
            "active": True,
            "spot": False,
            "future": True,
            "contract": True,
            "linear": True,
            "contractSize": 1,
            "precision": {"price": 5, "amount": 0},
            "limits": {"amount": {"min": 1}},
        }

    def __init__(
        self,
        config: dict[str, Any],
        *,
        exchange_config: dict[str, Any] | None = None,
        validate: bool = True,
        load_leverage_tiers: bool = False,
    ) -> None:
        del load_leverage_tiers
        self.config = config
        self.exchange_config = exchange_config or config.get("exchange", {})
        attach_native_forex_adapters(config)
        self.settings: OandaSettings | None = None
        self.core = config["forex_core_bridge"]
        self.protections = config["forex_protection_bridge"]
        if validate:
            self.settings = validate_native_forex_config(
                config, config.get("runmode", RunMode.BACKTEST)
            )

    @property
    def markets(self) -> dict[str, dict[str, Any]]:
        return {pair: self._market(pair) for pair in self.core.whitelist()}

    def get_markets(self, params: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        del params
        return self.markets

    def market_is_tradable(self, market: dict[str, Any]) -> bool:
        return bool(market.get("active", False))

    def get_pair_base_currency(self, pair: str) -> str:
        return pair.split("/", 1)[0]

    def get_pair_quote_currency(self, pair: str) -> str:
        return pair.split("/", 1)[1]

    def get_fee(
        self,
        symbol: str,
        order_type: str = "",
        side: str = "",
        amount: float = 1,
        price: float = 1,
        taker_or_maker: str = "maker",
    ) -> float:
        del symbol, order_type, side, amount, price, taker_or_maker
        return 0.0

    def get_option(self, name: str, default: Any = None) -> Any:
        options = self.exchange_config.get("ccxt_config", {}).get("options", {})
        return options.get(name, default)

    def get_proxy_coin(self) -> str:
        return str(self.config.get("stake_currency", "USD"))

    def ohlcv_candle_limit(self, timeframe: str, candle_type: Any = None, since_ms: int | None = None) -> int:
        del timeframe, candle_type, since_ms
        return 5000

    def validate_required_startup_candles(self, startup_candles: int, timeframe: str) -> int:
        del timeframe
        if startup_candles + 1 > self.ohlcv_candle_limit("") * 5:
            raise ValueError("OANDA startup candle requirement exceeds the native request budget")
        return startup_candles

    def close(self) -> None:
        """Keep the resolver lifecycle compatible; the REST client is lazy."""

    def validate_config(self, config: dict[str, Any] | None = None) -> OandaSettings:
        target = config or self.config
        self.settings = validate_native_forex_config(
            target, target.get("runmode", RunMode.BACKTEST)
        )
        return self.settings
