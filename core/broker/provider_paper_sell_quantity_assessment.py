from __future__ import annotations

"""Quantity-bound SELL shadow assessment; no approval or order route."""

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
POLICY_VERSION = "provider-paper-sell-quantity-shadow-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
STATUSES = frozenset(
    {
        "SHADOW_BLOCKED_LATCHED_STOP",
        "SHADOW_SELL_QUANTITY_BREACH_INACTIVE_UNRECONCILED",
        "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED",
        "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED",
    }
)
QUANTITY_FIELDS = (
    "held_long_quantity",
    "pending_sell_quantity",
    "available_sell_quantity",
    "proposed_sell_quantity",
    "risk_snapshot_age_seconds",
    "max_account_snapshot_age_seconds",
)
CHECK_FIELDS = (
    "base_order_notional_within_limit",
    "base_position_notional_within_limit",
    "base_gross_exposure_within_limit",
    "base_daily_loss_within_limit",
    "base_risk_snapshot_fresh",
    "current_risk_snapshot_fresh",
    "sell_quantity_evidence_complete",
    "sell_quantity_within_available",
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
    "internal_quantity_arithmetic_only": True,
    "broker_quantity_sufficiency_proven": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "sell_quantity_assessment_id",
        "record_type",
        "status",
        "assessed_at",
        "recorded_at",
        "shadow_assessment_id",
        "shadow_assessment_record_hash",
        "order_id",
        "proposal_record_hash",
        "policy_id",
        "policy_record_hash",
        "risk_snapshot_id",
        "risk_snapshot_record_hash",
        "position_quantity_snapshot_id",
        "position_quantity_record_hash",
        "order_quantity_snapshot_id",
        "order_quantity_record_hash",
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
    | set(QUANTITY_FIELDS)
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
            raise OSError("Unable to complete SELL quantity assessment append")
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
        records, [identity], [record_hash], id_field=id_field, label=label
    )
    if reasons or len(resolved) != 1:
        raise ValueError(reasons[0] if reasons else f"{label} is not uniquely pinned")
    return resolved[0]


