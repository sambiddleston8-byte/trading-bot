"""PIT-safe Catalyst/Event Specialist over admitted event observations only."""
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


SCHEMA_VERSION = "catalyst-event-pit-v1"
FEATURE_VERSION = "event-surprise-guidance-timing-v1"
SPECIALIST_VERSION = "catalyst-event-specialist-v1"
EVENT_TYPES = frozenset({"EARNINGS_RESULT", "GUIDANCE_CHANGE", "CORPORATE_EVENT", "SCHEDULED_EARNINGS"})
MAX_STALENESS_DAYS = 45


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bounded(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be decimal-compatible") from error
    if not result.is_finite() or not Decimal("-1") <= result <= Decimal("1"):
        raise ValueError(f"{name} must be finite and within [-1, 1]")
    return result


@dataclass(frozen=True)
class CatalystEventObservation:
    observation_id: str
    symbol: str
    event_id: str
    event_type: str
    effective_at: str
    reported_at: str
    available_at: str
    retrieved_at: str
    observation_cutoff_at: str
    revision: int
    prior_revision_sha256: str | None
    directional_impact: str | None
    confidence: str
    provenance: Mapping[str, Any]
    record_sha256: str

    def __post_init__(self) -> None:
        effective = _time(self.effective_at, "effective_at")
        reported = _time(self.reported_at, "reported_at")
        available = _time(self.available_at, "available_at")
        retrieved = _time(self.retrieved_at, "retrieved_at")
        cutoff = _time(self.observation_cutoff_at, "observation_cutoff_at")
        if not effective <= reported <= available <= retrieved or cutoff != available:
            raise ValueError("catalyst timestamps violate the five-timestamp PIT contract")
        if self.event_type not in EVENT_TYPES:
            raise ValueError("catalyst event type is unsupported")
        if self.revision < 1 or (self.revision == 1) != (self.prior_revision_sha256 is None):
            raise ValueError("catalyst revision chain is invalid")
        confidence = _bounded(self.confidence, "confidence")
        if confidence < 0:
            raise ValueError("catalyst confidence must be within [0, 1]")
        if self.event_type == "SCHEDULED_EARNINGS":
            if self.directional_impact is not None:
                raise ValueError("scheduled event timing cannot invent its outcome")
        elif self.directional_impact is None:
            raise ValueError("realized catalyst requires a directional impact")
        else:
            _bounded(self.directional_impact, "directional_impact")
        if not self.provenance.get("source_payload_sha256"):
            raise ValueError("catalyst provenance requires a quarantined source hash")
        material = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "record_sha256"}
        if _sha(material) != self.record_sha256:
            raise ValueError("catalyst record SHA-256 is invalid")


def build_catalyst_event_artifact(
    rows: Sequence[Mapping[str, Any]], *, retrieved_at: str, partition_role: str = "TRAIN"
) -> dict[str, Any]:
    """Normalize caller-supplied quarantined rows without performing I/O."""
    if partition_role != "TRAIN":
        raise ValueError("catalyst development is restricted to TRAIN")
    retrieved = _time(retrieved_at, "retrieved_at")
    records: list[dict[str, Any]] = []
    parents: dict[tuple[str, str], tuple[str, datetime]] = {}
    for raw in sorted(rows, key=lambda row: (str(row["symbol"]), str(row["event_id"]), int(row["revision"]))):
        symbol = str(raw["symbol"]).strip().upper()
        event_id = str(raw["event_id"]).strip()
        revision = int(raw["revision"])
        key = (symbol, event_id)
        prior = parents.get(key)
        prior_count = sum(record["symbol"] == symbol and record["event_id"] == event_id for record in records)
        if revision != prior_count + 1 or (revision == 1) != (prior is None):
            raise ValueError("catalyst revisions are missing or out of order")
        available = _time(raw["available_at"], "available_at")
        if available > retrieved:
            raise ValueError("catalyst evidence was not retrieved yet")
        if prior is not None and available <= prior[1]:
            raise ValueError("catalyst revision must become available after its parent")
        event_type = str(raw["event_type"]).strip().upper()
        impact = raw.get("directional_impact")
        material = {
            "observation_id": "CAT-" + hashlib.sha256(f"{symbol}:{event_id}:{revision}".encode()).hexdigest()[:32].upper(),
            "symbol": symbol,
            "event_id": event_id,
            "event_type": event_type,
            "effective_at": _time(raw["effective_at"], "effective_at").isoformat(),
            "reported_at": _time(raw["reported_at"], "reported_at").isoformat(),
            "available_at": available.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "observation_cutoff_at": available.isoformat(),
            "revision": revision,
            "prior_revision_sha256": None if prior is None else prior[0],
            "directional_impact": None if impact is None else _decimal(_bounded(impact, "directional_impact")),
            "confidence": _decimal(_bounded(raw["confidence"], "confidence")),
            "provenance": {
                "source_payload_sha256": str(raw["source_payload_sha256"]),
                "source_locator": str(raw["source_locator"]),
                "normalizer_version": SCHEMA_VERSION,
            },
        }
        material["record_sha256"] = _sha(material)
        observation = CatalystEventObservation(**material)
        records.append(material)
        parents[key] = (observation.record_sha256, available)
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


