import asyncio
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from freqtrade.enums import RunMode, TradingMode

from freqtrade.forex.models import (
    ForexMarketSession,
    ForexQuoteRate,
    OandaEnvironment,
    OandaInstrument,
    OandaPrice,
)
from freqtrade.forex.order_validation import (
    BrokerOrderValidator,
    OrderValidationError,
    ProtectiveStopPolicy,
)
from freqtrade.forex.position_semantics import (
    PositionMode,
    PositionSemanticsError,
    apply_order,
    close_by_opposite,
)
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.risk import pip_value_per_unit, quote_to_account_rate, units_for_fixed_risk
from freqtrade.forex.risk_limits import (
    RiskControl,
    RiskLimitError,
    RiskLimitPolicy,
    RiskLimits,
    RiskUsage,
    aggregate_currency_exposure,
)
from freqtrade.forex.config import OandaSettings, execution_mode_for_native_runmode, validate_native_forex_config
from freqtrade.forex.costs import ForexFillModel, financing_cost
from freqtrade.forex.execution import (
    ExecutionMode,
    ExecutionModeError,
    IdempotencyError,
    ReconciliationSnapshot,
    OandaExecutionGateway,
)
from freqtrade.forex.exit_rules import AtrStop, FixedStop, TakeProfit, TimeExit, TrailingStop
from freqtrade.forex.features import ForexFeaturePipeline, ForexFreqAIAdapter, ForexFreqAIExecutionGate
from freqtrade.forex.health import OandaHealthCheck, OandaHealthReport
from freqtrade.forex.historical import HistoricalCandleStore
from freqtrade.forex.hyperopt import ForexHyperopt
from freqtrade.forex.api import create_app
from freqtrade.forex.backtest import (
    BacktestResult,
    BacktestTrade,
    ForexBacktester,
    validate_backtest_split,
)
from freqtrade.forex.ledger import (
    NativeTradeOrderStore,
    PaperLedger,
    attach_native_forex_persistence,
)
from freqtrade.forex.native_core import OandaNativeCoreBridge, attach_native_forex_adapters
from freqtrade.forex.native_protections import ForexNativeProtectionBridge, ForexProtectionLimits
from freqtrade.forex.margin import (
    MarginError,
    MarginPosition,
    MarginSnapshot,
    margin_for_position,
    margin_snapshot,
    validate_exposure,
)
from freqtrade.forex.paper import DryRunSession, PaperPosition
from freqtrade.forex.practice_runs import PracticeRunRecord, PracticeRunRecorder
from freqtrade.forex.strategy_loop import (
    DryRunStrategyLoop,
    EmaCrossStrategy,
    ForexStrategyAdapter,
    Signal,
    StrategyStepResult,
)
from freqtrade.forex.strategies.ema_cross import ForexEmaStrategy
from freqtrade.forex.strategy_state import ForexStrategyState, ForexStrategyStateStore
from freqtrade.forex.runner import DryRunWorker, WorkerConfig
from freqtrade.forex.cli import (
    build_parser,
    paper_report_to_json,
    report_to_json,
    run_practice,
    run_practice_run,
)
from freqtrade.forex.models import OandaOrderResult
from freqtrade.forex.state import OandaAccountState, OandaPosition
from freqtrade.forex.stream import OandaTransactionStream, TransactionCursorStore
from freqtrade.forex.transactions import (
    BrokerOrderStatus,
    OandaTransaction,
    OrderStateMachine,
)
from freqtrade.resolvers.strategy_resolver import StrategyResolver
from freqtrade.resolvers.exchange_resolver import ExchangeResolver
from freqtrade.strategy.interface import IStrategy
from freqtrade.exchange.check_exchange import check_exchange


def test_units_for_fixed_risk_uses_stop_distance() -> None:
    from freqtrade.forex.models import OandaInstrument

    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )

    units = units_for_fixed_risk(
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        entry_price=Decimal("1.1000"),
        stop_price=Decimal("1.0950"),
        instrument=instrument,
    )

    assert units == 20000


def test_fill_model_applies_side_price_spread_slippage_and_partial_fill() -> None:
    price = OandaPrice(
        instrument="EUR_USD",
        time="2026-09-17T00:00:00Z",
        bid=Decimal("1.1000"),
        ask=Decimal("1.1002"),
    )
    model = ForexFillModel(fill_ratio=Decimal("0.6"), slippage=Decimal("0.0001"))

    long_fill = model.fill(requested_units=1000, price=price)
    short_fill = model.fill(requested_units=-1000, price=price)

    assert long_fill.filled_units == 600
    assert long_fill.unfilled_units == 400
    assert long_fill.fill_price == Decimal("1.1003")
    assert long_fill.spread_cost == Decimal("0.1200")
    assert long_fill.slippage_cost == Decimal("0.0600")
    assert long_fill.is_partial is True
    assert short_fill.filled_units == -600
    assert short_fill.unfilled_units == -400
    assert short_fill.fill_price == Decimal("1.0999")


def test_financing_cost_is_decimal_and_side_neutral() -> None:
    cost = financing_cost(
        entry_price=Decimal("1.1000"),
        units=-1000,
        days=Decimal("2.5"),
        rate_per_day=Decimal("0.0002"),
        quote_to_account_rate=Decimal("0.9"),
    )

    assert cost == Decimal("0.495000")


def test_financing_cost_is_directional_for_short_positions() -> None:
    cost = financing_cost(
        entry_price=Decimal("1.1000"),
        units=-1000,
        days=Decimal("2.5"),
        rate_per_day=Decimal("0.0002"),
        quote_to_account_rate=Decimal("0.9"),
        direction=Signal.SHORT,
    )

    assert cost == Decimal("-0.495000")


def test_margin_snapshot_calculates_free_margin_level_and_exposure() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    snapshot = margin_snapshot(
        equity=Decimal("10000"),
        leverage=Decimal("20"),
        positions=(
            MarginPosition(instrument, 10000, Decimal("1.1000"), Decimal("1")),
            MarginPosition(instrument, -5000, Decimal("1.1000"), Decimal("1")),
        ),
    )

    assert snapshot.gross_exposure == Decimal("16500.0000")
    assert snapshot.used_margin == Decimal("825.0000")
    assert snapshot.free_margin == Decimal("9175.0000")
    assert snapshot.margin_level == Decimal("1212.121212121212121212121212")


def test_margin_uses_currency_conversion_and_rejects_excess_exposure() -> None:
    instrument = OandaInstrument(
        name="GBP_JPY",
        display_name="GBP/JPY",
        pip_location=-2,
        display_precision=3,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    rate = ForexQuoteRate("USD", "JPY", Decimal("150"), "t", "oanda")

    assert margin_for_position(
        instrument=instrument,
        units=10000,
        entry_price=Decimal("190"),
        leverage=Decimal("10"),
        account_currency="USD",
        rates=(rate,),
    ) == Decimal("1266.666666666666666666666667")
    assert validate_exposure(
        current_exposure=Decimal("1000"),
        additional_exposure=Decimal("500"),
        maximum=Decimal("2000"),
    ) == Decimal("1500")
    with pytest.raises(MarginError, match="maximum exposure"):
        validate_exposure(
            current_exposure=Decimal("1800"),
            additional_exposure=Decimal("500"),
            maximum=Decimal("2000"),
        )


def test_risk_policy_enforces_daily_total_and_currency_exposure_limits() -> None:
    policy = RiskLimitPolicy(
        RiskLimits(
            max_daily_loss=Decimal("100"),
            max_total_risk=Decimal("250"),
            max_currency_exposure=Decimal("1000"),
        )
    )
    usage = RiskUsage(
        daily_loss=Decimal("40"),
        total_open_risk=Decimal("200"),
        currency_exposure=aggregate_currency_exposure(
            {"usd": Decimal("600")}, {"USD": Decimal("250"), "EUR": Decimal("300")}
        ),
    )

    assert usage.currency_exposure == {"USD": Decimal("850"), "EUR": Decimal("300")}
    assert usage.gross_correlation_exposure == Decimal("1150")
    policy.validate(usage)

    with pytest.raises(RiskLimitError, match="daily loss"):
        policy.validate(
            RiskUsage(Decimal("101"), Decimal("200"), {"USD": Decimal("1")})
        )
    with pytest.raises(RiskLimitError, match="total risk"):
        policy.validate(
            RiskUsage(Decimal("40"), Decimal("251"), {"USD": Decimal("1")})
        )
    with pytest.raises(RiskLimitError, match="correlation exposure"):
        policy.validate(
            RiskUsage(Decimal("40"), Decimal("200"), {"USD": Decimal("1001")})
        )


def test_risk_control_kill_switch_and_usage_limits() -> None:
    policy = RiskLimitPolicy(
        RiskLimits(
            max_daily_loss=Decimal("100"),
            max_total_risk=Decimal("250"),
            max_currency_exposure=Decimal("1000"),
        )
    )
    control = RiskControl(policy, RiskUsage(Decimal("40"), Decimal("200"), {"USD": Decimal("500")}))
    control.validate()

    control.update_usage(RiskUsage(Decimal("101"), Decimal("200"), {"USD": Decimal("500")}))
    with pytest.raises(RiskLimitError, match="daily loss"):
        control.validate()

    control.update_usage(RiskUsage(Decimal("40"), Decimal("200"), {"USD": Decimal("500")}))
    control.activate_kill_switch()
    with pytest.raises(RiskLimitError, match="kill switch"):
        control.validate()
    control.deactivate_kill_switch()
    control.validate()


def test_broker_order_validator_enforces_precision_distance_and_side() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1000"),
    )
    validator = BrokerOrderValidator(instrument, minimum_stop_distance=Decimal("0.0010"))

    assert validator.validate_units(1000) == 1000
    assert validator.validate_price(Decimal("1.10001")) == Decimal("1.10001")
    assert validator.validate_stop(
        side="long", entry_price=Decimal("1.1000"), stop_price=Decimal("1.0990")
    ) == Decimal("1.0990")
    assert validator.validate_take_profit(
        side="short", entry_price=Decimal("1.1000"), take_profit_price=Decimal("1.0980")
    ) == Decimal("1.0980")

    with pytest.raises(OrderValidationError, match="decimal places"):
        validator.validate_price(Decimal("1.100001"))
    with pytest.raises(OrderValidationError, match="minimum"):
        validator.validate_stop(
            side="long", entry_price=Decimal("1.1000"), stop_price=Decimal("1.0995")
        )
    with pytest.raises(OrderValidationError, match="below entry"):
        validator.validate_stop(
            side="long", entry_price=Decimal("1.1000"), stop_price=Decimal("1.1010")
        )
    with pytest.raises(OrderValidationError, match="minimum trade"):
        validator.validate_units(999)


def test_protective_stop_policy_requires_stop_or_explicit_reason() -> None:
    policy = ProtectiveStopPolicy()

    policy.validate(stop_loss_price="1.0990")
    policy.validate(stop_loss_price=None, no_stop_reason="strategy-managed exit")
    with pytest.raises(OrderValidationError, match="protective stop"):
        policy.validate(stop_loss_price=None)


