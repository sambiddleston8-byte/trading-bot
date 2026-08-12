from __future__ import annotations

"""Immutable promotion evidence bundle that cannot record human approval."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.shadow_test_result import ShadowTestResultLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


PROMOTION_BUNDLE_SCHEMA_VERSION = "1.0"
PROMOTION_BUNDLE_POLICY_VERSION = "human-gated-promotion-review-bundle-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_GITHUB_URL = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/(?:issues|pull)/[1-9][0-9]*$")


def _required(value: Any, name: str, maximum: int = 300) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _hash(value: Any, name: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _revision(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not _GIT_REVISION.fullmatch(result):
        raise ValueError("candidate_git_revision must be a full hexadecimal commit identifier")
    return result


def _github_url(value: Any, name: str, kind: str) -> str:
    result = _required(value, name, 500)
    if not _GITHUB_URL.fullmatch(result) or f"/{kind}/" not in result:
        raise ValueError(f"{name} must be a canonical GitHub {kind} URL")
    return result


def _bundle_id(shadow_result_id: str, assembled_at: str, material: dict[str, Any]) -> str:
    return "PROMOTION-" + hashlib.sha256(
        _canonical_json([shadow_result_id, assembled_at, material, PROMOTION_BUNDLE_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class PromotionReviewBundleLedger:
    """Assembles evidence for a later human decision; never makes that decision."""

    def __init__(self, path: str | Path, shadow_result_ledger: ShadowTestResultLedger) -> None:
        self.path = Path(path)
        self.shadow_result_ledger = shadow_result_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Promotion-bundle ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank promotion-bundle line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at promotion-bundle line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Promotion-bundle line {line_number} is not an object.")
                records.append(record)
        return records

    def assemble(
        self, *, shadow_result_id: str, candidate_git_revision: str,
        github_issue_url: str, github_pull_request_url: str,
        builder_identity: str, independent_reviewer_identity: str,
        deterministic_test_evidence_sha256: str,
        independent_review_evidence_sha256: str,
        rollback_plan_sha256: str, implementation_manifest_sha256: str,
        assembled_by: str, assembled_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        results = self.shadow_result_ledger.verify()
        result = next((item for item in results if item.get("shadow_result_id") == shadow_result_id), None)
        if result is None or result.get("status") != "SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW":
            raise ValueError("A verified passing shadow result is required")
        assembled = _timestamp(assembled_at or datetime.now(timezone.utc))
        if assembled > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("assembled_at cannot be in the future")
        if assembled < _timestamp(result["completed_at"]):
            raise ValueError("assembled_at cannot predate the shadow result")
        builder = _required(builder_identity, "builder_identity", 100)
        reviewer = _required(independent_reviewer_identity, "independent_reviewer_identity", 100)
        if builder.casefold() == reviewer.casefold():
            raise ValueError("independent reviewer must differ from builder")
        evidence = {
            "candidate_git_revision": _revision(candidate_git_revision),
            "github_issue_url": _github_url(github_issue_url, "github_issue_url", "issues"),
            "github_pull_request_url": _github_url(github_pull_request_url, "github_pull_request_url", "pull"),
            "builder_identity": builder,
            "independent_reviewer_identity": reviewer,
            "deterministic_test_evidence_sha256": _hash(deterministic_test_evidence_sha256, "deterministic_test_evidence_sha256"),
            "independent_review_evidence_sha256": _hash(independent_review_evidence_sha256, "independent_review_evidence_sha256"),
            "rollback_plan_sha256": _hash(rollback_plan_sha256, "rollback_plan_sha256"),
            "implementation_manifest_sha256": _hash(implementation_manifest_sha256, "implementation_manifest_sha256"),
        }
        record = {
            "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
            "policy_version": PROMOTION_BUNDLE_POLICY_VERSION,
            "promotion_bundle_id": _bundle_id(result["shadow_result_id"], assembled.isoformat(), evidence),
            "record_type": "CONTROLLED_LEARNING_PROMOTION_REVIEW_BUNDLE",
            "status": "COMPLETE_AWAITING_EXPLICIT_HUMAN_DECISION",
            "assembled_at": assembled.isoformat(),
            "assembled_by": _required(assembled_by, "assembled_by", 100),
            "shadow_result_id": result["shadow_result_id"],
            "shadow_result_record_hash": result["record_hash"],
            "candidate_strategy_version": result["candidate_strategy_version"],
            "baseline_strategy_version": result["baseline_strategy_version"],
            **evidence,
            "deterministic_tests_passed": True,
            "independent_review_passed": True,
            "rollback_plan_present": True,
            "human_decision_recorded": False,
            "promotion_approved": False,
            "incumbent_changed": False,
            "production_rule_changed": False,
            "production_active": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_results = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Promotion-bundle record {index} has been modified.")
            results, reasons = resolve_pinned_records(
                self.shadow_result_ledger.verify(), [record.get("shadow_result_id")],
                [record.get("shadow_result_record_hash")], id_field="shadow_result_id", label="shadow result",
            )
            if reasons or len(results) != 1:
                raise LedgerIntegrityError(f"Promotion-bundle record {index} lost its shadow result.")
            result = results[0]
            try:
                assembled = _timestamp(record.get("assembled_at")); builder = _required(record.get("builder_identity"), "builder", 100); reviewer = _required(record.get("independent_reviewer_identity"), "reviewer", 100)
                evidence = {
                    "candidate_git_revision": _revision(record.get("candidate_git_revision")),
                    "github_issue_url": _github_url(record.get("github_issue_url"), "issue", "issues"),
                    "github_pull_request_url": _github_url(record.get("github_pull_request_url"), "pull request", "pull"),
                    "builder_identity": builder, "independent_reviewer_identity": reviewer,
                    "deterministic_test_evidence_sha256": _hash(record.get("deterministic_test_evidence_sha256"), "tests"),
                    "independent_review_evidence_sha256": _hash(record.get("independent_review_evidence_sha256"), "review"),
                    "rollback_plan_sha256": _hash(record.get("rollback_plan_sha256"), "rollback"),
                    "implementation_manifest_sha256": _hash(record.get("implementation_manifest_sha256"), "implementation"),
                }
                _required(record.get("assembled_by"), "assembled_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Promotion-bundle record {index} has invalid values.") from error
            expected_id = _bundle_id(result["shadow_result_id"], assembled.isoformat(), evidence)
            fixed_false = ("human_decision_recorded","promotion_approved","incumbent_changed","production_rule_changed","production_active","deployment_performed","order_submitted","live_trading_enabled")
            boundary = (
                record.get("schema_version") == PROMOTION_BUNDLE_SCHEMA_VERSION and record.get("policy_version") == PROMOTION_BUNDLE_POLICY_VERSION
                and record.get("promotion_bundle_id") == expected_id and result["shadow_result_id"] not in seen_results
                and record.get("record_type") == "CONTROLLED_LEARNING_PROMOTION_REVIEW_BUNDLE" and record.get("status") == "COMPLETE_AWAITING_EXPLICIT_HUMAN_DECISION"
                and result.get("status") == "SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW"
                and record.get("shadow_result_id") == result["shadow_result_id"] and record.get("shadow_result_record_hash") == result["record_hash"]
                and record.get("candidate_strategy_version") == result["candidate_strategy_version"] and record.get("baseline_strategy_version") == result["baseline_strategy_version"]
                and assembled >= _timestamp(result["completed_at"]) and assembled <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and builder.casefold() != reviewer.casefold()
                and record.get("deterministic_tests_passed") is True and record.get("independent_review_passed") is True and record.get("rollback_plan_present") is True
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary: raise LedgerIntegrityError(f"Promotion-bundle record {index} violates its boundary.")
            seen_results.add(result["shadow_result_id"]); previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True); descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX); records = self.verify()
            existing = next((item for item in records if item["promotion_bundle_id"] == result["promotion_bundle_id"]), None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored} == {k:v for k,v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Promotion bundle already exists.")
            if any(item["shadow_result_id"] == result["shadow_result_id"] for item in records): raise LedgerIntegrityError("Shadow result already has a promotion bundle.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH}; record={**material,"record_hash":_record_hash(material)}
            target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try: _write_all(target,(_canonical_json(record)+"\n").encode()); os.fsync(target)
            finally: os.close(target)
            return record
        finally: fcntl.flock(descriptor,fcntl.LOCK_UN); os.close(descriptor)
