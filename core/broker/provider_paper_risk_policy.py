from __future__ import annotations

"""Human-preregistered Alpaca paper risk limits; registration activates nothing."""

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


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-risk-control-policy-v2"
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_AGE_BOUNDS = (1, 3600)
MONETARY_LIMIT_FIELDS = (
    "max_order_notional_usd",
    "max_position_notional_usd",
    "max_gross_exposure_usd",
    "max_daily_loss_usd",
)
FIXED_FALSE_FIELDS = (
    "broker_access_enabled",
    "paper_order_submission_enabled",
    "live_order_submission_enabled",
    "automatic_activation_allowed",
    "self_limit_change_allowed",
    "live_trading_enabled",
    # Honest boundary flags: this ledger enforces no limit, routes no order, and is
    # neither externally anchored nor cryptographically authenticated (a fully
    # self-consistent local rewrite of every derived hash is not detectable by verify()).
    "risk_limits_enforced",
    "order_route_exists",
    "external_head_anchor_present",
    "cryptographic_authentication_present",
)
DENIED_ACTIONS = [
    "ACCESS_BROKER_CREDENTIALS",
    "SUBMIT_BROKER_ORDER",
    "CANCEL_BROKER_ORDER",
    "REPLACE_BROKER_ORDER",
    "ACTIVATE_POLICY",
    "CHANGE_OWN_RISK_LIMITS",
    "ENABLE_LIVE_TRADING",
]
RECORD_FIELDS = frozenset(
    {
        "schema_version", "policy_version", "policy_id", "status", "record_type",
        "broker", "environment", "account_reference_sha256", "portfolio_version",
        "strategy_version", "max_account_snapshot_age_seconds",
        "max_risk_snapshot_age_seconds", "kill_switch_identifier",
        "effective_not_before", "recorded_at", "decided_by", "decision_reference",
        "human_decision_confirmed", "denied_actions", "git_revision",
        "previous_hash", "record_hash",
    }
    | set(MONETARY_LIMIT_FIELDS)
    | set(FIXED_FALSE_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete provider-paper-risk-policy append")
        offset += count


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _decimal_limit(value: Any, name: str) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be provided as a decimal string, not a float")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite positive decimal string") from error
    if not resolved.is_finite() or resolved <= 0:
        raise ValueError(f"{name} must be a finite positive decimal string")
    return format(resolved.normalize(), "f")


def _bounded_snapshot_age(value: Any, name: str = "max_account_snapshot_age_seconds") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(resolved) != str(value).strip() and not (
        isinstance(value, float) and value.is_integer()
    ):
        raise ValueError(f"{name} must be an integer")
    minimum, maximum = SNAPSHOT_AGE_BOUNDS
    if resolved < minimum or resolved > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return resolved


def _policy_id(
    effective: str,
    git_revision: str,
    account_hash: str,
    portfolio_version: str,
    strategy_version: str,
    limits: dict[str, str],
    account_snapshot_age: int,
    risk_snapshot_age: int,
    kill_switch_identifier: str,
    recorded_at: str,
    decided_by: str,
    decision_reference: str,
) -> str:
    material = [
        "PROVIDER_PAPER_RISK", effective, git_revision, account_hash, portfolio_version,
        strategy_version, dict(limits), account_snapshot_age, risk_snapshot_age,
        kill_switch_identifier,
        recorded_at, decided_by, decision_reference, POLICY_VERSION,
    ]
    return "PPRISK-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperRiskControlPolicyLedger:
    """Append-only, hash-chained risk-limit policy. Preregistration activates nothing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Provider-paper-risk-policy ledger has an incomplete final line."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank provider-paper-risk-policy line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at provider-paper-risk-policy line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Provider-paper-risk-policy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        account_reference_sha256: str,
        portfolio_version: str,
        strategy_version: str,
        max_order_notional_usd: Any,
        max_position_notional_usd: Any,
        max_gross_exposure_usd: Any,
        max_daily_loss_usd: Any,
        max_account_snapshot_age_seconds: Any,
        max_risk_snapshot_age_seconds: Any,
        kill_switch_identifier: str,
        decided_by: str,
        decision_reference: str,
        human_decision_confirmed: bool,
        effective_not_before: str | datetime,
        git_revision: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        effective = _timestamp(effective_not_before)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if effective < recorded + MINIMUM_LEAD_TIME:
            raise ValueError("effective_not_before must be at least five minutes after preregistration")
        account_hash = _sha256(account_reference_sha256, "account_reference_sha256")
        portfolio = _required(portfolio_version, "portfolio_version")
        strategy = _required(strategy_version, "strategy_version")
        limits = {
            name: _decimal_limit(value, name)
            for name, value in zip(
                MONETARY_LIMIT_FIELDS,
                (
                    max_order_notional_usd,
                    max_position_notional_usd,
                    max_gross_exposure_usd,
                    max_daily_loss_usd,
                ),
            )
        }
        if not (
            Decimal(limits["max_order_notional_usd"])
            <= Decimal(limits["max_position_notional_usd"])
            <= Decimal(limits["max_gross_exposure_usd"])
        ):
            raise ValueError(
                "max_order_notional_usd must be <= max_position_notional_usd <= max_gross_exposure_usd"
            )
        account_snapshot_age = _bounded_snapshot_age(
            max_account_snapshot_age_seconds, "max_account_snapshot_age_seconds"
        )
        risk_snapshot_age = _bounded_snapshot_age(
            max_risk_snapshot_age_seconds, "max_risk_snapshot_age_seconds"
        )
        kill_switch_id = _required(kill_switch_identifier, "kill_switch_identifier")
        git = _required(git_revision, "git_revision")
        decided = _required(decided_by, "decided_by")
        decision_ref = _required(decision_reference, "decision_reference")
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "policy_id": _policy_id(
                effective.isoformat(), git, account_hash, portfolio, strategy,
                limits,
                account_snapshot_age,
                risk_snapshot_age,
                kill_switch_id,
                recorded.isoformat(),
                decided,
                decision_ref,
            ),
            "status": "PREREGISTERED_INACTIVE",
            "record_type": "HUMAN_APPROVED_PROVIDER_PAPER_RISK_CONTROL_POLICY",
            "broker": "ALPACA",
            "environment": "PAPER",
            "account_reference_sha256": account_hash,
            "portfolio_version": portfolio,
            "strategy_version": strategy,
            **limits,
            "max_account_snapshot_age_seconds": account_snapshot_age,
            "max_risk_snapshot_age_seconds": risk_snapshot_age,
            "kill_switch_identifier": kill_switch_id,
            "effective_not_before": effective.isoformat(),
            "recorded_at": recorded.isoformat(),
            "decided_by": decided,
            "decision_reference": decision_ref,
            "human_decision_confirmed": True,
            "denied_actions": list(DENIED_ACTIONS),
            **{field: False for field in FIXED_FALSE_FIELDS},
            "git_revision": git,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Provider-paper-risk-policy record {index} has been modified.")
            try:
                effective = _timestamp(record.get("effective_not_before"))
                recorded = _timestamp(record.get("recorded_at"))
                account_hash = _sha256(record.get("account_reference_sha256"), "account_reference_sha256")
                portfolio = _required(record.get("portfolio_version"), "portfolio_version")
                strategy = _required(record.get("strategy_version"), "strategy_version")
                limits = {
                    name: _decimal_limit(record.get(name), name) for name in MONETARY_LIMIT_FIELDS
                }
                account_snapshot_age = _bounded_snapshot_age(
                    record.get("max_account_snapshot_age_seconds"),
                    "max_account_snapshot_age_seconds",
                )
                risk_snapshot_age = _bounded_snapshot_age(
                    record.get("max_risk_snapshot_age_seconds"),
                    "max_risk_snapshot_age_seconds",
                )
                kill_switch_id = _required(record.get("kill_switch_identifier"), "kill_switch_identifier")
                git = _required(record.get("git_revision"), "git_revision")
                decided = _required(record.get("decided_by"), "decided_by")
                decision_ref = _required(record.get("decision_reference"), "decision_reference")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Provider-paper-risk-policy record {index} has invalid values."
                ) from error
            expected_id = _policy_id(
                effective.isoformat(), git, account_hash, portfolio, strategy,
                limits,
                account_snapshot_age,
                risk_snapshot_age,
                kill_switch_id,
                recorded.isoformat(),
                decided,
                decision_ref,
            )
            relationship_valid = (
                Decimal(limits["max_order_notional_usd"])
                <= Decimal(limits["max_position_notional_usd"])
                <= Decimal(limits["max_gross_exposure_usd"])
            )
            boundary = (
                set(record) == RECORD_FIELDS
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("policy_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "PREREGISTERED_INACTIVE"
                and record.get("record_type") == "HUMAN_APPROVED_PROVIDER_PAPER_RISK_CONTROL_POLICY"
                and record.get("broker") == "ALPACA"
                and record.get("environment") == "PAPER"
                and record.get("human_decision_confirmed") is True
                and effective >= recorded + MINIMUM_LEAD_TIME
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and relationship_valid
                and all(record.get(name) == value for name, value in limits.items())
                and record.get("max_account_snapshot_age_seconds") == account_snapshot_age
                and record.get("max_risk_snapshot_age_seconds") == risk_snapshot_age
                and record.get("kill_switch_identifier") == kill_switch_id
                and record.get("denied_actions") == DENIED_ACTIONS
                and all(record.get(field) is False for field in FIXED_FALSE_FIELDS)
                and record.get("git_revision") == git
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Provider-paper-risk-policy record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, policy: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["policy_id"] == policy["policy_id"]), None
            )
            if existing is None and allow_existing:
                retry_ignored = {
                    "policy_id", "recorded_at", "previous_hash", "record_hash"
                }
                proposed_retry = {
                    key: value for key, value in policy.items() if key not in retry_ignored
                }
                existing = next(
                    (
                        item
                        for item in records
                        if {
                            key: value
                            for key, value in item.items()
                            if key not in retry_ignored
                        }
                        == proposed_retry
                    ),
                    None,
                )
            if existing:
                ignored = {"policy_id", "previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in policy.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Provider-paper-risk policy {policy['policy_id']} already exists."
                )
            material = {
                **policy,
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