@pytest.mark.asyncio
async def test_paper_session_triggers_long_stop_and_take_profit() -> None:
    price_client = AsyncMock()
    price_client.get_prices.side_effect = [
        [OandaPrice("EUR_USD", "t0", Decimal("1.1000"), Decimal("1.1001"))],
        [OandaPrice("EUR_USD", "t1", Decimal("1.0990"), Decimal("1.0991"))],
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(price_client, gateway, ("EUR_USD",))

    await session.refresh_prices()
    await session.open_market(
        "EUR_USD",
        1000,
        "trigger-entry-1",
        stop_loss_price="1.0995",
        take_profit_price="1.1020",
    )
    positions = await session.mark_to_market()

    assert positions == ()
    assert session.positions == {}


@pytest.mark.asyncio
async def test_paper_account_state_updates_equity_margin_and_daily_loss() -> None:
    price_client = AsyncMock()
    price_client.get_prices.side_effect = [
        [OandaPrice("EUR_USD", "t0", Decimal("1.1000"), Decimal("1.1001"))],
        [OandaPrice("EUR_USD", "t1", Decimal("1.0990"), Decimal("1.0991"))],
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(
        price_client,
        gateway,
        ("EUR_USD",),
        starting_balance=Decimal("10000"),
        leverage=Decimal("10"),
    )

    await session.refresh_prices()
    await session.open_market(
        "EUR_USD", 1000, "account-entry-1", stop_loss_price="1.0995"
    )
    open_state = session.account_state()
    assert open_state.balance == Decimal("10000")
    assert open_state.equity == Decimal("9999.9")
    assert open_state.used_margin == Decimal("110.01")
    assert open_state.free_margin == Decimal("9889.89")

    await session.mark_to_market()
    closed_state = session.account_state()
    assert closed_state.daily_loss == Decimal("1.1")
    assert closed_state.equity == Decimal("9998.9")


def test_position_semantics_support_netting_reduce_only_and_close_by_opposite() -> None:
    reduced = apply_order(
        current_units=1000,
        requested_units=-1500,
        mode=PositionMode.NETTING,
        reduce_only=True,
    )
    reversed_position = apply_order(
        current_units=1000,
        requested_units=-1500,
        mode=PositionMode.NETTING,
    )
    closed = close_by_opposite(current_units=-1000)

    assert reduced.submitted_units == -1000
    assert reduced.resulting_units == 0
    assert reduced.closed_units == 1000
    assert reduced.opened_units == 0
    assert reversed_position.resulting_units == -500
    assert reversed_position.closed_units == 1000
    assert reversed_position.opened_units == 500
    assert closed.resulting_units == 0

    with pytest.raises(PositionSemanticsError, match="reduce-only"):
        apply_order(current_units=1000, requested_units=500, reduce_only=True)
    with pytest.raises(PositionSemanticsError, match="trade identifier"):
        close_by_opposite(current_units=1000, mode=PositionMode.HEDGING)


def test_sizing_uses_pip_value_and_inverse_currency_conversion() -> None:
    from freqtrade.forex.models import ForexQuoteRate, OandaInstrument

    instrument = OandaInstrument(
        name="GBP_JPY",
        display_name="GBP/JPY",
        pip_location=-2,
        display_precision=3,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    rates = (ForexQuoteRate("USD", "JPY", Decimal("150"), "t", "oanda"),)

    assert quote_to_account_rate(
        instrument=instrument, account_currency="USD", rates=rates
    ) == Decimal("0.006666666666666666666666666667")
    assert pip_value_per_unit(
        instrument=instrument, account_currency="USD", rates=rates
    ) == Decimal("0.00006666666666666666666666666667")
    units = units_for_fixed_risk(
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        entry_price=Decimal("190.00"),
        stop_price=Decimal("189.00"),
        instrument=instrument,
        account_currency="USD",
        conversion_rates=rates,
    )

    assert units == 15000


def test_sizing_rejects_missing_currency_conversion() -> None:
    instrument = OandaInstrument(
        name="GBP_JPY",
        display_name="GBP/JPY",
        pip_location=-2,
        display_precision=3,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )

    with pytest.raises(ValueError, match="missing currency conversion"):
        units_for_fixed_risk(
            account_equity=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            entry_price=Decimal("190.00"),
            stop_price=Decimal("189.00"),
            instrument=instrument,
            account_currency="USD",
        )


def test_oanda_instrument_exposes_forex_domain_fields() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
        base_currency="EUR",
        quote_currency="USD",
    )

    assert instrument.base_currency == "EUR"
    assert instrument.quote_currency == "USD"
    assert instrument.pip_size == Decimal("0.0001")
    assert instrument.pipette_size == Decimal("0.00001")
    assert instrument.is_quote_currency("USD") is True


def test_oanda_instrument_rounds_units_and_tracks_quote_rates() -> None:
    from freqtrade.forex.models import ForexQuoteRate

    instrument = OandaInstrument(
        name="GBP_JPY",
        display_name="GBP/JPY",
        pip_location=-2,
        display_precision=3,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
        base_currency="GBP",
        quote_currency="JPY",
    )

    assert instrument.pip_size == Decimal("0.01")
    assert instrument.pipette_size == Decimal("0.001")
    assert instrument.round_units(Decimal("123.7")) == Decimal("123")

    rate = ForexQuoteRate(
        base_currency="USD",
        quote_currency="JPY",
        rate=Decimal("157.42"),
        timestamp="2026-09-17T00:00:00Z",
        source="oanda",
    )
    assert rate.convert(Decimal("1000"), "USD", "JPY") == Decimal("157420")


def test_oanda_price_and_position_use_explicit_trade_side_semantics() -> None:
    price = OandaPrice(
        instrument="EUR_USD",
        time="2026-09-17T00:00:00Z",
        bid=Decimal("1.10000"),
        ask=Decimal("1.10020"),
    )
    assert price.price_for_side("long") == Decimal("1.10020")
    assert price.price_for_side("short") == Decimal("1.10000")

    position = OandaPosition(
        instrument="EUR_USD",
        net_units=Decimal("0"),
        long_units=Decimal("1000"),
        short_units=Decimal("0"),
        average_price=Decimal("1.10010"),
        unrealized_pl=Decimal("0"),
        mode="netting",
    )
    assert position.mode == "netting"
    assert position.is_hedged is False


def test_fx_currency_and_account_model_support_quote_conversion() -> None:
    from freqtrade.forex.models import ForexAccount, ForexCurrency, ForexQuote

    currency = ForexCurrency("USD")
    quote = ForexQuote(
        base_currency="EUR",
        quote_currency="USD",
        bid=Decimal("1.10000"),
        ask=Decimal("1.10020"),
        timestamp="2026-09-17T00:00:00Z",
        source="oanda",
    )
    account = ForexAccount(
        id="A-1",
        currency=currency,
        balance=Decimal("10000"),
        margin_available=Decimal("7000"),
        unrealized_pl=Decimal("120"),
    )

    assert currency.code == "USD"
    assert quote.mid == Decimal("1.10010")
    assert quote.convert_amount(Decimal("1000"), "EUR", "USD") == Decimal("1100.20")
    assert account.currency.code == "USD"
    assert account.balance == Decimal("10000")


def test_fx_candle_and_market_session_model_timezones_and_completion() -> None:
    from freqtrade.forex.models import ForexMarketSession, ForexCandle

    candle = ForexCandle(
        time="2026-09-17T00:00:00Z",
        open=Decimal("1.10000"),
        high=Decimal("1.10100"),
        low=Decimal("1.09950"),
        close=Decimal("1.10040"),
        volume=100,
        complete=False,
    )
    session = ForexMarketSession(
        name="london",
        open_utc="08:00:00",
        close_utc="17:00:00",
        timezone="Europe/London",
    )

    assert candle.is_complete is False
    assert str(candle.time_utc.tzinfo) == "UTC"
    assert session.name == "london"
    assert session.is_open_at("2026-09-17T08:30:00Z") is True
    assert session.is_open_at("2026-09-17T17:30:00Z") is False


def test_market_data_provider_maps_timeframes_and_filters_incomplete_candles() -> None:
    from freqtrade.forex.models import OandaCandle

    assert OandaMarketDataProvider.to_oanda_granularity("5m") == "M5"
    assert OandaMarketDataProvider.to_oanda_granularity("15m") == "M15"
    assert OandaMarketDataProvider.to_oanda_granularity("1h") == "H1"

    candles = [
        OandaCandle(
            time="2026-09-17T00:00:00.000000000Z",
            complete=True,
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
            volume=42,
        ),
        OandaCandle(
            time="2026-09-17T00:05:00.000000000Z",
            complete=False,
            open=Decimal("1.10050"),
            high=Decimal("1.10150"),
            low=Decimal("1.09950"),
            close=Decimal("1.10060"),
            volume=41,
        ),
    ]

    filtered = OandaMarketDataProvider.filter_incomplete_candles(candles)
    assert len(filtered) == 1
    assert filtered[0].complete is True

    deduped = OandaMarketDataProvider.deduplicate_candles(
        [
            OandaCandle(
                time="2026-09-17T00:00:00.000000000Z",
                complete=True,
                open=Decimal("1.10000"),
                high=Decimal("1.10050"),
                low=Decimal("1.09950"),
                close=Decimal("1.10020"),
                volume=10,
            ),
            OandaCandle(
                time="2026-09-17T00:00:00.000000000Z",
                complete=True,
                open=Decimal("1.10020"),
                high=Decimal("1.10080"),
                low=Decimal("1.09980"),
                close=Decimal("1.10030"),
                volume=12,
            ),
            OandaCandle(
                time="2026-09-17T00:10:00.000000000Z",
                complete=True,
                open=Decimal("1.10030"),
                high=Decimal("1.10090"),
                low=Decimal("1.09990"),
                close=Decimal("1.10040"),
                volume=13,
            ),
        ]
    )
    assert len(deduped) == 2
    assert deduped[0].volume == 12

    gaps = OandaMarketDataProvider.detect_gaps(
        [
            OandaCandle(
                time="2026-09-17T00:00:00.000000000Z",
                complete=True,
                open=Decimal("1.10000"),
                high=Decimal("1.10050"),
                low=Decimal("1.09950"),
                close=Decimal("1.10020"),
                volume=10,
            ),
            OandaCandle(
                time="2026-09-17T00:10:00.000000000Z",
                complete=True,
                open=Decimal("1.10020"),
                high=Decimal("1.10080"),
                low=Decimal("1.09980"),
                close=Decimal("1.10030"),
                volume=12,
            ),
        ],
        timeframe="5m",
    )
    assert gaps == ["2026-09-17T00:05:00Z"]


def test_live_settings_require_explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OANDA_LIVE_CONFIRM", raising=False)
    with pytest.raises(ValueError, match="OANDA_LIVE_CONFIRM=1"):
        OandaSettings("token", "account", environment=OandaEnvironment.LIVE)


def test_oanda_settings_and_pair_mapping_from_freqtrade_config() -> None:
    settings = OandaSettings.from_freqtrade_config(
        {
            "timeframe": "5m",
            "exchange": {
                "api_key": "config-token",
                "account_id": "config-account",
                "pair_whitelist": ["EUR_USD", "GBP_USD"],
                "oanda_environment": "practice",
                "oanda_risk_fraction": 0.02,
            },
        }
    )

    assert settings.token == "config-token"
    assert settings.account_id == "config-account"
    assert settings.risk_fraction == "0.02"
    assert settings.execution_mode == "dry_run"
    assert OandaMarketDataProvider.to_oanda_instrument("eur/usd") == "EUR_USD"
    assert OandaMarketDataProvider.to_freqtrade_pair("GBP_USD") == "GBP/USD"


def test_oanda_settings_reject_unknown_execution_mode() -> None:
    with pytest.raises(ValueError, match="unsupported OANDA execution mode"):
        OandaSettings("token", "account", execution_mode="unknown")


def test_native_runmode_maps_to_forex_execution_contract(default_conf) -> None:
    default_conf.update(
        {
            "exchange": {
                "name": "oanda",
                "oanda_token": "token",
                "account_id": "account",
                "oanda_environment": "practice",
                "oanda_execution_mode": "dry_run",
                "pair_whitelist": ["EUR_USD"],
            }
        }
    )

    assert execution_mode_for_native_runmode("backtest") == "backtest"
    settings = validate_native_forex_config(default_conf, RunMode.BACKTEST)
    assert settings.execution_mode == "backtest"


def test_native_utility_runmode_validates_oanda_without_selecting_execution() -> None:
    config = {
        "exchange": {
            "name": "oanda",
            "oanda_token": "token",
            "account_id": "account",
            "oanda_environment": "practice",
            "oanda_execution_mode": "dry_run",
            "pair_whitelist": ["EUR_USD"],
        }
    }

    settings = validate_native_forex_config(config, RunMode.UTIL_EXCHANGE)

    assert settings.execution_mode == "dry_run"


def test_exchange_resolver_loads_native_oanda_without_ccxt(default_conf) -> None:
    default_conf.update(
        {
            "runmode": RunMode.BACKTEST,
            "exchange": {
                "name": "oanda",
                "oanda_token": "token",
                "account_id": "account",
                "oanda_environment": "practice",
                "oanda_execution_mode": "backtest",
                "pair_whitelist": ["EUR_USD"],
            },
        }
    )

    exchange = ExchangeResolver.load_exchange(default_conf)

    assert exchange.name == "OANDA"
    assert exchange.settings is not None
    assert exchange.settings.execution_mode == "backtest"
    assert check_exchange(default_conf)


def test_practice_cli_is_distinct_from_dry_run() -> None:
    assert build_parser().parse_args(["practice"]).command == "practice"
    assert build_parser().parse_args(["dry-run"]).command == "dry-run"


def test_paper_backup_cli_requires_destination() -> None:
    args = build_parser().parse_args(["paper-backup", "--destination", "backup.sqlite"])
    assert args.command == "paper-backup"
    assert args.ledger == "user_data/oanda/paper.sqlite"


def test_practice_run_recorder_persists_multiple_sessions(tmp_path) -> None:
    recorder = PracticeRunRecorder(tmp_path / "practice-runs.jsonl")
    recorder.append(
        PracticeRunRecord(
            run_id="run-1",
            session="london",
            started_at="2026-09-18T08:00:00Z",
            ended_at="2026-09-18T12:00:00Z",
            instruments=("EUR_USD",),
            status="completed",
            order_count=2,
        )
    )
    recorder.append(
        PracticeRunRecord(
            run_id="run-2",
            session="new-york",
            started_at="2026-09-18T13:00:00Z",
            ended_at="2026-09-18T17:00:00Z",
            instruments=("EUR_USD", "GBP_USD"),
            status="failed",
            notes="timeout during reconciliation",
        )
    )

    records = PracticeRunRecorder(recorder.path).records()
    assert [record.session for record in records] == ["london", "new-york"]
    assert records[0].order_count == 2
    assert records[1].notes == "timeout during reconciliation"


def test_native_trade_order_store_is_migration_safe(tmp_path) -> None:
    path = tmp_path / "native-compatible.sqlite"
    store = NativeTradeOrderStore(path)
    trade_id = store.open_trade("EUR_USD", 1000, Decimal("1.1000"))
    store.record_order(
        order_id="native-order-1",
        client_order_id="native-client-1",
        instrument="EUR_USD",
        units=1000,
        status="filled",
        fill_price=Decimal("1.1001"),
    )

    reopened = NativeTradeOrderStore(path)
    assert reopened.schema_version == 1
    assert reopened.orders()[0].client_order_id == "native-client-1"
    assert reopened.trades()[0].trade_id == trade_id


def test_paper_ledger_backup_round_trip(tmp_path) -> None:
    source_path = tmp_path / "paper.sqlite"
    backup_path = tmp_path / "backups" / "paper.sqlite"
    ledger = PaperLedger(source_path)
    ledger.open_trade("EUR_USD", 1000, Decimal("1.1000"))
    ledger.record_order(
        order_id="order-1",
        client_order_id="client-1",
        instrument="EUR_USD",
        units=1000,
        status="filled",
        fill_price=Decimal("1.1001"),
    )

    assert ledger.backup_to(backup_path) == backup_path

    restored = PaperLedger(backup_path)
    assert restored.schema_version == ledger.schema_version
    assert restored.orders()[0].client_order_id == "client-1"
    assert restored.open_trades()[0].instrument == "EUR_USD"


def test_native_command_config_attaches_shared_forex_persistence(tmp_path) -> None:
    config = {
        "exchange": {"name": "oanda", "oanda_persistence_path": str(tmp_path / "native.sqlite")}
    }

    attached = attach_native_forex_persistence(config)

    assert attached["forex_persistence_store"].schema_version == 1
    assert attached["forex_persistence_store"].ledger.path == tmp_path / "native.sqlite"


def test_native_command_config_leaves_non_oanda_unchanged() -> None:
    config = {"exchange": {"name": "binance"}}

    assert attach_native_forex_persistence(config) == config


def test_native_core_bridge_maps_pairlist_wallet_and_bid_ask_pricing() -> None:
    bridge = OandaNativeCoreBridge(("EUR_USD",))
    bridge.update_wallet(
        OandaAccountState(
            account_id="account",
            currency="USD",
            balance=Decimal("10000"),
            nav=Decimal("10010"),
            margin_available=Decimal("9000"),
            unrealized_pl=Decimal("10"),
        )
    )
    bridge.update_price(
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-18T10:00:00Z",
            bid=Decimal("1.1000"),
            ask=Decimal("1.1002"),
        )
    )

    pricing = bridge.pricing("EUR/USD")
    assert bridge.whitelist() == ("EUR/USD",)
    assert bridge.wallet.equity == Decimal("10010")
    assert pricing.entry_price(1000) == Decimal("1.1002")
    assert pricing.exit_price(1000) == Decimal("1.1000")
    assert pricing.entry_price(-1000) == Decimal("1.1000")
    assert pricing.exit_price(-1000) == Decimal("1.1002")


def test_native_command_wires_shared_core_and_protection_adapters(tmp_path) -> None:
    config = {
        "exchange": {
            "name": "oanda",
            "pair_whitelist": ["EUR_USD"],
            "oanda_persistence_path": str(tmp_path / "native.sqlite"),
            "oanda_max_spread": "0.0003",
        }
    }

    attach_native_forex_adapters(config)

    assert config["forex_pairlist"] == ("EUR/USD",)
    assert config["forex_core_bridge"].whitelist() == ("EUR/USD",)
    assert config["forex_protection_bridge"].limits.max_spread == Decimal("0.0003")


def test_native_command_maps_allowed_sessions_to_protection_bridge() -> None:
    config = {
        "exchange": {
            "name": "oanda",
            "pair_whitelist": ["EUR_USD"],
            "oanda_allowed_sessions": [
                {"name": "london", "open_utc": "08:00", "close_utc": "16:00"},
                {"name": "new_york", "open_utc": "13:00", "close_utc": "21:00"},
            ],
        }
    }

    attach_native_forex_adapters(config)

    sessions = config["forex_protection_bridge"].limits.allowed_sessions
    assert len(sessions) == 2
    assert sessions[0].name == "london"
    assert sessions[1].name == "new_york"
    assert sessions[0].is_open_at("2026-09-18T12:00:00Z") is True
    assert sessions[1].is_open_at("2026-09-18T12:00:00Z") is False


def test_native_backtest_config_injects_valid_forex_adapters() -> None:
    config = {
        "exchange": {
            "name": "oanda",
            "pair_whitelist": ["EUR_USD"],
            "oanda_execution_mode": "backtest",
            "oanda_max_spread": "0.0003",
            "oanda_allowed_sessions": [
                {"name": "london", "open_utc": "08:00", "close_utc": "16:00"},
            ],
        }
    }

    attached = attach_native_forex_persistence(config)

    assert attached["forex_pairlist"] == ("EUR/USD",)
    assert attached["forex_core_bridge"].whitelist() == ("EUR/USD",)
    assert attached["forex_protection_bridge"].limits.max_spread == Decimal("0.0003")
    assert attached["forex_protection_bridge"].limits.allowed_sessions[0].name == "london"


def test_native_backtesting_config_validation_injects_forex_adapters() -> None:
    config = {
        "exchange": {
            "name": "oanda",
            "oanda_token": "token",
            "account_id": "account",
            "oanda_environment": "practice",
            "oanda_execution_mode": "backtest",
            "pair_whitelist": ["EUR_USD"],
            "oanda_max_spread": "0.0003",
            "oanda_allowed_sessions": [
                {"name": "london", "open_utc": "08:00", "close_utc": "16:00"},
            ],
        },
        "runmode": RunMode.BACKTEST,
        "stake_amount": 10,
        "tradable_balance_ratio": 1.0,
        "dry_run_wallet": 1000,
    }

    prepared = attach_native_forex_persistence(config)

    assert prepared["forex_pairlist"] == ("EUR/USD",)
    assert prepared["forex_core_bridge"].whitelist() == ("EUR/USD",)
    assert prepared["forex_protection_bridge"].limits.max_spread == Decimal("0.0003")
    assert prepared["forex_protection_bridge"].limits.allowed_sessions[0].name == "london"
    assert prepared["forex_persistence_store"].schema_version == 1


def test_native_protection_bridge_enforces_fx_limits() -> None:
    bridge = ForexNativeProtectionBridge(
        ForexProtectionLimits(
            max_spread=Decimal("0.0001"),
            min_free_margin=Decimal("1000"),
            min_margin_level=Decimal("150"),
            allowed_sessions=(ForexMarketSession("london", "08:00", "16:00"),),
            max_financing_cost=Decimal("5"),
        )
    )
    price = OandaPrice(
        instrument="EUR_USD",
        time="2026-09-18T17:00:00Z",
        bid=Decimal("1.1000"),
        ask=Decimal("1.1003"),
    )
    margin = MarginSnapshot(
        equity=Decimal("10000"),
        used_margin=Decimal("8000"),
        free_margin=Decimal("500"),
        margin_level=Decimal("125"),
        gross_exposure=Decimal("80000"),
    )

    decision = bridge.evaluate(
        price=price,
        margin=margin,
        moment="2026-09-18T17:00:00Z",
        entry_price=Decimal("1.1000"),
        units=10000,
        days=Decimal("1"),
        financing_rate_per_day=Decimal("0.001"),
        direction="long",
    )

    assert decision.allowed is False
    assert decision.reasons == (
        "spread exceeds maximum",
        "free margin below minimum",
        "margin level below minimum",
        "market session is closed",
        "financing cost exceeds maximum",
    )


def test_practice_run_cli_accepts_multiple_sessions() -> None:
    args = build_parser().parse_args(
        ["practice-run", "--sessions", "london,new-york", "--steps", "2", "--interval-seconds", "0"]
    )
    assert args.command == "practice-run"
    assert args.sessions == "london,new-york"
    assert args.steps == 2


def test_practice_order_requires_protective_order_arguments() -> None:
    args = build_parser().parse_args(["practice", "--units", "1000"])
    assert args.units == 1000
    assert args.stop_loss_price is None


@pytest.mark.asyncio
async def test_practice_preflight_requires_practice_mode() -> None:
    with pytest.raises(ValueError, match="OANDA_EXECUTION_MODE=practice"):
        await run_practice(OandaSettings("token", "account"), build_parser().parse_args(["practice"]))


@pytest.mark.asyncio
async def test_practice_order_rejects_missing_protection_fields() -> None:
    settings = OandaSettings("token", "account", execution_mode="practice")
    args = build_parser().parse_args(["practice", "--units", "1000"])

    with pytest.raises(ValueError, match="require --units"):
        await run_practice(settings, args)


def test_candles_to_dataframe_matches_freqtrade_ohlcv_shape() -> None:
    from freqtrade.forex.models import OandaCandle

    frame = OandaMarketDataProvider.candles_to_dataframe(
        [
            OandaCandle(
                time="2026-09-15T10:00:00.000000000Z",
                complete=True,
                open=Decimal("1.10000"),
                high=Decimal("1.10100"),
                low=Decimal("1.09900"),
                close=Decimal("1.10050"),
                volume=42,
            )
        ]
    )

    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert frame.iloc[0]["close"] == 1.1005
    assert str(frame.iloc[0]["date"].tz) == "UTC"


@pytest.mark.asyncio
async def test_historical_provider_caches_exact_range_and_normalized_data(tmp_path: Path) -> None:
    from freqtrade.forex.models import OandaCandle

    client = AsyncMock()
    client.get_candles.return_value = [
        OandaCandle(
            time="2026-09-15T10:00:00.000000000Z",
            complete=True,
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
            volume=42,
        ),
        OandaCandle(
            time="2026-09-15T10:05:00.000000000Z",
            complete=False,
            open=Decimal("1.10050"),
            high=Decimal("1.10150"),
            low=Decimal("1.10000"),
            close=Decimal("1.10100"),
            volume=12,
        ),
    ]
    provider = OandaMarketDataProvider(client, OandaSettings("token", "account"))
    store = HistoricalCandleStore(tmp_path / "candles.json")
    start = "2026-09-15T10:00:00Z"
    end = "2026-09-15T10:10:00Z"

    first = await provider.fetch_historical(
        "EUR/USD", "5m", start=start, end=end, store=store
    )
    second = await provider.fetch_historical(
        "EUR/USD", "5m", start=start, end=end, store=store
    )

    assert len(first) == 1
    assert second.equals(first)
    client.get_candles.assert_awaited_once_with(
        "EUR_USD", "M5", from_time=start, to_time=end
    )
    cached_payload = json.loads((tmp_path / "candles.json").read_text(encoding="utf-8"))
    record = next(iter(cached_payload["ranges"].values()))
    assert len(record["raw"]) == 1
    assert record["normalized"][0]["close"] == "1.10050"


@pytest.mark.asyncio
async def test_historical_provider_rejects_reversed_range() -> None:
    provider = OandaMarketDataProvider(AsyncMock(), OandaSettings("token", "account"))

    with pytest.raises(ValueError, match="start must be before end"):
        await provider.fetch_historical(
            "EUR/USD",
            "5m",
            start="2026-09-15T10:10:00Z",
            end="2026-09-15T10:00:00Z",
        )


@pytest.mark.asyncio
async def test_dry_run_gateway_never_calls_oanda() -> None:
    client = AsyncMock()
    settings = OandaSettings("token", "account")
    gateway = OandaExecutionGateway(settings, ExecutionMode.DRY_RUN, client=client)

    result = await gateway.submit_market_order(
        "EUR_USD",
        1000,
        simulated_fill_price="1.10012",
        stop_loss_price="1.09900",
        client_order_id="dry-entry-1",
    )

    assert result.simulated is True
    assert result.order_id == "sim-dry-entry-1"
    client.create_market_order.assert_not_awaited()


def test_practice_mode_requires_practice_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OANDA_LIVE_CONFIRM", "1")
    settings = OandaSettings("token", "account", environment=OandaEnvironment.LIVE)
    with pytest.raises(ExecutionModeError, match="Practice environment"):
        OandaExecutionGateway(settings, ExecutionMode.PRACTICE, client=AsyncMock())


@pytest.mark.asyncio
async def test_practice_gateway_forwards_order_to_oanda() -> None:
    client = AsyncMock()
    client.create_market_order.return_value = OandaOrderResult(
        order_id="42",
        transaction_id="43",
        fill_price=Decimal("1.10012"),
        units=Decimal("-1000"),
    )
    settings = OandaSettings("token", "account")
    gateway = OandaExecutionGateway(settings, ExecutionMode.PRACTICE, client=client)

    result = await gateway.submit_market_order(
        "EUR_USD",
        -1000,
        stop_loss_price="1.10200",
        take_profit_price="1.09600",
        client_order_id="practice-entry-1",
    )

    assert result.simulated is False
    assert result.order_id == "42"
    client.create_market_order.assert_awaited_once_with(
        "EUR_USD",
        -1000,
        stop_loss_price="1.10200",
        take_profit_price="1.09600",
        client_order_id="practice-entry-1",
    )


@pytest.mark.asyncio
async def test_practice_gateway_blocks_order_when_risk_control_is_tripped() -> None:
    client = AsyncMock()
    control = RiskControl(
        RiskLimitPolicy(
            RiskLimits(
                max_daily_loss=Decimal("100"),
                max_total_risk=Decimal("250"),
                max_currency_exposure=Decimal("1000"),
            )
        ),
        RiskUsage(Decimal("101"), Decimal("0"), {"USD": Decimal("0")} ),
    )
    gateway = OandaExecutionGateway(
        OandaSettings("token", "account"),
        ExecutionMode.PRACTICE,
        client=client,
        risk_control=control,
    )

    with pytest.raises(RiskLimitError, match="daily loss"):
        await gateway.submit_market_order(
            "EUR_USD",
            1000,
            stop_loss_price="1.09900",
            client_order_id="risk-blocked-1",
        )
    client.create_market_order.assert_not_awaited()

    control.update_usage(RiskUsage(Decimal("0"), Decimal("0"), {"USD": Decimal("0")}))
    control.activate_kill_switch()
    with pytest.raises(RiskLimitError, match="kill switch"):
        await gateway.submit_market_order(
            "EUR_USD",
            1000,
            stop_loss_price="1.09900",
            client_order_id="risk-blocked-2",
        )

@pytest.mark.asyncio
async def test_practice_order_reports_simulated_vs_real_fill_difference() -> None:
    client = AsyncMock()
    client.create_market_order.return_value = OandaOrderResult(
        order_id="price-1",
        transaction_id="price-tx",
        fill_price=Decimal("1.10012"),
        units=Decimal("1000"),
    )
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.PRACTICE, client=client)

    result = await gateway.submit_market_order(
        "EUR_USD",
        1000,
        simulated_fill_price="1.10000",
        stop_loss_price="1.09900",
        client_order_id="price-report-1",
    )

    assert result.simulated_fill_price == "1.10000"
    assert result.fill_price == "1.10012"
    assert result.price_difference == "0.00012"
    assert result.as_dict()["price_difference"] == "0.00012"


