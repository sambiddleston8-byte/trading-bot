"""PIT-safe Political Disclosure Specialist over admitted official records only."""
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


SCHEMA_VERSION = "political-disclosure-pit-v1"
FEATURE_VERSION = "publication-delayed-official-disclosure-v1"
SPECIALIST_VERSION = "political-disclosure-specialist-v1"
OFFICIAL_SOURCES = frozenset({"OFFICIAL_HOUSE", "OFFICIAL_SENATE"})
TRANSACTION_TYPES = frozenset({"PURCHASE", "SALE", "EXCHANGE"})
MAX_STALENESS_DAYS = 90


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _money(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be decimal-compatible") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return result


@dataclass(frozen=True)
class PoliticalDisclosureObservation:
    observation_id: str
    symbol: str
    transaction_key: str
    disclosure_id: str
    source: str
    effective_at: str
    reported_at: str
    available_at: str
    retrieved_at: str
    observation_cutoff_at: str
    revision: int
    prior_revision_sha256: str | None
    transaction_type: str
    amount_min_usd: str
    amount_max_usd: str
    provenance: Mapping[str, Any]
    record_sha256: str

    def __post_init__(self) -> None:
        effective = _time(self.effective_at, "effective_at")
        reported = _time(self.reported_at, "reported_at")
        available = _time(self.available_at, "available_at")
        retrieved = _time(self.retrieved_at, "retrieved_at")
        cutoff = _time(self.observation_cutoff_at, "observation_cutoff_at")
        if not effective <= reported <= available <= retrieved or cutoff != available:
            raise ValueError("political disclosure timestamps violate the five-timestamp PIT contract")
        if self.source not in OFFICIAL_SOURCES:
            raise ValueError("political disclosure source is not official")
        if self.transaction_type not in TRANSACTION_TYPES:
            raise ValueError("political disclosure transaction type is unsupported")
        if self.revision < 1 or (self.revision == 1) != (self.prior_revision_sha256 is None):
            raise ValueError("political disclosure revision chain is invalid")
        minimum = _money(self.amount_min_usd, "amount_min_usd")
        maximum = _money(self.amount_max_usd, "amount_max_usd")
        if maximum < minimum:
            raise ValueError("political disclosure amount range is invalid")
        if not self.provenance.get("raw_document_sha256") or not self.provenance.get("availability_evidence_sha256"):
            raise ValueError("political disclosure provenance requires raw and availability evidence hashes")
        material = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "record_sha256"}
        if _sha(material) != self.record_sha256:
            raise ValueError("political disclosure record SHA-256 is invalid")


def build_political_disclosure_artifact(
    rows: Sequence[Mapping[str, Any]], *, retrieved_at: str, partition_role: str = "TRAIN"
) -> dict[str, Any]:
    """Normalize caller-supplied quarantined disclosures without network I/O."""
    if partition_role != "TRAIN":
        raise ValueError("political disclosure development is restricted to TRAIN")
    retrieved = _time(retrieved_at, "retrieved_at")
    records: list[dict[str, Any]] = []
    parents: dict[tuple[str, str], tuple[str, datetime]] = {}
    for raw in sorted(rows, key=lambda row: (str(row["symbol"]), str(row["transaction_key"]), int(row["revision"]))):
        symbol = str(raw["symbol"]).strip().upper()
        transaction_key = str(raw["transaction_key"]).strip()
        revision = int(raw["revision"])
        key = (symbol, transaction_key)
        prior = parents.get(key)
        prior_count = sum(record["symbol"] == symbol and record["transaction_key"] == transaction_key for record in records)
        if revision != prior_count + 1 or (revision == 1) != (prior is None):
            raise ValueError("political disclosure revisions are missing or out of order")
        available = _time(raw["available_at"], "available_at")
        if available > retrieved:
            raise ValueError("political disclosure evidence was not retrieved yet")
        if prior is not None and available <= prior[1]:
            raise ValueError("political disclosure revision must become available after its parent")
        minimum = _money(raw["amount_min_usd"], "amount_min_usd")
        maximum = _money(raw["amount_max_usd"], "amount_max_usd")
        if maximum < minimum:
            raise ValueError("political disclosure amount range is invalid")
        material = {
            "observation_id": "POL-" + hashlib.sha256(f"{symbol}:{transaction_key}:{revision}".encode()).hexdigest()[:32].upper(),
            "symbol": symbol,
            "transaction_key": transaction_key,
            "disclosure_id": str(raw["disclosure_id"]),
            "source": str(raw["source"]).strip().upper(),
            "effective_at": _time(raw["effective_at"], "effective_at").isoformat(),
            "reported_at": _time(raw["reported_at"], "reported_at").isoformat(),
            "available_at": available.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "observation_cutoff_at": available.isoformat(),
            "revision": revision,
            "prior_revision_sha256": None if prior is None else prior[0],
            "transaction_type": str(raw["transaction_type"]).strip().upper(),
            "amount_min_usd": _decimal(minimum),
            "amount_max_usd": _decimal(maximum),
            "provenance": {
                "raw_document_sha256": str(raw["raw_document_sha256"]),
                "availability_evidence_sha256": str(raw["availability_evidence_sha256"]),
                "source_locator": str(raw["source_locator"]),
                "normalizer_version": SCHEMA_VERSION,
            },
        }
        material["record_sha256"] = _sha(material)
        observation = PoliticalDisclosureObservation(**material)
        records.append(material)
        parents[key] = (observation.record_sha256, available)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "partition_role": partition_role,
        "feature_version": FEATURE_VERSION,
        "records": records,
        "availability_semantics": "OFFICIAL_PUBLICATION_TIMESTAMP_NOT_TRANSACTION_DATE",
        "validation_data_read": False,
        "untouched_test_included": False,
        "external_data_calls": False,
        "copy_trade_allowed": False,
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