class CatalystEventSpecialistBot:
    specialist_id = "CATALYST_EVENT"
    version = SPECIALIST_VERSION

    def __init__(self, artifact: Mapping[str, Any], *, expected_sha256: str) -> None:
        material = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != _sha(material) or artifact.get("artifact_sha256") != expected_sha256:
            raise ValueError("catalyst artifact differs from its admitted SHA-256")
        if artifact.get("schema_version") != SCHEMA_VERSION or artifact.get("partition_role") != "TRAIN":
            raise ValueError("catalyst artifact is not an admitted TRAIN artifact")
        if artifact.get("validation_data_read") is not False or artifact.get("untouched_test_included") is not False:
            raise ValueError("catalyst artifact crossed a sealed partition")
        self._records = tuple(CatalystEventObservation(**row) for row in artifact["records"])

    def score_tick(self, symbol: str, *, decision_at: str | datetime) -> SpecialistSignal:
        decision = _time(decision_at, "decision_at")
        resolved = str(symbol).strip().upper()
        available = [row for row in self._records if row.symbol == resolved and _time(row.available_at, "available_at") <= decision]
        latest_by_event: dict[str, CatalystEventObservation] = {}
        for row in available:
            current = latest_by_event.get(row.event_id)
            if current is None or (
                _time(row.available_at, "available_at"), row.revision
            ) > (
                _time(current.available_at, "available_at"), current.revision
            ):
                latest_by_event[row.event_id] = row
        evidence = [latest_by_event[event_id] for event_id in sorted(latest_by_event)]
        evidence_hash = _sha([row.record_sha256 for row in evidence])
        realized = [row for row in evidence if row.directional_impact is not None]
        if not realized:
            reason = "SCHEDULED_EVENT_OUTCOME_UNKNOWN" if evidence else "NO_CATALYST_COVERAGE"
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="ABSTAIN",
                maximum_input_available_at=max((row.available_at for row in evidence), default=decision.isoformat()),
                evidence_count=len(evidence), evidence_sha256=evidence_hash, reason=reason,
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        latest_at = max(_time(row.available_at, "available_at") for row in realized)
        if decision - latest_at > timedelta(days=MAX_STALENESS_DAYS):
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="STALE",
                maximum_input_available_at=latest_at.isoformat(), evidence_count=len(realized),
                evidence_sha256=_sha([row.record_sha256 for row in realized]),
                reason="CATALYST_EVIDENCE_STALE", model_version=self.version,
                feature_version=FEATURE_VERSION,
            )
        recent = [row for row in realized if decision - _time(row.available_at, "available_at") <= timedelta(days=MAX_STALENESS_DAYS)]
        with localcontext(DECIMAL_CONTEXT):
            weights = [Decimal(row.confidence) for row in recent]
            denominator = sum(weights, Decimal("0"))
            score = (
                sum((Decimal(row.directional_impact) * weight for row, weight in zip(recent, weights)), Decimal("0")) / denominator
                if denominator > 0 else Decimal("0")
            )
        status = "ACTIVE" if denominator > 0 and score != 0 else "NEUTRAL"
        return SpecialistSignal(
            specialist_id=self.specialist_id, specialist_version=self.version,
            symbol=resolved, decision_at=decision.isoformat(), score=score,
            confidence=min(Decimal("1"), denominator / Decimal(len(recent))), coverage=Decimal("1"),
            status=status, maximum_input_available_at=max(row.available_at for row in recent),
            evidence_count=len(recent), evidence_sha256=_sha([row.record_sha256 for row in recent]),
            reason="PIT_EVENT_SURPRISE_GUIDANCE_SCORE", model_version=self.version,
            feature_version=FEATURE_VERSION,
        )

    def score_frame(self, decisions: pd.DataFrame) -> pd.DataFrame:
        if set(decisions.columns) != {"symbol", "decision_at"}:
            raise ValueError("decision frame requires exactly symbol and decision_at")
        rows = []
        for row in decisions.itertuples(index=False):
            signal = self.score_tick(row.symbol, decision_at=row.decision_at)
            rows.append({"symbol": signal.symbol, "decision_at": signal.decision_at, "score": _decimal(signal.score)})
        return pd.DataFrame(rows, columns=("symbol", "decision_at", "score"))
