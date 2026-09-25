"""Live-price paper trading session for OANDA dry-run mode."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from freqtrade.forex.execution import ExecutionMode, OandaExecutionGateway
from freqtrade.forex.ledger import PaperLedger
from freqtrade.forex.models import OandaPrice


class OandaPriceClient(Protocol):
    async def get_prices(self, instruments: tuple[str, ...]) -> list[OandaPrice]: ...


@dataclass(frozen=True)
class PaperPosition:
    instrument: str
    units: int
    entry_price: Decimal
    last_bid: Decimal
    last_ask: Decimal
    trade_id: int | None = None
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None

    @property
    def unrealized_pl(self) -> Decimal:
        if self.units > 0:
            return (self.last_bid - self.entry_price) * self.units
        return (self.entry_price - self.last_ask) * abs(self.units)


@dataclass(frozen=True)
class PaperAccountState:
    balance: Decimal
    equity: Decimal
    used_margin: Decimal
    free_margin: Decimal
    daily_loss: Decimal


class DryRunSession:
    """Read live OANDA prices while keeping all positions in memory."""

    def __init__(
        self,
        price_client: OandaPriceClient,
        gateway: OandaExecutionGateway,
        instruments: tuple[str, ...],
        *,
        ledger: PaperLedger | None = None,
        starting_balance: Decimal = Decimal("0"),
        leverage: Decimal = Decimal("1"),
        max_open_positions: int | None = None,
    ) -> None:
        if gateway.mode is not ExecutionMode.DRY_RUN:
            raise ValueError("DryRunSession requires the dry_run execution mode")
        self.price_client = price_client
        self.gateway = gateway
        self.instruments = instruments
        self.prices: dict[str, OandaPrice] = {}
        self.positions: dict[str, PaperPosition] = {}
        self.ledger = ledger
        if starting_balance < 0 or leverage <= 0:
            raise ValueError("starting balance must be non-negative and leverage positive")
        if max_open_positions is not None and max_open_positions < 1:
            raise ValueError("max_open_positions must be at least 1 when set")
        self.starting_balance = starting_balance
        self.leverage = leverage
        self.max_open_positions = max_open_positions
        self._daily_realized_pl = Decimal("0")

    def account_state(self) -> PaperAccountState:
        realized_pl = (
            self.ledger.performance().realized_pl
            if self.ledger is not None
            else self._daily_realized_pl
        )
        unrealized_pl = sum(
            (position.unrealized_pl for position in self.positions.values()), Decimal("0")
        )
        used_margin = sum(
            (
                Decimal(abs(position.units)) * position.entry_price / self.leverage
                for position in self.positions.values()
            ),
            Decimal("0"),
        )
        balance = self.starting_balance + realized_pl
        equity = balance + unrealized_pl
        return PaperAccountState(
            balance=balance,
            equity=equity,
            used_margin=used_margin,
            free_margin=equity - used_margin,
            daily_loss=max(-self._daily_realized_pl, Decimal("0")),
        )

    async def refresh_prices(self) -> dict[str, OandaPrice]:
        prices = await self.price_client.get_prices(self.instruments)
        self.prices = {price.instrument: price for price in prices}
        self.restore_positions_from_ledger()
        return self.prices

    def restore_positions_from_ledger(self) -> dict[str, PaperPosition]:
        if self.ledger is None:
            return {}

        restored: dict[str, PaperPosition] = {}
        for record in self.ledger.open_positions():
            if record.instrument in self.positions:
                continue
            if record.instrument not in self.prices:
                raise RuntimeError(
                    f"no current price for {record.instrument}; call refresh_prices before restore"
                )
            price = self.prices[record.instrument]
            restored[record.instrument] = PaperPosition(
                instrument=record.instrument,
                units=record.units,
                entry_price=record.entry_price,
                last_bid=price.bid,
                last_ask=price.ask,
                trade_id=record.trade_id,
            )

        self.positions.update(restored)
        return restored

    async def open_market(
        self,
        instrument: str,
        units: int,
        client_order_id: str,
        *,
        stop_loss_price: str | None = None,
        take_profit_price: str | None = None,
    ) -> PaperPosition:
        if self.max_open_positions is not None and len(self.positions) >= self.max_open_positions:
            raise ValueError(
                "max_open_positions exceeded: cannot open a new paper position while "
                f"{len(self.positions)} position(s) are already open"
            )
        price = self._price_for(instrument)
        fill_price = price.ask if units > 0 else price.bid
        result = await self.gateway.submit_market_order(
            instrument,
            units,
            simulated_fill_price=str(fill_price),
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            no_stop_reason=(
                None
                if stop_loss_price is not None
                else "paper session protective-stop trigger is not enabled yet"
            ),
            client_order_id=client_order_id,
        )
        if self.ledger is not None:
            self.ledger.record_order(
                order_id=result.order_id,
                client_order_id=client_order_id,
                instrument=instrument,
                units=units,
                status="filled",
                fill_price=Decimal(result.fill_price or fill_price),
            )
        position = PaperPosition(
            instrument=instrument,
            units=units,
            entry_price=Decimal(result.fill_price or fill_price),
            last_bid=price.bid,
            last_ask=price.ask,
            stop_loss_price=Decimal(stop_loss_price) if stop_loss_price is not None else None,
            take_profit_price=Decimal(take_profit_price) if take_profit_price is not None else None,
        )
        if self.ledger is not None:
            position = PaperPosition(
                instrument=position.instrument,
                units=position.units,
                entry_price=position.entry_price,
                last_bid=position.last_bid,
                last_ask=position.last_ask,
                trade_id=self.ledger.open_trade(instrument, units, position.entry_price),
                stop_loss_price=position.stop_loss_price,
                take_profit_price=position.take_profit_price,
            )
            self.ledger.save_position(
                instrument, position.units, position.entry_price, position.trade_id
            )
        self.positions[instrument] = position
        return position

    async def close_market(self, instrument: str, client_order_id: str) -> PaperPosition:
        try:
            position = self.positions[instrument]
        except KeyError as exc:
            raise ValueError(f"no paper position for {instrument}") from exc
        price = self._price_for(instrument)
        result = await self.gateway.submit_market_order(
            instrument,
            -position.units,
            simulated_fill_price=str(price.bid if position.units > 0 else price.ask),
            no_stop_reason="close order reduces an existing position",
            client_order_id=client_order_id,
        )
        if self.ledger is not None:
            self.ledger.record_order(
                order_id=result.order_id,
                client_order_id=client_order_id,
                instrument=instrument,
                units=-position.units,
                status="filled",
                fill_price=price.bid if position.units > 0 else price.ask,
            )
        closed = PaperPosition(
            instrument=position.instrument,
            units=position.units,
            entry_price=position.entry_price,
            last_bid=price.bid,
            last_ask=price.ask,
            trade_id=position.trade_id,
            stop_loss_price=position.stop_loss_price,
            take_profit_price=position.take_profit_price,
        )
        if self.ledger is not None and position.trade_id is not None:
            self.ledger.close_trade(position.trade_id, price.bid if position.units > 0 else price.ask, closed.unrealized_pl)
            self.ledger.remove_position(instrument)
        self._daily_realized_pl += closed.unrealized_pl
        del self.positions[instrument]
        return closed

    async def mark_to_market(self) -> tuple[PaperPosition, ...]:
        await self.refresh_prices()
        self.positions = {
            instrument: PaperPosition(
                instrument=position.instrument,
                units=position.units,
                entry_price=position.entry_price,
                last_bid=self.prices[instrument].bid,
                last_ask=self.prices[instrument].ask,
                trade_id=position.trade_id,
                stop_loss_price=position.stop_loss_price,
                take_profit_price=position.take_profit_price,
            )
            for instrument, position in self.positions.items()
            if instrument in self.prices
        }
        if self.ledger is not None:
            for position in self.positions.values():
                if position.trade_id is not None:
                    self.ledger.mark_trade(position.trade_id, position.unrealized_pl)
        triggered = [
            position
            for position in self.positions.values()
            if self._protective_level_triggered(position)
        ]
        for position in triggered:
            await self.close_market(
                position.instrument,
                f"paper-trigger-{position.instrument}-{self.prices[position.instrument].time}",
            )
        return tuple(self.positions.values())

    @staticmethod
    def _protective_level_triggered(position: PaperPosition) -> bool:
        exit_price = position.last_bid if position.units > 0 else position.last_ask
        if position.stop_loss_price is not None:
            if position.units > 0 and exit_price <= position.stop_loss_price:
                return True
            if position.units < 0 and exit_price >= position.stop_loss_price:
                return True
        if position.take_profit_price is not None:
            if position.units > 0 and exit_price >= position.take_profit_price:
                return True
            if position.units < 0 and exit_price <= position.take_profit_price:
                return True
        return False

    def _price_for(self, instrument: str) -> OandaPrice:
        try:
            return self.prices[instrument]
        except KeyError as exc:
            raise RuntimeError(f"no current price for {instrument}; call refresh_prices first") from exc