def _identity(
    shadow: Mapping[str, Any],
    position: Mapping[str, Any],
    orders: Mapping[str, Any],
    kill_count: int,
    kill_head: str,
    assessed_at: str,
) -> str:
    material = [
        shadow["assessment_id"],
        shadow["record_hash"],
        position["quantity_snapshot_id"],
        position["record_hash"],
        orders["order_quantity_snapshot_id"],
        orders["record_hash"],
        kill_count,
        kill_head,
        assessed_at,
        POLICY_VERSION,
    ]
    return "PPSELLQ-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperSellQuantityAssessmentLedger:
    """Append-only SELL sufficiency shadow pinned to both quantity ledgers."""

    def __init__(
        self,
        path: str | Path,
        shadow_assessment_ledger: Any,
        position_quantity_ledger: Any,
        open_order_quantity_ledger: Any,
        kill_switch_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.shadow_assessment_ledger = shadow_assessment_ledger
        self.position_quantity_ledger = position_quantity_ledger
        self.open_order_quantity_ledger = open_order_quantity_ledger
        self.kill_switch_ledger = kill_switch_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("SELL quantity assessment has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank SELL quantity line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid SELL quantity JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"SELL quantity line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def assess(
        self,
        *,
        shadow_assessment_id: str,
        shadow_assessment_record_hash: str,
        position_quantity_snapshot_id: str,
        position_quantity_record_hash: str,
        order_quantity_snapshot_id: str,
        order_quantity_record_hash: str,
        assessed_at: str | datetime,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        shadow = _pin(
            self.shadow_assessment_ledger.verify(),
            shadow_assessment_id,
            shadow_assessment_record_hash,
            id_field="assessment_id",
            label="shadow risk assessment",
        )
        position = _pin(
            self.position_quantity_ledger.verify(),
            position_quantity_snapshot_id,
            position_quantity_record_hash,
            id_field="quantity_snapshot_id",
            label="position quantity evidence",
        )
        orders = _pin(
            self.open_order_quantity_ledger.verify(),
            order_quantity_snapshot_id,
            order_quantity_record_hash,
            id_field="order_quantity_snapshot_id",
            label="open-order quantity evidence",
        )
        proposal = _pin(
            self.shadow_assessment_ledger.proposal_ledger.verify(),
            shadow["order_id"],
            shadow["proposal_record_hash"],
            id_field="order_id",
            label="paper proposal",
        )
        policy = _pin(
            self.shadow_assessment_ledger.policy_ledger.verify(),
            shadow["policy_id"],
            shadow["policy_record_hash"],
            id_field="policy_id",
            label="paper risk policy",
        )
        return self._calculate_and_append(
            shadow,
            proposal,
            policy,
            position,
            orders,
            _timestamp(assessed_at),
            _timestamp(recorded_at or datetime.now(timezone.utc)),
            allow_existing=allow_existing,
        )

    def _build(
        self,
        shadow: Mapping[str, Any],
        proposal: Mapping[str, Any],
        policy: Mapping[str, Any],
        position: Mapping[str, Any],
        orders: Mapping[str, Any],
        kill_prefix: Sequence[Mapping[str, Any]],
        assessed: datetime,
        recorded: datetime,
        *,
        enforce_current_recording: bool = False,
    ) -> dict[str, Any]:
        if shadow.get("side") != "SELL" or proposal.get("side") != "SELL":
            raise ValueError("A verified SELL proposal and shadow assessment are required")
        if (
            shadow.get("order_id") != proposal.get("order_id")
            or shadow.get("proposal_record_hash") != proposal.get("record_hash")
        ):
            raise ValueError("Shadow and proposal identities do not match")
        if (
            shadow.get("policy_id") != policy.get("policy_id")
            or shadow.get("policy_record_hash") != policy.get("record_hash")
            or shadow.get("account_reference_sha256")
            != policy.get("account_reference_sha256")
        ):
            raise ValueError("Shadow and risk policy identities do not match")
        risk_id = shadow.get("risk_snapshot_id")
        risk_hash = shadow.get("risk_snapshot_record_hash")
        risk = self._risk_snapshot(risk_id, risk_hash)
        for evidence in (position, orders):
            if (
                evidence.get("risk_snapshot_id") != risk_id
                or evidence.get("risk_snapshot_record_hash") != risk_hash
                or evidence.get("account_reference_sha256")
                != shadow.get("account_reference_sha256")
                or evidence.get("observed_at")
                != risk.get("observed_at")
            ):
                raise ValueError("Quantity evidence does not share the exact shadow risk snapshot")

        now = datetime.now(timezone.utc)
        if recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if enforce_current_recording and recorded < now - MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must be within five minutes of the current clock")
        minimum_time = max(
            _timestamp(shadow.get("assessed_at")),
            _timestamp(position.get("recorded_at")),
            _timestamp(orders.get("recorded_at")),
        )
        if assessed < minimum_time or assessed > recorded:
            raise ValueError("assessed_at must follow all pinned evidence and not exceed recorded_at")

        ticker = str(proposal.get("ticker") or "")
        held = Fraction(0)
        position_exact = position.get("exact_fractions")
        raw_positions = position.get("positions")
        exact_positions = position_exact.get("positions") if isinstance(position_exact, Mapping) else None
        if (
            not isinstance(raw_positions, list)
            or not isinstance(exact_positions, list)
            or len(raw_positions) != len(exact_positions)
        ):
            raise ValueError("Position quantity evidence is invalid")
        exact_by_ticker: dict[str, Mapping[str, Any]] = {}
        for exact in exact_positions:
            if not isinstance(exact, Mapping):
                raise ValueError("Exact position quantity entry is invalid")
            exact_ticker = str(exact.get("ticker") or "")
            if not exact_ticker or exact_ticker in exact_by_ticker:
                raise ValueError("Exact position quantity tickers must be unique")
            exact_by_ticker[exact_ticker] = exact
        raw_tickers: set[str] = set()
        for raw in raw_positions:
            if not isinstance(raw, Mapping):
                raise ValueError("Position quantity entry is invalid")
            raw_ticker = str(raw.get("ticker") or "")
            if not raw_ticker or raw_ticker in raw_tickers or raw_ticker not in exact_by_ticker:
                raise ValueError("Position quantity ticker alignment is invalid")
            raw_tickers.add(raw_ticker)
            if raw_ticker == ticker:
                held += _fraction(
                    exact_by_ticker[raw_ticker].get("long_quantity"),
                    "held long quantity",
                )
        if raw_tickers != set(exact_by_ticker):
            raise ValueError("Position quantity lists do not exactly align")

        pending_sell = Fraction(0)
        order_exact = orders.get("exact_fractions")
        raw_orders = orders.get("open_orders")
        exact_orders = order_exact.get("open_orders") if isinstance(order_exact, Mapping) else None
        if (
            not isinstance(raw_orders, list)
            or not isinstance(exact_orders, list)
            or len(raw_orders) != len(exact_orders)
        ):
            raise ValueError("Open-order quantity evidence is invalid")
        exact_by_order: dict[str, Mapping[str, Any]] = {}
        for exact in exact_orders:
            if not isinstance(exact, Mapping):
                raise ValueError("Exact open-order quantity entry is invalid")
            order_hash = str(exact.get("order_reference_sha256") or "")
            if not order_hash or order_hash in exact_by_order:
                raise ValueError("Exact open-order references must be unique")
            exact_by_order[order_hash] = exact
        raw_order_hashes: set[str] = set()
        for raw in raw_orders:
            if not isinstance(raw, Mapping):
                raise ValueError("Open-order quantity entry is invalid")
            order_hash = str(raw.get("order_reference_sha256") or "")
            if (
                not order_hash
                or order_hash in raw_order_hashes
                or order_hash not in exact_by_order
            ):
                raise ValueError("Open-order quantity reference alignment is invalid")
            raw_order_hashes.add(order_hash)
            if raw.get("ticker") == ticker and raw.get("side") == "SELL":
                pending_sell += _fraction(
                    exact_by_order[order_hash].get("remaining_quantity"),
                    "pending SELL quantity",
                )
        if raw_order_hashes != set(exact_by_order):
            raise ValueError("Open-order quantity lists do not exactly align")

        proposed = _amount(proposal.get("quantity"), "proposed SELL quantity", positive=True)
        available = held - pending_sell
        quantity_within = available >= 0 and proposed <= available
        base_checks = {
            "base_order_notional_within_limit": shadow.get("order_notional_within_limit") is True,
            "base_position_notional_within_limit": shadow.get("position_notional_within_limit") is True,
            "base_gross_exposure_within_limit": shadow.get("gross_exposure_within_limit") is True,
            "base_daily_loss_within_limit": shadow.get("daily_loss_within_limit") is True,
            "base_risk_snapshot_fresh": shadow.get("risk_snapshot_fresh") is True,
        }

        risk_time = _timestamp(risk.get("observed_at"))
        risk_age = _duration_seconds(recorded - risk_time)
        maximum_age = _amount(
            policy.get("max_account_snapshot_age_seconds"),
            "maximum snapshot age",
            positive=True,
        )
        if risk_age < 0:
            raise ValueError("Risk snapshot cannot be observed after recording")
        current_fresh = risk_age <= maximum_age

        kill_count = len(kill_prefix)
        kill_head = kill_prefix[-1]["record_hash"] if kill_prefix else GENESIS_HASH
        if any(_timestamp(stop.get("triggered_at")) > assessed for stop in kill_prefix):
            raise ValueError("assessed_at must follow every already-recorded permanent stop")
        matching_stops = [
            stop
            for stop in kill_prefix
            if stop.get("account_reference_sha256")
            == shadow.get("account_reference_sha256")
            or stop.get("kill_switch_identifier")
            == policy.get("kill_switch_identifier")
        ]
        matching_stop = matching_stops[-1] if matching_stops else None
        base_latched = (
            shadow.get("kill_switch_latched") is True
            or shadow.get("status") == "SHADOW_BLOCKED_LATCHED_STOP"
        )
        checks = {
            **base_checks,
            "current_risk_snapshot_fresh": current_fresh,
            "sell_quantity_evidence_complete": True,
            "sell_quantity_within_available": quantity_within,
            "kill_switch_latched": base_latched or matching_stop is not None,
        }
        checks["mathematical_shadow_checks_pass"] = (
            all(base_checks.values())
            and current_fresh
            and quantity_within
            and not checks["kill_switch_latched"]
        )
        if checks["kill_switch_latched"]:
            status = "SHADOW_BLOCKED_LATCHED_STOP"
        elif not quantity_within:
            status = "SHADOW_SELL_QUANTITY_BREACH_INACTIVE_UNRECONCILED"
        elif not current_fresh or not all(base_checks.values()):
            status = "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"
        else:
            status = "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"

        quantities = {
            "held_long_quantity": held,
            "pending_sell_quantity": pending_sell,
            "available_sell_quantity": available,
            "proposed_sell_quantity": proposed,
            "risk_snapshot_age_seconds": risk_age,
            "max_account_snapshot_age_seconds": maximum_age,
        }
        assessed_text = assessed.isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "sell_quantity_assessment_id": _identity(
                shadow, position, orders, kill_count, kill_head, assessed_text
            ),
            "record_type": "PROVIDER_PAPER_SELL_QUANTITY_SHADOW_ASSESSMENT",
            "status": status,
            "assessed_at": assessed_text,
            "recorded_at": recorded.isoformat(),
            "shadow_assessment_id": shadow["assessment_id"],
            "shadow_assessment_record_hash": shadow["record_hash"],
            "order_id": proposal["order_id"],
            "proposal_record_hash": proposal["record_hash"],
            "policy_id": policy["policy_id"],
            "policy_record_hash": policy["record_hash"],
            "risk_snapshot_id": risk_id,
            "risk_snapshot_record_hash": risk_hash,
            "position_quantity_snapshot_id": position["quantity_snapshot_id"],
            "position_quantity_record_hash": position["record_hash"],
            "order_quantity_snapshot_id": orders["order_quantity_snapshot_id"],
            "order_quantity_record_hash": orders["record_hash"],
            "account_reference_sha256": shadow["account_reference_sha256"],
            "portfolio_version": shadow["portfolio_version"],
            "strategy_version": shadow["strategy_version"],
            "ticker": ticker,
            "side": "SELL",
            "kill_switch_record_count": kill_count,
            "kill_switch_head_hash": kill_head,
            "matching_stop_id": (
                matching_stop["stop_id"]
                if matching_stop
                else shadow.get("matching_stop_id")
            ),
            "matching_stop_record_hash": (
                matching_stop["record_hash"]
                if matching_stop
                else shadow.get("matching_stop_record_hash")
            ),
            **{name: _decimal(value) for name, value in quantities.items()},
            "exact_fractions": {
                name: _fraction_material(value) for name, value in quantities.items()
            },
            **checks,
            **FIXED_FIELDS,
        }

    def _risk_snapshot(self, identity: Any, record_hash: Any) -> Mapping[str, Any]:
        position_risk = _pin(
            self.position_quantity_ledger.risk_snapshot_ledger.verify(),
            identity,
            record_hash,
            id_field="snapshot_id",
            label="paper risk snapshot",
        )
        order_risk = _pin(
            self.open_order_quantity_ledger.risk_snapshot_ledger.verify(),
            identity,
            record_hash,
            id_field="snapshot_id",
            label="open-order paper risk snapshot",
        )
        shadow_risk = _pin(
            self.shadow_assessment_ledger.risk_snapshot_ledger.verify(),
            identity,
            record_hash,
            id_field="snapshot_id",
            label="shadow paper risk snapshot",
        )
        if position_risk != order_risk or position_risk != shadow_risk:
            raise ValueError("Quantity and shadow ledgers do not share one risk record")
        return position_risk

    def verify(self) -> list[dict[str, Any]]:
        shadows = self.shadow_assessment_ledger.verify()
        positions = self.position_quantity_ledger.verify()
        orders = self.open_order_quantity_ledger.verify()
        proposals = self.shadow_assessment_ledger.proposal_ledger.verify()
        policies = self.shadow_assessment_ledger.policy_ledger.verify()
        kills = self.kill_switch_ledger.verify()
        previous_hash = GENESIS_HASH
        previous_time: dict[tuple[str, str], datetime] = {}
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"SELL quantity record {index} has been modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                shadow = _pin(
                    shadows,
                    record.get("shadow_assessment_id"),
                    record.get("shadow_assessment_record_hash"),
                    id_field="assessment_id",
                    label="shadow risk assessment",
                )
                position = _pin(
                    positions,
                    record.get("position_quantity_snapshot_id"),
                    record.get("position_quantity_record_hash"),
                    id_field="quantity_snapshot_id",
                    label="position quantity evidence",
                )
                order = _pin(
                    orders,
                    record.get("order_quantity_snapshot_id"),
                    record.get("order_quantity_record_hash"),
                    id_field="order_quantity_snapshot_id",
                    label="open-order quantity evidence",
                )
                proposal = _pin(
                    proposals,
                    record.get("order_id"),
                    record.get("proposal_record_hash"),
                    id_field="order_id",
                    label="paper proposal",
                )
                policy = _pin(
                    policies,
                    record.get("policy_id"),
                    record.get("policy_record_hash"),
                    id_field="policy_id",
                    label="paper risk policy",
                )
                count = record.get("kill_switch_record_count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > len(kills):
                    raise ValueError("kill-switch record count is invalid")
                assessed = _timestamp(record.get("assessed_at"))
                if any(
                    _timestamp(stop.get("triggered_at")) > assessed
                    for stop in kills[:count]
                ) or any(
                    _timestamp(stop.get("triggered_at")) <= assessed
                    for stop in kills[count:]
                ):
                    raise ValueError("kill-switch prefix is not causally complete")
                expected = self._build(
                    shadow,
                    proposal,
                    policy,
                    position,
                    order,
                    kills[:count],
                    assessed,
                    _timestamp(record.get("recorded_at")),
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("record does not match its pinned SELL quantity calculation")
                key = (record["account_reference_sha256"], record["order_id"])
                if key in previous_time and assessed <= previous_time[key]:
                    raise ValueError("assessment time does not move forward")
                identity = record["sell_quantity_assessment_id"]
                if identity in seen:
                    raise ValueError("SELL quantity identity is duplicated")
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"SELL quantity record {index} violates its boundary."
                ) from error
            previous_time[key] = assessed
            seen.add(identity)
            previous_hash = record["record_hash"]
        return records

    def _calculate_and_append(
        self,
        shadow: Mapping[str, Any],
        proposal: Mapping[str, Any],
        policy: Mapping[str, Any],
        position: Mapping[str, Any],
        orders: Mapping[str, Any],
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
            kills = self.kill_switch_ledger.verify()
            result = self._build(
                shadow,
                proposal,
                policy,
                position,
                orders,
                kills,
                assessed,
                recorded,
                enforce_current_recording=True,
            )
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["sell_quantity_assessment_id"]
                    == result["sell_quantity_assessment_id"]
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
                    f"SELL quantity assessment {result['sell_quantity_assessment_id']} already exists."
                )
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                    and item["order_id"] == result["order_id"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["assessed_at"]) <= _timestamp(
                prior["assessed_at"]
            ):
                raise ValueError("assessed_at must move forward for each account and proposal")
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
