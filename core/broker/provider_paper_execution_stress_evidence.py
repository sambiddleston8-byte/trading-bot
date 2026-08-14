from __future__ import annotations

"""Exact pessimistic paper execution-price/cost evidence; no order route."""

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
POLICY_VERSION = "provider-paper-pessimistic-execution-stress-evidence-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
BPS = Fraction(10_000)
MEASURE_FIELDS = (
    "proposed_quantity",
    "reference_price_usd",
    "reference_notional_usd",
    "pessimistic_adverse_price_bps",
    "configured_commission_bps",
    "stressed_execution_price_usd",
    "stressed_gross_notional_usd",
    "configured_commission_fee_usd",
    "estimated_cash_debit_usd",
    "estimated_cash_credit_usd",
    "conservative_risk_notional_usd",
)
FIXED_FIELDS = {
    "shadow_calculation_only": True,
    "pessimistic_scenario_used": True,
    "execution_price_stress_applied": True,
    "fees_included": True,
    "configured_commission_fee_included": True,
    "reference_price_only": True,
    "policy_active": False,
    "reference_price_source_authenticated": False,
    "broker_reconciliation_complete": False,
    "volume_evidence_present": False,
    "market_impact_model_calibrated": False,
    "all_applicable_fees_complete": False,
    "regulatory_fees_complete": False,
    "borrow_costs_complete": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "position_quantity_assessed": False,
    "cash_sufficiency_assessed": False,
    "fill_price_certainty_resolved": False,
    "broker_access_enabled": False,
    "broker_credentials_accessed": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "live_order_submission_allowed": False,
    "human_review_eligible": False,
    "recommendation_provided": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
    "live_trading_enabled": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "execution_stress_evidence_id",
        "record_type",
        "status",
        "scenario",
        "assessed_at",
        "recorded_at",
        "order_id",
        "proposal_record_hash",
        "execution_stress_policy_id",
        "execution_stress_policy_record_hash",
        "account_reference_sha256",
        "portfolio_version",
        "strategy_version",
        "ticker",
        "side",
        "exact_fractions",
        "previous_hash",
        "record_hash",
    }
    | set(MEASURE_FIELDS)
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
            raise OSError("Unable to complete execution-stress evidence append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _amount(value: Any, name: str, *, positive: bool = False) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an exact finite decimal")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (positive and resolved == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} decimal")
    return Fraction(resolved)


def _fraction(material: Any, name: str) -> Fraction:
    if not isinstance(material, Mapping) or set(material) != {"numerator", "denominator"}:
        raise ValueError(f"{name} exact fraction has an invalid field alphabet")
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} denominator must be positive")
    return value


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal(value: Fraction) -> str:
    resolved = Decimal(value.numerator) / Decimal(value.denominator)
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _pin(
    records: Sequence[Mapping[str, Any]],
    identity: Any,
    record_hash: Any,
    *,
    id_field: str,
    label: str,
) -> Mapping[str, Any]:
    resolved, reasons = resolve_pinned_records(
        records, [identity], [record_hash], id_field=id_field, label=label
    )
    if reasons or len(resolved) != 1:
        raise ValueError(reasons[0] if reasons else f"{label} is not uniquely pinned")
    return resolved[0]