@pytest.mark.asyncio
async def test_practice_order_reconciles_account_and_positions_after_submission() -> None:
    client = AsyncMock()
    client.create_market_order.return_value = OandaOrderResult(
        order_id="52",
        transaction_id="53",
        fill_price=Decimal("1.10012"),
        units=Decimal("1000"),
    )
    client.get_account_summary.return_value = OandaAccountState(
        account_id="account",
        currency="USD",
        balance=Decimal("10000"),
        nav=Decimal("10001"),
        margin_available=Decimal("9000"),
        unrealized_pl=Decimal("1"),
    )
    client.get_open_positions.return_value = [
        OandaPosition(
            instrument="EUR_USD",
            net_units=Decimal("1000"),
            long_units=Decimal("1000"),
            short_units=Decimal("0"),
            average_price=Decimal("1.10012"),
            unrealized_pl=Decimal("1"),
        )
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.PRACTICE, client=client)

    result = await gateway.submit_market_order_and_reconcile(
        "EUR_USD",
        1000,
        stop_loss_price="1.09900",
        take_profit_price="1.10200",
        client_order_id="practice-reconcile-1",
    )

    assert result.reconciliation is not None
    assert result.reconciliation.account is not None
    assert result.reconciliation.account.nav == Decimal("10001")
    assert result.reconciliation.positions[0].net_units == Decimal("1000")
    client.create_market_order.assert_awaited_once()
    client.get_account_summary.assert_awaited_once()
    client.get_open_positions.assert_awaited_once()


@pytest.mark.asyncio
async def test_practice_netting_close_and_reverse_submit_expected_opposite_units() -> None:
    client = AsyncMock()
    client.create_market_order.side_effect = [
        OandaOrderResult(
            order_id="close-1", transaction_id="close-tx", fill_price=None, units=Decimal("-1000")
        ),
        OandaOrderResult(
            order_id="reverse-1", transaction_id="reverse-tx", fill_price=None, units=Decimal("-2000")
        ),
    ]
    client.get_account_summary.return_value = OandaAccountState(
        account_id="account",
        currency="USD",
        balance=Decimal("10000"),
        nav=Decimal("10000"),
        margin_available=Decimal("9000"),
        unrealized_pl=Decimal("0"),
    )
    client.get_open_positions.return_value = []
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.PRACTICE, client=client)

    await gateway.close_position("EUR_USD", 1000, client_order_id="close-1")
    await gateway.reverse_position(
        "EUR_USD",
        1000,
        stop_loss_price="1.10500",
        take_profit_price="1.09500",
        client_order_id="reverse-1",
    )

    assert [call.args[1] for call in client.create_market_order.await_args_list] == [-1000, -2000]
    assert client.create_market_order.await_args_list[0].kwargs["stop_loss_price"] is None
    assert client.create_market_order.await_args_list[1].kwargs["stop_loss_price"] == "1.10500"
    assert client.get_account_summary.await_count == 2
    assert client.get_open_positions.await_count == 2


@pytest.mark.asyncio
async def test_gateway_reuses_idempotent_result_without_duplicate_order() -> None:
    client = AsyncMock()
    client.create_market_order.return_value = OandaOrderResult(
        order_id="42", transaction_id="43", fill_price=Decimal("1.10012"), units=Decimal("1000")
    )
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.PRACTICE, client=client)

    first = await gateway.submit_market_order(
        "EUR_USD", 1000, stop_loss_price="1.09900", client_order_id="same-order"
    )
    second = await gateway.submit_market_order(
        "EUR_USD", 1000, stop_loss_price="1.09900", client_order_id="same-order"
    )

    assert second == first
    client.create_market_order.assert_awaited_once()
    with pytest.raises(IdempotencyError, match="different order"):
        await gateway.submit_market_order(
            "EUR_USD", -1000, stop_loss_price="1.10100", client_order_id="same-order"
        )


