from __future__ import annotations

"""Fail-closed admission of sealed metadata for a future historical replay."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.active_pipeline_replay_plan import ActivePipelineReplayPlanLedger
from core.portfolio.historical_data_source_approval import HistoricalDataSourceApprovalLedger
from core.portfolio.historical_universe_coverage import HistoricalUniverseCoverageLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "sealed-replay-dataset-admission-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
REQUIRED_ROLES = {
    "UNIVERSE_MEMBERSHIP",
    "DELISTING_OUTCOMES",
    "CORPORATE_ACTIONS",
    "TOTAL_RETURN_PRICES",
    "POINT_IN_TIME_FUNDAMENTALS",
    "MARKET_CALENDARS_AND_HALTS",
}
PLAN_UNIVERSES = {
    "SP500": {"SP500"},
    "NASDAQ100": {"NASDAQ100"},
    "SP500_AND_NASDAQ100": {"SP500", "NASDAQ100"},
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete replay-dataset admission append")
        offset += count


def _normalise_artifacts(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen_roles: set[str] = set()
    for value in values:
        role = _required(value.get("role"), "artifact role").upper()
        content_id = _required(value.get("content_evidence_id"), "content_evidence_id")
        if role not in REQUIRED_ROLES or role in seen_roles:
            raise ValueError("dataset artifacts contain an unsupported or duplicate role reference")
        artifacts.append({"role": role, "content_evidence_id": content_id})
        seen_roles.add(role)
    if {item["role"] for item in artifacts} != REQUIRED_ROLES:
        raise ValueError("dataset artifacts must cover every required replay-data role")
    return sorted(artifacts, key=lambda item: (item["role"], item["content_evidence_id"]))


def _normalise_coverage_refs(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        coverage_id = _required(value.get("coverage_id"), "coverage_id")
        record_hash = _required(value.get("coverage_record_hash"), "coverage_record_hash")
        if coverage_id in seen:
            raise ValueError("coverage references must be unique")
        refs.append({"coverage_id": coverage_id, "coverage_record_hash": record_hash})
        seen.add(coverage_id)
    return sorted(refs, key=lambda item: item["coverage_id"])


def dataset_commitment(manifest_payload: bytes) -> str:
    """Hash sealed manifest bytes without interpreting evaluation observations."""
    if not isinstance(manifest_payload, bytes) or not manifest_payload:
        raise ValueError("manifest_payload must contain bytes")
    return hashlib.sha256(manifest_payload).hexdigest()


def _manifest(payload: bytes) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("sealed dataset manifest must be valid JSON") from error
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "manifest_type", "artifacts", "coverage_references"
    }:
        raise ValueError("sealed dataset manifest has unsupported fields")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("manifest_type") != "ACTIVE_PIPELINE_REPLAY_DATASET"
    ):
        raise ValueError("sealed dataset manifest type or version is unsupported")
    return (
        _normalise_artifacts(value.get("artifacts") or []),
        _normalise_coverage_refs(value.get("coverage_references") or []),
    )


class ReplayDatasetAdmissionLedger:
    """Links verified prerequisites; it does not analyse data or execute a replay."""

    def __init__(
        self,
        path: str | Path,
        *,
        plan_ledger: ActivePipelineReplayPlanLedger,
        approval_ledger: HistoricalDataSourceApprovalLedger,
        content_ledger: AuthenticatedSourceContentLedger,
        coverage_ledger: HistoricalUniverseCoverageLedger,
    ) -> None:
        self.path = Path(path)
        self.plan_ledger = plan_ledger
        self.approval_ledger = approval_ledger
        self.content_ledger = content_ledger
        self.coverage_ledger = coverage_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Replay-dataset admission ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank admission line at {line_number}.")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid admission JSON at {line_number}.") from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(f"Admission line {line_number} is not an object.")
                records.append(value)
        return records

    def admit(
        self,
        *,
        replay_plan_id: str,
        source_approval_id: str,
        terms_content_evidence_id: str,
        dataset_manifest_content_evidence_id: str,
        admitted_by: str,
        admitted_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        admitted = _timestamp(admitted_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= admitted <= now + MAX_CLOCK_SKEW:
            raise ValueError("admitted_at must match the actual append time")
        prerequisites = self._resolve(
            replay_plan_id=replay_plan_id,
            source_approval_id=source_approval_id,
            terms_content_evidence_id=terms_content_evidence_id,
            dataset_manifest_content_evidence_id=dataset_manifest_content_evidence_id,
            admitted_at=admitted,
        )
        commitment = prerequisites["manifest"]["source_input_sha256"]
        normalized_artifacts = prerequisites["artifacts"]
        normalized_coverage = prerequisites["coverage_references"]
        identity = {
            "replay_plan_id": prerequisites["plan"]["replay_plan_id"],
            "replay_plan_record_hash": prerequisites["plan"]["record_hash"],
            "source_approval_id": prerequisites["approval"]["approval_id"],
            "source_approval_record_hash": prerequisites["approval"]["record_hash"],
            "terms_content_evidence_id": prerequisites["terms"]["content_evidence_id"],
            "terms_content_record_hash": prerequisites["terms"]["record_hash"],
            "dataset_manifest_content_evidence_id": prerequisites["manifest"]["content_evidence_id"],
            "dataset_manifest_content_record_hash": prerequisites["manifest"]["record_hash"],
            "dataset_commitment_sha256": commitment,
            "artifacts": normalized_artifacts,
            "coverage_references": normalized_coverage,
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "admission_id": "RDA-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper(),
            "record_type": "SEALED_REPLAY_DATASET_METADATA_ADMISSION",
            "status": "PREREQUISITES_LINKED_AWAITING_CONTROLLED_REPLAY",
            "admitted_at": admitted.isoformat(),
            "admitted_by": _required(admitted_by, "admitted_by"),
            **identity,
            "required_artifact_roles_present": True,
            "authenticated_source_bytes_verified": True,
            "approved_source_and_terms_linked": True,
            "bounded_universe_intervals_reconciled": True,
            "source_attestation_only": True,
            "ground_truth_independently_proven": False,
            "evaluation_observations_interpreted": False,
            "model_trained": False,
            "replay_executed": False,
            "performance_calculated": False,
            "performance_claim_allowed": False,
            "paper_broker_submission_enabled": False,
            "broker_connection_allowed": False,
            "live_trading_enabled": False,
        }
        return self._append(record, allow_existing=allow_existing)

    def _resolve(
        self,
        *,
        replay_plan_id: str,
        source_approval_id: str,
        terms_content_evidence_id: str,
        dataset_manifest_content_evidence_id: str,
        admitted_at: datetime,
    ) -> dict[str, Any]:
        plans = {item["replay_plan_id"]: item for item in self.plan_ledger.verify()}
        plan = plans.get(_required(replay_plan_id, "replay_plan_id"))
        if plan is None:
            raise ValueError("a verified replay plan is required")
        access_at = _timestamp(plan["evaluation_data_access_not_before"])
        if admitted_at < access_at:
            raise ValueError("sealed dataset manifest cannot be opened before its preregistered access time")
        approvals = {item["approval_id"]: item for item in self.approval_ledger.verify()}
        approval = approvals.get(_required(source_approval_id, "source_approval_id"))
        if approval is None:
            raise ValueError("a verified source approval is required")
        approved_at = _timestamp(approval["approved_at"])
        if approved_at > access_at:
            raise ValueError("source approval must predate any sealed dataset access")
        # Source bytes are not authenticated or read until the access gate above passes.
        contents = {item["content_evidence_id"]: item for item in self.content_ledger.verify()}
        coverages = {item["coverage_id"]: item for item in self.coverage_ledger.verify()}
        terms = contents.get(_required(terms_content_evidence_id, "terms_content_evidence_id"))
        manifest_id = _required(
            dataset_manifest_content_evidence_id, "dataset_manifest_content_evidence_id"
        )
        manifest = contents.get(manifest_id)
        if terms is None or manifest is None:
            raise ValueError("verified terms and manifest content are required")
        if (
            terms["source_uri"] != approval["terms_url"]
            or terms["source_input_sha256"] != approval["terms_content_sha256"]
        ):
            raise ValueError("approval terms must match exact authenticated terms bytes")
        if _timestamp(terms["retrieved_at"]) > approved_at:
            raise ValueError("exact authenticated terms bytes must predate human approval")
        required_universes = PLAN_UNIVERSES[plan["universe"]]
        if not required_universes.issubset(set(approval["approved_universes"])):
            raise ValueError("source approval does not include every planned universe")
        if "HISTORICAL_REPLAY" not in approval["permitted_uses"]:
            raise ValueError("source approval does not permit historical replay")
        plan_start = _timestamp(plan["evaluation_not_before"])
        plan_end = _timestamp(plan["evaluation_not_after"])
        approval_start = datetime.fromisoformat(approval["coverage_not_before"] + "T00:00:00+00:00")
        approval_end = datetime.fromisoformat(approval["coverage_not_after"] + "T23:59:59.999999+00:00")
        if approval_start > plan_start or approval_end < plan_end:
            raise ValueError("source approval does not span the planned evaluation window")
        if _timestamp(manifest["retrieved_at"]) < access_at:
            raise ValueError("sealed dataset manifest cannot be opened before its preregistered access time")
        manifest_record, manifest_payload = self.content_ledger.read_verified(manifest_id)
        if manifest_record["record_hash"] != manifest["record_hash"]:
            raise ValueError("sealed dataset manifest changed during verification")
        if manifest["media_type"] != "application/json":
            raise ValueError("sealed dataset manifest must use application/json")
        commitment = dataset_commitment(manifest_payload)
        if commitment != plan["sealed_evaluation_dataset_commitment_sha256"]:
            raise ValueError("sealed dataset manifest does not match the preregistered commitment")
        artifacts, coverage_references = _manifest(manifest_payload)
        artifact_records = []
        approved_hosts = set(approval["approved_data_hosts"])
        if urlsplit(manifest["source_uri"]).hostname not in approved_hosts:
            raise ValueError("dataset manifest host is not explicitly approved")
        for ref in artifacts:
            content = contents.get(ref["content_evidence_id"])
            if content is None:
                raise ValueError("every dataset artifact must reference authenticated source bytes")
            if urlsplit(content["source_uri"]).hostname not in approved_hosts:
                raise ValueError("dataset artifact host is not explicitly approved")
            retrieved = _timestamp(content["retrieved_at"])
            if retrieved < access_at or retrieved > admitted_at:
                raise ValueError("dataset artifact access violates the preregistered access boundary")
            artifact_records.append(content)
        membership_content_ids = {
            item["content_evidence_id"]
            for item in artifacts
            if item["role"] == "UNIVERSE_MEMBERSHIP"
        }
        referenced_membership_content = {
            (item["source_uri"], item["source_input_sha256"])
            for item in artifact_records
            if item["content_evidence_id"] in membership_content_ids
        }
        selected_coverages = []
        for ref in coverage_references:
            coverage = coverages.get(ref["coverage_id"])
            if coverage is None or coverage["record_hash"] != ref["coverage_record_hash"]:
                raise ValueError("coverage reference is missing or has the wrong immutable hash")
            if urlsplit(coverage["source_uri"]).hostname not in approved_hosts:
                raise ValueError("universe coverage host is not explicitly approved")
            if _timestamp(coverage["retrieved_at"]) > admitted_at:
                raise ValueError("universe coverage was not available at admission time")
            if (
                coverage["source_uri"], coverage["source_input_sha256"]
            ) not in referenced_membership_content:
                raise ValueError(
                    "universe coverage must link to an authenticated membership artifact"
                )
            selected_coverages.append(coverage)
        self._require_continuous_intervals(
            selected_coverages, required_universes, plan_start, plan_end
        )
        return {
            "plan": plan, "approval": approval, "terms": terms, "manifest": manifest,
            "artifacts": artifacts, "coverage_references": coverage_references,
        }

    @staticmethod
    def _require_continuous_intervals(
        coverages: Sequence[Mapping[str, Any]],
        required_universes: set[str],
        start: datetime,
        end: datetime,
    ) -> None:
        if {item["universe"] for item in coverages} != required_universes:
            raise ValueError("coverage references must contain exactly every planned universe")
        for universe in required_universes:
            intervals = sorted(
                (item for item in coverages if item["universe"] == universe),
                key=lambda item: item["covers_from_at"],
            )
            cursor = start
            for interval in intervals:
                interval_start = _timestamp(interval["covers_from_at"])
                interval_end = _timestamp(interval["through_at"])
                if interval_end < cursor:
                    continue
                if interval_start > cursor:
                    raise ValueError("universe coverage contains a gap in the evaluation window")
                cursor = max(cursor, interval_end)
                if cursor >= end:
                    break
            if cursor < end:
                raise ValueError("universe coverage does not span the evaluation window")

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _hash(material):
                raise LedgerIntegrityError(f"Replay-dataset admission {index} was modified.")
            try:
                admitted = _timestamp(record.get("admitted_at"))
                if admitted > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                    raise ValueError("admitted_at cannot be in the future")
                resolved = self._resolve(
                    replay_plan_id=record.get("replay_plan_id"),
                    source_approval_id=record.get("source_approval_id"),
                    terms_content_evidence_id=record.get("terms_content_evidence_id"),
                    dataset_manifest_content_evidence_id=record.get(
                        "dataset_manifest_content_evidence_id"
                    ),
                    admitted_at=admitted,
                )
                artifacts = resolved["artifacts"]
                coverage_refs = resolved["coverage_references"]
                commitment = resolved["manifest"]["source_input_sha256"]
                identity = {
                    "replay_plan_id": resolved["plan"]["replay_plan_id"],
                    "replay_plan_record_hash": resolved["plan"]["record_hash"],
                    "source_approval_id": resolved["approval"]["approval_id"],
                    "source_approval_record_hash": resolved["approval"]["record_hash"],
                    "terms_content_evidence_id": resolved["terms"]["content_evidence_id"],
                    "terms_content_record_hash": resolved["terms"]["record_hash"],
                    "dataset_manifest_content_evidence_id": resolved["manifest"]["content_evidence_id"],
                    "dataset_manifest_content_record_hash": resolved["manifest"]["record_hash"],
                    "dataset_commitment_sha256": commitment,
                    "artifacts": artifacts,
                    "coverage_references": coverage_refs,
                }
                expected_id = "RDA-" + hashlib.sha256(
                    _canonical_json(identity).encode("utf-8")
                ).hexdigest()[:32].upper()
                _required(record.get("admitted_by"), "admitted_by")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Replay-dataset admission {index} is invalid.") from error
            fixed_true = (
                "required_artifact_roles_present", "authenticated_source_bytes_verified",
                "approved_source_and_terms_linked", "bounded_universe_intervals_reconciled",
                "source_attestation_only",
            )
            fixed_false = (
                "ground_truth_independently_proven", "evaluation_observations_interpreted",
                "model_trained", "replay_executed", "performance_calculated",
                "performance_claim_allowed", "paper_broker_submission_enabled",
                "broker_connection_allowed", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("record_type") == "SEALED_REPLAY_DATASET_METADATA_ADMISSION"
                and record.get("status") == "PREREQUISITES_LINKED_AWAITING_CONTROLLED_REPLAY"
                and record.get("admission_id") == expected_id
                and expected_id not in seen
                and all(record.get(field) is True for field in fixed_true)
                and all(record.get(field) is False for field in fixed_false)
                and all(record.get(key) == value for key, value in identity.items())
                and commitment == resolved["plan"]["sealed_evaluation_dataset_commitment_sha256"]
            )
            if not boundary:
                raise LedgerIntegrityError(f"Replay-dataset admission {index} violates its boundary.")
            seen.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, record: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["admission_id"] == record["admission_id"]), None
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "admitted_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in record.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Replay-dataset admission already exists.")
            if any(item["replay_plan_id"] == record["replay_plan_id"] for item in records):
                raise LedgerIntegrityError("Replay plan already has its single dataset admission.")
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            result = {**material, "record_hash": _hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(result) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