def _identity(
    proposal: Mapping[str, Any],
    policy: Mapping[str, Any],
    assessed_at: str,
) -> str:
    material = [
        proposal["order_id"],
        proposal["record_hash"],
        policy["execution_stress_policy_id"],
        policy["record_hash"],
        "PESSIMISTIC",
        assessed_at,
        POLICY_VERSION,
    ]
    return "PPEVID-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperExecutionStressEvidenceLedger:
    """Append-only pessimistic price and configured-fee arithmetic."""

    def __init__(self, path: str | Path, proposal_ledger: Any, policy_ledger: Any) -> None:
        self.path = Path(path)
        self.proposal_ledger = proposal_ledger
        self.policy_ledger = policy_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Execution-stress evidence has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank execution-stress evidence at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid execution-stress evidence JSON at {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Execution-stress evidence {line_number} is not an object."
                    )
                records.append(record)
        return records

    def calculate(
        self,
        *,
        order_id: str,
        proposal_record_hash: str,
        execution_stress_policy_id: str,
        execution_stress_policy_record_hash: str,
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
            execution_stress_policy_id,
            execution_stress_policy_record_hash,
            id_field="execution_stress_policy_id",
            label="execution-stress policy",
        )
        result = self._build(
            proposal,
            policy,
            _timestamp(assessed_at),
            _timestamp(recorded_at or datetime.now(timezone.utc)),
            enforce_current_recording=True,
        )
        return self._append(result, allow_existing=allow_existing)

    def _build(
        self,
        proposal: Mapping[str, Any],
        policy: Mapping[str, Any],
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
            raise ValueError("An inactive Alpaca paper execution-stress policy is required")
        if (
            proposal.get("portfolio_version") != policy.get("portfolio_version")
            or proposal.get("strategy_version") != policy.get("strategy_version")
        ):
            raise ValueError("Proposal portfolio and strategy must match the stress policy")
        proposal_time = _timestamp(proposal.get("created_at"))
        effective = _timestamp(policy.get("effective_not_before"))
        now = datetime.now(timezone.utc)
        if recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if enforce_current_recording and recorded < now - MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must be within five minutes of the current clock")
        if proposal_time < effective:
            raise ValueError("Proposal cannot predate the effective execution-stress policy")
        if assessed < proposal_time or assessed > recorded:
            raise ValueError("assessed_at must follow the proposal and not exceed recorded_at")

        scenarios = policy.get("scenarios")
        if not isinstance(scenarios, Mapping) or set(scenarios) != {"BASE", "PESSIMISTIC"}:
            raise ValueError("Stress policy scenarios are invalid")
        pessimistic = scenarios.get("PESSIMISTIC")
        if not isinstance(pessimistic, Mapping):
            raise ValueError("PESSIMISTIC scenario is invalid")
        adverse_bps = sum(
            (
                _amount(pessimistic.get(field), field, positive=True)
                for field in (
                    "spread_bps_per_side",
                    "slippage_bps_per_side",
                    "latency_bps_per_side",
                    "market_impact_bps_per_side",
                )
            ),
            Fraction(0),
        )
        commission_bps = _amount(
            pessimistic.get("commission_bps_per_side"), "commission bps"
        )
        if adverse_bps >= BPS or commission_bps >= BPS:
            raise ValueError("PESSIMISTIC costs must remain below 100%")
        quantity = _amount(proposal.get("quantity"), "proposal quantity", positive=True)
        reference_price = _amount(
            proposal.get("reference_price"), "proposal reference price", positive=True
        )
        side = str(proposal.get("side") or "")
        if side not in {"BUY", "SELL"}:
            raise ValueError("Proposal side is invalid")
        reference_notional = quantity * reference_price
        direction = Fraction(1) if side == "BUY" else Fraction(-1)
        stressed_price = reference_price * (Fraction(1) + direction * adverse_bps / BPS)
        if stressed_price <= 0:
            raise ValueError("Stressed execution price must remain positive")
        stressed_gross = quantity * stressed_price
        commission_fee = stressed_gross * commission_bps / BPS
        cash_debit = stressed_gross + commission_fee if side == "BUY" else Fraction(0)
        cash_credit = stressed_gross - commission_fee if side == "SELL" else Fraction(0)
        if cash_credit < 0:
            raise ValueError("Configured commission cannot exceed stressed SELL proceeds")
        conservative_risk = max(reference_notional, stressed_gross) + commission_fee
        measures = {
            "proposed_quantity": quantity,
            "reference_price_usd": reference_price,
            "reference_notional_usd": reference_notional,
            "pessimistic_adverse_price_bps": adverse_bps,
            "configured_commission_bps": commission_bps,
            "stressed_execution_price_usd": stressed_price,
            "stressed_gross_notional_usd": stressed_gross,
            "configured_commission_fee_usd": commission_fee,
            "estimated_cash_debit_usd": cash_debit,
            "estimated_cash_credit_usd": cash_credit,
            "conservative_risk_notional_usd": conservative_risk,
        }
        assessed_text = assessed.isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "execution_stress_evidence_id": _identity(proposal, policy, assessed_text),
            "record_type": "PROVIDER_PAPER_PESSIMISTIC_EXECUTION_STRESS_EVIDENCE",
            "status": "PESSIMISTIC_STRESS_CALCULATED_INACTIVE_UNRECONCILED",
            "scenario": "PESSIMISTIC",
            "assessed_at": assessed_text,
            "recorded_at": recorded.isoformat(),
            "order_id": proposal["order_id"],
            "proposal_record_hash": proposal["record_hash"],
            "execution_stress_policy_id": policy["execution_stress_policy_id"],
            "execution_stress_policy_record_hash": policy["record_hash"],
            "account_reference_sha256": policy["account_reference_sha256"],
            "portfolio_version": proposal["portfolio_version"],
            "strategy_version": proposal["strategy_version"],
            "ticker": proposal["ticker"],
            "side": side,
            **{name: _decimal(value) for name, value in measures.items()},
            "exact_fractions": {
                name: _fraction_material(value) for name, value in measures.items()
            },
            **FIXED_FIELDS,
        }

    def verify(self) -> list[dict[str, Any]]:
        proposals = self.proposal_ledger.verify()
        policies = self.policy_ledger.verify()
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        previous_time: dict[tuple[str, str], datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Execution-stress evidence {index} was modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                proposal = _pin(
                    proposals,
                    record.get("order_id"),
                    record.get("proposal_record_hash"),
                    id_field="order_id",
                    label="paper proposal",
                )
                policy = _pin(
                    policies,
                    record.get("execution_stress_policy_id"),
                    record.get("execution_stress_policy_record_hash"),
                    id_field="execution_stress_policy_id",
                    label="execution-stress policy",
                )
                expected = self._build(
                    proposal,
                    policy,
                    _timestamp(record.get("assessed_at")),
                    _timestamp(record.get("recorded_at")),
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("record does not match exact stress calculation")
                key = (record["execution_stress_policy_id"], record["order_id"])
                assessed = _timestamp(record["assessed_at"])
                if key in previous_time and assessed <= previous_time[key]:
                    raise ValueError("assessment time does not move forward")
                identity = record["execution_stress_evidence_id"]
                if identity in seen:
                    raise ValueError("execution-stress evidence identity is duplicated")
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Execution-stress evidence {index} violates its boundary."
                ) from error
            previous_time[key] = assessed
            seen.add(identity)
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
                    if item["execution_stress_evidence_id"]
                    == result["execution_stress_evidence_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Execution-stress evidence already exists.")
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["execution_stress_policy_id"]
                    == result["execution_stress_policy_id"]
                    and item["order_id"] == result["order_id"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["assessed_at"]) <= _timestamp(
                prior["assessed_at"]
            ):
                raise ValueError("assessed_at must move forward for each policy and proposal")
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
