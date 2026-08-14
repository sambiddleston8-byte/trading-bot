from __future__ import annotations

"""Human-preregistered paper execution-cost stress; activates nothing."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.replay_execution_policy import ReplayExecutionPolicy


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-execution-stress-policy-v1"
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SCENARIOS = frozenset({"BASE", "PESSIMISTIC"})
SCENARIO_FIELDS = frozenset(
    {
        "commission_bps_per_side",
        "spread_bps_per_side",
        "slippage_bps_per_side",
        "latency_bps_per_side",
        "market_impact_bps_per_side",
        "maximum_volume_participation_rate",
        "maximum_order_age_minutes",
    }
)
DENIED_ACTIONS = [
    "ACCESS_BROKER_CREDENTIALS",
    "READ_BROKER_MARKET_DATA",
    "SUBMIT_BROKER_ORDER",
    "CANCEL_BROKER_ORDER",
    "REPLACE_BROKER_ORDER",
    "ACTIVATE_POLICY",
    "CHANGE_OWN_EXECUTION_COSTS",
    "ENABLE_LIVE_TRADING",
]
FIXED_FIELDS = {
    "shadow_calculation_only": True,
    "policy_active": False,
    "broker_access_enabled": False,
    "broker_credentials_accessed": False,
    "paper_order_submission_enabled": False,
    "live_order_submission_enabled": False,
    "automatic_activation_allowed": False,
    "self_cost_change_allowed": False,
    "execution_costs_enforced": False,
    "broker_reconciliation_complete": False,
    "reference_price_source_authenticated": False,
    "volume_evidence_present": False,
    "market_impact_model_calibrated": False,
    "regulatory_fees_complete": False,
    "borrow_costs_complete": False,
    "order_route_exists": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
    "live_trading_enabled": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "execution_stress_policy_id",
        "record_type",
        "status",
        "broker",
        "environment",
        "account_reference_sha256",
        "portfolio_version",
        "strategy_version",
        "scenarios",
        "effective_not_before",
        "recorded_at",
        "decided_by",
        "decision_reference",
        "human_decision_confirmed",
        "denied_actions",
        "git_revision",
        "previous_hash",
        "record_hash",
    }
    | set(FIXED_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete paper execution-stress policy append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _exact_decimal(value: Any, name: str) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an exact decimal string, not a float")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _scenarios(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != SCENARIOS:
        raise ValueError("scenarios must contain exactly BASE and PESSIMISTIC")
    exact: dict[str, dict[str, str]] = {}
    for name in ("BASE", "PESSIMISTIC"):
        raw = value[name]
        if not isinstance(raw, Mapping) or set(raw) != SCENARIO_FIELDS:
            raise ValueError(f"{name} must contain the exact execution-cost fields")
        exact[name] = {
            field: _exact_decimal(raw[field], f"{name}.{field}")
            for field in sorted(SCENARIO_FIELDS)
        }
    normalized = ReplayExecutionPolicy.define(exact)["scenarios"]
    adverse = sum(
        Decimal(normalized["PESSIMISTIC"][field])
        for field in (
            "spread_bps_per_side",
            "slippage_bps_per_side",
            "latency_bps_per_side",
            "market_impact_bps_per_side",
        )
    )
    if adverse >= Decimal("10000"):
        raise ValueError("PESSIMISTIC adverse price costs must remain below 100%")
    if Decimal(normalized["PESSIMISTIC"]["commission_bps_per_side"]) >= Decimal("10000"):
        raise ValueError("PESSIMISTIC commission must remain below 100%")
    return normalized


def _identity(
    account: str,
    portfolio: str,
    strategy: str,
    scenarios: Mapping[str, Any],
    effective: str,
    recorded: str,
    decided_by: str,
    decision_reference: str,
    git_revision: str,
) -> str:
    material = [
        account,
        portfolio,
        strategy,
        scenarios,
        effective,
        recorded,
        decided_by,
        decision_reference,
        git_revision,
        POLICY_VERSION,
    ]
    return "PPESTRESS-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperExecutionStressPolicyLedger:
    """Append-only inactive human cost policy for shadow paper calculations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Execution-stress policy has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank execution-stress line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid execution-stress JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Execution-stress line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        account_reference_sha256: str,
        portfolio_version: str,
        strategy_version: str,
        scenarios: Mapping[str, Mapping[str, Any]],
        effective_not_before: str | datetime,
        decided_by: str,
        decision_reference: str,
        human_decision_confirmed: bool,
        git_revision: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        effective = _timestamp(effective_not_before)
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if effective < recorded + MINIMUM_LEAD_TIME:
            raise ValueError("effective_not_before must be at least five minutes after preregistration")
        account = _sha256(account_reference_sha256, "account_reference_sha256")
        portfolio = _required(portfolio_version, "portfolio_version")
        strategy = _required(strategy_version, "strategy_version")
        normalized = _scenarios(scenarios)
        decided = _required(decided_by, "decided_by")
        reference = _required(decision_reference, "decision_reference")
        git = _required(git_revision, "git_revision")
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "execution_stress_policy_id": _identity(
                account,
                portfolio,
                strategy,
                normalized,
                effective.isoformat(),
                recorded.isoformat(),
                decided,
                reference,
                git,
            ),
            "record_type": "HUMAN_APPROVED_PROVIDER_PAPER_EXECUTION_STRESS_POLICY",
            "status": "PREREGISTERED_INACTIVE",
            "broker": "ALPACA",
            "environment": "PAPER",
            "account_reference_sha256": account,
            "portfolio_version": portfolio,
            "strategy_version": strategy,
            "scenarios": normalized,
            "effective_not_before": effective.isoformat(),
            "recorded_at": recorded.isoformat(),
            "decided_by": decided,
            "decision_reference": reference,
            "human_decision_confirmed": True,
            "denied_actions": list(DENIED_ACTIONS),
            "git_revision": git,
            **FIXED_FIELDS,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Execution-stress policy {index} was modified.")
            try:
                recorded = _timestamp(record.get("recorded_at"))
                effective = _timestamp(record.get("effective_not_before"))
                account = _sha256(record.get("account_reference_sha256"), "account_reference_sha256")
                portfolio = _required(record.get("portfolio_version"), "portfolio_version")
                strategy = _required(record.get("strategy_version"), "strategy_version")
                normalized = _scenarios(record.get("scenarios"))
                decided = _required(record.get("decided_by"), "decided_by")
                reference = _required(record.get("decision_reference"), "decision_reference")
                git = _required(record.get("git_revision"), "git_revision")
            except (ArithmeticError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Execution-stress policy {index} has invalid values."
                ) from error
            expected_id = _identity(
                account,
                portfolio,
                strategy,
                normalized,
                effective.isoformat(),
                recorded.isoformat(),
                decided,
                reference,
                git,
            )
            boundary = (
                set(record) == RECORD_FIELDS
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("execution_stress_policy_id") == expected_id
                and expected_id not in seen
                and record.get("record_type")
                == "HUMAN_APPROVED_PROVIDER_PAPER_EXECUTION_STRESS_POLICY"
                and record.get("status") == "PREREGISTERED_INACTIVE"
                and record.get("broker") == "ALPACA"
                and record.get("environment") == "PAPER"
                and record.get("scenarios") == normalized
                and effective >= recorded + MINIMUM_LEAD_TIME
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("human_decision_confirmed") is True
                and record.get("denied_actions") == DENIED_ACTIONS
                and all(record.get(field) == value for field, value in FIXED_FIELDS.items())
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Execution-stress policy {index} violates its boundary."
                )
            seen.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["execution_stress_policy_id"]
                    == result["execution_stress_policy_id"]
                ),
                None,
            )
            if existing is None and allow_existing:
                ignored = {
                    "execution_stress_policy_id",
                    "recorded_at",
                    "previous_hash",
                    "record_hash",
                }
                proposed = {key: value for key, value in result.items() if key not in ignored}
                existing = next(
                    (
                        item
                        for item in records
                        if {key: value for key, value in item.items() if key not in ignored}
                        == proposed
                    ),
                    None,
                )
            if existing:
                ignored = {
                    "execution_stress_policy_id",
                    "recorded_at",
                    "previous_hash",
                    "record_hash",
                }
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Execution-stress policy already exists.")
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