@pytest.mark.asyncio
async def test_reconcile_reads_account_and_net_positions() -> None:
    client = AsyncMock()
    client.get_account_summary.return_value = OandaAccountState(
        account_id="account",
        currency="USD",
        balance=Decimal("10000"),
        nav=Decimal("10100"),
        margin_available=Decimal("9000"),
        unrealized_pl=Decimal("100"),
    )
    client.get_open_positions.return_value = [
        OandaPosition(
            instrument="EUR_USD",
            net_units=Decimal("1000"),
            long_units=Decimal("1000"),
            short_units=Decimal("0"),
            average_price=Decimal("1.1000"),
            unrealized_pl=Decimal("2"),
        )
    ]
    gateway = OandaExecutionGateway(
        OandaSettings("token", "account"), ExecutionMode.PRACTICE, client=client
    )

    snapshot = await gateway.reconcile()

    assert isinstance(snapshot, ReconciliationSnapshot)
    assert snapshot.simulated is False
    assert snapshot.account is not None
    assert snapshot.account.nav == Decimal("10100")
    assert snapshot.positions[0].net_units == Decimal("1000")
    client.get_account_summary.assert_awaited_once()
    client.get_open_positions.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_reconcile_does_not_read_broker_state() -> None:
    client = AsyncMock()
    gateway = OandaExecutionGateway(
        OandaSettings("token", "account"), ExecutionMode.DRY_RUN, client=client
    )

    snapshot = await gateway.reconcile()

    assert snapshot == ReconciliationSnapshot(account=None, positions=(), simulated=True)
    client.get_account_summary.assert_not_awaited()
    client.get_open_positions.assert_not_awaited()


def test_order_state_machine_is_idempotent() -> None:
    machine = OrderStateMachine()
    created = OandaTransaction.from_payload(
        {
            "id": "10",
            "type": "ORDER_CREATE",
            "orderID": "9",
            "instrument": "EUR_USD",
            "units": "1000",
        }
    )
    fill = OandaTransaction.from_payload(
        {
            "id": "11",
            "type": "ORDER_FILL",
            "orderID": "9",
            "instrument": "EUR_USD",
            "units": "1000",
            "price": "1.10012",
        }
    )

    assert machine.apply(created).status is BrokerOrderStatus.OPEN
    lifecycle = machine.apply(fill)
    assert lifecycle is not None
    assert lifecycle.status is BrokerOrderStatus.FILLED
    assert lifecycle.fill_price == Decimal("1.10012")
    assert machine.apply(fill) == lifecycle
    assert len(machine.snapshot()) == 1


def test_order_state_machine_records_partial_fill_then_completion_and_cancel() -> None:
    machine = OrderStateMachine()
    machine.apply(
        OandaTransaction.from_payload(
            {"id": "30", "type": "ORDER_CREATE", "orderID": "29", "instrument": "EUR_USD", "units": "1000"}
        )
    )

    partial = machine.apply(
        OandaTransaction.from_payload(
            {"id": "31", "type": "ORDER_FILL", "orderID": "29", "units": "400", "price": "1.1001"}
        )
    )
    assert partial is not None
    assert partial.status is BrokerOrderStatus.PARTIALLY_FILLED
    assert partial.units == Decimal("1000")
    assert partial.filled_units == Decimal("400")

    completed = machine.apply(
        OandaTransaction.from_payload(
            {"id": "32", "type": "ORDER_FILL", "orderID": "29", "units": "600", "price": "1.1002"}
        )
    )
    assert completed is not None
    assert completed.status is BrokerOrderStatus.FILLED
    assert completed.filled_units == Decimal("1000")

    canceled = machine.apply(
        OandaTransaction.from_payload(
            {"id": "33", "type": "ORDER_CANCEL", "orderID": "29", "reason": "CLIENT_REQUEST"}
        )
    )
    assert canceled is not None
    assert canceled.status is BrokerOrderStatus.CANCELED


@pytest.mark.asyncio
async def test_transaction_stream_skips_heartbeats() -> None:
    body = (
        b'{"type":"HEARTBEAT","time":"2026-09-15T10:00:00Z"}\n'
        b'{"id":"11","type":"ORDER_FILL","orderID":"9",'
        b'"instrument":"EUR_USD","units":"1000","price":"1.10012"}\n'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/transactions/stream")
        assert request.url.params["sinceTransactionID"] == "10"
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            transactions = [
                transaction
                async for transaction in client.iter_transactions(since_transaction_id="10")
            ]

    assert len(transactions) == 1
    assert transactions[0].transaction_type == "ORDER_FILL"
    assert transactions[0].price == Decimal("1.10012")


def test_gateway_applies_transaction_to_order_state() -> None:
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    lifecycle = gateway.apply_transaction(
        OandaTransaction.from_payload(
            {
                "id": "21",
                "type": "ORDER_REJECT",
                "orderID": "20",
                "instrument": "GBP_USD",
                "reason": "INSUFFICIENT_MARGIN",
            }
        )
    )

    assert lifecycle is not None
    assert lifecycle.status is BrokerOrderStatus.REJECTED
    assert lifecycle.reason == "INSUFFICIENT_MARGIN"


@pytest.mark.asyncio
async def test_transaction_stream_reconnects_from_persisted_cursor(tmp_path) -> None:
    first = OandaTransaction.from_payload(
        {"id": "11", "type": "ORDER_CREATE", "orderID": "10", "units": "1000"}
    )
    second = OandaTransaction.from_payload(
        {"id": "12", "type": "ORDER_FILL", "orderID": "10", "units": "1000", "price": "1.1"}
    )

    class DisconnectingClient:
        def __init__(self) -> None:
            self.calls: list[str | None] = []

        async def iter_transactions(self, *, since_transaction_id: str | None = None):
            self.calls.append(since_transaction_id)
            if len(self.calls) == 1:
                yield first
                raise ConnectionError("stream disconnected")
            yield second
            raise ConnectionError("stop test stream")

    client = DisconnectingClient()
    stream = OandaTransactionStream(
        client,
        TransactionCursorStore(tmp_path / "oanda-cursor.json"),
        retry_delays=(0,),
        sleep=AsyncMock(),
    )
    iterator = stream.run()

    assert await anext(iterator) == first
    assert await anext(iterator) == second
    await iterator.aclose()

    assert client.calls == [None, "11"]
    assert TransactionCursorStore(tmp_path / "oanda-cursor.json").load() == "12"


@pytest.mark.asyncio
async def test_health_check_is_read_only_and_reports_spread() -> None:
    client = AsyncMock()
    client.get_account_summary.return_value = OandaAccountState(
        account_id="account",
        currency="USD",
        balance=Decimal("10000"),
        nav=Decimal("10000"),
        margin_available=Decimal("9000"),
        unrealized_pl=Decimal("0"),
    )
    client.get_instruments.return_value = [
        OandaInstrument(
            name="EUR_USD",
            display_name="EUR/USD",
            pip_location=-4,
            display_precision=5,
            trade_units_precision=0,
            minimum_trade_size=Decimal("1"),
        )
    ]
    client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-15T10:00:00Z",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10012"),
        )
    ]

    report = await OandaHealthCheck(client).run(("EUR_USD",))

    assert report.healthy is True
    assert report.total_spread == Decimal("0.00012")
    client.create_market_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_check_rejects_missing_price() -> None:
    client = AsyncMock()
    client.get_account_summary.return_value = OandaAccountState(
        account_id="account",
        currency="USD",
        balance=Decimal("10000"),
        nav=Decimal("10000"),
        margin_available=Decimal("9000"),
        unrealized_pl=Decimal("0"),
    )
    client.get_instruments.return_value = []
    client.get_prices.return_value = []

    with pytest.raises(ValueError, match="instruments not available"):
        await OandaHealthCheck(client).run(("EUR_USD",))


def test_forex_cli_parses_health_and_serializes_safe_report() -> None:
    args = build_parser().parse_args(["health", "--instruments", "EUR_USD,GBP_USD"])
    report = OandaHealthReport(
        account=OandaAccountState(
            account_id="account",
            currency="USD",
            balance=Decimal("10000"),
            nav=Decimal("10000"),
            margin_available=Decimal("9000"),
            unrealized_pl=Decimal("0"),
        ),
        instruments=(),
        prices=(),
    )

    output = report_to_json(report)

    assert args.command == "health"
    assert args.instruments == "EUR_USD,GBP_USD"
    assert '"healthy": false' in output
    assert "token" not in output


def test_forex_cli_serializes_paper_report_without_credentials(tmp_path) -> None:
    ledger = PaperLedger(tmp_path / "report.sqlite")
    ledger.open_trade("EUR_USD", 1000, Decimal("1.10010"))

    args = build_parser().parse_args(["paper-report", "--ledger", str(tmp_path / "report.sqlite")])
    output = paper_report_to_json(ledger)

    assert args.command == "paper-report"
    assert '"open_trades": 1' in output
    assert '"instrument": "EUR_USD"' in output
    assert "token" not in output


def test_read_only_api_returns_paper_report(tmp_path) -> None:
    ledger = PaperLedger(tmp_path / "api.sqlite")
    ledger.open_trade("EUR_USD", 1000, Decimal("1.10010"))

    with TestClient(create_app(tmp_path / "api.sqlite")) as client:
        response = client.get("/api/v1/paper/report")
        trades = client.get("/api/v1/paper/trades")

    assert response.status_code == 200
    assert response.json()["performance"]["open_trades"] == 1
    assert response.json()["trades"][0]["instrument"] == "EUR_USD"
    assert trades.status_code == 200
    assert len(trades.json()) == 1


def test_ai_review_workflow_is_exposed_and_approvable(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "review.sqlite")) as client:
        initial = client.get("/api/v1/ai/review")
        updated = client.post(
            "/api/v1/ai/review",
            json={"status": "approved", "notes": "Approved in Practice-safe dry-run mode."},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
        )

    assert initial.status_code == 200
    assert initial.json()["status"] == "pending"
    assert updated.status_code == 200
    assert updated.json()["status"] == "approved"
    assert "Practice-safe" in updated.json()["notes"]


def test_ai_review_is_scoped_by_pair_and_timeframe(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "scoped-review.sqlite")) as client:
        eur = client.post(
            "/api/v1/ai/review",
            json={"status": "approved", "pair": "EUR/USD", "timeframe": "M5", "notes": "EUR M5 approved"},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
        )
        gbp = client.post(
            "/api/v1/ai/review",
            json={"status": "rejected", "pair": "GBP/USD", "timeframe": "H1", "notes": "GBP H1 rejected"},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
        )
        eur_read = client.get("/api/v1/ai/review?pair=EUR%2FUSD&timeframe=M5")
        gbp_read = client.get("/api/v1/ai/review?pair=GBP%2FUSD&timeframe=H1")

    assert eur.status_code == 200
    assert gbp.status_code == 200
    assert eur_read.json()["status"] == "approved"
    assert eur_read.json()["pair"] == "EUR/USD"
    assert gbp_read.json()["status"] == "rejected"
    assert gbp_read.json()["timeframe"] == "H1"


def test_ai_scope_review_survives_api_restart(tmp_path) -> None:
    database = tmp_path / "persistent-review.sqlite"
    with TestClient(create_app(database)) as client:
        response = client.post(
            "/api/v1/ai/review",
            json={"status": "approved", "pair": "EUR/USD", "timeframe": "M5", "notes": "persisted"},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
        )
        assert response.status_code == 200

    with TestClient(create_app(database)) as restarted_client:
        restored = restarted_client.get("/api/v1/ai/review?pair=EUR%2FUSD&timeframe=M5")

    assert restored.status_code == 200
    assert restored.json()["status"] == "approved"
    assert restored.json()["notes"] == "persisted"


def test_approval_restores_exact_hyperopt_report_after_restart(tmp_path) -> None:
    database = tmp_path / "hyperopt-report.sqlite"
    report = {
        "pair": "GBP/USD",
        "timeframe": "H1",
        "status": "completed",
        "historyMode": "candles",
        "historyValue": 500,
        "attemptsRequested": 30,
        "bestParameters": {"entryThreshold": "0.25", "maxSpreadPct": "0.4"},
        "objective": "12.50",
        "dataHash": "data-hash",
        "featureSchemaHash": "feature-hash",
        "modelVersion": "baseline-v1",
        "hyperoptLoss": "OnlyProfitHyperOptLoss",
    }
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ai_hyperopt_reports (pair TEXT PRIMARY KEY, completed_at TEXT NOT NULL, report_json TEXT NOT NULL)")
    connection.execute("INSERT INTO ai_hyperopt_reports VALUES (?, ?, ?)", ("GBP/USD", "2026-09-22T10:00:00+00:00", json.dumps(report)))
    connection.commit()
    connection.close()

    with TestClient(create_app(database)) as client:
        response = client.post(
            "/api/v1/ai/review",
            json={"status": "approved", "pair": "GBP/USD", "timeframe": "H1", "requireOptimization": True},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
        )

    assert response.status_code == 200
    approved = response.json()["approvedRevision"]
    assert approved["timeframe"] == "H1"
    assert approved["hyperopt"]["entryThreshold"] == "0.25"
    assert approved["hyperopt"]["maxSpreadPct"] == "0.4"


def test_hyperopt_scheduler_requires_approved_pairs_and_staggers_all_pairs(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "scheduler.sqlite")) as client:
        blocked = client.post(
            "/api/v1/ai/hyperopt/scheduler",
            json={"enabled": True, "intervalMinutes": 60, "pairs": ["EUR/USD"]},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "hyperopt-scheduler"},
        )
        approved_pairs = []
        for pair, timeframe in (("EUR/USD", "M5"), ("GBP/USD", "H1"), ("USD/JPY", "M15")):
            config = client.get(f"/api/v1/ai/config?pair={pair.replace('/', '%2F')}").json()
            if timeframe != config["timeframe"]:
                config["timeframe"] = timeframe
                configured = client.post(
                    f"/api/v1/ai/config?pair={pair.replace('/', '%2F')}",
                    json=config,
                    headers={"X-User-Role": "admin", "X-CSRF-Token": "ui-config-save"},
                )
                assert configured.status_code == 200
            response = client.post(
                "/api/v1/ai/review",
                    json={"status": "approved", "pair": pair, "timeframe": timeframe, "requireOptimization": True, "notes": "approved"},
                headers={"X-User-Role": "operator", "X-CSRF-Token": "review-approval"},
            )
            assert response.status_code == 200
            approved_pairs.append(pair)
        configured = client.post(
            "/api/v1/ai/hyperopt/scheduler",
            json={"enabled": True, "intervalDays": 2, "gapMinutes": 120, "pairs": approved_pairs},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "hyperopt-scheduler"},
        )

    assert blocked.status_code == 409
    assert configured.status_code == 200
    assert configured.json()["pairs"] == approved_pairs
    assert configured.json()["approvedPairs"] == approved_pairs
    next_runs = configured.json()["nextRuns"]
    assert list(next_runs) == approved_pairs
    assert (datetime.fromisoformat(next_runs[approved_pairs[1]]) - datetime.fromisoformat(next_runs[approved_pairs[0]])).total_seconds() == 7200
    assert (datetime.fromisoformat(next_runs[approved_pairs[2]]) - datetime.fromisoformat(next_runs[approved_pairs[1]])).total_seconds() == 7200


