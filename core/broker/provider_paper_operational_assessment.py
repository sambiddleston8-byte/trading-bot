from __future__ import annotations

"""Combined internal paper checks that can only block, never submit."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.broker.provider_paper_kill_switch import validate_kill_switch_prefix
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-operational-assessment-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
IDEMPOTENT_RETRY_WINDOW = timedelta(seconds=2)
BPS = Fraction(10_000)
MEASURE_FIELDS = (
    "stressed_conservative_order_notional_usd",
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
    "account_snapshot_age_seconds",
    "shadow_assessment_age_seconds",
    "execution_stress_evidence_age_seconds",
    "proposal_reference_age_seconds",
    "max_risk_snapshot_age_seconds",
    "max_account_snapshot_age_seconds",
    "max_shadow_assessment_age_seconds",
    "max_execution_stress_evidence_age_seconds",
    "max_proposal_reference_age_seconds",
    "settled_cash_usd",
    "pending_account_buy_notional_usd",
    "pending_account_buy_stressed_cash_commitment_usd",
    "required_buy_cash_debit_usd",
    "total_buy_cash_commitment_usd",
    "available_sell_quantity",
    "proposed_sell_quantity",
)
CHECK_FIELDS = (
    "stressed_order_notional_within_limit",
    "projected_position_notional_within_limit",
    "projected_gross_exposure_within_limit",
    "daily_loss_within_limit",
    "risk_snapshot_fresh",
    "account_snapshot_fresh",
    "shadow_assessment_fresh",
    "execution_stress_evidence_fresh",
    "proposal_reference_fresh",
    "sell_quantity_check_applicable",
    "sell_quantity_within_available",
    "buy_cash_check_applicable",
    "buy_settled_cash_sufficient",
    "kill_switch_latched",
    "internal_mathematical_checks_pass",
    "operational_paper_readiness",
)
EXTERNAL_BLOCKERS = [
    "BROKER_RECONCILIATION_REQUIRED",
    "AUTHENTICATED_REFERENCE_PRICE_REQUIRED",
    "AUTHENTICATED_POSITION_ORDER_AND_CASH_EVIDENCE_REQUIRED",
    "VOLUME_AND_MARKET_IMPACT_EVIDENCE_REQUIRED",
    "COMPLETE_APPLICABLE_FEES_AND_BORROW_COSTS_REQUIRED",
    "CRYPTOGRAPHIC_AUTHENTICATION_REQUIRED",
    "EXTERNAL_STOP_HEAD_ANCHOR_REQUIRED",
    "SEPARATE_HUMAN_ACTIVATION_DECISION_REQUIRED",
]
FIXED_FIELDS = {
    "shadow_calculation_only": True,
    "pessimistic_execution_stress_applied": True,
    "configured_commission_fee_included": True,
    "policy_active": False,
    "paper_account_connected": False,
    "broker_reconciliation_complete": False,
    "reference_price_source_authenticated": False,
    "position_order_cash_evidence_authenticated": False,
    "volume_evidence_present": False,
    "market_impact_model_calibrated": False,
    "all_applicable_fees_complete": False,
    "regulatory_fees_complete": False,
    "borrow_costs_complete": False,
    "risk_limits_enforced": False,
    "fill_price_certainty_resolved": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
    "broker_access_enabled": False,
    "broker_credentials_accessed": False,
    "network_request_enabled": False,
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
        "operational_assessment_id",
        "record_type",
        "status",
        "assessed_at",
        "recorded_at",
        "shadow_assessment_id",
        "shadow_assessment_record_hash",
        "sell_quantity_assessment_id",
        "sell_quantity_assessment_record_hash",
        "execution_stress_evidence_id",
        "execution_stress_evidence_record_hash",
        "order_id",
        "proposal_record_hash",
        "risk_policy_id",
        "risk_policy_record_hash",
        "risk_snapshot_id",
        "risk_snapshot_record_hash",
        "account_snapshot_id",
        "account_snapshot_record_hash",
        "account_reference_sha256",
        "portfolio_version",
        "strategy_version",
        "ticker",
        "side",
        "kill_switch_record_count",
        "kill_switch_head_hash",
        "matching_stop_id",
        "matching_stop_record_hash",
        "open_internal_checks",
        "external_blockers",
        "next_authority",
        "exact_fractions",
        "previous_hash",
        "record_hash",
    }
    | set(MEASURE_FIELDS)
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
            raise OSError("Unable to complete paper operational assessment append")
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


def _fraction(
    material: Any,
    name: str,
    *,
    non_negative: bool = False,
    positive: bool = False,
) -> Fraction:
    if not isinstance(material, Mapping) or set(material) != {"numerator", "denominator"}:
        raise ValueError(f"{name} exact fraction has an invalid field alphabet")
    try:
        raw_numerator = material["numerator"]
        raw_denominator = material["denominator"]
        if (
            isinstance(raw_numerator, (bool, float))
            or isinstance(raw_denominator, (bool, float))
            or not isinstance(raw_numerator, (int, str))
            or not isinstance(raw_denominator, (int, str))
        ):
            raise ValueError
        numerator_text = str(raw_numerator)
        denominator_text = str(raw_denominator)
        if (
            not numerator_text
            or not denominator_text
            or numerator_text != numerator_text.strip()
            or denominator_text != denominator_text.strip()
            or not numerator_text.removeprefix("-").isdigit()
            or not denominator_text.isdigit()
        ):
            raise ValueError
        denominator = int(denominator_text)
        value = Fraction(int(numerator_text), denominator)
    except (TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} denominator must be positive")
    if (non_negative or positive) and value < 0:
        raise ValueError(f"{name} must be non-negative")
    if positive and value == 0:
        raise ValueError(f"{name} must be positive")
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
    sell: Mapping[str, Any] | None,
    stress: Mapping[str, Any],
    kill_count: int,
    kill_head: str,
    assessed_at: str,
) -> str:
    material = [
        shadow["assessment_id"],
        shadow["record_hash"],
        sell["sell_quantity_assessment_id"] if sell else None,
        sell["record_hash"] if sell else None,
        stress["execution_stress_evidence_id"],
        stress["record_hash"],
        kill_count,
        kill_head,
        assessed_at,
        POLICY_VERSION,
    ]
    return "PPOPER-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperOperationalAssessmentLedger:
    """Combine internal risk, quantity, cash and cost evidence; always non-operational."""

    def __init__(
        self,
        path: str | Path,
        shadow_assessment_ledger: Any,
        sell_quantity_ledger: Any,
        execution_stress_evidence_ledger: Any,
        kill_switch_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.shadow_assessment_ledger = shadow_assessment_ledger
        self.sell_quantity_ledger = sell_quantity_ledger
        self.execution_stress_evidence_ledger = execution_stress_evidence_ledger
        self.kill_switch_ledger = kill_switch_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Paper operational assessment has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank operational assessment at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid operational assessment JSON at {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Operational assessment {line_number} is not an object."
                    )
                records.append(record)
        return records

    def assess(
        self,
        *,
        shadow_assessment_id: str,
        shadow_assessment_record_hash: str,
        execution_stress_evidence_id: str,
        execution_stress_evidence_record_hash: str,
        sell_quantity_assessment_id: str | None = None,
        sell_quantity_assessment_record_hash: str | None = None,
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
        stress = _pin(
            self.execution_stress_evidence_ledger.verify(),
            execution_stress_evidence_id,
            execution_stress_evidence_record_hash,
            id_field="execution_stress_evidence_id",
            label="execution stress evidence",
        )
        sell: Mapping[str, Any] | None = None
        if shadow.get("side") == "SELL":
            sell = _pin(
                self.sell_quantity_ledger.verify(),
                sell_quantity_assessment_id,
                sell_quantity_assessment_record_hash,
                id_field="sell_quantity_assessment_id",
                label="SELL quantity assessment",
            )
        elif sell_quantity_assessment_id is not None or sell_quantity_assessment_record_hash is not None:
            raise ValueError("BUY operational assessment cannot include SELL quantity evidence")
        return self._calculate_and_append(
            shadow,
            sell,
            stress,
            _timestamp(assessed_at),
            _timestamp(recorded_at or datetime.now(timezone.utc)),
            allow_existing=allow_existing,
        )

    def _build(
        self,
        shadow: Mapping[str, Any],
        sell: Mapping[str, Any] | None,
        stress: Mapping[str, Any],
        kill_prefix: Sequence[Mapping[str, Any]],
        assessed: datetime,
        recorded: datetime,
        *,
        enforce_current_recording: bool = False,
    ) -> dict[str, Any]:
        proposal = _pin(
            self.shadow_assessment_ledger.proposal_ledger.verify(),
            shadow.get("order_id"),
            shadow.get("proposal_record_hash"),
            id_field="order_id",
            label="paper proposal",
        )
        policy = _pin(
            self.shadow_assessment_ledger.policy_ledger.verify(),
            shadow.get("policy_id"),
            shadow.get("policy_record_hash"),
            id_field="policy_id",
            label="paper risk policy",
        )
        stress_policy = _pin(
            self.execution_stress_evidence_ledger.policy_ledger.verify(),
            stress.get("execution_stress_policy_id"),
            stress.get("execution_stress_policy_record_hash"),
            id_field="execution_stress_policy_id",
            label="execution stress policy",
        )
        risk = _pin(
            self.shadow_assessment_ledger.risk_snapshot_ledger.verify(),
            shadow.get("risk_snapshot_id"),
            shadow.get("risk_snapshot_record_hash"),
            id_field="snapshot_id",
            label="paper risk snapshot",
        )
        account = _pin(
            self.shadow_assessment_ledger.risk_snapshot_ledger.account_snapshot_ledger.verify(),
            risk.get("account_snapshot_id"),
            risk.get("account_snapshot_record_hash"),
            id_field="snapshot_id",
            label="paper account snapshot",
        )
        if (
            stress.get("order_id") != proposal.get("order_id")
            or stress.get("proposal_record_hash") != proposal.get("record_hash")
            or stress.get("account_reference_sha256") != policy.get("account_reference_sha256")
            or stress.get("portfolio_version") != proposal.get("portfolio_version")
            or stress.get("strategy_version") != proposal.get("strategy_version")
            or stress.get("ticker") != proposal.get("ticker")
            or stress.get("side") != proposal.get("side")
            or stress_policy.get("account_reference_sha256")
            != policy.get("account_reference_sha256")
            or stress_policy.get("portfolio_version") != proposal.get("portfolio_version")
            or stress_policy.get("strategy_version") != proposal.get("strategy_version")
        ):
            raise ValueError("Stress evidence does not match the exact proposal and risk policy")
        if (
            risk.get("account_reference_sha256") != policy.get("account_reference_sha256")
            or account.get("account_reference_sha256") != policy.get("account_reference_sha256")
            or shadow.get("portfolio_version") != proposal.get("portfolio_version")
            or shadow.get("strategy_version") != proposal.get("strategy_version")
        ):
            raise ValueError("Risk, account, proposal and policy identities do not match")
        side = str(proposal.get("side") or "")
        if side not in {"BUY", "SELL"}:
            raise ValueError("Proposal side is invalid")
        if side == "SELL":
            if sell is None or (
                sell.get("shadow_assessment_id") != shadow.get("assessment_id")
                or sell.get("shadow_assessment_record_hash") != shadow.get("record_hash")
                or sell.get("order_id") != proposal.get("order_id")
                or sell.get("proposal_record_hash") != proposal.get("record_hash")
                or sell.get("risk_snapshot_id") != risk.get("snapshot_id")
                or sell.get("risk_snapshot_record_hash") != risk.get("record_hash")
            ):
                raise ValueError("SELL quantity evidence does not match the exact shadow inputs")
        elif sell is not None:
            raise ValueError("BUY assessment cannot use SELL quantity evidence")

        now = datetime.now(timezone.utc)
        if recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if enforce_current_recording and recorded < now - MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must be within five minutes of the current clock")
        evidence_times = [
            _timestamp(shadow.get("recorded_at")),
            _timestamp(stress.get("recorded_at")),
            _timestamp(risk.get("recorded_at")),
            _timestamp(account.get("recorded_at")),
        ]
        if sell is not None:
            evidence_times.append(_timestamp(sell.get("recorded_at")))
        if assessed < max(evidence_times) or assessed > recorded:
            raise ValueError("assessed_at must follow every pinned input and not exceed recorded_at")

        risk_exact = risk.get("exact_fractions")
        stress_exact = stress.get("exact_fractions")
        account_exact = account.get("exact_fractions")
        if not all(isinstance(item, Mapping) for item in (risk_exact, stress_exact, account_exact)):
            raise ValueError("Pinned exact evidence is invalid")
        ticker = str(proposal.get("ticker") or "")
        current_ticker = Fraction(0)
        raw_positions = risk.get("positions")
        exact_positions = risk_exact.get("positions")
        if not isinstance(raw_positions, list) or not isinstance(exact_positions, list) or len(raw_positions) != len(exact_positions):
            raise ValueError("Risk position evidence is invalid")
        for raw, exact in zip(raw_positions, exact_positions):
            if not isinstance(raw, Mapping) or not isinstance(exact, Mapping):
                raise ValueError("Risk position entry is invalid")
            if raw.get("ticker") == ticker:
                current_ticker += _fraction(
                    exact.get("long_market_value_usd"),
                    "current ticker exposure",
                    non_negative=True,
                )
        pending_ticker_buy = Fraction(0)
        pending_account_buy_notional = Fraction(0)
        raw_orders = risk.get("open_orders")
        exact_orders = risk_exact.get("open_orders")
        if not isinstance(raw_orders, list) or not isinstance(exact_orders, list) or len(raw_orders) != len(exact_orders):
            raise ValueError("Risk open-order evidence is invalid")
        for raw, exact in zip(raw_orders, exact_orders):
            if not isinstance(raw, Mapping) or not isinstance(exact, Mapping):
                raise ValueError("Risk open-order entry is invalid")
            remaining_notional = _fraction(
                exact.get("remaining_notional_usd"),
                "pending open-order notional",
                non_negative=True,
            )
            if raw.get("side") == "BUY":
                pending_account_buy_notional += remaining_notional
                if raw.get("ticker") == ticker:
                    pending_ticker_buy += remaining_notional

        stressed_order = _fraction(
            stress_exact.get("conservative_risk_notional_usd"),
            "stressed conservative order notional",
            positive=True,
        )
        current_gross = _fraction(
            risk_exact.get("conservative_gross_exposure_usd"),
            "current gross exposure",
            non_negative=True,
        )
        daily_loss = _fraction(
            risk_exact.get("daily_loss_usd"), "daily loss", non_negative=True
        )
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
        projected_ticker = current_ticker + pending_ticker_buy
        projected_gross = current_gross
        if side == "BUY":
            projected_ticker += stressed_order
            projected_gross += stressed_order
        scenarios = stress_policy.get("scenarios")
        pessimistic = scenarios.get("PESSIMISTIC") if isinstance(scenarios, Mapping) else None
        if not isinstance(pessimistic, Mapping):
            raise ValueError("Pinned pessimistic execution scenario is invalid")
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
            raise ValueError("Pinned pessimistic execution costs must remain below 100%")
        pending_account_buy_stressed_cash = (
            pending_account_buy_notional
            * (Fraction(1) + adverse_bps / BPS)
            * (Fraction(1) + commission_bps / BPS)
        )

        risk_age = _duration_seconds(recorded - _timestamp(risk.get("observed_at")))
        account_age = _duration_seconds(recorded - _timestamp(account.get("observed_at")))
        maximum_account_age = _amount(
            policy.get("max_account_snapshot_age_seconds"),
            "maximum account snapshot age",
            positive=True,
        )
        maximum_risk_age = _amount(
            policy.get("max_risk_snapshot_age_seconds"),
            "maximum risk snapshot age",
            positive=True,
        )
        maximum_order_age = _amount(
            pessimistic.get("maximum_order_age_minutes"),
            "maximum order age minutes",
            positive=True,
        ) * 60
        shadow_age = _duration_seconds(recorded - _timestamp(shadow.get("assessed_at")))
        stress_age = _duration_seconds(recorded - _timestamp(stress.get("assessed_at")))
        proposal_age = _duration_seconds(recorded - _timestamp(proposal.get("created_at")))
        if any(
            age < 0
            for age in (risk_age, account_age, shadow_age, stress_age, proposal_age)
        ):
            raise ValueError("Pinned evidence cannot postdate the operational assessment")
        settled_cash = _fraction(
            account_exact.get("settled_cash"), "settled cash", non_negative=True
        )
        required_cash = (
            _fraction(
                stress_exact.get("estimated_cash_debit_usd"),
                "required BUY cash",
                positive=True,
            )
            if side == "BUY"
            else Fraction(0)
        )
        total_buy_cash = pending_account_buy_stressed_cash + required_cash
        available_sell = Fraction(0)
        proposed_sell = Fraction(0)
        if sell is not None:
            sell_exact = sell.get("exact_fractions")
            if not isinstance(sell_exact, Mapping):
                raise ValueError("SELL exact quantity evidence is invalid")
            available_sell = _fraction(
                sell_exact.get("available_sell_quantity"),
                "available SELL quantity",
            )
            proposed_sell = _fraction(
                sell_exact.get("proposed_sell_quantity"),
                "proposed SELL quantity",
                positive=True,
            )
            if proposed_sell != _fraction(
                stress_exact.get("proposed_quantity"),
                "stressed proposed quantity",
                positive=True,
            ):
                raise ValueError("SELL quantity and stress proposal quantities do not match")

        checks = {
            "stressed_order_notional_within_limit": stressed_order
            <= limits["max_order_notional_usd"],
            "projected_position_notional_within_limit": projected_ticker
            <= limits["max_position_notional_usd"],
            "projected_gross_exposure_within_limit": projected_gross
            <= limits["max_gross_exposure_usd"],
            "daily_loss_within_limit": daily_loss <= limits["max_daily_loss_usd"],
            "risk_snapshot_fresh": risk_age <= maximum_risk_age,
            "account_snapshot_fresh": account_age <= maximum_account_age,
            "shadow_assessment_fresh": shadow_age <= maximum_risk_age,
            "execution_stress_evidence_fresh": stress_age <= maximum_order_age,
            "proposal_reference_fresh": proposal_age <= maximum_order_age,
            "sell_quantity_check_applicable": side == "SELL",
            "sell_quantity_within_available": (
                proposed_sell <= available_sell if sell is not None else True
            ),
            "buy_cash_check_applicable": side == "BUY",
            "buy_settled_cash_sufficient": total_buy_cash <= settled_cash if side == "BUY" else True,
        }
        if sell is not None and checks["sell_quantity_within_available"] != (
            sell.get("sell_quantity_within_available") is True
        ):
            raise ValueError("SELL quantity evidence boolean contradicts its exact quantities")

        kill_count = len(kill_prefix)
        kill_head = kill_prefix[-1]["record_hash"] if kill_prefix else GENESIS_HASH
        if any(_timestamp(stop.get("triggered_at")) > assessed for stop in kill_prefix):
            raise ValueError("assessed_at must follow every already-recorded permanent stop")
        matching = [
            stop
            for stop in kill_prefix
            if stop.get("account_reference_sha256") == policy.get("account_reference_sha256")
            or stop.get("kill_switch_identifier") == policy.get("kill_switch_identifier")
        ]
        matching_stop = matching[-1] if matching else None
        upstream_latched = (
            shadow.get("kill_switch_latched") is True
            or shadow.get("status") == "SHADOW_BLOCKED_LATCHED_STOP"
            or (
                sell is not None
                and (
                    sell.get("kill_switch_latched") is True
                    or sell.get("status") == "SHADOW_BLOCKED_LATCHED_STOP"
                )
            )
        )
        checks["kill_switch_latched"] = matching_stop is not None or upstream_latched
        checks["internal_mathematical_checks_pass"] = (
            checks["stressed_order_notional_within_limit"]
            and checks["projected_position_notional_within_limit"]
            and checks["projected_gross_exposure_within_limit"]
            and checks["daily_loss_within_limit"]
            and checks["risk_snapshot_fresh"]
            and checks["account_snapshot_fresh"]
            and checks["shadow_assessment_fresh"]
            and checks["execution_stress_evidence_fresh"]
            and checks["proposal_reference_fresh"]
            and checks["sell_quantity_within_available"]
            and checks["buy_settled_cash_sufficient"]
            and not checks["kill_switch_latched"]
        )
        checks["operational_paper_readiness"] = False

        ordered_internal = [
            ("STRESSED_ORDER_LIMIT_BREACH", checks["stressed_order_notional_within_limit"]),
            ("PROJECTED_POSITION_LIMIT_BREACH", checks["projected_position_notional_within_limit"]),
            ("PROJECTED_GROSS_LIMIT_BREACH", checks["projected_gross_exposure_within_limit"]),
            ("DAILY_LOSS_LIMIT_BREACH", checks["daily_loss_within_limit"]),
            ("RISK_SNAPSHOT_STALE", checks["risk_snapshot_fresh"]),
            ("ACCOUNT_SNAPSHOT_STALE", checks["account_snapshot_fresh"]),
            ("SHADOW_ASSESSMENT_STALE", checks["shadow_assessment_fresh"]),
            (
                "EXECUTION_STRESS_EVIDENCE_STALE",
                checks["execution_stress_evidence_fresh"],
            ),
            ("PROPOSAL_REFERENCE_STALE", checks["proposal_reference_fresh"]),
            ("SELL_QUANTITY_INSUFFICIENT", checks["sell_quantity_within_available"]),
            ("BUY_SETTLED_CASH_INSUFFICIENT", checks["buy_settled_cash_sufficient"]),
            ("PERMANENT_STOP_LATCHED", not checks["kill_switch_latched"]),
        ]
        open_internal = [name for name, passed in ordered_internal if not passed]
        if checks["kill_switch_latched"]:
            status = "BLOCKED_LATCHED_STOP"
        elif open_internal:
            status = "BLOCKED_INTERNAL_MATHEMATICAL_CHECKS"
        else:
            status = "BLOCKED_EXTERNAL_EVIDENCE_REQUIRED"
        measures = {
            "stressed_conservative_order_notional_usd": stressed_order,
            "current_ticker_long_exposure_usd": current_ticker,
            "pending_ticker_buy_exposure_usd": pending_ticker_buy,
            "projected_ticker_exposure_usd": projected_ticker,
            "current_conservative_gross_exposure_usd": current_gross,
            "projected_conservative_gross_exposure_usd": projected_gross,
            "daily_loss_usd": daily_loss,
            **limits,
            "risk_snapshot_age_seconds": risk_age,
            "account_snapshot_age_seconds": account_age,
            "shadow_assessment_age_seconds": shadow_age,
            "execution_stress_evidence_age_seconds": stress_age,
            "proposal_reference_age_seconds": proposal_age,
            "max_risk_snapshot_age_seconds": maximum_risk_age,
            "max_account_snapshot_age_seconds": maximum_account_age,
            "max_shadow_assessment_age_seconds": maximum_risk_age,
            "max_execution_stress_evidence_age_seconds": maximum_order_age,
            "max_proposal_reference_age_seconds": maximum_order_age,
            "settled_cash_usd": settled_cash,
            "pending_account_buy_notional_usd": pending_account_buy_notional,
            "pending_account_buy_stressed_cash_commitment_usd": (
                pending_account_buy_stressed_cash
            ),
            "required_buy_cash_debit_usd": required_cash,
            "total_buy_cash_commitment_usd": total_buy_cash,
            "available_sell_quantity": available_sell,
            "proposed_sell_quantity": proposed_sell,
        }
        assessed_text = assessed.isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "operational_assessment_id": _identity(
                shadow, sell, stress, kill_count, kill_head, assessed_text
            ),
            "record_type": "PROVIDER_PAPER_OPERATIONAL_READINESS_ASSESSMENT",
            "status": status,
            "assessed_at": assessed_text,
            "recorded_at": recorded.isoformat(),
            "shadow_assessment_id": shadow["assessment_id"],
            "shadow_assessment_record_hash": shadow["record_hash"],
            "sell_quantity_assessment_id": (
                sell["sell_quantity_assessment_id"] if sell is not None else None
            ),
            "sell_quantity_assessment_record_hash": (
                sell["record_hash"] if sell is not None else None
            ),
            "execution_stress_evidence_id": stress["execution_stress_evidence_id"],
            "execution_stress_evidence_record_hash": stress["record_hash"],
            "order_id": proposal["order_id"],
            "proposal_record_hash": proposal["record_hash"],
            "risk_policy_id": policy["policy_id"],
            "risk_policy_record_hash": policy["record_hash"],
            "risk_snapshot_id": risk["snapshot_id"],
            "risk_snapshot_record_hash": risk["record_hash"],
            "account_snapshot_id": account["snapshot_id"],
            "account_snapshot_record_hash": account["record_hash"],
            "account_reference_sha256": policy["account_reference_sha256"],
            "portfolio_version": proposal["portfolio_version"],
            "strategy_version": proposal["strategy_version"],
            "ticker": ticker,
            "side": side,
            "kill_switch_record_count": kill_count,
            "kill_switch_head_hash": kill_head,
            "matching_stop_id": (
                matching_stop["stop_id"]
                if matching_stop
                else (
                    sell.get("matching_stop_id")
                    if sell is not None and sell.get("matching_stop_id")
                    else shadow.get("matching_stop_id")
                )
            ),
            "matching_stop_record_hash": (
                matching_stop["record_hash"]
                if matching_stop
                else (
                    sell.get("matching_stop_record_hash")
                    if sell is not None and sell.get("matching_stop_record_hash")
                    else shadow.get("matching_stop_record_hash")
                )
            ),
            "open_internal_checks": open_internal,
            "external_blockers": list(EXTERNAL_BLOCKERS),
            "next_authority": "RESOLVE_EXTERNAL_EVIDENCE_AND_REQUIRE_SEPARATE_HUMAN_DECISION",
            **{name: _decimal(value) for name, value in measures.items()},
            "exact_fractions": {
                name: _fraction_material(value) for name, value in measures.items()
            },
            **checks,
            **FIXED_FIELDS,
        }

    def verify(self) -> list[dict[str, Any]]:
        shadows = self.shadow_assessment_ledger.verify()
        sells = self.sell_quantity_ledger.verify()
        stresses = self.execution_stress_evidence_ledger.verify()
        kills = self.kill_switch_ledger.verify()
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
                raise LedgerIntegrityError(f"Operational assessment {index} was modified.")
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
                stress = _pin(
                    stresses,
                    record.get("execution_stress_evidence_id"),
                    record.get("execution_stress_evidence_record_hash"),
                    id_field="execution_stress_evidence_id",
                    label="execution stress evidence",
                )
                sell = None
                if record.get("side") == "SELL":
                    sell = _pin(
                        sells,
                        record.get("sell_quantity_assessment_id"),
                        record.get("sell_quantity_assessment_record_hash"),
                        id_field="sell_quantity_assessment_id",
                        label="SELL quantity assessment",
                    )
                elif (
                    record.get("sell_quantity_assessment_id") is not None
                    or record.get("sell_quantity_assessment_record_hash") is not None
                ):
                    raise ValueError("BUY record cannot pin SELL evidence")
                count = record.get("kill_switch_record_count")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > len(kills):
                    raise ValueError("kill-switch record count is invalid")
                assessed = _timestamp(record.get("assessed_at"))
                validate_kill_switch_prefix(kills, count, record.get("recorded_at"))
                expected = self._build(
                    shadow,
                    sell,
                    stress,
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
                    raise ValueError("record does not match the pinned operational calculation")
                key = (record["account_reference_sha256"], record["order_id"])
                if key in previous_time and assessed <= previous_time[key]:
                    raise ValueError("assessment time does not move forward")
                identity = record["operational_assessment_id"]
                if identity in seen:
                    raise ValueError("operational assessment identity is duplicated")
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Operational assessment {index} violates its boundary."
                ) from error
            previous_time[key] = assessed
            seen.add(identity)
            previous_hash = record["record_hash"]
        return records

    def _calculate_and_append(
        self,
        shadow: Mapping[str, Any],
        sell: Mapping[str, Any] | None,
        stress: Mapping[str, Any],
        assessed: datetime,
        recorded: datetime,
        *,
        allow_existing: bool,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        kill_lock = Path(self.kill_switch_ledger.path).with_suffix(
            Path(self.kill_switch_ledger.path).suffix + ".lock"
        )
        kill_descriptor = os.open(kill_lock, os.O_CREAT | os.O_RDWR, 0o600)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(kill_descriptor, fcntl.LOCK_EX)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            kills = self.kill_switch_ledger.verify()
            append_time = datetime.now(timezone.utc)
            if recorded > append_time + MAX_CLOCK_SKEW:
                raise ValueError("recorded_at cannot be in the future")
            if recorded < append_time - MAX_CLOCK_SKEW:
                raise ValueError("recorded_at must be within five minutes of the current clock")
            effective_recorded = max(recorded, append_time)
            records = self.verify()
            kill_count = len(kills)
            kill_head = kills[-1]["record_hash"] if kills else GENESIS_HASH
            identity = _identity(
                shadow,
                sell,
                stress,
                kill_count,
                kill_head,
                assessed.isoformat(),
            )
            existing = next(
                (
                    item
                    for item in records
                    if item["operational_assessment_id"] == identity
                ),
                None,
            )
            if existing is not None:
                if allow_existing:
                    if (
                        _timestamp(existing["recorded_at"])
                        < append_time - IDEMPOTENT_RETRY_WINDOW
                    ):
                        raise ValueError(
                            "Existing assessment is historical; use a later assessed_at"
                        )
                    return existing
                raise LedgerIntegrityError("Operational assessment already exists.")
            result = self._build(
                shadow,
                sell,
                stress,
                kills,
                assessed,
                effective_recorded,
                enforce_current_recording=True,
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
