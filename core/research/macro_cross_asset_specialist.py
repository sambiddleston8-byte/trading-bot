"""PIT-safe Macro/Cross-Asset Specialist over admitted factor snapshots only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

import pandas as pd

from core.features.pit_feature_contract import DECIMAL_CONTEXT
from core.research.specialist_signals import SpecialistSignal, _decimal, _time


SCHEMA_VERSION = "macro-cross-asset-pit-v1"
FEATURE_VERSION = "rates-inflation-liquidity-cross-asset-v1"
SPECIALIST_VERSION = "macro-cross-asset-specialist-v1"
FACTOR_NAMES = ("rates", "inflation", "liquidity", "cross_asset")
MAX_STALENESS_DAYS = 120
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
class MacroCrossAssetObservation:
    observation_id: str
    symbol: str
    snapshot_id: str
    effective_at: str
    reported_at: str
    available_at: str
    retrieved_at: str
    observation_cutoff_at: str
    revision: int
    prior_revision_sha256: str | None
    factors: Mapping[str, str]
    symbol_sensitivities: Mapping[str, str]
    provenance: Mapping[str, Any]
    record_sha256: str

    def __post_init__(self) -> None:
        effective = _time(self.effective_at, "effective_at")
        reported = _time(self.reported_at, "reported_at")
        available = _time(self.available_at, "available_at")
        retrieved = _time(self.retrieved_at, "retrieved_at")
        cutoff = _time(self.observation_cutoff_at, "observation_cutoff_at")
        if not effective <= reported <= available <= retrieved or cutoff != available:
            raise ValueError("macro timestamps violate the five-timestamp PIT contract")
        if self.revision < 1 or (self.revision == 1) != (self.prior_revision_sha256 is None):
            raise ValueError("macro revision chain is invalid")
        if set(self.factors) != set(FACTOR_NAMES) or set(self.symbol_sensitivities) != set(FACTOR_NAMES):
            raise ValueError("macro factor family is incomplete")
        for name in FACTOR_NAMES:
            _bounded(self.factors[name], f"{name} factor")
            _bounded(self.symbol_sensitivities[name], f"{name} sensitivity")
        payloads = self.provenance.get("series_payload_sha256")
        if not isinstance(payloads, Mapping) or set(payloads) != set(FACTOR_NAMES):
            raise ValueError("macro provenance requires every factor-series hash")
        if any(not _SHA256.fullmatch(str(payloads[name])) for name in FACTOR_NAMES):
            raise ValueError("macro provenance contains an invalid SHA-256")
        if not str(self.provenance.get("source_locator") or "").strip():
            raise ValueError("macro provenance requires a source locator")
        material = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "record_sha256"}
        if _sha(material) != self.record_sha256:
            raise ValueError("macro record SHA-256 is invalid")


def build_macro_cross_asset_artifact(
    rows: Sequence[Mapping[str, Any]], *, retrieved_at: str, partition_role: str = "TRAIN"
) -> dict[str, Any]:
    """Normalize caller-supplied quarantined snapshots without performing I/O."""
    if partition_role != "TRAIN":
        raise ValueError("macro development is restricted to TRAIN")
    retrieved = _time(retrieved_at, "retrieved_at")
    records: list[dict[str, Any]] = []
    parents: dict[tuple[str, str], tuple[str, datetime, datetime]] = {}
    for raw in sorted(rows, key=lambda row: (str(row["symbol"]), str(row["snapshot_id"]), int(row["revision"]))):
        symbol = str(raw["symbol"]).strip().upper()
        snapshot_id = str(raw["snapshot_id"]).strip()
        if not symbol or not snapshot_id:
            raise ValueError("macro symbol and snapshot_id are required")
        revision = int(raw["revision"])
        key = (symbol, snapshot_id)
        prior = parents.get(key)
        prior_count = sum(record["symbol"] == symbol and record["snapshot_id"] == snapshot_id for record in records)
        if revision != prior_count + 1 or (revision == 1) != (prior is None):
            raise ValueError("macro revisions are missing or out of order")
        effective = _time(raw["effective_at"], "effective_at")
        available = _time(raw["available_at"], "available_at")
        if available > retrieved:
            raise ValueError("macro evidence was not retrieved yet")
        if prior is not None and (available <= prior[1] or effective != prior[2]):
            raise ValueError("macro revision must preserve its period and become available after its parent")
        raw_factors = raw["factors"]
        raw_sensitivities = raw["symbol_sensitivities"]
        if not isinstance(raw_factors, Mapping) or not isinstance(raw_sensitivities, Mapping):
            raise ValueError("macro factors and sensitivities must be mappings")
        factors = {name: _decimal(_bounded(raw_factors.get(name), f"{name} factor")) for name in FACTOR_NAMES}
        sensitivities = {name: _decimal(_bounded(raw_sensitivities.get(name), f"{name} sensitivity")) for name in FACTOR_NAMES}
        payloads = raw["series_payload_sha256"]
        material = {
            "observation_id": "MAC-" + hashlib.sha256(f"{symbol}:{snapshot_id}:{revision}".encode()).hexdigest()[:32].upper(),
            "symbol": symbol,
            "snapshot_id": snapshot_id,
            "effective_at": effective.isoformat(),
            "reported_at": _time(raw["reported_at"], "reported_at").isoformat(),
            "available_at": available.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "observation_cutoff_at": available.isoformat(),
            "revision": revision,
            "prior_revision_sha256": None if prior is None else prior[0],
            "factors": factors,
            "symbol_sensitivities": sensitivities,
            "provenance": {
                "series_payload_sha256": {name: str(payloads[name]) for name in FACTOR_NAMES},
                "source_locator": str(raw["source_locator"]),
                "normalizer_version": SCHEMA_VERSION,
            },
        }
        material["record_sha256"] = _sha(material)
        observation = MacroCrossAssetObservation(**material)
        records.append(material)
        parents[key] = (observation.record_sha256, available, effective)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "partition_role": partition_role,
        "feature_version": FEATURE_VERSION,
        "records": records,
        "risk_authority": False,
        "constraint_output_allowed": False,
        "validation_data_read": False,
        "untouched_test_included": False,
        "external_data_calls": False,
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


class MacroCrossAssetSpecialistBot:
    specialist_id = "MACRO_CROSS_ASSET"
    version = SPECIALIST_VERSION

    def __init__(self, artifact: Mapping[str, Any], *, expected_sha256: str) -> None:
        material = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != _sha(material) or artifact.get("artifact_sha256") != expected_sha256:
            raise ValueError("macro artifact differs from its admitted SHA-256")
        if artifact.get("schema_version") != SCHEMA_VERSION or artifact.get("partition_role") != "TRAIN":
            raise ValueError("macro artifact is not an admitted TRAIN artifact")
        if artifact.get("validation_data_read") is not False or artifact.get("untouched_test_included") is not False:
            raise ValueError("macro artifact crossed a sealed partition")
        if artifact.get("external_data_calls") is not False or artifact.get("risk_authority") is not False or artifact.get("constraint_output_allowed") is not False:
            raise ValueError("macro artifact violates its alpha-only research boundary")
        self._records = tuple(MacroCrossAssetObservation(**row) for row in artifact["records"])

    def score_tick(self, symbol: str, *, decision_at: str | datetime) -> SpecialistSignal:
        decision = _time(decision_at, "decision_at")
        resolved = str(symbol).strip().upper()
        available = [row for row in self._records if row.symbol == resolved and _time(row.available_at, "available_at") <= decision]
        latest_by_snapshot: dict[str, MacroCrossAssetObservation] = {}
        for row in available:
            current = latest_by_snapshot.get(row.snapshot_id)
            if current is None or (_time(row.available_at, "available_at"), row.revision) > (_time(current.available_at, "available_at"), current.revision):
                latest_by_snapshot[row.snapshot_id] = row
        if not latest_by_snapshot:
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="ABSTAIN",
                maximum_input_available_at=decision.isoformat(), evidence_count=0,
                evidence_sha256=_sha([]), reason="NO_MACRO_CROSS_ASSET_COVERAGE",
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        current = max(
            latest_by_snapshot.values(),
            key=lambda row: (_time(row.effective_at, "effective_at"), _time(row.available_at, "available_at"), row.snapshot_id),
        )
        latest_at = _time(current.available_at, "available_at")
        if decision - latest_at > timedelta(days=MAX_STALENESS_DAYS):
            return SpecialistSignal(
                specialist_id=self.specialist_id, specialist_version=self.version,
                symbol=resolved, decision_at=decision.isoformat(), score=Decimal("0"),
                confidence=Decimal("0"), coverage=Decimal("0"), status="STALE",
                maximum_input_available_at=latest_at.isoformat(), evidence_count=1,
                evidence_sha256=_sha([current.record_sha256]), reason="MACRO_CROSS_ASSET_EVIDENCE_STALE",
                model_version=self.version, feature_version=FEATURE_VERSION,
            )
        with localcontext(DECIMAL_CONTEXT):
            factors = [Decimal(current.factors[name]) for name in FACTOR_NAMES]
            sensitivities = [Decimal(current.symbol_sensitivities[name]) for name in FACTOR_NAMES]
            score = sum((factor * sensitivity for factor, sensitivity in zip(factors, sensitivities)), Decimal("0")) / Decimal(len(FACTOR_NAMES))
            confidence = sum((abs(factor) for factor in factors), Decimal("0")) / Decimal(len(FACTOR_NAMES))
        status = "ACTIVE" if score != 0 and confidence > 0 else "NEUTRAL"
        return SpecialistSignal(
            specialist_id=self.specialist_id, specialist_version=self.version,
            symbol=resolved, decision_at=decision.isoformat(), score=score,
            confidence=confidence, coverage=Decimal("1"), status=status,
            maximum_input_available_at=current.available_at, evidence_count=1,
            evidence_sha256=_sha([current.record_sha256]),
            reason="PIT_MACRO_FACTOR_SENSITIVITY_SCORE",
            reason_codes=("ALPHA_OPINION_ONLY", "NO_RISK_AUTHORITY"),
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