def test_backtest_api_runs_real_backtest_and_persists_history(monkeypatch, tmp_path) -> None:
    class DummyPrice:
        instrument = "EUR_USD"
        spread = Decimal("0.00010")
        midpoint = Decimal("1.10000")

    class DummyAccount:
        currency = "USD"
        balance = Decimal("10000")

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_instruments(self, instruments):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal("1"),
                )
            ]

        async def get_account_summary(self):
            return DummyAccount()

        async def get_candles(self, instrument, granularity, count):
            return [
                {
                    "time": f"2026-01-{day:02d}T00:00:00Z",
                    "mid": {"o": "1.10000", "h": "1.10100", "l": "1.09900", "c": "1.10050"},
                }
                for day in range(1, 11)
            ]

        async def get_prices(self, instruments):
            return [DummyPrice()]

    class DummyBacktester:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def run(self, frame):
            return SimpleNamespace(
                starting_balance=Decimal("10000"),
                ending_balance=Decimal("11000"),
                net_pl=Decimal("1000"),
                trades=[
                    SimpleNamespace(net_pl=Decimal("600")),
                    SimpleNamespace(net_pl=Decimal("400")),
                ],
                win_rate=Decimal("0.50"),
                max_drawdown=Decimal("250"),
                total_costs=Decimal("50"),
                trade_output=(
                    {"instrument": "EUR_USD", "net_pl": "600"},
                    {"instrument": "EUR_USD", "net_pl": "400"},
                ),
                equity_curve=(
                    SimpleNamespace(timestamp="2026-01-01T00:00:00Z", balance=Decimal("10000")),
                    SimpleNamespace(timestamp="2026-01-10T00:00:00Z", balance=Decimal("11000")),
                ),
                daily_breakdown=(),
                weekly_breakdown=(),
                monthly_breakdown=(),
            )

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", DummyClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: SimpleNamespace(
        token="token",
        account_id="acct",
        environment=SimpleNamespace(value="practice"),
        instruments=("EUR_USD",),
        execution_mode="dry_run",
        risk_fraction="0.01",
    ))
    monkeypatch.setattr("freqtrade.forex.api.ForexBacktester", DummyBacktester)

    with TestClient(create_app(tmp_path / "backtest.sqlite")) as client:
        response = client.post(
            "/api/v1/backtests/run",
            json={"pair": "EUR/USD", "timeframe": "M5", "steps": 120},
            headers={"X-User-Role": "operator", "X-CSRF-Token": "demo-backtest"},
        )
        history = client.get("/api/v1/backtests")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["netPl"] == "1000.00"
    assert response.json()["trades"] == 2
    assert history.status_code == 200
    assert history.json()[0]["pair"] == "EUR/USD"


def test_dashboard_is_available_and_contains_read_only_views(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "dashboard.sqlite")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Dry-run desk" in response.text
    assert "/api/v1/paper/report" in response.text
    assert "/api/v1/health" in response.text
    assert "/api/v1/orders" not in response.text


@pytest.mark.asyncio
async def test_dry_run_session_uses_ask_for_long_and_marks_pnl() -> None:
    client = AsyncMock()
    client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:00:00Z",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
        )
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(client, gateway, ("EUR_USD",))

    await session.refresh_prices()
    position = await session.open_market("EUR_USD", 1000, "paper-1")
    client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:05:00Z",
            bid=Decimal("1.10100"),
            ask=Decimal("1.10110"),
        )
    ]
    marked = await session.mark_to_market()

    assert position.entry_price == Decimal("1.10010")
    assert marked[0].unrealized_pl == Decimal("0.90")
    gateway_client = gateway.client
    assert gateway_client is None


@pytest.mark.asyncio
async def test_dry_run_session_requires_price_before_order() -> None:
    client = AsyncMock()
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(client, gateway, ("EUR_USD",))

    with pytest.raises(RuntimeError, match="call refresh_prices first"):
        await session.open_market("EUR_USD", -1000, "paper-2")


def test_strategy_adapter_matches_freqtrade_native_contract() -> None:
    df = pd.DataFrame(
        {
            "date": [
                "2026-09-15T00:00:00Z",
                "2026-09-15T00:05:00Z",
                "2026-09-15T00:10:00Z",
                "2026-09-15T00:15:00Z",
            ],
            "close": [1.0, 1.01, 1.02, 1.03],
        }
    )

    adapter = ForexStrategyAdapter(
        timeframe="5m",
        indicators=lambda frame: frame.assign(fast=frame["close"].ewm(span=2, adjust=False).mean()),
        entry_signal=lambda frame: frame["close"] > frame["fast"],
        exit_signal=lambda frame: frame["close"] < frame["fast"],
    )

    populated = adapter.populate_indicators(df.copy(), {"pair": "EUR/USD"})
    entry = adapter.populate_entry_trend(populated.copy(), {"pair": "EUR/USD"})
    exit_df = adapter.populate_exit_trend(populated.copy(), {"pair": "EUR/USD"})

    assert "fast" in populated.columns
    assert bool(entry.iloc[-1]["enter_long"]) is True
    assert bool(exit_df.iloc[-1]["exit_long"]) is False
    assert adapter.timeframe == "5m"


def test_ai_baseline_strategy_uses_safe_freqai_features_and_dry_run_mode() -> None:
    from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline

    strategy = ForexAIStrategyBaseline({"forex_ai_label_period": 2, "forex_ai_model": "hybrid"})
    frame = pd.DataFrame(
        {
            "date": [f"2026-09-15T00:{minute:02d}:00Z" for minute in range(0, 8)],
            "open": [1.10, 1.11, 1.10, 1.12, 1.13, 1.12, 1.14, 1.15],
            "high": [1.12, 1.12, 1.12, 1.13, 1.14, 1.13, 1.15, 1.16],
            "low": [1.09, 1.10, 1.09, 1.11, 1.12, 1.11, 1.13, 1.14],
            "close": [1.10, 1.11, 1.10, 1.12, 1.13, 1.12, 1.14, 1.15],
        }
    )

    populated = strategy.populate_indicators(frame.copy(), {"pair": "EUR/USD"})
    entry = strategy.populate_entry_trend(populated.copy(), {"pair": "EUR/USD"})
    exit_df = strategy.populate_exit_trend(populated.copy(), {"pair": "EUR/USD"})

    assert strategy.model == "hybrid"
    assert strategy.execution_mode == "dry-run"
    assert {"spread_pct", "volatility_5", "atr_14", "session_hour", "signal_strength"}.issubset(populated.columns)
    assert "enter_long" in entry.columns and "enter_short" in entry.columns
    assert "exit_long" in exit_df.columns and "exit_short" in exit_df.columns


def test_ai_baseline_signal_trace_reports_features_and_spread_block() -> None:
    from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-09-15", periods=8, freq="5min", tz="UTC"),
            "open": [1.10] * 8,
            "high": [1.20] * 8,
            "low": [1.00] * 8,
            "close": [1.10] * 8,
        }
    )
    trace = ForexAIStrategyBaseline({"forex_ai_max_spread_pct": "0.1"}).signal_trace(frame)

    assert trace["signal"] == "flat"
    assert trace["reason"] == "blocked_spread_limit"
    assert {"signalStrength", "spreadPct", "volatility", "atr", "sessionHour", "features"} <= trace.keys()


def test_ai_model_revision_contract_requires_accepted_evaluation() -> None:
    from freqtrade.forex.ai_models import ModelArtifact, ModelEvaluation, ModelRevision, ModelSpec

    spec = ModelSpec("lightgbm_regressor", "lgbm-v1", "EUR/USD", "M5", "future_return", "features-hash", 7)
    artifact = ModelArtifact.create(
        artifact_id="artifact-1",
        artifact_payload=b"model-bytes",
        training_data_hash="data-hash",
        training_start="2026-01-01T00:00:00Z",
        training_end="2026-01-31T00:00:00Z",
    )
    rejected = ModelRevision("revision-1", spec, artifact, ModelEvaluation({}, {}, {}, {}, False, ("oos_failed",)))
    with pytest.raises(ValueError, match="failed evaluation"):
        rejected.approve("operator")

    accepted = ModelRevision("revision-2", spec, artifact, ModelEvaluation({}, {}, {}, {}, True))
    approved = accepted.approve("operator")
    assert approved.approved is True
    assert approved.approved_by == "operator"


def test_forex_ai_dataset_manifest_is_reproducible_and_split() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=40, freq="5min", tz="UTC"),
            "open": [1 + index * 0.001 for index in range(40)],
            "high": [1.001 + index * 0.001 for index in range(40)],
            "low": [0.999 + index * 0.001 for index in range(40)],
            "close": [1.0005 + index * 0.001 for index in range(40)],
            "volume": [100] * 40,
        }
    )
    dataset, manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=40)
    _, repeated = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=40)

    assert manifest.pair == "EUR/USD"
    assert manifest.train_rows + manifest.validation_rows + manifest.out_of_sample_rows == len(dataset)
    assert manifest.training_data_hash == repeated.training_data_hash
    assert manifest.feature_schema_hash == repeated.feature_schema_hash


def test_lightgbm_research_model_stays_out_of_execution_path() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset
    from freqtrade.forex.ai_lgbm import LightGBMFutureReturnModel

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC"),
            "open": [1 + index * 0.001 for index in range(60)],
            "high": [1.001 + index * 0.001 for index in range(60)],
            "low": [0.999 + index * 0.001 for index in range(60)],
            "close": [1.0005 + index * 0.001 for index in range(60)],
            "volume": [100] * 60,
        }
    )
    dataset, manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=60)
    result = LightGBMFutureReturnModel(seed=7, estimators=10).fit_and_evaluate(dataset, manifest)

    assert result.revision.spec.model_type == "lightgbm_regressor"
    assert result.revision.spec.pair == "EUR/USD"
    assert result.revision.approved is False
    assert len(result.predictions) == manifest.out_of_sample_rows


def test_robust_lightgbm_evaluates_two_pairs_and_two_periods() -> None:
    from freqtrade.forex.ai_lgbm import run_robust_lightgbm

    def make_frame(offset: float) -> pd.DataFrame:
        close = [1 + offset + index * 0.001 for index in range(50)]
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=50, freq="5min", tz="UTC"),
            "open": close,
            "high": [value + 0.0005 for value in close],
            "low": [value - 0.0005 for value in close],
            "close": close,
            "volume": [100] * 50,
        })

    result = run_robust_lightgbm(
        {"EUR/USD": (make_frame(0), make_frame(0.01)), "GBP/USD": (make_frame(0.1), make_frame(0.11))},
        timeframes={"EUR/USD": "M5", "GBP/USD": "M5"},
        history_values={"EUR/USD": 50, "GBP/USD": 50},
    )

    assert result.pairs_tested == 2
    assert result.periods_tested == 2
    assert len(result.slices) == 4


def test_lightgbm_return_confidence_calibration_is_bounded_and_oos_safe() -> None:
    from freqtrade.forex.ai_lgbm import calibrate_return_confidence

    calibration = calibrate_return_confidence(
        pd.Series([0.01, -0.02, 0.03, -0.01, 0.02]),
        [0.008, -0.01, 0.02, 0.005, 0.01],
    )

    assert calibration["samples"] == 5
    assert 0 <= calibration["overallAccuracy"] <= 1
    assert all(0 <= bucket["meanConfidence"] <= 1 for bucket in calibration["buckets"])
    assert all(0 <= bucket["directionalAccuracy"] <= 1 for bucket in calibration["buckets"])


def test_lightgbm_direction_classifier_is_research_only() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset
    from freqtrade.forex.ai_lgbm import LightGBMDirectionClassifier

    close = [1 + (0.001 if index % 2 == 0 else -0.0005) * index for index in range(60)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0005 for value in close],
        "low": [value - 0.0005 for value in close],
        "close": close,
        "volume": [100] * 60,
    })
    dataset, manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=60)
    result = LightGBMDirectionClassifier(seed=7, estimators=10, neutral_band=0.00001).fit_and_evaluate(dataset, manifest)

    assert result.class_labels == ("short", "flat", "long")
    assert 0 <= result.out_of_sample_metrics["accuracy"] <= 1
    assert 0 <= result.out_of_sample_metrics["f1Macro"] <= 1
    assert result.accepted in {True, False}


def test_research_models_are_compared_on_same_oos_split() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset
    from freqtrade.forex.ai_lgbm import compare_research_models

    close = [1 + 0.001 * index for index in range(60)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0005 for value in close],
        "low": [value - 0.0005 for value in close],
        "close": close,
        "volume": [100] * 60,
    })
    dataset, manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=60)
    comparison = compare_research_models(dataset, manifest)

    assert {"regressor", "classifier", "baseline", "dataHash", "featureSchemaHash"} <= comparison.keys()
    assert comparison["regressor"]["model"] == "LightGBMRegressor"
    assert comparison["classifier"]["classes"] == ["short", "flat", "long"]


