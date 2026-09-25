"""Durable OANDA transaction streaming with reconnect and cursor recovery."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from freqtrade.forex.transactions import OandaTransaction


class TransactionStreamClient(Protocol):
    def iter_transactions(
        self, *, since_transaction_id: str | None = None
    ) -> AsyncIterator[OandaTransaction]: ...


class TransactionCursorStore:
    """Persist the last consumed transaction ID using an atomic file replace."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        cursor = payload.get("last_transaction_id")
        return str(cursor) if cursor is not None else None

    def save(self, transaction_id: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as temporary:
            json.dump({"last_transaction_id": transaction_id}, temporary)
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)


class OandaTransactionStream:
    """Reconnect a transaction stream and persist progress before yielding."""

    def __init__(
        self,
        client: TransactionStreamClient,
        cursor_store: TransactionCursorStore,
        *,
        retry_delays: Sequence[float] = (1.0, 2.0, 5.0, 15.0),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not retry_delays:
            raise ValueError("at least one retry delay is required")
        self.client = client
        self.cursor_store = cursor_store
        self.retry_delays = tuple(retry_delays)
        self.sleep = sleep

    async def run(self) -> AsyncIterator[OandaTransaction]:
        retry_index = 0
        while True:
            cursor = self.cursor_store.load()
            try:
                received = False
                async for transaction in self.client.iter_transactions(
                    since_transaction_id=cursor
                ):
                    self.cursor_store.save(transaction.transaction_id)
                    cursor = transaction.transaction_id
                    received = True
                    retry_index = 0
                    yield transaction
                if received:
                    retry_index = 0
                delay = self.retry_delays[min(retry_index, len(self.retry_delays) - 1)]
                retry_index += 1
                await self.sleep(delay)
            except Exception:
                delay = self.retry_delays[min(retry_index, len(self.retry_delays) - 1)]
                retry_index += 1
                await self.sleep(delay)
