"""Mode-gated order execution for the OANDA forex adapter."""

import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from freqtrade.forex.config import OandaSettings
from freqtrade.forex.models import OandaEnvironment, OandaOrderResult
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.order_validation import ProtectiveStopPolicy
from freqtrade.forex.position_semantics import PositionMode, apply_order, close_by_opposite
from freqtrade.forex.risk_limits import RiskControl
from freqtrade.forex.state import OandaAccountState, OandaPosition
from freqtrade.forex.transactions import OandaTransaction, OrderLifecycle, OrderStateMachine


class ExecutionMode(StrEnum):
    BACKTEST = "backtest"
    HYPEROPT = "hyperopt"
    DRY_RUN = "dry_run"
    PRACTICE = "practice"
    LIVE = "live"


class ExecutionModeError(ValueError):
    """Raised when an execution mode is inconsistent with its environment."""


class IdempotencyError(ValueError):
    """Raised when a client order id is reused for a different request."""


class OandaOrderClient(Protocol):
    async def create_market_order(
        self,
        instrument: str,
        units: int,
        *,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        client_order_id: str | None = None,
    ) -> OandaOrderResult: ...

    async def get_account_summary(self) -> OandaAccountState: ...

    async def get_open_positions(self) -> list[OandaPosition]: ...


@dataclass(frozen=True)
class ExecutionResult:
    order_id: str
    transaction_id: str
    instrument: str
    units: int
    fill_price: str | None
    simulated: bool
    reconciliation: "ReconciliationSnapshot | None" = None
    simulated_fill_price: str | None = None
    price_difference: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "transaction_id": self.transaction_id,
            "instrument": self.instrument,
            "units": self.units,
            "fill_price": self.fill_price,
            "simulated": self.simulated,
            "simulated_fill_price": self.simulated_fill_price,
            "price_difference": self.price_difference,
            "reconciliation": self.reconciliation,
        }


@dataclass(frozen=True)
class ReconciliationSnapshot:
    account: OandaAccountState | None
    positions: tuple[OandaPosition, ...]
    simulated: bool


