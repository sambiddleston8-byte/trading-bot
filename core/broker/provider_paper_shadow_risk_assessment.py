from __future__ import annotations

"""Shadow-only paper-risk comparison; no approval or order route is produced."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-shadow-risk-assessment-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
STATUSES = {
    "SHADOW_BLOCKED_LATCHED_STOP",
    "SHADOW_INCOMPLETE_SELL_QUANTITY_EVIDENCE",
    "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED",
    "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED",
}
MONEY_FIELDS = (
    "order_notional_usd",
    "current_ticker_long_exposure_usd",
    "pending_ticker_buy_exposure_usd",
    "projected_ticker_exposure_usd",
    "current_conservative_gross_exposure_usd",
    "projected_conservative_gross_exposure_usd",
    "daily_loss_usd",
    "max_order_notional_usd",
    "max_position_notional_usd",
    "max_gross_exposure_usd",
    "max_daily_loss_usd",
    "risk_snapshot_age_seconds",
)
CHECK_FIELDS = (
    "order_notional_within_limit",
    "position_notional_within_limit",
    "gross_exposure_within_limit",
    "daily_loss_within_limit",
    "risk_snapshot_fresh",
    "sell_quantity_evidence_complete",
    "kill_switch_latched",
    "mathematical_shadow_checks_pass",
)
FIXED_FIELDS = {
    "shadow_calculation_only": True,
    "policy_active": False,
    "risk_snapshot_broker_reconciled": False,
    "risk_snapshot_cryptographically_authenticated": False,
    "risk_limits_enforced": False,
    "execution_price_stress_applied": False,
    "fees_included": False,
    "fill_price_uncertainty_resolved": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
    "recording_clock_window_enforced": True,
    "broker_access_enabled": False,
    "broker_credentials_accessed": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "live_order_submission_allowed": False,
    "human_review_eligible": False,
    "recommendation_provided": False,
    "live_trading_enabled": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "assessment_id",
        "record_type",
        "status",
        "assessed_at",
        "recorded_at",
        "order_id",
        "proposal_record_hash",
        "policy_id",
        "policy_record_hash",
        "risk_snapshot_id",
        "risk_snapshot_record_hash",
        "account_reference_sha256",
        "portfolio_version",
        "strategy_version",
        "ticker",
        "side",
        "kill_switch_record_count",
        "kill_switch_head_hash",
        "matching_stop_id",
        "matching_stop_record_hash",
        "exact_fractions",
        "previous_hash",
        "record_hash",
    }
    | set(MONEY_FIELDS)
    | set(CHECK_FIELDS)
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
            raise OSError("Unable to complete shadow-risk assessment append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _amount(value: Any, name: str, *, positive: bool = False) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an exact finite decimal")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact finite decimal") from error
    if not decimal.is_finite() or decimal < 0 or (positive and decimal == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} decimal")
    return Fraction(decimal)


def _fraction(material: Any, name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} denominator must be positive")
    return value


def _decimal(value: Fraction) -> str:
    resolved = Decimal(value.numerator) / Decimal(value.denominator)
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _duration_seconds(value: timedelta) -> Fraction:
    return Fraction(value.days * 86400 + value.seconds, 1) + Fraction(
        value.microseconds, 1_000_000
    )


def _pin(
    records: Sequence[Mapping[str, Any]],
    identity: Any,
    record_hash: Any,
    *,
    id_field: str,
    label: str,
) -> Mapping[str, Any]:
    resolved, reasons = resolve_pinned_records(
        records,
        [identity],
        [record_hash],
        id_field=id_field,
        label=label,
    )
    if reasons or len(resolved) != 1:
        raise ValueError(reasons[0] if reasons else f"{label} is not uniquely pinned")
    return resolved[0]


def _assessment_id(
    proposal: Mapping[str, Any],
    policy: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    kill_count: int,
    kill_head: str,
    assessed_at: str,
) -> str:
    material = [
        proposal["order_id"],
        proposal["record_hash"],
        policy["policy_id"],
        policy["record_hash"],
        snapshot["snapshot_id"],
        snapshot["record_hash"],
        kill_count,
        kill_head,
        assessed_at,
        POLICY_VERSION,
    ]
    return "PPSRA-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperShadowRiskAssessmentLedger:
    """Append-only shadow comparisons. This class cannot approve or route orders."""

    def __init__(
        self,
        path: str | Path,
        proposal_ledger: Any,
        policy_ledger: Any,
        risk_snapshot_ledger: Any,
        kill_switch_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.proposal_ledger = proposal_ledger
        self.policy_ledger = policy_ledger
        self.risk_snapshot_ledger = risk_snapshot_ledger
        self.kill_switch_ledger = kill_switch_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Shadow-risk ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank shadow-risk line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid shadow-risk JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Shadow-risk line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def assess(
        self,
        *,
        order_id: str,
        proposal_record_hash: str,
        policy_id: str,
        policy_record_hash: str,
        risk_snapshot_id: str,
        risk_snapshot_record_hash: str,
        assessed_at: str | datetime,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        proposal = _pin(
            self.proposal_ledger.verify(),
            order_id,
            proposal_record_hash,
            id_field="order_id",
            label="paper proposal",
        )
        policy = _pin(
            self.policy_ledger.verify(),
            policy_id,
            policy_record_hash,
            id_field="policy_id",
            label="paper risk policy",
        )
        snapshot = _pin(
            self.risk_snapshot_ledger.verify(),
            risk_snapshot_id,
            risk_snapshot_record_hash,
            id_field="snapshot_id",
            label="paper risk snapshot",
        )
        return self._calculate_and_append(
            proposal,
            policy,
            snapshot,
            _timestamp(assessed_at),
            _timestamp(recorded_at or datetime.now(timezone.utc)),
            allow_existing=allow_existing,
        )

    def _build(
        self,
        proposal: Mapping[str, Any],
        policy: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        kill_prefix: Sequence[Mapping[str, Any]],
        assessed: datetime,
        recorded: datetime,
        *,
        enforce_current_recording: bool = False,
    ) -> dict[str, Any]:
        if proposal.get("status") != "PROPOSED" or proposal.get("execution_mode") != "PAPER_ONLY":
            raise ValueError("A verified PAPER_ONLY proposal is required")
        if (
            policy.get("status") != "PREREGISTERED_INACTIVE"
            or policy.get("broker") != "ALPACA"
            or policy.get("environment") != "PAPER"
        ):
            raise ValueError("An inactive Alpaca paper risk policy is required")
        if policy.get("account_reference_sha256") != snapshot.get("account_reference_sha256"):
            raise ValueError("Policy and risk snapshot account identities do not match")
        if (
            policy.get("portfolio_version") != proposal.get("portfolio_version")
            or policy.get("strategy_version") != proposal.get("strategy_version")
        ):
            raise ValueError("Proposal portfolio and strategy must match the policy")

        proposal_time = _timestamp(proposal.get("created_at"))
        snapshot_time = _timestamp(snapshot.get("observed_at"))
        effective_time = _timestamp(policy.get("effective_not_before"))
        now = datetime.now(timezone.utc)
        if recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if enforce_current_recording and recorded < now - MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must be within five minutes of the current clock")
        if assessed > recorded:
            raise ValueError("assessed_at cannot be after recorded_at")
        if assessed < max(proposal_time, snapshot_time, effective_time):
            raise ValueError("assessed_at predates required proposal, evidence, or policy time")

        order_quantity = _amount(proposal.get("quantity"), "proposal quantity", positive=True)
        reference_price = _amount(
            proposal.get("reference_price"), "proposal reference price", positive=True
        )
        order_notional = order_quantity * reference_price
        ticker = str(proposal.get("ticker") or "")
        side = str(proposal.get("side") or "")
        if side not in {"BUY", "SELL"}:
            raise ValueError("Proposal side is invalid")

        exact = snapshot.get("exact_fractions")
        positions = snapshot.get("positions")
        orders = snapshot.get("open_orders")
        if not isinstance(exact, Mapping) or not isinstance(positions, list) or not isinstance(orders, list):
            raise ValueError("Risk snapshot normalized evidence is invalid")
        position_fractions = exact.get("positions")
        order_fractions = exact.get("open_orders")
        if not isinstance(position_fractions, list) or len(position_fractions) != len(positions):
            raise ValueError("Risk snapshot position evidence is invalid")
        if not isinstance(order_fractions, list) or len(order_fractions) != len(orders):
            raise ValueError("Risk snapshot order evidence is invalid")

        current_ticker = Fraction(0)
        for raw, fraction in zip(positions, position_fractions):
            if raw.get("ticker") == ticker:
                current_ticker = _fraction(
                    fraction.get("long_market_value_usd"), "position exposure"
                )
        pending_ticker_buy = Fraction(0)
        for raw, fraction in zip(orders, order_fractions):
            if raw.get("ticker") == ticker and raw.get("side") == "BUY":
                pending_ticker_buy += _fraction(
                    fraction.get("remaining_notional_usd"), "pending order exposure"
                )

        current_gross = _fraction(
            exact.get("conservative_gross_exposure_usd"), "current gross exposure"
        )
        daily_loss = _fraction(exact.get("daily_loss_usd"), "daily loss")
        projected_ticker = current_ticker + pending_ticker_buy
        projected_gross = current_gross
        if side == "BUY":
            projected_ticker += order_notional
            projected_gross += order_notional

        limits = {
            "max_order_notional_usd": _amount(
                policy.get("max_order_notional_usd"), "max order limit", positive=True
            ),
            "max_position_notional_usd": _amount(
                policy.get("max_position_notional_usd"), "max position limit", positive=True
            ),
            "max_gross_exposure_usd": _amount(
                policy.get("max_gross_exposure_usd"), "max gross limit", positive=True
            ),
            "max_daily_loss_usd": _amount(
                policy.get("max_daily_loss_usd"), "max daily loss", positive=True
            ),
        }
        snapshot_age = _duration_seconds(recorded - snapshot_time)
        maximum_age = _amount(
            policy.get("max_account_snapshot_age_seconds"), "maximum snapshot age", positive=True
        )

        kill_count = len(kill_prefix)
        kill_head = kill_prefix[-1]["record_hash"] if kill_prefix else GENESIS_HASH
        matching_stops = [
            stop
            for stop in kill_prefix
            if (
                stop.get("account_reference_sha256")
                == policy.get("account_reference_sha256")
                or stop.get("kill_switch_identifier") == policy.get("kill_switch_identifier")
            )
        ]
        matching_stop = matching_stops[-1] if matching_stops else None
        checks = {
            "order_notional_within_limit": order_notional
            <= limits["max_order_notional_usd"],
            "position_notional_within_limit": projected_ticker
            <= limits["max_position_notional_usd"],
            "gross_exposure_within_limit": projected_gross
            <= limits["max_gross_exposure_usd"],
            "daily_loss_within_limit": daily_loss <= limits["max_daily_loss_usd"],
            "risk_snapshot_fresh": snapshot_age <= maximum_age,
            "sell_quantity_evidence_complete": side == "BUY",
            "kill_switch_latched": matching_stop is not None,
        }
        checks["mathematical_shadow_checks_pass"] = (
            checks["order_notional_within_limit"]
            and checks["position_notional_within_limit"]
            and checks["gross_exposure_within_limit"]
            and checks["daily_loss_within_limit"]
            and checks["risk_snapshot_fresh"]
            and checks["sell_quantity_evidence_complete"]
            and not checks["kill_switch_latched"]
        )
        if checks["kill_switch_latched"]:
            status = "SHADOW_BLOCKED_LATCHED_STOP"
        elif not checks["sell_quantity_evidence_complete"]:
            status = "SHADOW_INCOMPLETE_SELL_QUANTITY_EVIDENCE"
        elif checks["mathematical_shadow_checks_pass"]:
            status = "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"
        else:
            status = "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"

        money = {
            "order_notional_usd": order_notional,
            "current_ticker_long_exposure_usd": current_ticker,
            "pending_ticker_buy_exposure_usd": pending_ticker_buy,
            "projected_ticker_exposure_usd": projected_ticker,
            "current_conservative_gross_exposure_usd": current_gross,
            "projected_conservative_gross_exposure_usd": projected_gross,
            "daily_loss_usd": daily_loss,
            **limits,
            "risk_snapshot_age_seconds": snapshot_age,
        }
        assessed_text = assessed.isoformat()
        assessment_id = _assessment_id(
            proposal, policy, snapshot, kill_count, kill_head, assessed_text
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "assessment_id": assessment_id,
            "record_type": "PROVIDER_PAPER_SHADOW_RISK_ASSESSMENT",
            "status": status,
            "assessed_at": assessed_text,
            "recorded_at": recorded.isoformat(),
            "order_id": proposal["order_id"],
            "proposal_record_hash": proposal["record_hash"],
            "policy_id": policy["policy_id"],
            "policy_record_hash": policy["record_hash"],
            "risk_snapshot_id": snapshot["snapshot_id"],
            "risk_snapshot_record_hash": snapshot["record_hash"],
            "account_reference_sha256": policy["account_reference_sha256"],
            "portfolio_version": proposal["portfolio_version"],
            "strategy_version": proposal["strategy_version"],
            "ticker": ticker,
            "side": side,
            "kill_switch_record_count": kill_count,
            "kill_switch_head_hash": kill_head,
            "matching_stop_id": matching_stop["stop_id"] if matching_stop else None,
            "matching_stop_record_hash": matching_stop["record_hash"]
            if matching_stop
            else None,
            **{name: _decimal(value) for name, value in money.items()},
            "exact_fractions": {
                name: _fraction_material(value) for name, value in money.items()
            },
            **checks,
            **FIXED_FIELDS,
        }

    def verify(self) -> list[dict[str, Any]]:
        proposal_records = self.proposal_ledger.verify()
        policy_records = self.policy_ledger.verify()
        snapshot_records = self.risk_snapshot_ledger.verify()
        kill_records = self.kill_switch_ledger.verify()
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        previous_time: dict[tuple[str, str], datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Shadow-risk record {index} has been modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                proposal = _pin(
                    proposal_records,
                    record.get("order_id"),
                    record.get("proposal_record_hash"),
                    id_field="order_id",
                    label="paper proposal",
                )
                policy = _pin(
                    policy_records,
                    record.get("policy_id"),
                    record.get("policy_record_hash"),
                    id_field="policy_id",
                    label="paper risk policy",
                )
                snapshot = _pin(
                    snapshot_records,
                    record.get("risk_snapshot_id"),
                    record.get("risk_snapshot_record_hash"),
                    id_field="snapshot_id",
                    label="paper risk snapshot",
                )
                count = record.get("kill_switch_record_count")
                if isinstance(count, bool) or not isinstance(count, int):
                    raise ValueError("kill-switch record count is invalid")
                if count < 0 or count > len(kill_records):
                    raise ValueError("kill-switch pinned prefix is unavailable")
                expected = self._build(
                    proposal,
                    policy,
                    snapshot,
                    kill_records[:count],
                    _timestamp(record.get("assessed_at")),
                    _timestamp(record.get("recorded_at")),
                    enforce_current_recording=False,
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("record does not match its pinned calculation")
                key = (record["policy_id"], record["order_id"])
                assessed = _timestamp(record["assessed_at"])
                if key in previous_time and assessed <= previous_time[key]:
                    raise ValueError("assessment time does not move forward")
                if record["assessment_id"] in seen_ids:
                    raise ValueError("assessment identity is duplicated")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Shadow-risk record {index} violates its boundary."
                ) from error
            seen_ids.add(record["assessment_id"])
            previous_time[key] = assessed
            previous_hash = record["record_hash"]
        return records

    def _calculate_and_append(
        self,
        proposal: Mapping[str, Any],
        policy: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        assessed: datetime,
        recorded: datetime,
        *,
        allow_existing: bool,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        kill_lock_path = Path(self.kill_switch_ledger.path).with_suffix(
            Path(self.kill_switch_ledger.path).suffix + ".lock"
        )
        kill_descriptor = os.open(kill_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(kill_descriptor, fcntl.LOCK_EX)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            kill_records = self.kill_switch_ledger.verify()
            result = self._build(
                proposal,
                policy,
                snapshot,
                kill_records,
                assessed,
                recorded,
                enforce_current_recording=True,
            )
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["assessment_id"] == result["assessment_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError(
                    f"Shadow-risk assessment {result['assessment_id']} already exists."
                )
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["policy_id"] == result["policy_id"]
                    and item["order_id"] == result["order_id"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["assessed_at"]) <= _timestamp(
                prior["assessed_at"]
            ):
                raise ValueError(
                    "assessed_at must move forward for each policy and proposal"
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
            fcntl.flock(kill_descriptor, fcntl.LOCK_UN)
            os.close(kill_descriptor)