def test_run_ai_hyperopt_reports_progress_and_stops_cooperatively() -> None:
    from freqtrade.forex.ai_hyperopt import run_ai_hyperopt
    from freqtrade.forex.models import OandaInstrument

    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    close = [1.1 + 0.0001 * index for index in range(40)]
    candles = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=40, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0002 for value in close],
        "low": [value - 0.0002 for value in close],
        "close": close,
        "volume": [100] * 40,
    })

    progress: list[tuple[int, int]] = []
    result = run_ai_hyperopt(
        candles,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        entry_thresholds=(Decimal("0.1"), Decimal("0.2"), Decimal("0.3")),
        max_spreads=(Decimal("1"),),
        on_attempt=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert result.candidates_tested == 3

    stopped_progress: list[int] = []

    def should_stop() -> bool:
        return len(stopped_progress) >= 2

    stopped_result = run_ai_hyperopt(
        candles,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        entry_thresholds=(Decimal("0.1"), Decimal("0.2"), Decimal("0.3")),
        max_spreads=(Decimal("1"),),
        on_attempt=lambda done, total: stopped_progress.append(done),
        should_stop=should_stop,
    )

    assert stopped_result.candidates_tested == 2


def test_run_ai_hyperopt_robust_stops_cooperatively_with_consistent_coverage() -> None:
    from freqtrade.forex.ai_hyperopt import run_ai_hyperopt_robust
    from freqtrade.forex.models import OandaInstrument

    def make_instrument(name: str) -> OandaInstrument:
        return OandaInstrument(name=name, display_name=name.replace("_", "/"), pip_location=-4, display_precision=5, trade_units_precision=0, minimum_trade_size=Decimal("1"))

    def make_frame(offset: float) -> pd.DataFrame:
        close = [1.1 + offset + 0.0001 * index for index in range(50)]
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=50, freq="5min", tz="UTC"),
            "open": close,
            "high": [value + 0.0002 for value in close],
            "low": [value - 0.0002 for value in close],
            "close": close,
            "volume": [100] * 50,
        })

    candles_by_pair = {"EUR_USD": make_frame(0), "GBP_USD": make_frame(0.05)}
    instruments = {"EUR_USD": make_instrument("EUR_USD"), "GBP_USD": make_instrument("GBP_USD")}
    spreads = {"EUR_USD": Decimal("0.0001"), "GBP_USD": Decimal("0.0001")}

    call_count = {"n": 0}

    def on_attempt(done: int, total: int) -> None:
        call_count["n"] += 1

    def should_stop() -> bool:
        return call_count["n"] >= 3

    candidates = run_ai_hyperopt_robust(
        candles_by_pair,
        instruments,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spreads=spreads,
        max_attempts=3,
        on_attempt=on_attempt,
        should_stop=should_stop,
    )

    assert candidates
    assert all(int(row["coverage"]) == 1 for row in candidates)


def test_hyperopt_loss_functions_are_computed_and_change_ranking() -> None:
    from freqtrade.forex.ai_hyperopt import HYPEROPT_LOSS_FUNCTIONS, compute_hyperopt_objective, run_ai_hyperopt
    from freqtrade.forex.models import OandaInstrument

    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    close = [1.1 + 0.0002 * index for index in range(60)]
    candles = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0003 for value in close],
        "low": [value - 0.0003 for value in close],
        "close": close,
        "volume": [100] * 60,
    })

    for loss_name in HYPEROPT_LOSS_FUNCTIONS:
        result = run_ai_hyperopt(
            candles,
            instrument,
            starting_balance=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            spread=Decimal("0.0001"),
            entry_thresholds=(Decimal("0.1"), Decimal("0.2")),
            max_spreads=(Decimal("1"),),
            hyperopt_loss=loss_name,
        )
        assert result.candidates_tested == 2
        # objective on the best candidate must equal a fresh recomputation for the same loss.
        recomputed = compute_hyperopt_objective(result.best.validation_result, loss_name)
        assert recomputed == result.best.objective

    with pytest.raises(ValueError):
        run_ai_hyperopt(
            candles,
            instrument,
            starting_balance=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            spread=Decimal("0.0001"),
            entry_thresholds=(Decimal("0.1"),),
            max_spreads=(Decimal("1"),),
            hyperopt_loss="NotARealLoss",
        )


def test_freqai_indicator_periods_and_shifted_candles_change_feature_schema() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset

    close = [1 + 0.001 * index for index in range(60)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0005 for value in close],
        "low": [value - 0.0005 for value in close],
        "close": close,
        "volume": [100] * 60,
    })
    default_dataset, default_manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=60)
    custom_dataset, custom_manifest = build_forex_ai_dataset(
        frame,
        pair="EUR_USD",
        timeframe="M5",
        history_value=60,
        indicator_periods=(10, 20, 50),
        include_shifted_candles=3,
    )

    assert {"atr_10", "atr_20", "atr_50", "volatility_10", "volatility_20", "volatility_50"}.issubset(custom_dataset.columns)
    assert {"shift_return_1", "shift_return_2", "shift_return_3"}.issubset(custom_dataset.columns)
    assert custom_manifest.feature_schema_hash != default_manifest.feature_schema_hash
    assert custom_manifest.indicator_periods == (10, 20, 50)
    assert custom_manifest.include_shifted_candles == 3


def test_freqai_train_and_backtest_period_days_control_the_split() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset

    close = [1 + 0.0005 * index for index in range(400)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=400, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0002 for value in close],
        "low": [value - 0.0002 for value in close],
        "close": close,
        "volume": [100] * 400,
    })
    _, manifest = build_forex_ai_dataset(
        frame,
        pair="EUR_USD",
        timeframe="M5",
        history_value=400,
        train_period_days=1,
        backtest_period_days=1,
    )

    assert manifest.train_period_days == 1
    assert manifest.backtest_period_days == 1
    assert manifest.train_rows == 288
    assert manifest.validation_rows == 109
    assert manifest.out_of_sample_rows == 1


def test_freqai_weight_factor_and_di_threshold_are_applied_during_training() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset
    from freqtrade.forex.ai_lgbm import LightGBMFutureReturnModel

    close = [1 + 0.001 * index for index in range(80)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=80, freq="5min", tz="UTC"),
        "open": close,
        "high": [value + 0.0005 for value in close],
        "low": [value - 0.0005 for value in close],
        "close": close,
        "volume": [100] * 80,
    })
    dataset, manifest = build_forex_ai_dataset(frame, pair="EUR_USD", timeframe="M5", history_value=80)
    result = LightGBMFutureReturnModel(seed=7, estimators=10, weight_factor=0.1, di_threshold=0.01).fit_and_evaluate(dataset, manifest)

    assert result.revision.evaluation.trading_metrics["weightFactor"] == 0.1
    assert result.revision.evaluation.trading_metrics["diThreshold"] == 0.01
    assert result.revision.evaluation.trading_metrics["diFilteredOosRows"] >= 0
    assert len(result.predictions) == manifest.out_of_sample_rows


def test_native_ema_strategy_is_loadable_and_configurable() -> None:
    strategy = ForexEmaStrategy(
        {
            "forex_fast_period": 2,
            "forex_slow_period": 3,
        }
    )
    frame = pd.DataFrame({"close": [1.0, 1.01, 1.02, 1.01, 1.00]})

    populated = strategy.populate_indicators(frame, {"pair": "EUR/USD"})
    populated = strategy.populate_entry_trend(populated, {"pair": "EUR/USD"})
    populated = strategy.populate_exit_trend(populated, {"pair": "EUR/USD"})

    assert strategy.timeframe == "5m"
    assert strategy.fast_period == 2
    assert strategy.slow_period == 3
    assert {"fast_ema", "slow_ema", "enter_long", "exit_long"} <= set(populated.columns)
    assert not populated["enter_long"].iloc[: strategy.startup_candle_count].any()


def test_native_ema_strategy_keeps_long_and_short_signals_independent() -> None:
    strategy = ForexEmaStrategy({"forex_fast_period": 2, "forex_slow_period": 3})
    index = range(strategy.startup_candle_count + 1)
    base = pd.DataFrame(
        {
            "fast_ema": [1.0] * strategy.startup_candle_count + [1.1],
            "slow_ema": [1.0] * strategy.startup_candle_count + [1.0],
        },
        index=index,
    )

    entry = strategy.populate_entry_trend(base.copy(), {"pair": "EUR/USD"})
    exit_df = strategy.populate_exit_trend(base.copy(), {"pair": "EUR/USD"})

    assert strategy.can_short is True
    assert bool(entry.iloc[-1]["enter_long"]) is True
    assert bool(entry.iloc[-1]["enter_short"]) is False
    assert bool(exit_df.iloc[-1]["exit_long"]) is False
    assert bool(exit_df.iloc[-1]["exit_short"]) is True


def test_exit_rules_keep_long_and_short_price_semantics_independent() -> None:
    fixed = FixedStop(Decimal("0.0010"))
    target = TakeProfit(Decimal("0.0020"))
    atr = AtrStop(Decimal("1.5"))

    assert fixed.levels(side="long", entry_price=Decimal("1.1000")).stop_price == Decimal("1.0990")
    assert fixed.levels(side="short", entry_price=Decimal("1.1000")).stop_price == Decimal("1.1010")
    assert target.levels(side="long", entry_price=Decimal("1.1000")).take_profit_price == Decimal("1.1020")
    assert target.levels(side="short", entry_price=Decimal("1.1000")).take_profit_price == Decimal("1.0980")
    assert atr.levels(side="short", entry_price=Decimal("1.1000"), atr=Decimal("0.0010")).stop_price == Decimal("1.10150")


def test_trailing_stop_only_moves_in_favorable_direction() -> None:
    trailing = TrailingStop(Decimal("0.0010"))

    long_stop = trailing.stop_price(
        side="long", current_price=Decimal("1.1050"), previous_stop=Decimal("1.1030")
    )
    short_stop = trailing.stop_price(
        side="short", current_price=Decimal("1.0950"), previous_stop=Decimal("1.0970")
    )

    assert long_stop == Decimal("1.1040")
    assert short_stop == Decimal("1.0960")
    assert trailing.stop_price(
        side="long", current_price=Decimal("1.1020"), previous_stop=long_stop
    ) == long_stop
    assert trailing.stop_price(
        side="short", current_price=Decimal("1.0980"), previous_stop=short_stop
    ) == short_stop


def test_time_exit_uses_candle_age_and_validates_order() -> None:
    time_exit = TimeExit(max_candles=3)

    assert time_exit.should_exit(entry_index=4, current_index=6) is False
    assert time_exit.should_exit(entry_index=4, current_index=7) is True
    with pytest.raises(ValueError, match="ordered"):
        time_exit.should_exit(entry_index=4, current_index=3)


def test_strategy_state_round_trips_without_broker_position_data(tmp_path) -> None:
    store = ForexStrategyStateStore(tmp_path / "strategy-state.json")
    state = ForexStrategyState(
        strategy_name="ForexEmaStrategy",
        timeframe="5m",
        last_candle_time="2026-09-17T10:00:00Z",
        last_signal=Signal.SHORT,
        processed_candles=42,
        indicators={"fast_ema": "1.0990", "slow_ema": "1.1000"},
    )

    store.save(state)
    restored = ForexStrategyStateStore(store.path).load()

    assert restored == state
    assert "position" not in restored.to_dict()
    assert "units" not in restored.to_dict()


def test_strategy_state_rejects_unknown_schema(tmp_path) -> None:
    path = tmp_path / "strategy-state.json"
    path.write_text('{"schema_version": 99}', encoding="utf-8")

    with pytest.raises(ValueError, match="schema version"):
        ForexStrategyStateStore(path).load()


def test_forex_feature_pipeline_preserves_alignment_and_warmup() -> None:
    frame = pd.DataFrame({"close": [1.0, 1.01, 1.02]}, index=[10, 20, 30])
    pipeline = ForexFeaturePipeline(
        (lambda data: data.assign(momentum=data["close"].diff()),),
        warmup_candles=2,
    )

    populated = pipeline.apply(frame)
    ready = pipeline.ready_mask(populated)

    assert populated.index.equals(frame.index)
    assert len(populated) == len(frame)
    assert ready.tolist() == [False, False, True]

    with pytest.raises(ValueError, match="preserve candle length"):
        ForexFeaturePipeline((lambda data: data.iloc[:-1],)).apply(frame)


def test_forex_freqai_adapter_builds_schema_and_labels_without_leakage() -> None:
    frame = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 8)],
            "open": [1.1000, 1.1005, 1.1010, 1.1008, 1.1015, 1.1012, 1.1022],
            "high": [1.1010, 1.1015, 1.1020, 1.1018, 1.1025, 1.1020, 1.1030],
            "low": [1.0990, 1.0995, 1.1000, 1.0998, 1.1005, 1.1002, 1.1010],
            "close": [1.1000, 1.1005, 1.1010, 1.1008, 1.1015, 1.1012, 1.1022],
        }
    )
    adapter = ForexFreqAIAdapter(label_period=2)
    dataset = adapter.build_dataset(frame)

    assert "spread_points" in dataset.columns
    assert "volatility_5" in dataset.columns
    assert "atr_14" in dataset.columns
    assert "session_hour" in dataset.columns
    assert "label" in dataset.columns
    assert dataset["label"].notna().sum() == len(frame) - adapter.label_period
    assert dataset["label"].dropna().iloc[0] > 0


def test_forex_freqai_research_and_backtest_must_complete_before_dry_run() -> None:
    gate = ForexFreqAIExecutionGate()

    with pytest.raises(ValueError, match="research.*backtest.*dry-run"):
        gate.register("dry_run")

    gate.register("research")
    with pytest.raises(ValueError, match="research.*backtest.*dry-run"):
        gate.register("dry_run")

    gate.register("backtest")
    gate.set_model_metadata(
        model_version="v1",
        feature_schema_hash="schema:abc",
        training_data_hash="data:abc",
    )
    gate.register("dry_run")
    assert gate.completed == ("research", "backtest", "dry_run")


def test_native_ema_strategy_loads_through_strategy_resolver(default_conf) -> None:
    default_conf.update(
        {
            "strategy": "ForexEmaStrategy",
            "strategy_path": str(Path(__file__).parents[2] / "freqtrade/forex/strategies"),
            "forex_fast_period": 2,
            "forex_slow_period": 3,
            "trading_mode": TradingMode.FUTURES,
        }
    )

    strategy = StrategyResolver.load_strategy(default_conf)

    assert isinstance(strategy, IStrategy)
    assert strategy.__class__.__name__ == "ForexEmaStrategy"
    assert strategy.fast_period == 2
    assert strategy.slow_period == 3


def test_ema_strategy_rejects_invalid_periods() -> None:
    with pytest.raises(ValueError, match="fast_period"):
        EmaCrossStrategy(fast_period=26, slow_period=12)


def test_ema_strategy_returns_flat_without_enough_candles() -> None:
    strategy = EmaCrossStrategy(fast_period=3, slow_period=5)
    candles = pd.DataFrame({"close": [1, 2, 3, 4, 5]})

    assert strategy.signal(candles) is Signal.FLAT


def test_backtester_applies_spread_once_and_reports_trade() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.LONG, Signal.SHORT, Signal.SHORT]
    candles = pd.DataFrame(
        {
            "date": ["t0", "t1", "t2"],
            "open": [1.1000, 1.1010, 1.1020],
            "high": [1.1010, 1.1020, 1.1030],
            "low": [1.0990, 1.1000, 1.1010],
            "close": [1.1000, 1.1010, 1.1020],
        }
    )
    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
    ).run(candles)

    assert len(result.trades) == 2
    assert result.trades[0].units == 100000
    assert result.trades[0].spread_cost == Decimal("10.00000")
    assert result.trades[1].units == -99900
    assert result.trades[1].spread_cost == Decimal("9.99000")
    for trade in result.trades:
        assert trade.gross_pl - trade.spread_cost == trade.net_pl
    assert result.ending_balance == Decimal("9980.01000")


def test_backtester_applies_slippage_and_financing_costs() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.LONG, Signal.SHORT]
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1010, 1.1020],
            "low": [1.0990, 1.1000],
            "close": [1.1000, 1.1010],
        }
    )
    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0.00001"),
    ).run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.slippage_cost == Decimal("2.00000")
    assert trade.financing_cost == Decimal("2.20210")
    assert trade.net_pl == Decimal("-14.20210")
    assert result.total_costs == Decimal("14.20210")


