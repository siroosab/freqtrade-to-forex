"""Research-only LightGBM future-return model for the Forex FreqAI pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error

from freqtrade.forex.ai_dataset import ForexAIDatasetManifest
from freqtrade.forex.ai_dataset import build_forex_ai_dataset
from freqtrade.forex.ai_models import ModelArtifact, ModelEvaluation, ModelRevision, ModelSpec
from freqtrade.forex.ai_strategy import ForexAIStrategyBaseline


def _recency_weights(count: int, weight_factor: float) -> np.ndarray:
    """Exponentially discount older training rows; weight_factor=0 disables weighting."""
    if count <= 0:
        return np.ones(0)
    if weight_factor <= 0:
        return np.ones(count)
    decay = min(max(float(weight_factor), 0.0), 0.999999)
    exponents = np.arange(count - 1, -1, -1)
    return (1.0 - decay) ** exponents


def _dissimilarity_index(train_features: pd.DataFrame, eval_features: pd.DataFrame) -> pd.Series:
    """Distance of each evaluation row from the train distribution, normalized by average train spread."""
    mean = train_features.mean()
    std = train_features.std(ddof=0).replace(0, 1.0)
    train_z = (train_features - mean) / std
    center = train_z.mean()
    avg_train_distance = float(((train_z - center) ** 2).sum(axis=1).pow(0.5).mean()) or 1.0
    eval_z = (eval_features - mean) / std
    distance = ((eval_z - center) ** 2).sum(axis=1).pow(0.5)
    return distance / avg_train_distance


def _apply_di_filter(
    actual: pd.Series, predicted: Any, di_scores: pd.Series, di_threshold: float
) -> tuple[pd.Series, Any, int]:
    """Drop rows whose Dissimilarity Index exceeds di_threshold; di_threshold<=0 disables the filter."""
    if di_threshold <= 0:
        return actual.reset_index(drop=True), pd.Series(predicted).reset_index(drop=True), 0
    mask = di_scores.reset_index(drop=True) <= di_threshold
    actual_reset = actual.reset_index(drop=True)
    predicted_reset = pd.Series(predicted).reset_index(drop=True)
    return actual_reset[mask].reset_index(drop=True), predicted_reset[mask].reset_index(drop=True), int((~mask).sum())


@dataclass(frozen=True)
class LightGBMResearchResult:
    revision: ModelRevision
    predictions: tuple[float, ...]

    def comparison(self, dataset: pd.DataFrame, manifest: ForexAIDatasetManifest) -> dict[str, Any]:
        oos_start = manifest.train_rows + manifest.validation_rows
        actual = dataset.iloc[oos_start:]["label"].astype(float).reset_index(drop=True)
        predicted = pd.Series(self.predictions, dtype=float)
        directional_accuracy = float((((actual >= 0) == (predicted >= 0)).mean())) if len(actual) else 0.0
        return {
            "model": "LightGBMRegressor",
            "modelVersion": self.revision.spec.model_version,
            "oosMae": str(self.revision.evaluation.out_of_sample_metrics["mae"]),
            "oosRmse": str(self.revision.evaluation.out_of_sample_metrics["rmse"]),
            "directionalAccuracy": f"{directional_accuracy:.4f}",
            "accepted": self.revision.evaluation.accepted,
            "rejectionReasons": list(self.revision.evaluation.rejection_reasons),
        }


@dataclass(frozen=True)
class RobustLightGBMResult:
    slices: tuple[LightGBMResearchResult, ...]
    pairs_tested: int
    periods_tested: int
    accepted: bool
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LightGBMClassifierResult:
    model_version: str
    pair: str
    timeframe: str
    feature_schema_hash: str
    training_data_hash: str
    validation_metrics: dict[str, float]
    out_of_sample_metrics: dict[str, float]
    class_labels: tuple[str, ...]
    accepted: bool
    rejection_reasons: tuple[str, ...]
    di_filtered_validation_rows: int = 0
    di_filtered_oos_rows: int = 0


def calibrate_return_confidence(actual: pd.Series, predicted: Any, *, bins: int = 5) -> dict[str, Any]:
    """Calibrate directional confidence from out-of-sample return predictions."""
    actual_values = pd.Series(actual).astype(float).reset_index(drop=True)
    predicted_values = pd.Series(predicted).astype(float).reset_index(drop=True)
    if len(actual_values) != len(predicted_values) or len(actual_values) == 0:
        raise ValueError("confidence calibration requires equal non-empty actual and predicted values")
    direction_correct = ((actual_values >= 0) == (predicted_values >= 0)).astype(float)
    confidence = predicted_values.abs() / max(predicted_values.abs().max(), 1e-12)
    bucket_count = max(2, min(bins, len(confidence)))
    ranked = confidence.rank(method="first").astype(int) - 1
    buckets = (ranked * bucket_count // len(confidence)).clip(upper=bucket_count - 1)
    calibration = []
    for bucket in sorted(set(int(value) for value in buckets)):
        mask = buckets == bucket
        calibration.append({
            "bucket": bucket + 1,
            "samples": int(mask.sum()),
            "meanConfidence": float(confidence[mask].mean()),
            "directionalAccuracy": float(direction_correct[mask].mean()),
        })
    return {"samples": len(actual_values), "overallAccuracy": float(direction_correct.mean()), "buckets": calibration}


def run_robust_lightgbm(
    candles_by_pair: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    *,
    timeframes: dict[str, str],
    history_values: dict[str, int],
    label_period: int = 2,
    seed: int = 7,
) -> RobustLightGBMResult:
    """Evaluate LightGBM independently across two or more pairs and periods."""
    if len(candles_by_pair) < 2:
        raise ValueError("robust LightGBM evaluation requires at least two pairs")
    slices: list[LightGBMResearchResult] = []
    reasons: list[str] = []
    for pair, periods in candles_by_pair.items():
        if len(periods) < 2:
            raise ValueError("robust LightGBM evaluation requires two periods per pair")
        for period_index, candles in enumerate(periods[:2]):
            dataset, manifest = build_forex_ai_dataset(
                candles,
                pair=pair,
                timeframe=timeframes[pair],
                history_value=history_values[pair],
                label_period=label_period,
            )
            result = LightGBMFutureReturnModel(seed=seed).fit_and_evaluate(dataset, manifest)
            slices.append(result)
            if not result.revision.evaluation.accepted:
                reasons.extend(f"{pair}:period_{period_index}:{reason}" for reason in result.revision.evaluation.rejection_reasons)
    return RobustLightGBMResult(tuple(slices), len(candles_by_pair), 2, not reasons, tuple(reasons))


class LightGBMFutureReturnModel:
    """Train/evaluate LightGBM on the train split only; never executes orders."""

    def __init__(
        self,
        *,
        seed: int = 7,
        estimators: int = 100,
        learning_rate: float = 0.05,
        weight_factor: float = 0.0,
        di_threshold: float = 0.0,
    ) -> None:
        self.seed = seed
        self.estimators = estimators
        self.learning_rate = learning_rate
        self.weight_factor = weight_factor
        self.di_threshold = di_threshold
        self.model = LGBMRegressor(
            n_estimators=estimators,
            learning_rate=learning_rate,
            num_leaves=15,
            max_depth=5,
            random_state=seed,
            verbosity=-1,
        )
        self.feature_columns: tuple[str, ...] = ()

    def fit_and_evaluate(
        self,
        dataset: pd.DataFrame,
        manifest: ForexAIDatasetManifest,
        *,
        model_version: str = "lightgbm-return-v1",
    ) -> LightGBMResearchResult:
        feature_columns = manifest.feature_columns
        self.feature_columns = feature_columns
        train_rows = manifest.train_rows
        validation_end = train_rows + manifest.validation_rows
        x_train = dataset.loc[: train_rows - 1, feature_columns]
        y_train = dataset.loc[: train_rows - 1, "label"]
        sample_weight = _recency_weights(len(x_train), self.weight_factor)
        self.model.fit(x_train, y_train, sample_weight=sample_weight)

        train_prediction = self.model.predict(x_train)
        validation = dataset.iloc[train_rows:validation_end]
        oos = dataset.iloc[validation_end:]
        validation_prediction = self.model.predict(validation.loc[:, feature_columns])
        oos_prediction = self.model.predict(oos.loc[:, feature_columns])
        train_metrics = self._metrics(y_train, train_prediction)

        validation_di = _dissimilarity_index(x_train, validation.loc[:, feature_columns])
        oos_di = _dissimilarity_index(x_train, oos.loc[:, feature_columns])
        validation_actual, validation_filtered_pred, validation_filtered_rows = _apply_di_filter(validation["label"], validation_prediction, validation_di, self.di_threshold)
        oos_actual, oos_filtered_pred, oos_filtered_rows = _apply_di_filter(oos["label"], oos_prediction, oos_di, self.di_threshold)
        validation_metrics = self._metrics(validation_actual, validation_filtered_pred) if len(validation_actual) else {"mae": Decimal("0"), "rmse": Decimal("0")}
        oos_metrics = self._metrics(oos_actual, oos_filtered_pred) if len(oos_actual) else {"mae": Decimal("0"), "rmse": Decimal("0")}
        accepted = bool(len(oos_actual) > 0 and oos_metrics["mae"] <= validation_metrics["mae"] * 2)
        reasons = () if accepted else ("oos_mae_degraded",) if len(oos_actual) else ("oos_fully_filtered_by_di_threshold",)
        spec = ModelSpec(model_version=model_version, model_type="lightgbm_regressor", pair=manifest.pair, timeframe=manifest.timeframe, label="future_return", feature_schema_hash=manifest.feature_schema_hash, seed=self.seed)
        artifact = ModelArtifact.create(artifact_id=f"{manifest.pair}-{manifest.timeframe}-{model_version}", artifact_payload=repr(self.model.get_params()).encode(), training_data_hash=manifest.training_data_hash, training_start=manifest.train_range[0], training_end=manifest.train_range[1])
        evaluation = ModelEvaluation(
            train_metrics=train_metrics,
            validation_metrics=validation_metrics,
            out_of_sample_metrics=oos_metrics,
            trading_metrics={
                "validation_rows": len(validation),
                "oos_rows": len(oos),
                "diThreshold": self.di_threshold,
                "diFilteredValidationRows": validation_filtered_rows,
                "diFilteredOosRows": oos_filtered_rows,
                "weightFactor": self.weight_factor,
            },
            accepted=accepted,
            rejection_reasons=reasons,
        )
        revision = ModelRevision(revision_id=f"{manifest.pair}-{model_version}-{manifest.training_data_hash}", spec=spec, artifact=artifact, evaluation=evaluation)
        return LightGBMResearchResult(revision=revision, predictions=tuple(float(value) for value in oos_prediction))

    @staticmethod
    def _metrics(actual: pd.Series, predicted: Any) -> dict[str, Decimal]:
        return {
            "mae": Decimal(str(mean_absolute_error(actual, predicted))),
            "rmse": Decimal(str(mean_squared_error(actual, predicted) ** 0.5)),
        }


class LightGBMDirectionClassifier:
    """Research-only three-way direction classifier with a neutral return band."""

    def __init__(
        self,
        *,
        seed: int = 7,
        estimators: int = 100,
        neutral_band: float = 0.0001,
        weight_factor: float = 0.0,
        di_threshold: float = 0.0,
    ) -> None:
        if neutral_band < 0:
            raise ValueError("neutral_band must be non-negative")
        self.neutral_band = neutral_band
        self.weight_factor = weight_factor
        self.di_threshold = di_threshold
        self.model = LGBMClassifier(
            n_estimators=estimators,
            learning_rate=0.05,
            num_leaves=15,
            max_depth=5,
            random_state=seed,
            verbosity=-1,
        )

    def fit_and_evaluate(
        self,
        dataset: pd.DataFrame,
        manifest: ForexAIDatasetManifest,
        *,
        model_version: str = "lightgbm-direction-v1",
    ) -> LightGBMClassifierResult:
        labels = self._labels(dataset["label"])
        train_end = manifest.train_rows
        validation_end = train_end + manifest.validation_rows
        features = list(manifest.feature_columns)
        x_train = dataset.iloc[:train_end][features]
        y_train = labels.iloc[:train_end]
        sample_weight = _recency_weights(len(x_train), self.weight_factor)
        self.model.fit(x_train, y_train, sample_weight=sample_weight)
        validation = dataset.iloc[train_end:validation_end]
        oos = dataset.iloc[validation_end:]
        validation_pred_raw = self.model.predict(validation[features])
        oos_pred_raw = self.model.predict(oos[features])

        validation_di = _dissimilarity_index(x_train, validation[features])
        oos_di = _dissimilarity_index(x_train, oos[features])
        validation_actual, validation_pred, validation_filtered_rows = _apply_di_filter(labels.iloc[train_end:validation_end], validation_pred_raw, validation_di, self.di_threshold)
        oos_actual, oos_pred, oos_filtered_rows = _apply_di_filter(labels.iloc[validation_end:], oos_pred_raw, oos_di, self.di_threshold)
        validation_metrics = self._metrics(validation_actual, validation_pred) if len(validation_actual) else {"accuracy": 0.0, "f1Macro": 0.0}
        oos_metrics = self._metrics(oos_actual, oos_pred) if len(oos_actual) else {"accuracy": 0.0, "f1Macro": 0.0}
        accepted = bool(len(oos_actual) > 0 and oos_metrics["f1Macro"] >= validation_metrics["f1Macro"] * 0.5)
        reasons = () if accepted else ("oos_f1_degraded",) if len(oos_actual) else ("oos_fully_filtered_by_di_threshold",)
        return LightGBMClassifierResult(
            model_version=model_version,
            pair=manifest.pair,
            timeframe=manifest.timeframe,
            feature_schema_hash=manifest.feature_schema_hash,
            training_data_hash=manifest.training_data_hash,
            validation_metrics=validation_metrics,
            out_of_sample_metrics=oos_metrics,
            class_labels=("short", "flat", "long"),
            accepted=accepted,
            rejection_reasons=reasons,
            di_filtered_validation_rows=validation_filtered_rows,
            di_filtered_oos_rows=oos_filtered_rows,
        )

    def _labels(self, returns: pd.Series) -> pd.Series:
        return returns.map(lambda value: "long" if value > self.neutral_band else "short" if value < -self.neutral_band else "flat")

    @staticmethod
    def _metrics(actual: pd.Series, predicted: Any) -> dict[str, float]:
        return {
            "accuracy": float(accuracy_score(actual, predicted)),
            "f1Macro": float(f1_score(actual, predicted, average="macro", zero_division=0)),
        }


def compare_research_models(
    dataset: pd.DataFrame,
    manifest: ForexAIDatasetManifest,
    *,
    seed: int = 7,
    weight_factor: float = 0.0,
    di_threshold: float = 0.0,
) -> dict[str, Any]:
    """Compare regressor, classifier, and deterministic baseline on one OOS split."""
    regressor = LightGBMFutureReturnModel(seed=seed, estimators=100, weight_factor=weight_factor, di_threshold=di_threshold).fit_and_evaluate(dataset, manifest)
    classifier = LightGBMDirectionClassifier(seed=seed, estimators=100, weight_factor=weight_factor, di_threshold=di_threshold).fit_and_evaluate(dataset, manifest)
    baseline = ForexAIStrategyBaseline()
    oos_start = manifest.train_rows + manifest.validation_rows
    oos = dataset.iloc[oos_start:]
    baseline_signals = [baseline.signal(oos.iloc[:index + 1]).value for index in range(len(oos))]
    return {
        "regressor": {
            **regressor.comparison(dataset, manifest),
            "rejectionReasons": list(regressor.revision.evaluation.rejection_reasons),
            "diFilteredOosRows": regressor.revision.evaluation.trading_metrics.get("diFilteredOosRows", 0),
        },
        "classifier": {
            "model": "LightGBMDirectionClassifier",
            "modelVersion": classifier.model_version,
            "accuracy": classifier.out_of_sample_metrics["accuracy"],
            "f1Macro": classifier.out_of_sample_metrics["f1Macro"],
            "accepted": classifier.accepted,
            "rejectionReasons": list(classifier.rejection_reasons),
            "classes": list(classifier.class_labels),
            "diFilteredOosRows": classifier.di_filtered_oos_rows,
        },
        "baseline": {
            "model": "ForexAIStrategyBaseline",
            "oosSamples": len(baseline_signals),
            "nonFlatSignals": sum(signal != "flat" for signal in baseline_signals),
        },
        "dataset": {
            "pair": manifest.pair,
            "timeframe": manifest.timeframe,
            "trainRows": manifest.train_rows,
            "validationRows": manifest.validation_rows,
            "oosRows": manifest.out_of_sample_rows,
            "indicatorPeriods": list(manifest.indicator_periods),
            "includeShiftedCandles": manifest.include_shifted_candles,
            "trainPeriodDays": manifest.train_period_days,
            "backtestPeriodDays": manifest.backtest_period_days,
            "weightFactor": weight_factor,
            "diThreshold": di_threshold,
        },
        "dataHash": manifest.training_data_hash,
        "featureSchemaHash": manifest.feature_schema_hash,
    }
