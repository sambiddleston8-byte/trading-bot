from __future__ import annotations

"""Immutable source-input provenance for the active research decision route."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.decision_ledger import (
    GENESIS_HASH,
    InvestmentDecisionLedger,
    LedgerIntegrityError,
    canonical_timestamp,
)


INPUT_MANIFEST_SCHEMA_VERSION = "1.0"
INPUT_MANIFEST_POLICY_VERSION = "active-research-point-in-time-inputs-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_INPUT_FAMILIES = (
    "financial_statements",
    "market_price",
    "analyst_estimates",
    "news_and_catalysts",
    "technical_market_history",
    "market_regime",
    "macro_environment",
)
POLICY = {
    "route": "INVESTMENT_RESEARCH_PIPELINE_TO_CANONICAL_CONTRACT_TO_MASTER_DECISION",
    "availability_policy": "PUBLICLY_AVAILABLE_AND_RETRIEVED_NO_LATER_THAN_CANONICAL_GENERATION",
    "missing_policy": "MISSING_INPUT_FAMILY_MAKES_MANIFEST_INCOMPLETE",
    "backfill_policy": "POST_DECISION_OR_POST_GENERATION_EVIDENCE_CANNOT_BE_CLAIMED_AS_INPUT",
    "authority_policy": "PROVENANCE_RECORD_ONLY_NOT_FORECAST_VALIDATION_OR_BROKER_AUTHORIZATION",
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
            raise OSError("Unable to complete point-in-time input-manifest append")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
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


def _normalise_inputs(
    inputs: Mapping[str, Mapping[str, Any]],
    *,
    canonical_generated_at: datetime,
) -> dict[str, dict[str, Any]]:
    if set(inputs) != set(REQUIRED_INPUT_FAMILIES):
        raise ValueError(
            "inputs must contain exactly: " + ", ".join(REQUIRED_INPUT_FAMILIES)
        )
    resolved: dict[str, dict[str, Any]] = {}
    evidence_locations: set[tuple[str, str]] = set()
    for family in REQUIRED_INPUT_FAMILIES:
        value = inputs[family]
        if not isinstance(value, Mapping):
            raise ValueError(f"{family} must be an input-evidence object")
        status = str(value.get("status") or "").upper()
        if status == "MISSING":
            resolved[family] = {
                "status": "MISSING",
                "missing_reason": _required(value.get("missing_reason"), f"{family}.missing_reason"),
            }
            continue
        if status != "AVAILABLE":
            raise ValueError(f"{family}.status must be AVAILABLE or MISSING")
        effective = _timestamp(value.get("effective_at"))
        public = _timestamp(value.get("publicly_available_at"))
        retrieved = _timestamp(value.get("retrieved_at"))
        if effective > public:
            raise ValueError(f"{family}.effective_at cannot postdate public availability")
        if public > retrieved:
            raise ValueError(f"{family}.retrieved_at cannot predate public availability")
        if retrieved > canonical_generated_at:
            raise ValueError(
                f"{family} retrieved after canonical generation cannot be claimed as an input"
            )
        source_hash = _sha256(value.get("source_input_sha256"), f"{family}.source_input_sha256")
        locator = _required(value.get("source_locator"), f"{family}.source_locator")
        location = (source_hash, locator)
        if location in evidence_locations:
            raise ValueError("each input family requires a distinct source evidence location")
        evidence_locations.add(location)
        resolved[family] = {
            "status": "AVAILABLE",
            "provider": _required(value.get("provider"), f"{family}.provider"),
            "dataset_or_endpoint": _required(
                value.get("dataset_or_endpoint"), f"{family}.dataset_or_endpoint"
            ),
            "effective_at": effective.isoformat(),
            "publicly_available_at": public.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "source_uri": _https_uri(value.get("source_uri"), f"{family}.source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": locator,
        }
    return resolved


def _manifest_id(
    decision_id: str,
    canonical_sha256: str,
    inputs: Mapping[str, Any],
) -> str:
    return "RINPUT-" + hashlib.sha256(
        _canonical_json(
            [decision_id, canonical_sha256, inputs, INPUT_MANIFEST_POLICY_VERSION]
        ).encode("utf-8")
    ).hexdigest()[:32].upper()


class ActiveResearchInputManifestLedger:
    """Append-only provenance; no input, score or recommendation is calculated."""

    def __init__(self, path: str | Path, decision_ledger: InvestmentDecisionLedger) -> None:
        self.path = Path(path)
        self.decision_ledger = decision_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Research-input manifest has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank research-input line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at research-input line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Research-input line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def _decision(self, decision_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.decision_ledger.verify()
                if item.get("decision_id") == decision_id
            ),
            None,
        )

    def record(
        self,
        *,
        decision_id: str,
        canonical_research_sha256: str,
        canonical_research_generated_at: str | datetime,
        inputs: Mapping[str, Mapping[str, Any]],
        recorded_by: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        decision_key = _required(decision_id, "decision_id")
        decision = self._decision(decision_key)
        if decision is None:
            raise ValueError("A verified investment decision is required")
        generated = _timestamp(canonical_research_generated_at)
        data_as_of = _timestamp(decision["data_as_of"])
        decided = _timestamp(decision["decided_at"])
        if generated > data_as_of or data_as_of > decided:
            raise ValueError(
                "canonical generation must not postdate decision data_as_of or decided_at"
            )
        resolved_inputs = _normalise_inputs(inputs, canonical_generated_at=generated)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if recorded < decided:
            raise ValueError("recorded_at cannot predate the investment decision")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        canonical_hash = _sha256(canonical_research_sha256, "canonical_research_sha256")
        complete = all(value["status"] == "AVAILABLE" for value in resolved_inputs.values())
        result = {
            "schema_version": INPUT_MANIFEST_SCHEMA_VERSION,
            "policy_version": INPUT_MANIFEST_POLICY_VERSION,
            "manifest_id": _manifest_id(decision_key, canonical_hash, resolved_inputs),
            "record_type": "ACTIVE_RESEARCH_POINT_IN_TIME_INPUT_MANIFEST",
            "status": "COMPLETE_POINT_IN_TIME_PROVENANCE" if complete else "INCOMPLETE_PROVENANCE",
            "recorded_at": recorded.isoformat(),
            "recorded_by": _required(recorded_by, "recorded_by"),
            "decision_id": decision_key,
            "decision_record_hash": decision["record_hash"],
            "ticker": decision["ticker"],
            "portfolio_version": decision["portfolio_version"],
            "strategy_git_revision": decision["git_revision"],
            "model_versions": decision["model_versions"],
            "data_as_of": decision["data_as_of"],
            "decided_at": decision["decided_at"],
            "canonical_research_sha256": canonical_hash,
            "canonical_research_generated_at": generated.isoformat(),
            "inputs": resolved_inputs,
            "available_input_families": [
                key for key in REQUIRED_INPUT_FAMILIES if resolved_inputs[key]["status"] == "AVAILABLE"
            ],
            "missing_input_families": [
                key for key in REQUIRED_INPUT_FAMILIES if resolved_inputs[key]["status"] == "MISSING"
            ],
            "point_in_time_provenance_complete": complete,
            "forecast_validated": False,
            "methodology_gate_cleared": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "learning_eligible": False,
            "live_trading_enabled": False,
            **POLICY,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_decisions: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Research-input record {index} has been modified.")
            decision = self._decision(str(record.get("decision_id") or ""))
            if decision is None:
                raise LedgerIntegrityError(f"Research-input record {index} lost its decision.")
            try:
                generated = _timestamp(record["canonical_research_generated_at"])
                inputs = _normalise_inputs(record["inputs"], canonical_generated_at=generated)
                canonical_hash = _sha256(
                    record.get("canonical_research_sha256"), "canonical_research_sha256"
                )
                recorded = _timestamp(record["recorded_at"])
                _required(record.get("recorded_by"), "recorded_by")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Research-input record {index} has invalid provenance."
                ) from error
            complete = all(value["status"] == "AVAILABLE" for value in inputs.values())
            available = [key for key in REQUIRED_INPUT_FAMILIES if inputs[key]["status"] == "AVAILABLE"]
            missing = [key for key in REQUIRED_INPUT_FAMILIES if inputs[key]["status"] == "MISSING"]
            expected_id = _manifest_id(decision["decision_id"], canonical_hash, inputs)
            boundary = (
                record.get("schema_version") == INPUT_MANIFEST_SCHEMA_VERSION
                and record.get("policy_version") == INPUT_MANIFEST_POLICY_VERSION
                and record.get("manifest_id") == expected_id
                and expected_id not in seen_ids
                and decision["decision_id"] not in seen_decisions
                and record.get("record_type") == "ACTIVE_RESEARCH_POINT_IN_TIME_INPUT_MANIFEST"
                and record.get("status") == (
                    "COMPLETE_POINT_IN_TIME_PROVENANCE" if complete else "INCOMPLETE_PROVENANCE"
                )
                and record.get("decision_record_hash") == decision["record_hash"]
                and all(record.get(field) == decision.get(field) for field in (
                    "ticker", "portfolio_version", "model_versions", "data_as_of", "decided_at"
                ))
                and record.get("strategy_git_revision") == decision["git_revision"]
                and generated <= _timestamp(decision["data_as_of"]) <= _timestamp(decision["decided_at"])
                and recorded >= _timestamp(decision["decided_at"])
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("inputs") == inputs
                and record.get("available_input_families") == available
                and record.get("missing_input_families") == missing
                and record.get("point_in_time_provenance_complete") is complete
                and all(record.get(field) is False for field in (
                    "forecast_validated", "methodology_gate_cleared", "recommendation_provided",
                    "broker_submission_enabled", "learning_eligible", "live_trading_enabled",
                ))
                and all(record.get(key) == value for key, value in POLICY.items())
            )
            if not boundary:
                raise LedgerIntegrityError(f"Research-input record {index} violates its boundary.")
            seen_ids.add(expected_id)
            seen_decisions.add(decision["decision_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["manifest_id"] == result["manifest_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {
                    k: v for k, v in result.items() if k not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("Research-input manifest already exists.")
            if any(item["decision_id"] == result["decision_id"] for item in records):
                raise LedgerIntegrityError("Decision already has a research-input manifest.")
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