def test_backtester_supports_multi_instrument_portfolio_and_max_open_positions() -> None:
    instrument_a = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    instrument_b = OandaInstrument(
        name="GBP_USD",
        display_name="GBP/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    eur_strategy = MagicMock()
    eur_strategy.signal.return_value = Signal.LONG
    gbp_strategy = MagicMock()
    gbp_strategy.signal.return_value = Signal.LONG
    eur_candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1010, 1.1020],
            "low": [1.0990, 1.1000],
            "close": [1.1000, 1.1010],
        }
    )
    gbp_candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.3000, 1.3010],
            "high": [1.3010, 1.3020],
            "low": [1.2990, 1.3000],
            "close": [1.3000, 1.3010],
        }
    )

    result = ForexBacktester(
        MagicMock(),
        instrument_a,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
        max_open_positions=1,
    ).run(
        {"EUR_USD": eur_candles, "GBP_USD": gbp_candles},
        strategies={
            "EUR_USD": eur_strategy,
            "GBP_USD": gbp_strategy,
        },
        instruments={
            "EUR_USD": instrument_a,
            "GBP_USD": instrument_b,
        },
    )

    assert len(result.trades) == 1
    assert result.trades[0].instrument == "EUR_USD"


def test_backtester_converts_trade_pnl_and_margin_to_account_currency() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.LONG, Signal.FLAT]
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1010, 1.1050],
            "low": [1.0990, 1.1000],
            "close": [1.1000, 1.1050],
        }
    )
    rate = ForexQuoteRate("USD", "EUR", Decimal("0.9"), "2026-01-01T00:00:00Z", "oanda")

    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
        account_currency="EUR",
        rates=(rate,),
    ).run(candles)

    assert result.account_currency == "EUR"
    assert result.trades[0].account_currency == "EUR"
    assert result.trades[0].net_pl == Decimal("351")
    assert result.ending_balance == Decimal("10351")
    assert result.margin_snapshot.used_margin == Decimal("0")


def test_backtester_supports_partial_fill_and_variable_spread() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.LONG, Signal.FLAT]
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1010, 1.1050],
            "low": [1.0990, 1.1000],
            "close": [1.1000, 1.1050],
        }
    )

    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread={
            "2026-01-01T00:00:00Z": Decimal("0.00010"),
            "2026-01-01T01:00:00Z": Decimal("0.00020"),
        },
        fill_ratio=Decimal("0.5"),
    ).run(candles)

    assert result.trades[0].units == 50000
    assert result.trades[0].spread_cost == Decimal("5.00000")
    assert result.trades[0].net_pl == Decimal("190.00000000000000000000000")
    assert result.ending_balance == Decimal("10190.00000000000000000000000")


def test_backtest_result_reports_equity_drawdown_and_period_breakdowns() -> None:
    trades = (
        BacktestTrade(
            instrument="EUR_USD",
            direction=Signal.LONG,
            units=1000,
            entry_time="2026-01-01T23:00:00Z",
            exit_time="2026-01-02T01:00:00Z",
            entry_price=Decimal("1.1000"),
            exit_price=Decimal("1.1010"),
            gross_pl=Decimal("10"),
            spread_cost=Decimal("1"),
            slippage_cost=Decimal("0"),
            financing_cost=Decimal("0"),
            net_pl=Decimal("10"),
        ),
        BacktestTrade(
            instrument="EUR_USD",
            direction=Signal.SHORT,
            units=-1000,
            entry_time="2026-01-02T02:00:00Z",
            exit_time="2026-01-08T01:00:00Z",
            entry_price=Decimal("1.1010"),
            exit_price=Decimal("1.1020"),
            gross_pl=Decimal("-20"),
            spread_cost=Decimal("1"),
            slippage_cost=Decimal("0"),
            financing_cost=Decimal("0"),
            net_pl=Decimal("-20"),
        ),
    )
    result = BacktestResult(
        starting_balance=Decimal("1000"),
        ending_balance=Decimal("990"),
        trades=trades,
    )

    assert result.trade_output[0]["net_pl"] == "10"
    assert [point.balance for point in result.equity_curve] == [
        Decimal("1000"),
        Decimal("1010"),
        Decimal("990"),
    ]
    assert result.max_drawdown == Decimal("20")
    assert result.max_drawdown_rate == Decimal("20") / Decimal("1010")
    assert result.daily_breakdown[0].period == "2026-01-02"
    assert result.daily_breakdown[0].net_pl == Decimal("10")
    assert result.weekly_breakdown[0].period == "2026-W01"
    assert result.monthly_breakdown[0].period == "2026-01"


def test_backtester_rejects_lookahead_or_recursive_strategy_source() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    base_kwargs = {
        "starting_balance": Decimal("10000"),
        "risk_fraction": Decimal("0.01"),
        "stop_pips": Decimal("10"),
        "spread": Decimal("0.00010"),
    }
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1010],
            "high": [1.1010, 1.1020],
            "low": [1.0990, 1.1000],
            "close": [1.1000, 1.1010],
        }
    )

    class LookaheadStrategy:
        def signal(self, candles: pd.DataFrame) -> Signal:
            return Signal.LONG if candles.shift(-1)["close"].iloc[-1] > 1.1 else Signal.FLAT

    class RecursiveStrategy:
        def signal(self, candles: pd.DataFrame) -> Signal:
            return self.signal(candles)

    with pytest.raises(ValueError, match="lookahead|future data"):
        ForexBacktester(LookaheadStrategy(), instrument, **base_kwargs).run(candles)

    with pytest.raises(ValueError, match="recursive"):
        ForexBacktester(RecursiveStrategy(), instrument, **base_kwargs).run(candles)

    assert ForexBacktester(EmaCrossStrategy(), instrument, **base_kwargs).run(candles).trades == ()


def test_backtest_validation_supports_train_test_and_walk_forward_splits() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 61)],
            "open": [1.1000 + i * 0.00005 for i in range(60)],
            "high": [1.1005 + i * 0.00005 for i in range(60)],
            "low": [1.0995 + i * 0.00005 for i in range(60)],
            "close": [1.1000 + i * 0.00005 for i in range(60)],
        }
    )

    validation = validate_backtest_split(
        candles,
        strategy=EmaCrossStrategy(),
        instrument=instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
    )

    assert validation["train"]["window_end"] < validation["test"]["window_start"]
    assert validation["walk_forward"][0]["window_end"] < validation["walk_forward"][1]["window_start"]
    assert validation["test"]["result"].ending_balance >= Decimal("0")


def test_backtester_uses_detail_candles_for_intrabar_long_stop() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.LONG, Signal.LONG]
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1000],
            "high": [1.1010, 1.1100],
            "low": [1.0990, 1.0900],
            "close": [1.1000, 1.1050],
        }
    )
    detail = pd.DataFrame(
        {
            "date": ["2026-01-01T01:05:00Z"],
            "high": [1.1010],
            "low": [1.0989],
        }
    )

    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
    ).run(candles, detail_candles=detail)

    assert len(result.trades) == 1
    assert result.trades[0].exit_time == "2026-01-01T01:05:00Z"
    assert result.trades[0].exit_price == Decimal("1.09905")


def test_backtester_uses_detail_candles_for_intrabar_short_take_profit() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.side_effect = [Signal.SHORT, Signal.SHORT]
    candles = pd.DataFrame(
        {
            "date": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
            "open": [1.1000, 1.1000],
            "high": [1.1010, 1.1100],
            "low": [1.0990, 1.0900],
            "close": [1.1000, 1.1050],
        }
    )
    detail = pd.DataFrame(
        {
            "date": ["2026-01-01T01:05:00Z"],
            "high": [1.1001],
            "low": [1.0989],
        }
    )

    result = ForexBacktester(
        strategy,
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
        spread=Decimal("0.00010"),
        take_profit_pips=Decimal("5"),
    ).run(candles, detail_candles=detail)

    assert len(result.trades) == 1
    assert result.trades[0].exit_time == "2026-01-01T01:05:00Z"
    assert result.trades[0].exit_price == Decimal("1.09945")


def test_hyperopt_runs_independent_validation_split() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 61)],
            "open": [1.1000 + day * 0.00005 for day in range(1, 61)],
            "high": [1.1010 + day * 0.00005 for day in range(1, 61)],
            "low": [1.0990 + day * 0.00005 for day in range(1, 61)],
            "close": [1.1000 + day * 0.00005 for day in range(1, 61)],
        }
    )
    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        fast_periods=(2, 3),
        slow_periods=(5,),
        stop_pips=(Decimal("10"),),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
    )

    assert result.validation["train"]["window_end"] < result.validation["test"]["window_start"]
    assert result.validation["walk_forward"][0]["window_end"] < result.validation["walk_forward"][1]["window_start"]
    assert result.validation["test"]["result"].ending_balance >= Decimal("0")


def test_hyperopt_selects_cost_aware_candidate() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 13)],
            "open": [1.1000 + day * 0.0002 for day in range(12)],
            "high": [1.1010 + day * 0.0002 for day in range(12)],
            "low": [1.0990 + day * 0.0002 for day in range(12)],
            "close": [1.1000 + day * 0.0002 for day in range(12)],
        }
    )
    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(candles, fast_periods=(2, 3), slow_periods=(5,), stop_pips=(Decimal("10"),))

    assert result.candidates_tested == 2
    assert result.best.fast_period in (2, 3)
    assert result.best.slow_period == 5


def test_hyperopt_requires_walk_forward_and_out_of_sample_validation() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 21)],
            "open": [1.1000 + day * 0.00015 for day in range(20)],
            "high": [1.1010 + day * 0.00015 for day in range(20)],
            "low": [1.0990 + day * 0.00015 for day in range(20)],
            "close": [1.1000 + day * 0.00015 for day in range(20)],
        }
    )

    with pytest.raises(ValueError, match="walk-forward and out-of-sample|at least 2"):
        ForexHyperopt(
            instrument,
            starting_balance=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            spread=Decimal("0.0001"),
            slippage=Decimal("0.00001"),
            financing_rate_per_day=Decimal("0"),
            quote_to_account_rate=Decimal("1"),
        ).run(
            candles,
            parameter_space=(
                {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
                {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
            ),
            train_fraction=Decimal("0.7"),
            walk_forward_steps=1,
        )

    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
    )

    assert result.candidates_tested == 2
    assert result.validation is not None
    assert len(result.validation["walk_forward"]) >= 1


def test_hyperopt_accepts_multi_metric_loss_function() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 26)],
            "open": [1.1000 + day * 0.00012 for day in range(25)],
            "high": [1.1010 + day * 0.00012 for day in range(25)],
            "low": [1.0990 + day * 0.00012 for day in range(25)],
            "close": [1.1000 + day * 0.00012 for day in range(25)],
        }
    )

    def custom_loss(result, drawdown):
        return -(result.net_pl - drawdown * Decimal("0.5")) + Decimal(len(result.trades))

    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
        loss_function=custom_loss,
    )

    assert result.candidates_tested == 2
    assert result.validation is not None
    assert len(result.validation["walk_forward"]) == 2
    assert result.best.objective <= 0


def test_hyperopt_supports_seeded_reproducibility_and_resume(tmp_path) -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 31)],
            "open": [1.1000 + day * 0.00015 for day in range(30)],
            "high": [1.1010 + day * 0.00015 for day in range(30)],
            "low": [1.0990 + day * 0.00015 for day in range(30)],
            "close": [1.1000 + day * 0.00015 for day in range(30)],
        }
    )
    save_path = tmp_path / "hyperopt_state.json"

    first = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
        random_seed=4242,
        save_path=save_path,
    )
    resumed = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
        random_seed=4242,
        resume_from=save_path,
    )

    assert first.best.fast_period == resumed.best.fast_period
    assert first.best.stop_pips == resumed.best.stop_pips
    assert first.best.objective == resumed.best.objective
    assert save_path.exists()


def test_hyperopt_requires_pair_and_period_robustness_guard() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 31)],
            "open": [1.1000 + day * 0.00015 for day in range(30)],
            "high": [1.1010 + day * 0.00015 for day in range(30)],
            "low": [1.0990 + day * 0.00015 for day in range(30)],
            "close": [1.1000 + day * 0.00015 for day in range(30)],
        }
    )

    with pytest.raises(ValueError, match="at least 2 pairs|pair robustness"):
        ForexHyperopt(
            instrument,
            starting_balance=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            spread=Decimal("0.0001"),
            slippage=Decimal("0.00001"),
            financing_rate_per_day=Decimal("0"),
            quote_to_account_rate=Decimal("1"),
        ).run(
            candles,
            parameter_space=(
                {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
                {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
            ),
            train_fraction=Decimal("0.7"),
            walk_forward_steps=2,
            pair_names=("EUR/USD",),
        )

    with pytest.raises(ValueError, match="at least 2 periods|period robustness"):
        ForexHyperopt(
            instrument,
            starting_balance=Decimal("10000"),
            risk_fraction=Decimal("0.01"),
            spread=Decimal("0.0001"),
            slippage=Decimal("0.00001"),
            financing_rate_per_day=Decimal("0"),
            quote_to_account_rate=Decimal("1"),
        ).run(
            candles,
            parameter_space=(
                {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
                {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
            ),
            train_fraction=Decimal("0.7"),
            walk_forward_steps=2,
            pair_names=("EUR/USD", "GBP/USD"),
            periods=("5m",),
        )

    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
        pair_names=("EUR/USD", "GBP/USD"),
        periods=("5m", "15m"),
    )
    assert result.best is not None
    assert result.validation is not None


def test_hyperopt_supports_parallel_execution() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    candles = pd.DataFrame(
        {
            "date": [f"2026-01-{day:02d}T00:00:00Z" for day in range(1, 31)],
            "open": [1.1000 + day * 0.00012 for day in range(30)],
            "high": [1.1010 + day * 0.00012 for day in range(30)],
            "low": [1.0990 + day * 0.00012 for day in range(30)],
            "close": [1.1000 + day * 0.00012 for day in range(30)],
        }
    )

    result = ForexHyperopt(
        instrument,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00001"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
    ).run(
        candles,
        parameter_space=(
            {"fast_period": 2, "slow_period": 5, "stop_pips": Decimal("10"), "risk_fraction": Decimal("0.01")},
            {"fast_period": 3, "slow_period": 8, "stop_pips": Decimal("15"), "risk_fraction": Decimal("0.02")},
            {"fast_period": 4, "slow_period": 10, "stop_pips": Decimal("12"), "risk_fraction": Decimal("0.015")},
        ),
        train_fraction=Decimal("0.7"),
        walk_forward_steps=2,
        parallel=True,
        workers=2,
    )

    assert result.candidates_tested == 3
    assert result.validation is not None
    assert result.best.fast_period in (2, 3, 4)


@pytest.mark.asyncio
async def test_strategy_loop_sizes_and_opens_paper_position() -> None:
    provider = AsyncMock()
    provider.fetch_ohlcv.return_value = pd.DataFrame(
        {"date": ["2026-09-16T10:00:00Z"], "close": [1.1]}
    )
    price_client = AsyncMock()
    price_client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:00:00Z",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
        )
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(price_client, gateway, ("EUR_USD",))
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    strategy = MagicMock()
    strategy.signal.return_value = Signal.LONG
    loop = DryRunStrategyLoop(
        provider,
        session,
        strategy,
        instrument,
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal("10"),
    )

    result = await loop.step("EUR/USD", "5m", candle_count=50)

    assert result.signal is Signal.LONG
    assert result.position is not None
    assert result.position.units == 100000
    assert result.position.entry_price == Decimal("1.10010")


@pytest.mark.asyncio
async def test_paper_ledger_persists_mark_and_close(tmp_path) -> None:
    price_client = AsyncMock()
    price_client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:00:00Z",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
        )
    ]
    ledger = PaperLedger(tmp_path / "paper.sqlite")
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(price_client, gateway, ("EUR_USD",), ledger=ledger)

    await session.refresh_prices()
    position = await session.open_market("EUR_USD", 1000, "ledger-entry-1")
    assert position.trade_id == 1

    price_client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:05:00Z",
            bid=Decimal("1.10100"),
            ask=Decimal("1.10110"),
        )
    ]
    await session.mark_to_market()
    assert ledger.open_trades()[0].unrealized_pl == Decimal("0.90000")

    closed = await session.close_market("EUR_USD", "ledger-exit-1")
    assert closed.unrealized_pl == Decimal("0.90000")
    assert [order.status for order in ledger.orders()] == ["filled", "filled"]
    assert ledger.open_positions() == []
    records = ledger.all_trades()
    assert records[0].status == "closed"
    assert records[0].exit_price == Decimal("1.10100")
    assert ledger.performance().realized_pl == Decimal("0.90000")
    assert ledger.performance().unrealized_pl == Decimal("0")


