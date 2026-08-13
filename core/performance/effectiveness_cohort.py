from __future__ import annotations

"""Evidence-pinned cohort labels for complete simulated investment outcomes."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.complete_relative_return import CompleteFixedHorizonRelativeReturnLedger
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


EFFECTIVENESS_COHORT_SCHEMA_VERSION = "1.0"
EFFECTIVENESS_COHORT_POLICY_VERSION = "point-in-time-effectiveness-cohort-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVIDENCED_DIMENSIONS = (
    "market_regime",
    "sector",
    "company_size",
    "valuation_regime",
    "volatility_regime",
    "signal",
)
POLICY = {
    "label_policy": "SOURCE_LABELS_PRESERVED_WITHOUT_TRANSLATION",
    "availability_policy": "OBSERVED_AT_NO_LATER_THAN_DECISION_IS_DECISION_TIME_AVAILABLE",
    "backfill_policy": "BACKFILLED_LABELS_RECORDED_BUT_NOT_ELIGIBLE_FOR_EFFECTIVENESS_CLAIMS",
    "outcome_policy": "COMPLETE_SIMULATED_BENCHMARK_RELATIVE_RETURN_ONLY",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTPS URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} cannot contain credentials")
    return resolved


def _cohort_id(outcome_id: str, labels: Mapping[str, Any]) -> str:
    material = [outcome_id, labels, EFFECTIVENESS_COHORT_POLICY_VERSION]
    return "ECOHORT-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _normalise_labels(
    labels: Mapping[str, Mapping[str, Any]],
    *,
    decided_at: datetime,
    outcome_at: datetime,
) -> dict[str, dict[str, Any]]:
    if set(labels) != set(EVIDENCED_DIMENSIONS):
        raise ValueError(
            "labels must contain exactly: " + ", ".join(EVIDENCED_DIMENSIONS)
        )
    resolved: dict[str, dict[str, Any]] = {}
    evidence_locations: set[tuple[str, str]] = set()
    for dimension in EVIDENCED_DIMENSIONS:
        value = labels[dimension]
        if not isinstance(value, Mapping):
            raise ValueError(f"{dimension} evidence must be an object")
        effective = _as_datetime(value.get("effective_at"))
        observed = _as_datetime(value.get("observed_at"))
        if effective > outcome_at:
            raise ValueError(f"{dimension}.effective_at cannot postdate the outcome")
        if observed < effective:
            raise ValueError(f"{dimension}.observed_at cannot predate effective_at")
        source_hash = _sha256(value.get("source_input_sha256"), f"{dimension}.source_input_sha256")
        source_locator = _required(value.get("source_locator"), f"{dimension}.source_locator")
        evidence_location = (source_hash, source_locator)
        if evidence_location in evidence_locations:
            raise ValueError("each cohort dimension requires a distinct source evidence location")
        evidence_locations.add(evidence_location)
        resolved[dimension] = {
            "label": _required(value.get("label"), f"{dimension}.label"),
            "taxonomy_name": _required(value.get("taxonomy_name"), f"{dimension}.taxonomy_name"),
            "taxonomy_version": _required(
                value.get("taxonomy_version"), f"{dimension}.taxonomy_version"
            ),
            "effective_at": effective.isoformat(),
            "observed_at": observed.isoformat(),
            "availability": (
                "AVAILABLE_BY_DECISION"
                if observed <= decided_at
                else "BACKFILLED_AFTER_DECISION"
            ),
            "source_uri": _https_uri(value.get("source_uri"), f"{dimension}.source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": source_locator,
        }
    return resolved


class EffectivenessCohortAssignmentLedger:
    """Append-only labels; this layer never calculates effectiveness or learns."""

    def __init__(
        self,
        path: str | Path,
        relative_return_ledger: CompleteFixedHorizonRelativeReturnLedger,
    ) -> None:
        self.path = Path(path)
        self.relative_return_ledger = relative_return_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Effectiveness-cohort ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank effectiveness-cohort line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at effectiveness-cohort line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Effectiveness-cohort line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def _support(self, outcome_id: str):
        outcome = next(
            (
                item
                for item in self.relative_return_ledger.verify()
                if item.get("result_id") == outcome_id
            ),
            None,
        )
        pair = None
        if outcome is not None:
            asset = next(
                (
                    item
                    for item in self.relative_return_ledger.asset_outcome_ledger.verify()
                    if item.get("result_id") == outcome.get("asset_outcome_result_id")
                ),
                None,
            )
            if asset is not None:
                pair = next(
                    (
                        item
                        for item in self.relative_return_ledger.asset_outcome_ledger.prediction_pair_ledger.verify()
                        if item.get("pair_id") == asset.get("prediction_pair_id")
                    ),
                    None,
                )
        reasons = []
        if outcome is None:
            reasons.append("Verified complete benchmark-relative outcome is missing.")
        if pair is None:
            reasons.append("Verified timestamped prediction pair is missing.")
        return outcome, pair, reasons

    @staticmethod
    def not_assignable(outcome_id: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "NOT_ASSIGNABLE",
            "relative_return_result_id": str(outcome_id),
            "reasons": list(reasons),
            "record_appended": False,
            "effectiveness_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "live_trading_enabled": False,
        }

    def assign(
        self,
        *,
        relative_return_result_id: str,
        labels: Mapping[str, Mapping[str, Any]],
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        outcome_id = _required(relative_return_result_id, "relative_return_result_id")
        outcome, pair, reasons = self._support(outcome_id)
        if reasons or outcome is None or pair is None:
            return self.not_assignable(outcome_id, reasons)
        normalised = _normalise_labels(
            labels,
            decided_at=_as_datetime(pair["decided_at"]),
            outcome_at=_as_datetime(outcome["calculated_at"]),
        )
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        latest = max(
            _as_datetime(outcome["calculated_at"]),
            *(_as_datetime(value["observed_at"]) for value in normalised.values()),
        )
        if recorded < latest:
            raise ValueError("recorded_at cannot predate the outcome or label observations")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        all_available = all(
            value["availability"] == "AVAILABLE_BY_DECISION"
            for value in normalised.values()
        )
        result = {
            "schema_version": EFFECTIVENESS_COHORT_SCHEMA_VERSION,
            "policy_version": EFFECTIVENESS_COHORT_POLICY_VERSION,
            "cohort_assignment_id": _cohort_id(outcome_id, normalised),
            "status": "ASSIGNED",
            "record_type": "EVIDENCE_PINNED_EFFECTIVENESS_COHORT_ASSIGNMENT",
            "simulation_only": True,
            "recorded_at": recorded.isoformat(),
            "relative_return_result_id": outcome_id,
            "relative_return_record_hash": outcome["record_hash"],
            "prediction_pair_id": pair["pair_id"],
            "prediction_pair_record_hash": pair["record_hash"],
            "decision_id": outcome["decision_id"],
            "decided_at": pair["decided_at"],
            "ticker": outcome["ticker"],
            "portfolio_version": outcome["portfolio_version"],
            "investment_horizon": outcome["horizon"],
            "strategy_version": outcome["strategy_version"],
            "model_versions": outcome["model_versions"],
            "labels": normalised,
            "all_labels_available_by_decision": all_available,
            "effectiveness_claim_eligible": all_available,
            "effectiveness_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "order_submission_enabled": False,
            "live_trading_enabled": False,
            **POLICY,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_outcomes: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Effectiveness-cohort record {index} has been modified.")
            outcome, pair, reasons = self._support(str(record.get("relative_return_result_id") or ""))
            if reasons or outcome is None or pair is None:
                raise LedgerIntegrityError(f"Effectiveness-cohort record {index} lost support.")
            try:
                labels = _normalise_labels(
                    record["labels"],
                    decided_at=_as_datetime(pair["decided_at"]),
                    outcome_at=_as_datetime(outcome["calculated_at"]),
                )
                recorded = _as_datetime(record["recorded_at"])
                latest = max(
                    _as_datetime(outcome["calculated_at"]),
                    *(_as_datetime(value["observed_at"]) for value in labels.values()),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Effectiveness-cohort record {index} has invalid values."
                ) from error
            expected_id = _cohort_id(outcome["result_id"], labels)
            all_available = all(
                value["availability"] == "AVAILABLE_BY_DECISION" for value in labels.values()
            )
            boundary = (
                record.get("schema_version") == EFFECTIVENESS_COHORT_SCHEMA_VERSION
                and record.get("policy_version") == EFFECTIVENESS_COHORT_POLICY_VERSION
                and record.get("cohort_assignment_id") == expected_id
                and expected_id not in seen_ids
                and outcome["result_id"] not in seen_outcomes
                and record.get("status") == "ASSIGNED"
                and record.get("record_type") == "EVIDENCE_PINNED_EFFECTIVENESS_COHORT_ASSIGNMENT"
                and record.get("simulation_only") is True
                and record.get("relative_return_record_hash") == outcome["record_hash"]
                and record.get("prediction_pair_id") == pair["pair_id"]
                and record.get("prediction_pair_record_hash") == pair["record_hash"]
                and all(record.get(field) == outcome.get(field) for field in (
                    "decision_id", "ticker", "portfolio_version", "strategy_version", "model_versions"
                ))
                and record.get("decided_at") == pair["decided_at"]
                and record.get("investment_horizon") == outcome["horizon"]
                and record.get("labels") == labels
                and record.get("all_labels_available_by_decision") is all_available
                and record.get("effectiveness_claim_eligible") is all_available
                and record.get("effectiveness_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("order_submission_enabled") is False
                and record.get("live_trading_enabled") is False
                and all(record.get(key) == value for key, value in POLICY.items())
                and recorded >= latest
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Effectiveness-cohort record {index} violates its boundary.")
            seen_ids.add(expected_id)
            seen_outcomes.add(outcome["result_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["cohort_assignment_id"] == result["cohort_assignment_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {
                    k: v for k, v in result.items() if k not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("Effectiveness cohort assignment already exists.")
            if any(
                item["relative_return_result_id"] == result["relative_return_result_id"]
                for item in records
            ):
                raise LedgerIntegrityError("Outcome already has a cohort assignment.")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
