from __future__ import annotations

"""Immutable evidence gate that can permit human review, never live trading."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.broker.local_paper_execution import LIVE_PROMOTION_GATE_LABELS
from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    _canonical_json,
    canonical_timestamp,
)


LIVE_READINESS_SCHEMA_VERSION = "1.0"
LIVE_READINESS_POLICY_VERSION = "live-readiness-evidence-gate-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
POLICY = {
    "evidence_policy": (
        "EVERY_GATE_REQUIRES_PINNED_DISTINCT_EVIDENCE_LOCATION_AND_INDEPENDENT_REVIEW"
    ),
    "failure_policy": "MISSING_FAILED_OR_UNVERIFIABLE_EVIDENCE_BLOCKS",
    "authority_policy": "EVIDENCE_COMPLETENESS_PERMITS_HUMAN_REVIEW_ONLY",
    "trading_policy": (
        "NO_CREDENTIAL_NETWORK_BROKER_ORDER_LIVE_DEPLOYMENT_OR_AUTOMATIC_PROMOTION_CAPABILITY"
    ),
}
AUTHORITY_FIELDS = (
    "credentials_permitted",
    "network_request_enabled",
    "broker_connection_allowed",
    "order_submission_enabled",
    "live_trading_enabled",
    "deployment_performed",
    "automatic_promotion_enabled",
)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Failed to append the complete live-readiness record")
        remaining = remaining[written:]


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _revision(value: Any) -> str:
    resolved = str(value or "").strip().lower()
    if not GIT_REVISION_PATTERN.fullmatch(resolved):
        raise ValueError("git_revision must be a full hexadecimal commit identifier")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    return resolved


def _normalise_gates(
    values: Mapping[str, Mapping[str, Any]],
    *,
    assessed_at: datetime,
    builder_identity: str,
    assessor_identity: str,
    strategy_owner_identity: str | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, Mapping) or set(values) != set(LIVE_PROMOTION_GATE_LABELS):
        raise ValueError(
            "gates must contain exactly: " + ", ".join(LIVE_PROMOTION_GATE_LABELS)
        )
    disallowed = {builder_identity.casefold(), assessor_identity.casefold()}
    if strategy_owner_identity is not None:
        disallowed.add(strategy_owner_identity.casefold())
    locations: set[tuple[str, str]] = set()
    resolved: dict[str, dict[str, Any]] = {}
    for key, label in LIVE_PROMOTION_GATE_LABELS.items():
        item = values[key]
        if not isinstance(item, Mapping):
            raise ValueError(f"{key} must be an evidence object")
        status = _required(item.get("status"), f"{key}.status", 20).upper()
        if status not in {"PASSED", "FAILED"}:
            raise ValueError(f"{key}.status must be PASSED or FAILED")
        observed = _timestamp(item.get("observed_at"))
        if observed > assessed_at:
            raise ValueError(f"{key}.observed_at cannot postdate assessed_at")
        source_hash = _sha256(
            item.get("source_input_sha256"), f"{key}.source_input_sha256"
        )
        locator = _required(item.get("source_locator"), f"{key}.source_locator")
        location = (source_hash, locator)
        if location in locations:
            raise ValueError("each live-readiness gate requires a distinct evidence location")
        locations.add(location)
        reviewer = _required(
            item.get("independent_reviewer_identity"),
            f"{key}.independent_reviewer_identity",
            100,
        )
        if reviewer.casefold() in disallowed:
            raise ValueError(
                "independent reviewer must differ from assessor, builder, and strategy owner"
            )
        resolved[key] = {
            "label": label,
            "status": status,
            "observed_at": observed.isoformat(),
            "source_input_sha256": source_hash,
            "source_locator": locator,
            "source_uri": _https_uri(item.get("source_uri"), f"{key}.source_uri"),
            "evidence_summary": _required(
                item.get("evidence_summary"), f"{key}.evidence_summary"
            ),
            "independent_reviewer_identity": reviewer,
        }
    return resolved


def _assessment_id(
    strategy_version: str,
    git_revision: str,
    window_start: str,
    window_end: str,
    assessed_at: str,
    gates: Mapping[str, Any],
) -> str:
    material = [
        strategy_version,
        git_revision,
        window_start,
        window_end,
        assessed_at,
        gates,
        LIVE_READINESS_POLICY_VERSION,
    ]
    return "LREADY-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class LiveReadinessEvidenceGateLedger:
    """Append-only evidence that may permit human review but grants no authority."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Live-readiness ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank live-readiness line {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid live-readiness JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Live-readiness line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def assess(
        self,
        *,
        strategy_version: str,
        git_revision: str,
        evidence_window_start: str | datetime,
        evidence_window_end: str | datetime,
        gates: Mapping[str, Mapping[str, Any]],
        builder_identity: str,
        assessed_by: str,
        strategy_owner_identity: str | None = None,
        assessed_at: str | datetime | None = None,
        supersedes_assessment_id: str | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        assessed = _timestamp(assessed_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= assessed <= now + MAX_CLOCK_SKEW:
            raise ValueError("assessed_at must match the actual assessment time")
        start = _timestamp(evidence_window_start)
        end = _timestamp(evidence_window_end)
        if end <= start:
            raise ValueError("evidence_window_end must be later than evidence_window_start")
        if end > assessed:
            raise ValueError("evidence window cannot postdate assessed_at")
        strategy = _required(strategy_version, "strategy_version", 200)
        revision = _revision(git_revision)
        builder = _required(builder_identity, "builder_identity", 100)
        owner = (
            _required(strategy_owner_identity, "strategy_owner_identity", 100)
            if strategy_owner_identity is not None
            else None
        )
        assessor = _required(assessed_by, "assessed_by", 100)
        if assessor.casefold() in {
            builder.casefold(),
            str(owner or "").casefold(),
        }:
            raise ValueError("assessor must differ from builder and strategy owner")
        resolved_gates = _normalise_gates(
            gates,
            assessed_at=assessed,
            builder_identity=builder,
            assessor_identity=assessor,
            strategy_owner_identity=owner,
        )
        open_gates = [
            key for key in LIVE_PROMOTION_GATE_LABELS
            if resolved_gates[key]["status"] != "PASSED"
        ]
        result = {
            "schema_version": LIVE_READINESS_SCHEMA_VERSION,
            "policy_version": LIVE_READINESS_POLICY_VERSION,
            "assessment_id": _assessment_id(
                strategy,
                revision,
                start.isoformat(),
                end.isoformat(),
                assessed.isoformat(),
                resolved_gates,
            ),
            "record_type": "EVIDENCE_BACKED_LIVE_READINESS_GATE",
            "status": "BLOCKED" if open_gates else "HUMAN_REVIEW_ELIGIBLE_ONLY",
            "assessed_at": assessed.isoformat(),
            "assessed_by": assessor,
            "strategy_version": strategy,
            "git_revision": revision,
            "evidence_window_start": start.isoformat(),
            "evidence_window_end": end.isoformat(),
            "builder_identity": builder,
            "strategy_owner_identity": owner,
            "supersedes_assessment_id": (
                str(supersedes_assessment_id).strip()
                if supersedes_assessment_id is not None
                else None
            ),
            "gates": resolved_gates,
            "open_gates": open_gates,
            "all_evidence_passed": not open_gates,
            **{field: False for field in AUTHORITY_FIELDS},
            "next_authority": (
                "RESOLVE_LIVE_READINESS_GATES"
                if open_gates
                else "SEPARATE_EXPLICIT_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
            ),
            **POLICY,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: dict[str, dict[str, Any]] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Live-readiness record {index} was modified.")
            try:
                assessed = _timestamp(record.get("assessed_at"))
                start = _timestamp(record.get("evidence_window_start"))
                end = _timestamp(record.get("evidence_window_end"))
                strategy = _required(record.get("strategy_version"), "strategy", 200)
                revision = _revision(record.get("git_revision"))
                builder = _required(record.get("builder_identity"), "builder", 100)
                owner = (
                    _required(record.get("strategy_owner_identity"), "owner", 100)
                    if record.get("strategy_owner_identity") is not None
                    else None
                )
                assessor = _required(record.get("assessed_by"), "assessor", 100)
                if assessor.casefold() in {builder.casefold(), str(owner or "").casefold()}:
                    raise ValueError("assessor is not independent")
                gates = _normalise_gates(
                    record.get("gates"),
                    assessed_at=assessed,
                    builder_identity=builder,
                    assessor_identity=assessor,
                    strategy_owner_identity=owner,
                )
            except (AttributeError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Live-readiness record {index} has invalid evidence."
                ) from error
            expected_id = _assessment_id(
                strategy,
                revision,
                start.isoformat(),
                end.isoformat(),
                assessed.isoformat(),
                gates,
            )
            open_gates = [
                key for key in LIVE_PROMOTION_GATE_LABELS
                if gates[key]["status"] != "PASSED"
            ]
            supersedes = record.get("supersedes_assessment_id")
            predecessor = seen.get(str(supersedes)) if supersedes else None
            chain_valid = (
                supersedes is None
                or (
                    predecessor is not None
                    and predecessor["strategy_version"] == strategy
                    and assessed > _timestamp(predecessor["assessed_at"])
                )
            )
            boundary = (
                record.get("schema_version") == LIVE_READINESS_SCHEMA_VERSION
                and record.get("policy_version") == LIVE_READINESS_POLICY_VERSION
                and record.get("assessment_id") == expected_id
                and expected_id not in seen
                and record.get("record_type") == "EVIDENCE_BACKED_LIVE_READINESS_GATE"
                and record.get("status") == (
                    "BLOCKED" if open_gates else "HUMAN_REVIEW_ELIGIBLE_ONLY"
                )
                and record.get("gates") == gates
                and record.get("open_gates") == open_gates
                and record.get("all_evidence_passed") is (not open_gates)
                and start < end <= assessed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(field) is False for field in AUTHORITY_FIELDS)
                and record.get("next_authority") == (
                    "RESOLVE_LIVE_READINESS_GATES"
                    if open_gates
                    else "SEPARATE_EXPLICIT_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
                )
                and all(record.get(key) == value for key, value in POLICY.items())
                and chain_valid
            )
            if not boundary:
                raise LedgerIntegrityError(f"Live-readiness record {index} violates its boundary.")
            seen[expected_id] = record
            previous_hash = record["record_hash"]
        for strategy in {item["strategy_version"] for item in records}:
            matches = [item for item in records if item["strategy_version"] == strategy]
            superseded = {
                item["supersedes_assessment_id"]
                for item in matches
                if item.get("supersedes_assessment_id")
            }
            heads = [item for item in matches if item["assessment_id"] not in superseded]
            if len(heads) > 1:
                raise LedgerIntegrityError(
                    "Live-readiness assessments have competing heads."
                )
        return records

    def latest(self, *, strategy_version: str) -> dict[str, Any] | None:
        strategy = _required(strategy_version, "strategy_version", 200)
        matches = [item for item in self.verify() if item["strategy_version"] == strategy]
        superseded = {
            item["supersedes_assessment_id"]
            for item in matches
            if item.get("supersedes_assessment_id")
        }
        heads = [item for item in matches if item["assessment_id"] not in superseded]
        if len(heads) > 1:
            raise LedgerIntegrityError("Live-readiness assessments have competing heads.")
        return heads[0] if heads else None

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["assessment_id"] == result["assessment_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Live-readiness assessment already exists.")
            same_strategy = [
                item for item in records
                if item["strategy_version"] == result["strategy_version"]
            ]
            if same_strategy:
                current = self.latest(strategy_version=result["strategy_version"])
                if result.get("supersedes_assessment_id") != current["assessment_id"]:
                    raise LedgerIntegrityError(
                        "New assessment must supersede the current strategy head."
                    )
                if _timestamp(result["assessed_at"]) <= _timestamp(current["assessed_at"]):
                    raise LedgerIntegrityError(
                        "New assessment must be later than the current strategy head."
                    )
            elif result.get("supersedes_assessment_id") is not None:
                raise LedgerIntegrityError(
                    "First strategy assessment cannot supersede another record."
                )
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
