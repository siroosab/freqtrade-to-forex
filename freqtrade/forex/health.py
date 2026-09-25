"""Read-only OANDA connectivity and Practice health checks."""

from dataclasses import dataclass
from decimal import Decimal

from freqtrade.forex.models import OandaInstrument, OandaPrice
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.state import OandaAccountState


@dataclass(frozen=True)
class OandaHealthReport:
    account: OandaAccountState
    instruments: tuple[OandaInstrument, ...]
    prices: tuple[OandaPrice, ...]

    @property
    def healthy(self) -> bool:
        return bool(self.account.account_id and self.instruments and self.prices)

    @property
    def instruments_by_name(self) -> dict[str, OandaInstrument]:
        return {instrument.name: instrument for instrument in self.instruments}

    @property
    def total_spread(self) -> Decimal:
        return sum((price.spread for price in self.prices), Decimal("0"))


class OandaHealthCheck:
    """Perform only read-only requests against OANDA."""

    def __init__(self, client: OandaClient) -> None:
        self.client = client

    async def run(self, instruments: tuple[str, ...]) -> OandaHealthReport:
        account, metadata, prices = await _read_health_data(self.client, instruments)
        missing = set(instruments) - {instrument.name for instrument in metadata}
        if missing:
            raise ValueError(f"OANDA instruments not available: {', '.join(sorted(missing))}")
        missing_prices = set(instruments) - {price.instrument for price in prices}
        if missing_prices:
            raise ValueError(f"OANDA prices not available: {', '.join(sorted(missing_prices))}")
        return OandaHealthReport(
            account=account,
            instruments=tuple(metadata),
            prices=tuple(prices),
        )


async def _read_health_data(
    client: OandaClient, instruments: tuple[str, ...]
) -> tuple[OandaAccountState, list[OandaInstrument], list[OandaPrice]]:
    import asyncio

    return await asyncio.gather(
        client.get_account_summary(),
        client.get_instruments(instruments),
        client.get_prices(instruments),
    )
