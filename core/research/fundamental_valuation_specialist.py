"""PIT-safe Fundamental/Valuation Specialist using admitted filing observations.

This module deliberately has no network client.  Raw captures must cross an
external quarantine/admission boundary before they can be normalized here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
from typing import Any, Mapping, Sequence

import pandas as pd

from core.features.pit_feature_contract import DECIMAL_CONTEXT
from core.research.specialist_signals import SpecialistSignal, _decimal, _time


SCHEMA_VERSION = "fundamental-valuation-pit-v1"
FEATURE_VERSION = "filing-quality-valuation-revision-v1"
SPECIALIST_VERSION = "fundamental-valuation-dispersion-v1"
MAX_STALENESS_DAYS = 120
REQUIRED_METRICS = (
    "earnings_yield",
    "fcf_yield",
    "roic",
    "estimate_revision",
    "valuation_dispersion",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be decimal-compatible") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _clip(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("-1"), value))


@dataclass(frozen=True)
class FundamentalObservation:
    observation_id: str
    symbol: str
    fiscal_period: str
    effective_at: str
    reported_at: str
    available_at: str
    retrieved_at: str
    observation_cutoff_at: str
    revision: int
    prior_revision_sha256: str | None
    metrics: Mapping[str, str]
    provenance: Mapping[str, Any]
    record_sha256: str

    def __post_init__(self) -> None:
        effective = _time(self.effective_at, "effective_at")
        reported = _time(self.reported_at, "reported_at")
        available = _time(self.available_at, "available_at")
        retrieved = _time(self.retrieved_at, "retrieved_at")
        cutoff = _time(self.observation_cutoff_at, "observation_cutoff_at")
        if not effective <= reported <= available <= retrieved or cutoff != available:
            raise ValueError("fundamental timestamps violate the five-timestamp PIT contract")
        if self.revision < 1 or (self.revision == 1) != (self.prior_revision_sha256 is None):
            raise ValueError("fundamental revision chain is invalid")
        if set(self.metrics) != set(REQUIRED_METRICS):
            raise ValueError("fundamental metric vector is incomplete")
        for name, value in self.metrics.items():
            resolved = _finite(value, name)
            if name == "valuation_dispersion" and not Decimal("-1") <= resolved <= Decimal("1"):
                raise ValueError("valuation_dispersion must be bounded within [-1, 1]")
        if not self.provenance.get("source_payload_sha256"):
            raise ValueError("fundamental provenance requires its quarantined source hash")
        material = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "record_sha256"}
        if _sha(material) != self.record_sha256:
            raise ValueError("fundamental record SHA-256 is invalid")


def build_fundamental_artifact(
    rows: Sequence[Mapping[str, Any]], *, retrieved_at: str, partition_role: str = "TRAIN"
) -> dict[str, Any]:
    """Normalize already-quarantined deterministic rows; never fetch a provider."""
    if partition_role != "TRAIN":
        raise ValueError("fundamental development is restricted to TRAIN")
    retrieved = _time(retrieved_at, "retrieved_at")
    records = []
    parents: dict[tuple[str, str], str] = {}
    parent_availability: dict[tuple[str, str], datetime] = {}
    for raw in sorted(rows, key=lambda row: (row["symbol"], row["fiscal_period"], int(row["revision"]))):
        symbol = str(raw["symbol"]).strip().upper()
        fiscal_period = str(raw["fiscal_period"]).strip()
        revision = int(raw["revision"])
        key = (symbol, fiscal_period)
        prior = parents.get(key)
        if (revision == 1) != (prior is None) or revision != 1 + sum(
            record["symbol"] == symbol and record["fiscal_period"] == fiscal_period
            for record in records
        ):
            raise ValueError("fundamental input revisions are missing or out of order")
        available = _time(raw["available_at"], "available_at")
        if available > retrieved:
            raise ValueError("fundamental evidence was not retrieved yet")
        if key in parent_availability and available <= parent_availability[key]:
            raise ValueError(
                "fundamental revision must become available after its parent"
            )
        metrics = {name: _decimal(_finite(raw["metrics"][name], name)) for name in REQUIRED_METRICS}
        material = {
            "observation_id": "FUND-" + hashlib.sha256(f"{symbol}:{fiscal_period}:{revision}".encode()).hexdigest()[:32].upper(),
            "symbol": symbol,
            "fiscal_period": fiscal_period,
            "effective_at": _time(raw["effective_at"], "effective_at").isoformat(),
            "reported_at": _time(raw["reported_at"], "reported_at").isoformat(),
            "available_at": available.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "observation_cutoff_at": available.isoformat(),
            "revision": revision,
            "prior_revision_sha256": prior,
            "metrics": metrics,
            "provenance": {
                "source_payload_sha256": str(raw["source_payload_sha256"]),
                "source_locator": str(raw["source_locator"]),
                "normalizer_version": SCHEMA_VERSION,
            },
        }
        material["record_sha256"] = _sha(material)
        record = FundamentalObservation(**material)
        records.append(material)
        parents[key] = record.record_sha256
        parent_availability[key] = available
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "partition_role": partition_role,
        "feature_version": FEATURE_VERSION,
        "records": records,
        "validation_data_read": False,
        "untouched_test_included": False,
        "external_data_calls": False,
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


class FundamentalValuationSpecialistBot:
    """Independent bounded quality/valuation/revision opinion."""

    specialist_id = "FUNDAMENTAL_VALUATION"
    version = SPECIALIST_VERSION

    def __init__(self, artifact: Mapping[str, Any], *, expected_sha256: str) -> None:
        material = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != _sha(material) or artifact.get("artifact_sha256") != expected_sha256:
            raise ValueError("fundamental artifact differs from its admitted SHA-256")
        if artifact.get("schema_version") != SCHEMA_VERSION or artifact.get("partition_role") != "TRAIN":
            raise ValueError("fundamental artifact is not an admitted TRAIN artifact")
        if artifact.get("validation_data_read") is not False or artifact.get("untouched_test_included") is not False:
            raise ValueError("fundamental artifact crossed a sealed partition")
        self._records = tuple(FundamentalObservation(**row) for row in artifact["records"])

    @staticmethod
    def _score(metrics: Mapping[str, str]) -> Decimal:
        with localcontext(DECIMAL_CONTEXT):
            absolute_valuation = _clip(((Decimal(metrics["earnings_yield"]) + Decimal(metrics["fcf_yield"])) / Decimal("2") - Decimal("0.04")) / Decimal("0.04"))
            valuation = (absolute_valuation + Decimal(metrics["valuation_dispersion"])) / Decimal("2")
            quality = _clip((Decimal(metrics["roic"]) - Decimal("0.10")) / Decimal("0.10"))
            revision = _clip(Decimal(metrics["estimate_revision"]) / Decimal("0.10"))
            return _clip((valuation + quality + revision) / Decimal("3"))

    def score_tick(self, symbol: str, *, decision_at: str | datetime) -> SpecialistSignal:
        decision = _time(decision_at, "decision_at")
        resolved = str(symbol).strip().upper()
        available = [row for row in self._records if row.symbol == resolved and _time(row.available_at, "available_at") <= decision]
        if not available:
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="ABSTAIN",
                evidence_count=0, evidence_sha256=_sha([]), reason="NO_FUNDAMENTAL_COVERAGE",
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        latest = max(available, key=lambda row: (_time(row.available_at, "available_at"), row.revision))
        age = decision - _time(latest.available_at, "available_at")
        if age > timedelta(days=MAX_STALENESS_DAYS):
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="STALE",
                maximum_input_available_at=latest.available_at, evidence_count=1,
                evidence_sha256=_sha([latest.record_sha256]), reason="FUNDAMENTAL_EVIDENCE_STALE",
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        return SpecialistSignal(
            specialist_id=self.specialist_id, specialist_version=self.version,
            symbol=resolved, decision_at=decision.isoformat(), score=self._score(latest.metrics),
            maximum_input_available_at=latest.available_at, evidence_count=1,
            evidence_sha256=_sha([latest.record_sha256]), reason="PIT_QUALITY_VALUATION_REVISION_SCORE",
            model_version=self.version, feature_version=FEATURE_VERSION,
        )

    def score_frame(self, decisions: pd.DataFrame) -> pd.DataFrame:
        if set(decisions.columns) != {"symbol", "decision_at"}:
            raise ValueError("decision frame requires exactly symbol and decision_at")
        rows = []
        for row in decisions.itertuples(index=False):
            signal = self.score_tick(row.symbol, decision_at=row.decision_at)
            rows.append({"symbol": signal.symbol, "decision_at": signal.decision_at, "score": _decimal(signal.score)})
        return pd.DataFrame(rows, columns=("symbol", "decision_at", "score"))