class OandaExecutionGateway:
    """Keep simulation modes unable to send orders to OANDA."""

    def __init__(
        self,
        settings: OandaSettings,
        mode: ExecutionMode,
        *,
        client: OandaOrderClient | None = None,
        protective_stop_policy: ProtectiveStopPolicy | None = None,
        risk_control: RiskControl | None = None,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self.client = client
        self.protective_stop_policy = protective_stop_policy or ProtectiveStopPolicy()
        self.risk_control = risk_control
        self.order_state = OrderStateMachine()
        self._idempotency_lock = asyncio.Lock()
        self._order_requests: dict[str, tuple[object, ...]] = {}
        self._order_results: dict[str, ExecutionResult] = {}
        self._validate_mode()

    async def submit_market_order(
        self,
        instrument: str,
        units: int,
        *,
        simulated_fill_price: str | None = None,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        no_stop_reason: str | None = None,
        client_order_id: str,
    ) -> ExecutionResult:
        if units == 0:
            raise ValueError("order units cannot be zero")
        if not client_order_id:
            raise ValueError("client_order_id is required for idempotent execution")
        if self.risk_control is not None:
            self.risk_control.validate()
        self.protective_stop_policy.validate(
            stop_loss_price=stop_loss_price, no_stop_reason=no_stop_reason
        )

        request = (
            instrument,
            units,
            simulated_fill_price,
            stop_loss_price,
            take_profit_price,
            no_stop_reason,
        )
        async with self._idempotency_lock:
            previous_request = self._order_requests.get(client_order_id)
            if previous_request is not None and previous_request != request:
                raise IdempotencyError("client_order_id was reused for a different order")
            if client_order_id in self._order_results:
                return self._order_results[client_order_id]
            self._order_requests[client_order_id] = request
            try:
                result = await self._submit_market_order(
                    instrument,
                    units,
                    simulated_fill_price=simulated_fill_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    client_order_id=client_order_id,
                )
            except Exception:
                self._order_requests.pop(client_order_id, None)
                raise
            self._order_results[client_order_id] = result
            return result

    async def _submit_market_order(
        self,
        instrument: str,
        units: int,
        *,
        simulated_fill_price: str | None,
        stop_loss_price: str | None,
        take_profit_price: str | None,
        client_order_id: str,
    ) -> ExecutionResult:
        if self.mode in {
            ExecutionMode.BACKTEST,
            ExecutionMode.HYPEROPT,
            ExecutionMode.DRY_RUN,
        }:
            return ExecutionResult(
                order_id=f"sim-{client_order_id}",
                transaction_id=f"sim-tx-{client_order_id}",
                instrument=instrument,
                units=units,
                fill_price=simulated_fill_price,
                simulated=True,
                simulated_fill_price=simulated_fill_price,
                price_difference="0" if simulated_fill_price is not None else None,
            )
        if self.client is None:
            raise RuntimeError("an OANDA client is required for broker execution")
        result = await self.client.create_market_order(
            instrument,
            units,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            client_order_id=client_order_id,
        )
        return ExecutionResult(
            order_id=result.order_id,
            transaction_id=result.transaction_id,
            instrument=instrument,
            units=units,
            fill_price=str(result.fill_price) if result.fill_price is not None else None,
            simulated=False,
            simulated_fill_price=simulated_fill_price,
            price_difference=(
                str(Decimal(str(result.fill_price)) - Decimal(simulated_fill_price))
                if result.fill_price is not None and simulated_fill_price is not None
                else None
            ),
        )

    async def submit_market_order_and_reconcile(
        self,
        instrument: str,
        units: int,
        *,
        simulated_fill_price: str | None = None,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
        no_stop_reason: str | None = None,
        client_order_id: str,
    ) -> ExecutionResult:
        """Submit one order and immediately read broker account and positions."""
        result = await self.submit_market_order(
            instrument,
            units,
            simulated_fill_price=simulated_fill_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            no_stop_reason=no_stop_reason,
            client_order_id=client_order_id,
        )
        return replace(result, reconciliation=await self.reconcile())

    async def close_position(
        self,
        instrument: str,
        current_units: int,
        *,
        client_order_id: str,
    ) -> ExecutionResult:
        transition = close_by_opposite(current_units=current_units, mode=PositionMode.NETTING)
        return await self.submit_market_order_and_reconcile(
            instrument,
            transition.submitted_units,
            no_stop_reason="netting position close",
            client_order_id=client_order_id,
        )

    async def reverse_position(
        self,
        instrument: str,
        current_units: int,
        *,
        stop_loss_price: str,
        take_profit_price: str | None = None,
        client_order_id: str,
    ) -> ExecutionResult:
        transition = apply_order(
            current_units=current_units,
            requested_units=-2 * current_units,
            mode=PositionMode.NETTING,
        )
        return await self.submit_market_order_and_reconcile(
            instrument,
            transition.submitted_units,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            client_order_id=client_order_id,
        )

    async def reconcile(self) -> ReconciliationSnapshot:
        if self.mode in {
            ExecutionMode.BACKTEST,
            ExecutionMode.HYPEROPT,
            ExecutionMode.DRY_RUN,
        }:
            return ReconciliationSnapshot(account=None, positions=(), simulated=True)
        if self.client is None:
            raise RuntimeError("an OANDA client is required for broker reconciliation")
        account, positions = await _load_broker_state(self.client)
        return ReconciliationSnapshot(
            account=account,
            positions=tuple(positions),
            simulated=False,
        )

    def apply_transaction(self, transaction: OandaTransaction) -> OrderLifecycle | None:
        return self.order_state.apply(transaction)

    def _validate_mode(self) -> None:
        if self.mode is ExecutionMode.PRACTICE and self.settings.environment is not OandaEnvironment.PRACTICE:
            raise ExecutionModeError("practice mode requires the OANDA Practice environment")
        if self.mode is ExecutionMode.LIVE and self.settings.environment is not OandaEnvironment.LIVE:
            raise ExecutionModeError("live mode requires the OANDA Live environment")
        if self.mode in {ExecutionMode.PRACTICE, ExecutionMode.LIVE} and self.client is None:
            raise ExecutionModeError("broker execution requires an OANDA client")


async def _load_broker_state(
    client: OandaOrderClient,
) -> tuple[OandaAccountState, list[OandaPosition]]:
    account, positions = await asyncio.gather(
        client.get_account_summary(), client.get_open_positions()
    )
    return account, positions
