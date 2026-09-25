"""Persistent SQLite ledger for simulated forex trades and P/L."""

import sqlite3
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


PERSISTENCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PaperTradeRecord:
    trade_id: int
    instrument: str
    units: int
    entry_price: Decimal
    exit_price: Decimal | None
    realized_pl: Decimal | None
    unrealized_pl: Decimal | None
    status: str


@dataclass(frozen=True)
class PaperOrderRecord:
    order_id: str
    client_order_id: str
    instrument: str
    units: int
    status: str
    fill_price: Decimal | None


@dataclass(frozen=True)
class PaperPositionRecord:
    instrument: str
    units: int
    entry_price: Decimal
    trade_id: int


@dataclass(frozen=True)
class PaperPerformance:
    realized_pl: Decimal
    unrealized_pl: Decimal
    closed_trades: int
    open_trades: int


class PaperLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forex_schema_migrations (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    entry_price TEXT NOT NULL,
                    exit_price TEXT,
                    realized_pl TEXT,
                    unrealized_pl TEXT,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)")}
            if "unrealized_pl" not in columns:
                connection.execute("ALTER TABLE paper_trades ADD COLUMN unrealized_pl TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    instrument TEXT NOT NULL,
                    units INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fill_price TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    instrument TEXT PRIMARY KEY,
                    units INTEGER NOT NULL,
                    entry_price TEXT NOT NULL,
                    trade_id INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO forex_schema_migrations (component, version)
                VALUES ('trade_order', ?)
                ON CONFLICT(component) DO UPDATE SET version = MAX(version, excluded.version)
                """,
                (PERSISTENCE_SCHEMA_VERSION,),
            )

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM forex_schema_migrations WHERE component = 'trade_order'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def record_order(
        self,
        *,
        order_id: str,
        client_order_id: str,
        instrument: str,
        units: int,
        status: str,
        fill_price: Decimal | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_orders
                    (order_id, client_order_id, instrument, units, status, fill_price)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status = excluded.status,
                    fill_price = excluded.fill_price
                """,
                (order_id, client_order_id, instrument, units, status, str(fill_price) if fill_price is not None else None),
            )

    def orders(self) -> list[PaperOrderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT order_id, client_order_id, instrument, units, status, fill_price "
                "FROM paper_orders ORDER BY rowid"
            ).fetchall()
        return [
            PaperOrderRecord(
                order_id=row[0],
                client_order_id=row[1],
                instrument=row[2],
                units=int(row[3]),
                status=row[4],
                fill_price=Decimal(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]

    def save_position(self, instrument: str, units: int, entry_price: Decimal, trade_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_positions (instrument, units, entry_price, trade_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instrument) DO UPDATE SET
                    units = excluded.units,
                    entry_price = excluded.entry_price,
                    trade_id = excluded.trade_id
                """,
                (instrument, units, str(entry_price), trade_id),
            )

    def remove_position(self, instrument: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM paper_positions WHERE instrument = ?", (instrument,))

    def open_positions(self) -> list[PaperPositionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT instrument, units, entry_price, trade_id FROM paper_positions ORDER BY instrument"
            ).fetchall()
        return [
            PaperPositionRecord(row[0], int(row[1]), Decimal(row[2]), int(row[3]))
            for row in rows
        ]

    def open_trade(self, instrument: str, units: int, entry_price: Decimal) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO paper_trades (instrument, units, entry_price, status) VALUES (?, ?, ?, ?)",
                (instrument, units, str(entry_price), "open"),
            )
            return int(cursor.lastrowid)

    def close_trade(self, trade_id: int, exit_price: Decimal, realized_pl: Decimal) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE paper_trades
                SET exit_price = ?, realized_pl = ?, status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'open'
                """,
                (str(exit_price), str(realized_pl), trade_id),
            )

    def mark_trade(self, trade_id: int, unrealized_pl: Decimal) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE paper_trades SET unrealized_pl = ? WHERE id = ? AND status = 'open'",
                (str(unrealized_pl), trade_id),
            )

    def open_trades(self) -> list[PaperTradeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, instrument, units, entry_price, exit_price, realized_pl, status, unrealized_pl "
                "FROM paper_trades WHERE status = 'open' ORDER BY id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def all_trades(self) -> list[PaperTradeRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, instrument, units, entry_price, exit_price, realized_pl, status, unrealized_pl "
                "FROM paper_trades ORDER BY id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def performance(self) -> PaperPerformance:
        records = self.all_trades()
        return PaperPerformance(
            realized_pl=sum(
                (record.realized_pl or Decimal("0") for record in records if record.status == "closed"),
                Decimal("0"),
            ),
            unrealized_pl=sum(
                (record.unrealized_pl or Decimal("0") for record in records if record.status == "open"),
                Decimal("0"),
            ),
            closed_trades=sum(record.status == "closed" for record in records),
            open_trades=sum(record.status == "open" for record in records),
        )

    def backup_to(self, destination: Path) -> Path:
        """Create a consistent SQLite backup without copying a live file directly."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        if temporary.exists():
            temporary.unlink()
        source = self._connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.replace(temporary, destination)
        return destination

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _record(row: tuple[Any, ...]) -> PaperTradeRecord:
        return PaperTradeRecord(
            trade_id=int(row[0]),
            instrument=row[1],
            units=int(row[2]),
            entry_price=Decimal(row[3]),
            exit_price=Decimal(row[4]) if row[4] is not None else None,
            realized_pl=Decimal(row[5]) if row[5] is not None else None,
            status=row[6],
            unrealized_pl=Decimal(row[7]) if row[7] is not None else None,
        )


class NativeTradeOrderStore:
    """Migration-safe native-compatible facade over the SQLite forex ledger.

    The facade intentionally preserves the existing paper tables and public record
    types. A future native persistence migration can replace this backend without
    changing strategy or execution callers.
    """

    def __init__(self, path: Path) -> None:
        self.ledger = PaperLedger(path)

    @property
    def schema_version(self) -> int:
        return self.ledger.schema_version

    def record_order(self, **kwargs: Any) -> None:
        self.ledger.record_order(**kwargs)

    def orders(self) -> list[PaperOrderRecord]:
        return self.ledger.orders()

    def open_trade(self, instrument: str, units: int, entry_price: Decimal) -> int:
        return self.ledger.open_trade(instrument, units, entry_price)

    def close_trade(self, trade_id: int, exit_price: Decimal, realized_pl: Decimal) -> None:
        self.ledger.close_trade(trade_id, exit_price, realized_pl)

    def trades(self) -> list[PaperTradeRecord]:
        return self.ledger.all_trades()


def attach_native_forex_persistence(config: dict[str, Any]) -> dict[str, Any]:
    """Attach the shared FX persistence facade to a native command config."""
    if config.get("exchange", {}).get("name", "").lower() != "oanda":
        return config
    from freqtrade.forex.native_core import attach_native_forex_adapters

    attach_native_forex_adapters(config)
    exchange = config.get("exchange", {})
    path = exchange.get(
        "oanda_persistence_path",
        config.get("forex_persistence_path", "user_data/oanda/native-trade-order.sqlite"),
    )
    config["forex_persistence_store"] = NativeTradeOrderStore(Path(path))
    return config