@pytest.mark.asyncio
async def test_dry_run_session_restores_open_positions_from_ledger(tmp_path) -> None:
    price_client = AsyncMock()
    price_client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:05:00Z",
            bid=Decimal("1.10100"),
            ask=Decimal("1.10110"),
        )
    ]
    ledger = PaperLedger(tmp_path / "resume.sqlite")
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)

    trade_id = ledger.open_trade("EUR_USD", 1000, Decimal("1.10010"))
    ledger.save_position("EUR_USD", 1000, Decimal("1.10010"), trade_id)

    session = DryRunSession(price_client, gateway, ("EUR_USD",), ledger=ledger)
    await session.refresh_prices()

    form = session.positions["EUR_USD"]
    assert form.trade_id == trade_id
    assert form.units == 1000
    assert form.entry_price == Decimal("1.10010")
    assert form.last_bid == Decimal("1.10100")
    assert form.last_ask == Decimal("1.10110")

    fresh = DryRunSession(price_client, gateway, ("EUR_USD",), ledger=ledger)
    await fresh.refresh_prices()
    assert fresh.positions["EUR_USD"].entry_price == Decimal("1.10010")


@pytest.mark.asyncio
async def test_dry_run_session_enforces_multi_pair_and_max_open_positions() -> None:
    price_client = AsyncMock()
    price_client.get_prices.return_value = [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-09-16T10:00:00Z",
            bid=Decimal("1.10000"),
            ask=Decimal("1.10010"),
        ),
        OandaPrice(
            instrument="GBP_USD",
            time="2026-09-16T10:00:00Z",
            bid=Decimal("1.30000"),
            ask=Decimal("1.30010"),
        ),
    ]
    gateway = OandaExecutionGateway(OandaSettings("token", "account"), ExecutionMode.DRY_RUN)
    session = DryRunSession(
        price_client,
        gateway,
        ("EUR_USD", "GBP_USD"),
        max_open_positions=1,
    )

    await session.refresh_prices()
    first = await session.open_market("EUR_USD", 1000, "multi-open-1")
    assert first.instrument == "EUR_USD"

    with pytest.raises(ValueError, match="max_open_positions"):
        await session.open_market("GBP_USD", 1000, "multi-open-2")

    assert len(session.positions) == 1
    assert set(session.positions) == {"EUR_USD"}


def test_strategy_step_result_serializes_json_payload() -> None:
    result = StrategyStepResult(
        signal=Signal.LONG,
        position=PaperPosition(
            instrument="EUR_USD",
            units=1000,
            entry_price=Decimal("1.10010"),
            last_bid=Decimal("1.10000"),
            last_ask=Decimal("1.10020"),
        ),
        reason="long_entry",
        order_event="filled",
        costs={"spread": "0.00020", "estimated_cost": "0.20000"},
    )

    payload = result.as_dict()
    assert payload["signal"] == "long"
    assert payload["reason"] == "long_entry"
    assert payload["order_event"] == "filled"
    assert payload["costs"]["spread"] == "0.00020"
    json.loads(json.dumps(payload))


@pytest.mark.asyncio
async def test_dry_run_worker_runs_bounded_steps_and_reports_results() -> None:
    loop = AsyncMock()
    loop.step.return_value = StrategyStepResult(signal=Signal.FLAT, position=None)
    delays: list[float] = []
    results: list[StrategyStepResult] = []
    worker = DryRunWorker(
        loop,
        WorkerConfig(pair="EUR/USD", timeframe="5m", candle_count=50, interval_seconds=300),
        sleep=AsyncMock(side_effect=lambda delay: delays.append(delay)),
        on_result=results.append,
    )

    steps = await worker.run(max_steps=2)

    assert steps == 2
    assert loop.step.await_count == 2
    assert delays == [300]
    assert len(results) == 2


@pytest.mark.asyncio
async def test_dry_run_worker_stops_cleanly_while_waiting_for_next_step() -> None:
    loop = AsyncMock()
    loop.step.return_value = StrategyStepResult(signal=Signal.FLAT, position=None)
    delays: list[float] = []

    async def slow_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(5.0)

    worker = DryRunWorker(
        loop,
        WorkerConfig(pair="EUR/USD", timeframe="5m", candle_count=50, interval_seconds=60),
        sleep=slow_sleep,
    )

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    worker.stop()
    steps = await task

    assert steps == 1
    assert loop.step.await_count == 1
    assert delays == [60]


@pytest.mark.asyncio
async def test_dry_run_worker_run_forever_stops_on_stop_event() -> None:
    loop = AsyncMock()
    loop.step.return_value = StrategyStepResult(signal=Signal.FLAT, position=None)
    delays: list[float] = []

    async def slow_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(5.0)

    worker = DryRunWorker(
        loop,
        WorkerConfig(pair="EUR/USD", timeframe="5m", candle_count=50, interval_seconds=60),
        sleep=slow_sleep,
    )

    task = asyncio.create_task(worker.run(max_steps=None))
    await asyncio.sleep(0.05)
    worker.stop()
    steps = await task

    assert steps == 1
    assert delays == [60]


def test_dry_run_worker_rejects_non_dry_run_mode() -> None:
    with pytest.raises(ValueError, match="dry_run execution mode"):
        DryRunWorker(
            AsyncMock(),
            WorkerConfig(pair="EUR/USD"),
            execution_mode=ExecutionMode.PRACTICE,
        )


@pytest.mark.asyncio
async def test_oanda_client_reads_instruments_prices_and_candles() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/instruments"):
            return httpx.Response(
                200,
                json={
                    "instruments": [
                        {
                            "name": "EUR_USD",
                            "displayName": "EUR/USD",
                            "pipLocation": -4,
                            "displayPrecision": 5,
                            "tradeUnitsPrecision": 0,
                            "minimumTradeSize": "1",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/pricing"):
            return httpx.Response(
                200,
                json={
                    "prices": [
                        {
                            "instrument": "EUR_USD",
                            "time": "2026-09-15T10:00:00.000000000Z",
                            "bids": [{"price": "1.10000"}],
                            "asks": [{"price": "1.10012"}],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "time": "2026-09-15T10:00:00.000000000Z",
                        "complete": True,
                        "volume": 42,
                        "mid": {
                            "o": "1.10000",
                            "h": "1.10100",
                            "l": "1.09900",
                            "c": "1.10050",
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        headers={"Authorization": "Bearer test-token"},
        transport=transport,
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            instruments = await client.get_instruments(["EUR_USD"])
            prices = await client.get_prices(["EUR_USD"])
            candles = await client.get_candles("EUR_USD", "M5", count=1)

    assert instruments[0].pip_size == Decimal("0.0001")
    assert prices[0].spread == Decimal("0.00012")
    assert candles[0].close == Decimal("1.10050")
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[2].url.params["granularity"] == "M5"


@pytest.mark.asyncio
async def test_oanda_client_supports_stop_take_profit_modify_and_cancel_orders() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(
                200, json={"orderCancelTransaction": {"id": "cancel-tx", "orderID": "stop-1"}, "lastTransactionID": "cancel-tx"}
            )
        if request.method == "PUT":
            return httpx.Response(
                200, json={"orderCreateTransaction": {"id": "stop-1"}, "lastTransactionID": "modify-tx"}
            )
        body = request.content.decode()
        order_type = "STOP" if '"price":"1.0990"' in body else "TAKE_PROFIT"
        return httpx.Response(
            201,
            json={
                "orderCreateTransaction": {"id": f"{order_type.lower()}-1", "units": "1000"},
                "lastTransactionID": f"{order_type.lower()}-tx",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=transport,
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            stop = await client.create_stop_order("EUR_USD", 1000, "1.0990", client_order_id="stop-1")
            target = await client.create_take_profit_order("EUR_USD", 1000, "1.1050", client_order_id="target-1")
            modified = await client.modify_order("stop-1", price="1.0985")
            canceled = await client.cancel_order("stop-1")

    assert stop.order_id == "stop-1"
    assert target.order_id == "take_profit-1"
    assert modified.transaction_id == "modify-tx"
    assert canceled.order_id == "stop-1"
    assert [request.method for request in requests] == ["POST", "POST", "PUT", "DELETE"]
    assert '"type":"STOP"' in requests[0].content.decode()
    assert '"timeInForce":"GTC"' in requests[1].content.decode()


@pytest.mark.asyncio
async def test_oanda_client_retries_transient_errors_and_pages_large_candle_requests() -> None:
    page_calls: list[tuple[str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page_calls.append((request.url.path, request.url.params.get("from")))
        if request.url.path.endswith("/candles") and request.url.params.get("from") is None:
            if len(page_calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"errorCode": "RATE_LIMITED"})
            return httpx.Response(
                200,
                json={
                    "candles": [
                        {
                            "time": "2026-09-15T10:00:00.000000000Z",
                            "complete": True,
                            "volume": 42,
                            "mid": {"o": "1.10000", "h": "1.10100", "l": "1.09900", "c": "1.10050"},
                        },
                        {
                            "time": "2026-09-15T10:05:00.000000000Z",
                            "complete": True,
                            "volume": 42,
                            "mid": {"o": "1.10050", "h": "1.10150", "l": "1.09950", "c": "1.10060"},
                        },
                    ]
                },
            )
        if request.url.path.endswith("/candles") and request.url.params.get("from") == "2026-09-15T10:05:00Z":
            return httpx.Response(
                200,
                json={
                    "candles": [
                        {
                            "time": "2026-09-15T10:10:00.000000000Z",
                            "complete": True,
                            "volume": 42,
                            "mid": {"o": "1.10060", "h": "1.10160", "l": "1.09960", "c": "1.10070"},
                        }
                    ]
                },
            )
        return httpx.Response(500, json={"errorCode": "SERVER_ERROR"})

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            candles = await client.get_candles("EUR_USD", "M5", count=10000)

    assert len(candles) == 3
    assert page_calls[0][0].endswith("/candles")
    assert page_calls[2][1] == "2026-09-15T10:05:00Z"


@pytest.mark.asyncio
async def test_oanda_client_retries_disconnect_and_timeout_during_practice_order() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("network disconnected", request=request)
        if attempts == 2:
            raise httpx.ReadTimeout("broker timeout", request=request)
        return httpx.Response(
            201,
            json={
                "orderCreateTransaction": {"id": "practice-order-1", "units": "1000"},
                "orderFillTransaction": {
                    "id": "practice-fill-1",
                    "orderID": "practice-order-1",
                    "instrument": "EUR_USD",
                    "units": "1000",
                    "price": "1.10012",
                },
                "lastTransactionID": "practice-fill-1",
            },
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient(
            "ignored", "account", http_client=http_client, max_retries=2, retry_backoff=0
        ) as client:
            result = await client.create_market_order(
                "EUR_USD",
                1000,
                stop_loss_price="1.09900",
                take_profit_price="1.10200",
                client_order_id="practice-network-1",
            )

    assert result.order_id == "practice-order-1"
    assert result.fill_price == Decimal("1.10012")
    assert attempts == 3


@pytest.mark.asyncio
async def test_oanda_client_reads_account_and_open_positions() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(
                200,
                json={
                    "account": {
                        "id": "account",
                        "currency": "USD",
                        "balance": "10000.00",
                        "NAV": "10100.00",
                        "marginAvailable": "9000.00",
                        "unrealizedPL": "100.00",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "positions": [
                    {
                        "instrument": "EUR_USD",
                        "long": {"units": "1000", "averagePrice": "1.1000"},
                        "short": {"units": "-250", "averagePrice": "1.1020"},
                        "unrealizedPL": "1.25",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            account = await client.get_account_summary()
            positions = await client.get_open_positions()

    assert account.margin_available == Decimal("9000.00")
    assert positions[0].net_units == Decimal("750")
    assert positions[0].long_units == Decimal("1000")
    assert positions[0].short_units == Decimal("-250")


@pytest.mark.asyncio
async def test_oanda_client_creates_signed_market_order_with_attached_risk_orders() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            201,
            json={
                "lastTransactionID": "11",
                "orderCreateTransaction": {"id": "10", "units": "-1000"},
                "orderFillTransaction": {"price": "1.10012"},
            },
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            result = await client.create_market_order(
                "EUR_USD",
                -1000,
                stop_loss_price="1.10200",
                take_profit_price="1.09600",
                client_order_id="strategy-entry-1",
            )

    assert result.order_id == "10"
    assert result.fill_price == Decimal("1.10012")
    assert captured is not None
    order = captured.read().decode()
    assert '"units":"-1000"' in order
    assert '"price":"1.10200"' in order
    assert '"price":"1.09600"' in order
    assert '"id":"strategy-entry-1"' in order


@pytest.mark.asyncio
async def test_oanda_client_reports_api_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorCode": "UNAUTHORIZED"})

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            with pytest.raises(Exception, match="401"):
                await client.get_prices(["EUR_USD"])
