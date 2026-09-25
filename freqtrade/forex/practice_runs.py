"""Durable records for multi-session OANDA Practice runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PracticeRunRecord:
    run_id: str
    session: str
    started_at: str
    ended_at: str
    instruments: tuple[str, ...]
    status: str
    order_count: int = 0
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.session.strip():
            raise ValueError("session must not be empty")
        if self.status not in {"completed", "failed", "aborted"}:
            raise ValueError("unsupported Practice run status")
        if self.order_count < 0:
            raise ValueError("order_count must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session": self.session,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "instruments": list(self.instruments),
            "status": self.status,
            "order_count": self.order_count,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PracticeRunRecord":
        return cls(
            run_id=str(payload["run_id"]),
            session=str(payload["session"]),
            started_at=str(payload["started_at"]),
            ended_at=str(payload["ended_at"]),
            instruments=tuple(str(item) for item in payload.get("instruments", [])),
            status=str(payload["status"]),
            order_count=int(payload.get("order_count", 0)),
            notes=str(payload.get("notes", "")),
        )


class PracticeRunRecorder:
    """Append-only JSONL storage for auditable Practice session results."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: PracticeRunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            stream.flush()

    def records(self) -> tuple[PracticeRunRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[PracticeRunRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(PracticeRunRecord.from_dict(json.loads(line)))
        return tuple(records)
