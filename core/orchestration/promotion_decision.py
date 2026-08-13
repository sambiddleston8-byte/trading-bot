from __future__ import annotations

"""Immutable human promotion/rejection decision; never activates a strategy."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.promotion_review_bundle import PromotionReviewBundleLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "explicit-human-strategy-promotion-decision-v2"
MAX_CLOCK_SKEW = timedelta(minutes=5)
DECISIONS = {"APPROVE_FOR_SEPARATE_IMPLEMENTATION", "REJECT_CANDIDATE"}


def _required(value: Any, name: str, maximum: int = 2000) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decision_id(bundle_id: str, decision: str, decided_at: str) -> str:
    return "PROMOTION-DECISION-" + hashlib.sha256(
        _canonical_json([bundle_id, decision, decided_at, POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class PromotionDecisionLedger:
    """Records a human decision but changes no code, incumbent or runtime state."""

    def __init__(
        self, path: str | Path, bundle_ledger: PromotionReviewBundleLedger
    ) -> None:
        self.path = Path(path)
        self.bundle_ledger = bundle_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Promotion-decision ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank promotion-decision line {line_number}.")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid promotion-decision JSON at line {line_number}."
                    ) from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(
                        f"Promotion-decision line {line_number} is not an object."
                    )
                records.append(value)
        return records

    def decide(
        self, *, promotion_bundle_id: str, decision: str, rationale: str,
        decided_by: str, human_decision_confirmed: bool,
        decided_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        if human_decision_confirmed is not True:
            raise ValueError("Explicit human decision confirmation is required")
        bundle = next(
            (
                item for item in self.bundle_ledger.verify()
                if item["promotion_bundle_id"] == promotion_bundle_id
            ),
            None,
        )
        if bundle is None:
            raise ValueError("A verified complete promotion-review bundle is required")
        resolved_decision = _required(decision, "decision", 80).upper()
        if resolved_decision not in DECISIONS:
            raise ValueError("decision must explicitly approve implementation or reject")
        decided = _timestamp(decided_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= decided <= now + MAX_CLOCK_SKEW:
            raise ValueError("decided_at must match the actual decision time")
        if decided < _timestamp(bundle["assembled_at"]):
            raise ValueError("decision cannot predate the review bundle")
        approved = resolved_decision == "APPROVE_FOR_SEPARATE_IMPLEMENTATION"
        decision_maker = _required(decided_by, "decided_by", 100)
        disallowed_deciders = {
            str(bundle.get("builder_identity") or "").strip().casefold(),
            str(bundle.get("independent_reviewer_identity") or "").strip().casefold(),
        }
        if decision_maker.casefold() in disallowed_deciders:
            raise ValueError(
                "human decision maker must differ from the builder and independent reviewer"
            )
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "promotion_decision_id": _decision_id(
                bundle["promotion_bundle_id"], resolved_decision, decided.isoformat()
            ),
            "record_type": "EXPLICIT_HUMAN_STRATEGY_PROMOTION_DECISION",
            "status": (
                "HUMAN_APPROVED_FOR_SEPARATE_IMPLEMENTATION_NOT_ACTIVATED"
                if approved else "HUMAN_REJECTED_CANDIDATE"
            ),
            "decision": resolved_decision,
            "decided_at": decided.isoformat(),
            "decided_by": decision_maker,
            "rationale": _required(rationale, "rationale"),
            "human_decision_confirmed": True,
            "promotion_bundle_id": bundle["promotion_bundle_id"],
            "promotion_bundle_record_hash": bundle["record_hash"],
            "shadow_result_id": bundle["shadow_result_id"],
            "candidate_strategy_version": bundle["candidate_strategy_version"],
            "baseline_strategy_version": bundle["baseline_strategy_version"],
            "candidate_git_revision": bundle["candidate_git_revision"],
            "implementation_authorized": approved,
            "candidate_rejected": not approved,
            "incumbent_unchanged": True,
            "code_changed": False,
            "production_rule_changed": False,
            "production_active": False,
            "deployment_performed": False,
            "broker_connection_allowed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
            "self_promotion_allowed": False,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Promotion-decision record {index} was modified.")
            bundles, reasons = resolve_pinned_records(
                self.bundle_ledger.verify(), [record.get("promotion_bundle_id")],
                [record.get("promotion_bundle_record_hash")],
                id_field="promotion_bundle_id", label="promotion bundle",
            )
            if reasons or len(bundles) != 1:
                raise LedgerIntegrityError(
                    f"Promotion-decision record {index} lost its review bundle."
                )
            bundle = bundles[0]
            try:
                decision = _required(record.get("decision"), "decision", 80).upper()
                decided = _timestamp(record.get("decided_at"))
                decision_maker = _required(record.get("decided_by"), "decided_by", 100)
                _required(record.get("rationale"), "rationale")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Promotion-decision record {index} has invalid values."
                ) from error
            approved = decision == "APPROVE_FOR_SEPARATE_IMPLEMENTATION"
            expected_status = (
                "HUMAN_APPROVED_FOR_SEPARATE_IMPLEMENTATION_NOT_ACTIVATED"
                if approved else "HUMAN_REJECTED_CANDIDATE"
            )
            fixed_false = (
                "code_changed", "production_rule_changed", "production_active",
                "deployment_performed", "broker_connection_allowed", "order_submitted",
                "live_trading_enabled", "self_promotion_allowed",
            )
            valid = (
                decision in DECISIONS
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("promotion_decision_id") == _decision_id(
                    bundle["promotion_bundle_id"], decision, decided.isoformat()
                )
                and bundle["promotion_bundle_id"] not in seen
                and record.get("record_type") == "EXPLICIT_HUMAN_STRATEGY_PROMOTION_DECISION"
                and record.get("status") == expected_status
                and record.get("human_decision_confirmed") is True
                and decision_maker.casefold() not in {
                    str(bundle.get("builder_identity") or "").strip().casefold(),
                    str(bundle.get("independent_reviewer_identity") or "").strip().casefold(),
                }
                and decided >= _timestamp(bundle["assembled_at"])
                and decided <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("shadow_result_id") == bundle["shadow_result_id"]
                and record.get("candidate_strategy_version") == bundle["candidate_strategy_version"]
                and record.get("baseline_strategy_version") == bundle["baseline_strategy_version"]
                and record.get("candidate_git_revision") == bundle["candidate_git_revision"]
                and record.get("implementation_authorized") is approved
                and record.get("candidate_rejected") is (not approved)
                and record.get("incumbent_unchanged") is True
                and all(record.get(field) is False for field in fixed_false)
            )
            if not valid:
                raise LedgerIntegrityError(
                    f"Promotion-decision record {index} violates its boundary."
                )
            seen.add(bundle["promotion_bundle_id"])
            previous = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"),
            os.O_CREAT | os.O_RDWR, 0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item for item in records
                    if item["promotion_bundle_id"] == result["promotion_bundle_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and (
                    {key: value for key, value in existing.items() if key not in ignored}
                    == {key: value for key, value in result.items() if key not in ignored}
                ):
                    return existing
                raise LedgerIntegrityError("Promotion bundle already has a human decision.")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode())
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
