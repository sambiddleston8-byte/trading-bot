from __future__ import annotations

"""Immutable mechanical evaluation of completed paper/shadow evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.experiment_result import HIGHER_IS_BETTER, LOWER_IS_BETTER
from core.orchestration.shadow_test_plan import ShadowTestPlanLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SHADOW_RESULT_SCHEMA_VERSION = "1.0"
SHADOW_RESULT_POLICY_VERSION = "complete-paper-shadow-result-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Any, name: str, *, non_negative: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite() or (non_negative and result < 0):
        qualifier = "non-negative " if non_negative else ""
        raise ValueError(f"{name} must be a {qualifier}finite decimal")
    return result


def _canonical_decimal(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _positive_int(value: Any, name: str, maximum: int = 10_000_000) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if result < 1 or result > maximum or str(result) != str(value).strip():
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return result


def _hash(value: Any, name: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _result_id(
    plan_id: str, completed_at: str, evidence_hash: str,
    complete_decisions: int,
) -> str:
    return "SHADOW-RESULT-" + hashlib.sha256(
        _canonical_json([
            plan_id, completed_at, evidence_hash, complete_decisions,
            SHADOW_RESULT_POLICY_VERSION,
        ]).encode()
    ).hexdigest()[:32].upper()


class ShadowTestResultLedger:
    """Records complete shadow evidence without granting promotion authority."""

    def __init__(self, path: str | Path, shadow_plan_ledger: ShadowTestPlanLedger) -> None:
        self.path = Path(path)
        self.shadow_plan_ledger = shadow_plan_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Shadow-result ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank shadow-result line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at shadow-result line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Shadow-result line {line_number} is not an object.")
                records.append(record)
        return records

    def record(
        self, *, shadow_plan_id: str, evidence_bundle_sha256: str,
        complete_decisions: int, baseline_primary_metric: Any,
        candidate_primary_metric: Any, baseline_maximum_drawdown: Any,
        candidate_maximum_drawdown: Any, baseline_turnover: Any,
        candidate_turnover: Any, completed_by: str,
        completed_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        plans = self.shadow_plan_ledger.verify()
        plan = next((item for item in plans if item.get("shadow_plan_id") == shadow_plan_id), None)
        if plan is None:
            raise ValueError("A verified shadow plan is required")
        completed = _timestamp(completed_at or datetime.now(timezone.utc))
        if completed > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("completed_at cannot be in the future")
        if completed < _timestamp(plan["shadow_not_after"]):
            raise ValueError("result cannot complete before the fixed shadow window ends")
        decisions = _positive_int(complete_decisions, "complete_decisions")
        if decisions < int(plan["minimum_complete_decisions"]):
            raise ValueError("complete_decisions is below the preregistered minimum")
        baseline = _decimal(baseline_primary_metric, "baseline_primary_metric")
        candidate = _decimal(candidate_primary_metric, "candidate_primary_metric")
        baseline_drawdown = _decimal(baseline_maximum_drawdown, "baseline_maximum_drawdown", non_negative=True)
        candidate_drawdown = _decimal(candidate_maximum_drawdown, "candidate_maximum_drawdown", non_negative=True)
        baseline_turnover_value = _decimal(baseline_turnover, "baseline_turnover", non_negative=True)
        candidate_turnover_value = _decimal(candidate_turnover, "candidate_turnover", non_negative=True)
        metric = plan["primary_metric"]
        if metric in LOWER_IS_BETTER:
            improvement = baseline - candidate
        elif metric in HIGHER_IS_BETTER:
            improvement = candidate - baseline
        else:
            raise ValueError("The preregistered metric has no fixed direction")
        drawdown_degradation = candidate_drawdown - baseline_drawdown
        turnover_increase = candidate_turnover_value - baseline_turnover_value
        primary_passed = improvement >= Decimal(plan["minimum_primary_improvement"])
        drawdown_passed = drawdown_degradation <= Decimal(plan["maximum_drawdown_degradation"])
        turnover_passed = turnover_increase <= Decimal(plan["maximum_turnover_increase"])
        accepted = primary_passed and drawdown_passed and turnover_passed
        evidence_hash = _hash(evidence_bundle_sha256, "evidence_bundle_sha256")
        result = {
            "schema_version": SHADOW_RESULT_SCHEMA_VERSION,
            "policy_version": SHADOW_RESULT_POLICY_VERSION,
            "shadow_result_id": _result_id(
                plan["shadow_plan_id"], completed.isoformat(), evidence_hash, decisions,
            ),
            "record_type": "CONTROLLED_LEARNING_COMPLETE_PAPER_SHADOW_RESULT",
            "status": "SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW" if accepted else "SHADOW_CRITERIA_NOT_MET",
            "paper_only": True,
            "completed_at": completed.isoformat(),
            "completed_by": _required(completed_by, "completed_by", 100),
            "shadow_plan_id": plan["shadow_plan_id"],
            "shadow_plan_record_hash": plan["record_hash"],
            "strategy_registry_entry_id": plan["strategy_registry_entry_id"],
            "candidate_strategy_version": plan["candidate_strategy_version"],
            "baseline_strategy_version": plan["baseline_strategy_version"],
            "evidence_bundle_sha256": evidence_hash,
            "complete_decisions": decisions,
            "primary_metric": metric,
            "baseline_primary_metric": _canonical_decimal(baseline),
            "candidate_primary_metric": _canonical_decimal(candidate),
            "primary_improvement": _canonical_decimal(improvement),
            "minimum_primary_improvement": plan["minimum_primary_improvement"],
            "baseline_maximum_drawdown": _canonical_decimal(baseline_drawdown),
            "candidate_maximum_drawdown": _canonical_decimal(candidate_drawdown),
            "drawdown_degradation": _canonical_decimal(drawdown_degradation),
            "maximum_drawdown_degradation": plan["maximum_drawdown_degradation"],
            "baseline_turnover": _canonical_decimal(baseline_turnover_value),
            "candidate_turnover": _canonical_decimal(candidate_turnover_value),
            "turnover_increase": _canonical_decimal(turnover_increase),
            "maximum_turnover_increase": plan["maximum_turnover_increase"],
            "primary_threshold_passed": primary_passed,
            "drawdown_constraint_passed": drawdown_passed,
            "turnover_constraint_passed": turnover_passed,
            "all_shadow_criteria_met": accepted,
            "complete_fixed_window": True,
            "retrospective_window_selection_used": False,
            "window_extension_used": False,
            "metric_substitution_used": False,
            "promotion_approved": False,
            "incumbent_changed": False,
            "production_active": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_plans = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Shadow-result record {index} has been modified.")
            plans, reasons = resolve_pinned_records(
                self.shadow_plan_ledger.verify(), [record.get("shadow_plan_id")],
                [record.get("shadow_plan_record_hash")], id_field="shadow_plan_id", label="shadow plan",
            )
            if reasons or len(plans) != 1:
                raise LedgerIntegrityError(f"Shadow-result record {index} lost its plan.")
            plan = plans[0]
            try:
                completed = _timestamp(record.get("completed_at")); evidence_hash = _hash(record.get("evidence_bundle_sha256"), "evidence")
                decisions = _positive_int(record.get("complete_decisions"), "decisions")
                baseline = _decimal(record.get("baseline_primary_metric"), "baseline"); candidate = _decimal(record.get("candidate_primary_metric"), "candidate")
                baseline_drawdown = _decimal(record.get("baseline_maximum_drawdown"), "baseline drawdown", non_negative=True); candidate_drawdown = _decimal(record.get("candidate_maximum_drawdown"), "candidate drawdown", non_negative=True)
                baseline_turnover = _decimal(record.get("baseline_turnover"), "baseline turnover", non_negative=True); candidate_turnover = _decimal(record.get("candidate_turnover"), "candidate turnover", non_negative=True)
                _required(record.get("completed_by"), "completed_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Shadow-result record {index} has invalid values.") from error
            metric = plan["primary_metric"]
            if metric not in LOWER_IS_BETTER | HIGHER_IS_BETTER:
                raise LedgerIntegrityError(f"Shadow-result record {index} has an unsupported metric.")
            improvement = baseline - candidate if metric in LOWER_IS_BETTER else candidate - baseline
            drawdown_degradation = candidate_drawdown - baseline_drawdown; turnover_increase = candidate_turnover - baseline_turnover
            primary_passed = improvement >= Decimal(plan["minimum_primary_improvement"]); drawdown_passed = drawdown_degradation <= Decimal(plan["maximum_drawdown_degradation"]); turnover_passed = turnover_increase <= Decimal(plan["maximum_turnover_increase"])
            accepted = primary_passed and drawdown_passed and turnover_passed
            expected_id = _result_id(
                plan["shadow_plan_id"], completed.isoformat(), evidence_hash, decisions,
            )
            fixed_false = ("retrospective_window_selection_used","window_extension_used","metric_substitution_used","promotion_approved","incumbent_changed","production_active","deployment_performed","order_submitted","live_trading_enabled")
            boundary = (
                record.get("schema_version") == SHADOW_RESULT_SCHEMA_VERSION and record.get("policy_version") == SHADOW_RESULT_POLICY_VERSION
                and record.get("shadow_result_id") == expected_id and plan["shadow_plan_id"] not in seen_plans
                and record.get("record_type") == "CONTROLLED_LEARNING_COMPLETE_PAPER_SHADOW_RESULT"
                and record.get("status") == ("SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW" if accepted else "SHADOW_CRITERIA_NOT_MET")
                and record.get("paper_only") is True and record.get("shadow_plan_id") == plan["shadow_plan_id"] and record.get("shadow_plan_record_hash") == plan["record_hash"]
                and record.get("strategy_registry_entry_id") == plan["strategy_registry_entry_id"] and record.get("candidate_strategy_version") == plan["candidate_strategy_version"] and record.get("baseline_strategy_version") == plan["baseline_strategy_version"]
                and completed >= _timestamp(plan["shadow_not_after"]) and completed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW and decisions >= int(plan["minimum_complete_decisions"])
                and record.get("primary_metric") == metric and record.get("baseline_primary_metric") == _canonical_decimal(baseline) and record.get("candidate_primary_metric") == _canonical_decimal(candidate) and record.get("primary_improvement") == _canonical_decimal(improvement) and record.get("minimum_primary_improvement") == plan["minimum_primary_improvement"]
                and record.get("baseline_maximum_drawdown") == _canonical_decimal(baseline_drawdown) and record.get("candidate_maximum_drawdown") == _canonical_decimal(candidate_drawdown) and record.get("drawdown_degradation") == _canonical_decimal(drawdown_degradation) and record.get("maximum_drawdown_degradation") == plan["maximum_drawdown_degradation"]
                and record.get("baseline_turnover") == _canonical_decimal(baseline_turnover) and record.get("candidate_turnover") == _canonical_decimal(candidate_turnover) and record.get("turnover_increase") == _canonical_decimal(turnover_increase) and record.get("maximum_turnover_increase") == plan["maximum_turnover_increase"]
                and record.get("primary_threshold_passed") is primary_passed and record.get("drawdown_constraint_passed") is drawdown_passed and record.get("turnover_constraint_passed") is turnover_passed and record.get("all_shadow_criteria_met") is accepted
                and record.get("complete_fixed_window") is True and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary: raise LedgerIntegrityError(f"Shadow-result record {index} violates its boundary.")
            seen_plans.add(plan["shadow_plan_id"]); previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True); descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX); records = self.verify()
            existing = next((item for item in records if item["shadow_result_id"] == result["shadow_result_id"]), None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored} == {k:v for k,v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Shadow result already exists.")
            if any(item["shadow_plan_id"] == result["shadow_plan_id"] for item in records): raise LedgerIntegrityError("Shadow plan already has a result.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH}; record={**material,"record_hash":_record_hash(material)}
            target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try: _write_all(target,(_canonical_json(record)+"\n").encode()); os.fsync(target)
            finally: os.close(target)
            return record
        finally: fcntl.flock(descriptor,fcntl.LOCK_UN); os.close(descriptor)
