"""Versioned model contracts for the safe Forex FreqAI pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    model_type: str
    model_version: str
    pair: str
    timeframe: str
    label: str
    feature_schema_hash: str
    seed: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    artifact_hash: str
    training_data_hash: str
    training_start: str
    training_end: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        artifact_payload: bytes,
        training_data_hash: str,
        training_start: str,
        training_end: str,
    ) -> "ModelArtifact":
        return cls(
            artifact_id=artifact_id,
            artifact_hash=hashlib.sha256(artifact_payload).hexdigest(),
            training_data_hash=training_data_hash,
            training_start=training_start,
            training_end=training_end,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelEvaluation:
    train_metrics: dict[str, Decimal | float | int]
    validation_metrics: dict[str, Decimal | float | int]
    out_of_sample_metrics: dict[str, Decimal | float | int]
    trading_metrics: dict[str, Decimal | float | int]
    accepted: bool
    rejection_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))


@dataclass(frozen=True)
class ModelRevision:
    revision_id: str
    spec: ModelSpec
    artifact: ModelArtifact
    evaluation: ModelEvaluation
    approved: bool = False
    approved_by: str | None = None
    approved_at: str | None = None

    def approve(self, reviewer: str) -> "ModelRevision":
        if not self.evaluation.accepted:
            raise ValueError("cannot approve a model revision that failed evaluation")
        return ModelRevision(
            revision_id=self.revision_id,
            spec=self.spec,
            artifact=self.artifact,
            evaluation=self.evaluation,
            approved=True,
            approved_by=reviewer,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "spec": self.spec.as_dict(),
            "artifact": self.artifact.as_dict(),
            "evaluation": self.evaluation.as_dict(),
            "approved": self.approved,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }
