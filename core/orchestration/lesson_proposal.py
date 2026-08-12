from __future__ import annotations

"""Immutable evidence-backed sandbox lessons; no system behavior is changed."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.fixed_horizon_round_trip import FixedHorizonRoundTripOutcomeLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


LESSON_SCHEMA_VERSION = "1.0"
LESSON_POLICY_VERSION = "evidence-backed-sandbox-lesson-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_PROPOSER_TYPES = {"HUMAN", "CODEX", "CLAUDE_CODE"}
MINIMUM_EVIDENCE_RECORDS = 1
MAXIMUM_EVIDENCE_RECORDS = 100


def _required(value: Any, name: str, *, maximum: int = 2000) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _safe_identifier(value: Any, name: str) -> str:
    resolved = _required(value, name, maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", resolved):
        raise ValueError(f"{name} uses unsafe characters")
    return resolved


def _lesson_id(
    proposer_type: str,
    evidence_ids: Sequence[str],
    hypothesis: str,
    proposed_at: str,
) -> str:
    material = [proposer_type, list(evidence_ids), hypothesis, proposed_at, LESSON_POLICY_VERSION]
    return "LESSON-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class SandboxLessonProposalLedger:
    """Append-only hypotheses that remain unapproved and non-operative."""

    def __init__(
        self,
        path: str | Path,
        outcome_ledger: FixedHorizonRoundTripOutcomeLedger,
    ) -> None:
        self.path = Path(path)
        self.outcome_ledger = outcome_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Lesson-proposal ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank lesson-proposal line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at lesson-proposal line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Lesson-proposal line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def propose(
        self,
        *,
        proposer_type: str,
        proposer_id: str,
        supporting_outcome_ids: Sequence[str],
        observation_summary: str,
        suspected_cause: str,
        uncertainty_statement: str,
        falsifiable_hypothesis: str,
        proposed_experiment: str,
        expected_disconfirming_result: str,
        proposed_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_type = _required(proposer_type, "proposer_type", maximum=40).upper()
        if resolved_type == "HERMES":
            raise ValueError("HERMES proposals remain blocked until verified activation exists")
        if resolved_type not in ALLOWED_PROPOSER_TYPES:
            raise ValueError("proposer_type is not approved")
        identity = _safe_identifier(proposer_id, "proposer_id")
        requested_ids = [
            _safe_identifier(item, "supporting_outcome_id")
            for item in supporting_outcome_ids
        ]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("supporting_outcome_ids cannot contain duplicates")
        ids = sorted(requested_ids)
        if len(ids) < MINIMUM_EVIDENCE_RECORDS or len(ids) > MAXIMUM_EVIDENCE_RECORDS:
            raise ValueError("supporting_outcome_ids require 1 to 100 unique records")
        all_outcomes = self.outcome_ledger.verify()
        outcomes, reasons = resolve_pinned_records(
            all_outcomes,
            ids,
            [
                next(
                    (item["record_hash"] for item in all_outcomes if item["result_id"] == identifier),
                    "",
                )
                for identifier in ids
            ],
            id_field="result_id",
            label="fixed-horizon-outcome",
        )
        if reasons or len(outcomes) != len(ids):
            raise ValueError("Every lesson requires existing verified complete outcomes")
        proposed = _as_datetime(proposed_at or datetime.now(timezone.utc))
        if proposed > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("proposed_at cannot be in the future")
        latest = max(_as_datetime(item["paired_at"]) for item in outcomes)
        if proposed < latest:
            raise ValueError("proposed_at cannot predate supporting outcomes")
        observation = _required(observation_summary, "observation_summary")
        cause = _required(suspected_cause, "suspected_cause")
        uncertainty = _required(uncertainty_statement, "uncertainty_statement")
        hypothesis = _required(falsifiable_hypothesis, "falsifiable_hypothesis")
        experiment = _required(proposed_experiment, "proposed_experiment")
        disconfirming = _required(
            expected_disconfirming_result, "expected_disconfirming_result"
        )
        result = {
            "schema_version": LESSON_SCHEMA_VERSION,
            "policy_version": LESSON_POLICY_VERSION,
            "lesson_id": _lesson_id(resolved_type, ids, hypothesis, proposed.isoformat()),
            "status": "SANDBOX_PROPOSAL_UNAPPROVED",
            "record_type": "EVIDENCE_BACKED_CONTROLLED_LEARNING_LESSON_PROPOSAL",
            "simulation_only": True,
            "proposer_type": resolved_type,
            "proposer_id": identity,
            "proposed_at": proposed.isoformat(),
            "supporting_outcome_ids": ids,
            "supporting_outcome_hashes": [
                next(item["record_hash"] for item in outcomes if item["result_id"] == identifier)
                for identifier in ids
            ],
            "supporting_outcome_count": len(ids),
            "observation_summary": observation,
            "suspected_cause": cause,
            "cause_established": False,
            "uncertainty_statement": uncertainty,
            "falsifiable_hypothesis": hypothesis,
            "proposed_experiment": experiment,
            "expected_disconfirming_result": disconfirming,
            "human_approval_recorded": False,
            "experiment_executed": False,
            "lesson_validated": False,
            "production_rule_changed": False,
            "model_weight_changed": False,
            "code_changed": False,
            "permission_changed": False,
            "promotion_performed": False,
            "deployment_performed": False,
            "order_submitted": False,
            "learning_applied": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned(self, record: Mapping[str, Any]):
        return resolve_pinned_records(
            self.outcome_ledger.verify(),
            record.get("supporting_outcome_ids"),
            record.get("supporting_outcome_hashes"),
            id_field="result_id",
            label="fixed-horizon-outcome",
        )

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Lesson-proposal record {index} has been modified.")
            outcomes, reasons = self._pinned(record)
            if reasons:
                raise LedgerIntegrityError(f"Lesson-proposal record {index} lost evidence.")
            try:
                proposer_type = _required(record.get("proposer_type"), "proposer_type", maximum=40).upper()
                if proposer_type not in ALLOWED_PROPOSER_TYPES:
                    raise ValueError("unsupported proposer")
                _safe_identifier(record.get("proposer_id"), "proposer_id")
                proposed = _as_datetime(record.get("proposed_at"))
                ids = sorted({_safe_identifier(item, "supporting_outcome_id") for item in record.get("supporting_outcome_ids") or []})
                observation = _required(record.get("observation_summary"), "observation_summary")
                cause = _required(record.get("suspected_cause"), "suspected_cause")
                uncertainty = _required(record.get("uncertainty_statement"), "uncertainty_statement")
                hypothesis = _required(record.get("falsifiable_hypothesis"), "falsifiable_hypothesis")
                experiment = _required(record.get("proposed_experiment"), "proposed_experiment")
                disconfirming = _required(record.get("expected_disconfirming_result"), "expected_disconfirming_result")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Lesson-proposal record {index} has invalid values.") from error
            expected_id = _lesson_id(proposer_type, ids, hypothesis, proposed.isoformat())
            fixed_false = (
                "human_approval_recorded", "experiment_executed", "lesson_validated",
                "production_rule_changed", "model_weight_changed", "code_changed",
                "permission_changed", "promotion_performed", "deployment_performed",
                "order_submitted", "learning_applied", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == LESSON_SCHEMA_VERSION
                and record.get("policy_version") == LESSON_POLICY_VERSION
                and record.get("lesson_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "SANDBOX_PROPOSAL_UNAPPROVED"
                and record.get("record_type") == "EVIDENCE_BACKED_CONTROLLED_LEARNING_LESSON_PROPOSAL"
                and record.get("simulation_only") is True
                and len(ids) == len(outcomes)
                and MINIMUM_EVIDENCE_RECORDS <= len(ids) <= MAXIMUM_EVIDENCE_RECORDS
                and record.get("supporting_outcome_ids") == ids
                and record.get("supporting_outcome_count") == len(ids)
                and record.get("cause_established") is False
                and all(record.get(field) is False for field in fixed_false)
                and proposed >= max(_as_datetime(item["paired_at"]) for item in outcomes)
                and proposed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(key) == value for key, value in {
                    "observation_summary": observation,
                    "suspected_cause": cause,
                    "uncertainty_statement": uncertainty,
                    "falsifiable_hypothesis": hypothesis,
                    "proposed_experiment": experiment,
                    "expected_disconfirming_result": disconfirming,
                }.items())
            )
            if not boundary:
                raise LedgerIntegrityError(f"Lesson-proposal record {index} violates its boundary.")
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["lesson_id"] == result["lesson_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Lesson proposal {result['lesson_id']} already exists.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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

    def repair_incomplete_tail(self) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix, tail = raw[:complete_end], raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(self.path.suffix + f".incomplete-tail-{uuid4().hex}")
                backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    _write_all(backup_descriptor, tail)
                    os.fsync(backup_descriptor)
                finally:
                    os.close(backup_descriptor)
                target = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    _write_all(target, prefix)
                    os.fsync(target)
                finally:
                    os.close(target)
                self.verify()
                return backup
            target = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(target, b"\n")
                os.fsync(target)
            finally:
                os.close(target)
            self.verify()
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