class PoliticalDisclosureSpecialistBot:
    specialist_id = "POLITICAL_DISCLOSURE"
    version = SPECIALIST_VERSION

    def __init__(self, artifact: Mapping[str, Any], *, expected_sha256: str) -> None:
        material = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != _sha(material) or artifact.get("artifact_sha256") != expected_sha256:
            raise ValueError("political disclosure artifact differs from its admitted SHA-256")
        if artifact.get("schema_version") != SCHEMA_VERSION or artifact.get("partition_role") != "TRAIN":
            raise ValueError("political disclosure artifact is not an admitted TRAIN artifact")
        if artifact.get("validation_data_read") is not False or artifact.get("untouched_test_included") is not False:
            raise ValueError("political disclosure artifact crossed a sealed partition")
        if artifact.get("external_data_calls") is not False or artifact.get("copy_trade_allowed") is not False:
            raise ValueError("political disclosure artifact violates its research-only boundary")
        self._records = tuple(PoliticalDisclosureObservation(**row) for row in artifact["records"])

    def score_tick(self, symbol: str, *, decision_at: str | datetime) -> SpecialistSignal:
        decision = _time(decision_at, "decision_at")
        resolved = str(symbol).strip().upper()
        available = [row for row in self._records if row.symbol == resolved and _time(row.available_at, "available_at") <= decision]
        latest_by_transaction: dict[str, PoliticalDisclosureObservation] = {}
        for row in available:
            current = latest_by_transaction.get(row.transaction_key)
            if current is None or (_time(row.available_at, "available_at"), row.revision) > (_time(current.available_at, "available_at"), current.revision):
                latest_by_transaction[row.transaction_key] = row
        evidence = [latest_by_transaction[key] for key in sorted(latest_by_transaction)]
        if not evidence:
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="ABSTAIN",
                maximum_input_available_at=decision.isoformat(), evidence_count=0,
                evidence_sha256=_sha([]), reason="NO_OFFICIAL_POLITICAL_DISCLOSURE_COVERAGE",
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        latest_at = max(_time(row.available_at, "available_at") for row in evidence)
        if decision - latest_at > timedelta(days=MAX_STALENESS_DAYS):
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="STALE",
                maximum_input_available_at=latest_at.isoformat(), evidence_count=len(evidence),
                evidence_sha256=_sha([row.record_sha256 for row in evidence]),
                reason="POLITICAL_DISCLOSURE_EVIDENCE_STALE", model_version=self.version,
                feature_version=FEATURE_VERSION,
            )
        recent = [row for row in evidence if decision - _time(row.available_at, "available_at") <= timedelta(days=MAX_STALENESS_DAYS)]
        buys = [row for row in recent if row.transaction_type == "PURCHASE"]
        sales = [row for row in recent if row.transaction_type == "SALE"]
        with localcontext(DECIMAL_CONTEXT):
            buy_min = sum((Decimal(row.amount_min_usd) for row in buys), Decimal("0"))
            buy_max = sum((Decimal(row.amount_max_usd) for row in buys), Decimal("0"))
            sale_min = sum((Decimal(row.amount_min_usd) for row in sales), Decimal("0"))
            sale_max = sum((Decimal(row.amount_max_usd) for row in sales), Decimal("0"))
            net_lower = buy_min - sale_max
            net_upper = buy_max - sale_min
            scale = buy_max + sale_max
            if scale == 0 or net_lower <= 0 <= net_upper:
                score = Decimal("0")
            elif net_lower > 0:
                score = min(Decimal("1"), net_lower / scale)
            else:
                score = max(Decimal("-1"), net_upper / scale)
            confidence = Decimal("0") if scale == 0 else min(Decimal("1"), abs(score))
        status = "ACTIVE" if score != 0 else "NEUTRAL"
        return SpecialistSignal(
            specialist_id=self.specialist_id, specialist_version=self.version,
            symbol=resolved, decision_at=decision.isoformat(), score=score,
            confidence=confidence, coverage=Decimal("1"), status=status,
            maximum_input_available_at=max(row.available_at for row in recent),
            evidence_count=len(recent), evidence_sha256=_sha([row.record_sha256 for row in recent]),
            reason="PIT_PUBLICATION_DELAYED_DISCLOSURE_RANGE_SCORE",
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
