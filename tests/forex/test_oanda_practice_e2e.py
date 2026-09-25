"""Opt-in OANDA Practice end-to-end checks.

Read-only checks require OANDA_PRACTICE_E2E=1. Order checks additionally require
OANDA_PRACTICE_E2E_ORDERS=1 and must only use a disposable Practice account.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from freqtrade.forex.config import OandaSettings
from freqtrade.forex.execution import ExecutionMode, OandaExecutionGateway
from freqtrade.forex.health import OandaHealthCheck
from freqtrade.forex.models import OandaEnvironment
from freqtrade.forex.oanda import OandaClient


pytestmark = pytest.mark.asyncio


def _settings() -> OandaSettings:
    if os.getenv("OANDA_PRACTICE_E2E") != "1":
        pytest.skip("set OANDA_PRACTICE_E2E=1 to run OANDA Practice E2E checks")
    settings = OandaSettings.from_environment()
    if settings.environment is not OandaEnvironment.PRACTICE:
        pytest.fail("Practice E2E checks require OANDA_ENVIRONMENT=practice")
    if settings.execution_mode != ExecutionMode.PRACTICE:
        pytest.fail("Practice E2E checks require OANDA_EXECUTION_MODE=practice")
    return settings


def _instrument(settings: OandaSettings) -> str:
    instrument = os.getenv("OANDA_E2E_INSTRUMENT", settings.instruments[0])
    if instrument not in settings.instruments:
        pytest.fail("OANDA_E2E_INSTRUMENT must be included in OANDA_INSTRUMENTS")
    return instrument


async def test_practice_e2e_health_uses_only_allowed_instrument() -> None:
    settings = _settings()
    instrument = _instrument(settings)

    async with OandaClient(
        settings.token,
        settings.account_id,
        environment=settings.environment,
    ) as client:
        report = await OandaHealthCheck(client).run((instrument,))

    assert report.healthy
    assert tuple(item.name for item in report.instruments) == (instrument,)
    assert tuple(item.instrument for item in report.prices) == (instrument,)
    assert report.account.account_id == settings.account_id


async def test_practice_e2e_order_reconcile_and_netting_close() -> None:
    if os.getenv("OANDA_PRACTICE_E2E_ORDERS") != "1":
        pytest.skip("set OANDA_PRACTICE_E2E_ORDERS=1 to place Practice E2E orders")
    settings = _settings()
    instrument_name = _instrument(settings)
    pair = instrument_name.replace("_", "/")

    async with OandaClient(
        settings.token,
        settings.account_id,
        environment=settings.environment,
    ) as client:
        metadata = (await client.get_instruments((instrument_name,)))[0]
        price = (await client.get_prices((instrument_name,)))[0]
        units = int(os.getenv("OANDA_E2E_UNITS", str(int(metadata.minimum_trade_size))))
        if units <= 0:
            pytest.fail("OANDA_E2E_UNITS must be positive")
        stop_price = price.midpoint - metadata.pip_size * Decimal("10")
        target_price = price.midpoint + metadata.pip_size * Decimal("10")
        gateway = OandaExecutionGateway(settings, ExecutionMode.PRACTICE, client=client)
        result = await gateway.submit_market_order_and_reconcile(
            instrument_name,
            units,
            simulated_fill_price=str(price.midpoint),
            stop_loss_price=str(stop_price),
            take_profit_price=str(target_price),
            client_order_id=f"e2e-entry-{instrument_name.lower()}",
        )
        assert not result.simulated
        assert result.reconciliation is not None
        assert result.reconciliation.account is not None
        position = next(
            (item for item in result.reconciliation.positions if item.instrument == instrument_name),
            None,
        )
        if position is None or position.net_units == 0:
            pytest.fail(f"Practice order did not create an observable {pair} net position")

        try:
            closed = await gateway.close_position(
                instrument_name,
                int(position.net_units),
                client_order_id=f"e2e-close-{instrument_name.lower()}",
            )
            assert not closed.simulated
            assert closed.reconciliation is not None
            remaining = next(
                (item for item in closed.reconciliation.positions if item.instrument == instrument_name),
                None,
            )
            assert remaining is None or remaining.net_units == 0
        finally:
            # The close above is the normal cleanup path. A failed close is surfaced
            # rather than issuing an untracked second order automatically.
            pass
