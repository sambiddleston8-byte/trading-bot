from __future__ import annotations

"""Immutable evidence assessment for the future paper-broker boundary."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


METHODOLOGY_ASSESSMENT_SCHEMA_VERSION = "1.0"
METHODOLOGY_ASSESSMENT_POLICY_VERSION = "paper-methodology-evidence-gate-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
METHODOLOGY_GATE_LABELS = {
    "point_in_time_availability": "Point-in-time input availability",
    "forecast_confidence_accuracy": "Forecast-confidence accuracy",
    "single_factor_lineage": "Single-count factor lineage",
    "historical_universe_membership": "Historical universe membership",
    "active_pipeline_out_of_sample": "Active-pipeline out-of-sample validation",
    "execution_cost_realism": "Fees, spread, slippage, latency and liquidity realism",
    "research_execution_data_parity": "Backtest, shadow and paper data-policy parity",
}
POLICY = {
    "evidence_policy": "EVERY_GATE_REQUIRES_PINNED_DISTINCT_EVIDENCE_LOCATION",
    "failure_policy": "MISSING_FAILED_OR_UNVERIFIABLE_EVIDENCE_BLOCKS",
    "authority_policy": "EVIDENCE_COMPLETENESS_CANNOT_AUTHORIZE_BROKER_SUBMISSION",
    "trading_policy": "NO_NETWORK_CREDENTIAL_ORDER_OR_LIVE_TRADING_CAPABILITY",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete methodology-assessment append")
        written += count


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


def _normalise_gates(
    gates: Mapping[str, Mapping[str, Any]], *, assessed_at: datetime
) -> dict[str, dict[str, Any]]:
    if set(gates) != set(METHODOLOGY_GATE_LABELS):
        raise ValueError(
            "gates must contain exactly: " + ", ".join(METHODOLOGY_GATE_LABELS)
        )
    resolved: dict[str, dict[str, Any]] = {}
    locations: set[tuple[str, str]] = set()
    for key, label in METHODOLOGY_GATE_LABELS.items():
        value = gates[key]
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be an evidence object")
        status = str(value.get("status") or "").upper()
        if status not in {"PASSED", "FAILED"}:
            raise ValueError(f"{key}.status must be PASSED or FAILED")
        observed = _as_datetime(value.get("observed_at"))
        if observed > assessed_at:
            raise ValueError(f"{key}.observed_at cannot postdate assessed_at")
        source_hash = _sha256(value.get("source_input_sha256"), f"{key}.source_input_sha256")
        locator = _required(value.get("source_locator"), f"{key}.source_locator")
        location = (source_hash, locator)
        if location in locations:
            raise ValueError("each methodology gate requires a distinct evidence location")
        locations.add(location)
        resolved[key] = {
            "label": label,
            "status": status,
            "evidence_summary": _required(value.get("evidence_summary"), f"{key}.evidence_summary"),
            "observed_at": observed.isoformat(),
            "source_uri": _https_uri(value.get("source_uri"), f"{key}.source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": locator,
        }
    return resolved


def _assessment_id(
    strategy_version: str,
    git_revision: str,
    assessed_at: str,
    gates: Mapping[str, Any],
) -> str:
    material = [
        strategy_version,
        git_revision,
        assessed_at,
        gates,
        METHODOLOGY_ASSESSMENT_POLICY_VERSION,
    ]
    return "MPREF-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class EvidenceBackedPaperSubmissionPreflightLedger:
    """Append-only assessments that can block but never authorize submission."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Methodology preflight has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank methodology-preflight line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at methodology-preflight line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Methodology-preflight line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def assess(
        self,
        *,
        strategy_version: str,
        git_revision: str,
        assessed_by: str,
        gates: Mapping[str, Mapping[str, Any]],
        assessed_at: str | datetime | None = None,
        supersedes_assessment_id: str | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        assessed = _as_datetime(assessed_at or datetime.now(timezone.utc))
        if assessed > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("assessed_at cannot be in the future")
        resolved_gates = _normalise_gates(gates, assessed_at=assessed)
        version = _required(strategy_version, "strategy_version")
        revision = _required(git_revision, "git_revision")
        assessor = _required(assessed_by, "assessed_by")
        supersedes = str(supersedes_assessment_id or "").strip() or None
        passed = [key for key, value in resolved_gates.items() if value["status"] == "PASSED"]
        open_gates = [key for key in METHODOLOGY_GATE_LABELS if key not in passed]
        result = {
            "schema_version": METHODOLOGY_ASSESSMENT_SCHEMA_VERSION,
            "policy_version": METHODOLOGY_ASSESSMENT_POLICY_VERSION,
            "assessment_id": _assessment_id(version, revision, assessed.isoformat(), resolved_gates),
            "record_type": "EVIDENCE_BACKED_PAPER_SUBMISSION_PREFLIGHT",
            "status": "BLOCKED" if open_gates else "EVIDENCE_COMPLETE_AWAITING_HUMAN_DECISION",
            "assessed_at": assessed.isoformat(),
            "assessed_by": assessor,
            "strategy_version": version,
            "git_revision": revision,
            "supersedes_assessment_id": supersedes,
            "gates": resolved_gates,
            "open_gates": open_gates,
            "all_evidence_passed": not open_gates,
            "broker_submission_enabled": False,
            "paper_account_connected": False,
            "credentials_permitted": False,
            "network_request_enabled": False,
            "order_submission_enabled": False,
            "live_trading_enabled": False,
            "next_authority": (
                "RESOLVE_METHODOLOGY_GATES"
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
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Methodology-preflight record {index} has been modified.")
            try:
                assessed = _as_datetime(record["assessed_at"])
                gates = _normalise_gates(record["gates"], assessed_at=assessed)
                version = _required(record.get("strategy_version"), "strategy_version")
                revision = _required(record.get("git_revision"), "git_revision")
                _required(record.get("assessed_by"), "assessed_by")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Methodology-preflight record {index} has invalid evidence."
                ) from error
            expected_id = _assessment_id(version, revision, assessed.isoformat(), gates)
            open_gates = [
                key for key in METHODOLOGY_GATE_LABELS if gates[key]["status"] != "PASSED"
            ]
            supersedes = record.get("supersedes_assessment_id")
            predecessor = seen.get(str(supersedes)) if supersedes else None
            chain_valid = (
                supersedes is None
                or (
                    predecessor is not None
                    and predecessor["strategy_version"] == version
                    and assessed > _as_datetime(predecessor["assessed_at"])
                )
            )
            boundary = (
                record.get("schema_version") == METHODOLOGY_ASSESSMENT_SCHEMA_VERSION
                and record.get("policy_version") == METHODOLOGY_ASSESSMENT_POLICY_VERSION
                and record.get("assessment_id") == expected_id
                and expected_id not in seen
                and record.get("record_type") == "EVIDENCE_BACKED_PAPER_SUBMISSION_PREFLIGHT"
                and record.get("status") == (
                    "BLOCKED" if open_gates else "EVIDENCE_COMPLETE_AWAITING_HUMAN_DECISION"
                )
                and record.get("gates") == gates
                and record.get("open_gates") == open_gates
                and record.get("all_evidence_passed") is (not open_gates)
                and all(record.get(field) is False for field in (
                    "broker_submission_enabled", "paper_account_connected", "credentials_permitted",
                    "network_request_enabled", "order_submission_enabled", "live_trading_enabled",
                ))
                and record.get("next_authority") == (
                    "RESOLVE_METHODOLOGY_GATES"
                    if open_gates
                    else "SEPARATE_EXPLICIT_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
                )
                and all(record.get(key) == value for key, value in POLICY.items())
                and assessed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and chain_valid
            )
            if not boundary:
                raise LedgerIntegrityError(f"Methodology-preflight record {index} violates its boundary.")
            seen[expected_id] = record
            previous_hash = record["record_hash"]
        return records

    def latest(self, *, strategy_version: str) -> dict[str, Any] | None:
        version = _required(strategy_version, "strategy_version")
        matches = [item for item in self.verify() if item["strategy_version"] == version]
        superseded = {
            item["supersedes_assessment_id"]
            for item in matches
            if item.get("supersedes_assessment_id")
        }
        heads = [item for item in matches if item["assessment_id"] not in superseded]
        if len(heads) > 1:
            raise LedgerIntegrityError("Methodology assessments have competing unsuperseded heads.")
        return heads[0] if heads else None

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["assessment_id"] == result["assessment_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {
                    k: v for k, v in result.items() if k not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("Methodology assessment already exists.")
            same_strategy = [
                item for item in records if item["strategy_version"] == result["strategy_version"]
            ]
            if same_strategy:
                predecessor = result.get("supersedes_assessment_id")
                current = self.latest(strategy_version=result["strategy_version"])
                if predecessor != current["assessment_id"]:
                    raise LedgerIntegrityError("New assessment must supersede the current strategy head.")
                if _as_datetime(result["assessed_at"]) <= _as_datetime(current["assessed_at"]):
                    raise LedgerIntegrityError(
                        "New assessment must be later than the current strategy head."
                    )
            elif result.get("supersedes_assessment_id") is not None:
                raise LedgerIntegrityError("First strategy assessment cannot supersede another record.")
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
