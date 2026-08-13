from __future__ import annotations

"""Exact cash-reconciled daily simulated portfolio valuation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.daily_position_value import DailyPositionValueLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.paper_position_state import PaperPositionStateLedger
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.portfolio_funding import PortfolioFundingLedger
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


DAILY_PORTFOLIO_VALUATION_SCHEMA_VERSION = "1.0"
DAILY_PORTFOLIO_VALUATION_CALCULATION_VERSION = "fifo-sell-aware-daily-portfolio-value-v3"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FILL_INCLUSION_POLICY = "FILLED_AT_OR_BEFORE_EXACT_SESSION_CLOSE"
FORMULA = {
    "recorded_entry_cost": "sum(BUY simulated_fill_gross_value + BUY recorded_fee)",
    "remaining_cash": (
        "initial_funding + position_state_net_trade_cash_flow "
        "+ cumulative_gross_usd_dividend_cash_pre_tax "
        "+ cumulative_external_cash_flow"
    ),
    "total_equity": "remaining_cash + total_position_market_value",
    "position_weight": "position_market_value / total_equity",
    "cash_weight": "remaining_cash / total_equity",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _number(value: Any, name: str, *, allow_zero: bool = False) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (resolved == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return Fraction(resolved)


def _valuation_id(portfolio_version: str, session_date: str, position_snapshot_id: str) -> str:
    material = [
        portfolio_version,
        session_date,
        position_snapshot_id,
        DAILY_PORTFOLIO_VALUATION_CALCULATION_VERSION,
    ]
    return "DPFVAL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    funding: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    position_values: Sequence[Mapping[str, Any]],
    flows: Sequence[Mapping[str, Any]],
    position_state: Mapping[str, Any],
) -> dict[str, Any]:
    initial_funding = _fraction(funding["exact_amount"], "initial funding")
    values_by_fill = {item["fill_id"]: item for item in position_values}
    buy_fills = [fill for fill in fills if fill.get("side") == "BUY"]
    if len(values_by_fill) != len(position_values) or set(values_by_fill) != {
        item["fill_id"] for item in buy_fills
    }:
        raise ValueError("Daily values must match every historical BUY fill exactly once")
    if set(position_state.get("supporting_fill_ids") or []) != {
        item["fill_id"] for item in fills
    }:
        raise ValueError("Position state must pin the exact as-of fill history")
    sold_tickers = {str(fill["ticker"]) for fill in fills if fill.get("side") == "SELL"}
    if any(
        value.get("events_applied")
        for value in position_values
        if str(value.get("ticker")) in sold_tickers
    ):
        raise ValueError(
            "Sold tickers with split or dividend history require event-aware lot accounting"
        )
    remaining_by_fill = {
        lot["buy_fill_id"]: _fraction(
            lot["exact_remaining_quantity"], "remaining lot quantity"
        )
        for position in position_state.get("open_positions") or []
        for lot in position.get("supporting_open_lots") or []
    }
    total_entry_cost = Fraction(0)
    total_entry_fees = Fraction(0)
    total_market_value = Fraction(0)
    total_dividends = Fraction(0)
    positions = []
    for fill in sorted(buy_fills, key=lambda item: item["fill_id"]):
        gross = _number(fill["gross_value"], "fill gross value")
        fee = _number(fill["fees"], "entry fee", allow_zero=True)
        entry_cost = gross + fee
        value = values_by_fill[fill["fill_id"]]
        exact = value["exact_fractions"]
        full_market_value = _fraction(exact["position_market_value"], "position market value")
        dividend_cash = _fraction(
            exact["cumulative_gross_dividend_cash"], "cumulative dividend cash"
        )
        total_entry_cost += entry_cost
        total_entry_fees += fee
        original_quantity = _number(fill["filled_quantity"], "original filled quantity")
        remaining_quantity = remaining_by_fill.get(fill["fill_id"], Fraction(0))
        if remaining_quantity > original_quantity:
            raise ValueError("Remaining lot quantity cannot exceed its original BUY quantity")
        if fill["ticker"] not in sold_tickers and remaining_quantity != original_quantity:
            raise ValueError("Unsold ticker position state must retain its complete BUY quantity")
        market_value = full_market_value * remaining_quantity / original_quantity
        total_market_value += market_value
        total_dividends += dividend_cash
        if remaining_quantity == 0:
            continue
        positions.append(
            {
                "ticker": fill["ticker"],
                "fill_id": fill["fill_id"],
                "daily_position_value_id": value["result_id"],
                "daily_position_value_record_hash": value["record_hash"],
                "recorded_entry_cost": _decimal_string(entry_cost),
                "remaining_quantity": _decimal_string(remaining_quantity),
                "position_market_value": _decimal_string(market_value),
                "cumulative_gross_dividend_cash": _decimal_string(dividend_cash),
                "exact_fractions": {
                    "recorded_entry_cost": _fraction_material(entry_cost),
                    "remaining_quantity": _fraction_material(remaining_quantity),
                    "position_market_value": _fraction_material(market_value),
                    "cumulative_gross_dividend_cash": _fraction_material(dividend_cash),
                },
            }
        )
    cumulative_flow = sum(
        (_fraction(item["exact_signed_amount"], "external cash flow") for item in flows),
        Fraction(0),
    )
    net_trade_cash = _fraction(
        position_state["exact_fractions"]["net_trade_cash_flow"],
        "position-state net trade cash flow",
    )
    cash = initial_funding + net_trade_cash + total_dividends + cumulative_flow
    if cash < 0:
        raise ValueError("Reconciled daily cash cannot be negative")
    equity = cash + total_market_value
    if equity <= 0:
        raise ValueError("Daily portfolio equity must be positive")
    for position in positions:
        market_value = _fraction(position["exact_fractions"]["position_market_value"], "market value")
        weight = market_value / equity
        position["portfolio_weight"] = _decimal_string(weight)
        position["exact_fractions"]["portfolio_weight"] = _fraction_material(weight)
    cash_weight = cash / equity
    weight_total = cash_weight + sum(
        (_fraction(item["exact_fractions"]["portfolio_weight"], "weight") for item in positions),
        Fraction(0),
    )
    if weight_total != 1:
        raise ValueError("Daily portfolio weights must reconcile exactly")
    exact_totals = {
        "initial_funding": initial_funding,
        "total_recorded_entry_cost": total_entry_cost,
        "total_recorded_entry_fees": total_entry_fees,
        "position_state_net_trade_cash_flow": net_trade_cash,
        "cumulative_gross_usd_dividend_cash_pre_tax": total_dividends,
        "cumulative_external_cash_flow": cumulative_flow,
        "remaining_cash": cash,
        "total_position_market_value": total_market_value,
        "total_equity": equity,
        "cash_weight": cash_weight,
        "weight_total": weight_total,
    }
    return {
        "positions": positions,
        "position_count": len(positions),
        **{key: _decimal_string(value) for key, value in exact_totals.items()},
        "exact_fractions": {
            key: _fraction_material(value) for key, value in exact_totals.items()
        },
    }


class DailyPortfolioValuationLedger:
    """Append-only daily portfolio value; no return or risk metric is calculated."""

    def __init__(
        self,
        path: str | Path,
        position_value_ledger: DailyPositionValueLedger,
        funding_ledger: PortfolioFundingLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
        position_state_ledger: PaperPositionStateLedger,
    ) -> None:
        self.path = Path(path)
        self.position_value_ledger = position_value_ledger
        self.funding_ledger = funding_ledger
        self.cash_flow_ledger = cash_flow_ledger
        self.position_state_ledger = position_state_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-portfolio-valuation ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank daily-portfolio-valuation line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-portfolio-valuation line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-portfolio-valuation line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(portfolio_version: str, session_date: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "market_session_date": str(session_date),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "daily_portfolio_valuation_calculated": False,
            "portfolio_return_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(self, portfolio_version: str, session_date: str):
        execution_ledger = self.position_value_ledger.observation_ledger.execution_ledger
        proposals = [
            item for item in execution_ledger.proposal_ledger.verify()
            if item.get("portfolio_version") == portfolio_version
        ]
        all_fills = [
            item for item in execution_ledger.verify()
            if item.get("portfolio_version") == portfolio_version
        ]
        funding = self.funding_ledger.funding_for(portfolio_version)
        all_values = self.position_value_ledger.verify()
        values = [
            item for item in all_values
            if item.get("portfolio_version") == portfolio_version
            and item.get("market_session_date") == session_date
        ]
        reasons = []
        if not proposals:
            reasons.append("Verified funded portfolio proposals are missing.")
        if funding is None:
            reasons.append("Verified initial funding is missing.")
        effective_times = {item.get("effective_at") for item in values}
        if len(effective_times) != 1:
            reasons.append("Daily position values must share one exact close timestamp.")
        close_at = _as_datetime(next(iter(effective_times))) if len(effective_times) == 1 else None
        fills = sorted(
            (
                item for item in all_fills
                if close_at is not None and _as_datetime(item["filled_at"]) <= close_at
            ),
            key=lambda item: item["fill_id"],
        )
        if not fills:
            reasons.append("Verified simulated fills at or before the session close are missing.")
        funding_orders = set(funding.get("proposal_order_ids") or []) if funding else set()
        fill_orders = {item["order_id"] for item in fills}
        if not fill_orders.issubset(funding_orders) or len(fills) != len(fill_orders):
            reasons.append("Every as-of fill must belong to the proposal set pinned by initial funding.")
        fill_ids = {item["fill_id"] for item in fills if item.get("side") == "BUY"}
        value_fill_ids = {item["fill_id"] for item in values}
        if value_fill_ids != fill_ids or len(values) != len(value_fill_ids):
            reasons.append("Every historical BUY fill requires one exact-close daily position value.")
        flows = sorted(
            (
                item for item in self.cash_flow_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and close_at is not None
                and _as_datetime(item["effective_at"]) <= close_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        reference = proposals[0] if proposals else None
        if reference and any(
            any(item.get(field) != reference.get(field) for field in identity_fields)
            for item in [*fills, *values, *flows]
        ):
            reasons.append("All daily valuation evidence must share strategy, model and Git identity.")
        if funding and (
            funding.get("proposal_order_ids") != [item["order_id"] for item in sorted(proposals, key=lambda x: x["order_id"])]
            or any(funding.get(field) != reference.get(field) for field in identity_fields)
        ):
            reasons.append("Funding must cover the exact proposal set and identity.")
        return funding, fills, values, flows, close_at, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        market_session_date: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        session = str(market_session_date or "").strip()
        funding, fills, values, flows, close_at, reasons = self._support(version, session)
        if reasons:
            return self.not_calculable(version, session, reasons)
        assert funding is not None and close_at is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            [_as_datetime(funding["recorded_at"])]
            + [_as_datetime(item["calculated_at"]) for item in values]
            + [_as_datetime(item["recorded_at"]) for item in flows]
        )
        if calculated < latest:
            return self.not_calculable(version, session, ["calculated_at cannot predate supporting evidence."])
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(version, session, ["calculated_at cannot be in the future."])
        try:
            position_state = self.position_state_ledger.calculate(
                portfolio_version=version,
                as_of=close_at,
                calculated_at=calculated,
            )
        except (LedgerIntegrityError, TypeError, ValueError) as error:
            return self.not_calculable(version, session, [str(error)])
        try:
            economics = _economics(funding, fills, values, flows, position_state)
        except (TypeError, ValueError) as error:
            return self.not_calculable(version, session, [str(error)])
        fills = sorted(fills, key=lambda item: item["fill_id"])
        values = sorted(values, key=lambda item: item["fill_id"])
        result = {
            "schema_version": DAILY_PORTFOLIO_VALUATION_SCHEMA_VERSION,
            "calculation_version": DAILY_PORTFOLIO_VALUATION_CALCULATION_VERSION,
            "valuation_id": _valuation_id(version, session, position_state["snapshot_id"]),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_PRE_TAX_DAILY_PORTFOLIO_VALUATION",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "market_session_date": session,
            "effective_at": close_at.isoformat(),
            "fill_inclusion_policy": FILL_INCLUSION_POLICY,
            "funding_id": funding["funding_id"],
            "funding_record_hash": funding["record_hash"],
            "position_snapshot_id": position_state["snapshot_id"],
            "position_snapshot_record_hash": position_state["record_hash"],
            "supporting_fill_ids": [item["fill_id"] for item in fills],
            "supporting_fill_hashes": [item["record_hash"] for item in fills],
            "supporting_position_value_ids": [item["result_id"] for item in values],
            "supporting_position_value_hashes": [item["record_hash"] for item in values],
            "supporting_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_cash_flow_hashes": [item["record_hash"] for item in flows],
            "contains_backfilled_close_evidence": any(
                item["contains_backfilled_close_evidence"] for item in values
            ),
            "contains_post_close_corporate_action_evidence": any(
                item["contains_post_close_corporate_action_evidence"] for item in values
            ),
            "daily_portfolio_valuation_calculated": True,
            "broker_cash_reconciled": False,
            "tax_and_withholding_modelled": False,
            "portfolio_return_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": fills[0]["strategy_version"],
            "model_versions": fills[0]["model_versions"],
            "git_revision": fills[0]["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(self, record: Mapping[str, Any]):
        execution_ledger = self.position_value_ledger.observation_ledger.execution_ledger
        all_fills = execution_ledger.verify()
        fills, fill_reasons = resolve_pinned_records(
            all_fills,
            record.get("supporting_fill_ids"),
            record.get("supporting_fill_hashes"),
            id_field="fill_id",
            label="fill",
        )
        values, value_reasons = resolve_pinned_records(
            self.position_value_ledger.verify(),
            record.get("supporting_position_value_ids"),
            record.get("supporting_position_value_hashes"),
            id_field="result_id",
            label="daily-position-value",
        )
        flows, flow_reasons = resolve_pinned_records(
            self.cash_flow_ledger.verify(),
            record.get("supporting_cash_flow_ids"),
            record.get("supporting_cash_flow_hashes"),
            id_field="flow_id",
            label="cash-flow",
        )
        funding = self.funding_ledger.funding_for(record.get("portfolio_version"))
        states, state_reasons = resolve_pinned_records(
            self.position_state_ledger.verify(),
            [record.get("position_snapshot_id")],
            [record.get("position_snapshot_record_hash")],
            id_field="snapshot_id",
            label="paper-position-state",
        )
        position_state = states[0] if len(states) == 1 else None
        reasons = [*fill_reasons, *value_reasons, *flow_reasons, *state_reasons]
        if funding is None or funding.get("funding_id") != record.get("funding_id") or funding.get("record_hash") != record.get("funding_record_hash"):
            reasons.append("Pinned funding is missing or changed.")
        if {item.get("fill_id") for item in fills if item.get("side") == "BUY"} != {
            item.get("fill_id") for item in values
        }:
            reasons.append("Pinned daily values do not match pinned BUY fills.")
        if position_state is None or position_state.get("as_of") != record.get("effective_at"):
            reasons.append("Pinned position state is missing or not at the exact close.")
        return funding, list(fills), list(values), list(flows), position_state, reasons

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Daily-portfolio-valuation chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Daily-portfolio-valuation record {index} has been modified.")
            funding, fills, values, flows, position_state, reasons = self._pinned_support(record)
            if reasons or funding is None or position_state is None:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-valuation record {index} lost supporting evidence."
                )
            try:
                economics = _economics(funding, fills, values, flows, position_state)
                calculated = _as_datetime(record.get("calculated_at"))
                latest = max(
                    [_as_datetime(funding["recorded_at"])]
                    + [_as_datetime(item["calculated_at"]) for item in values]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-valuation record {index} has invalid values."
                ) from error
            fills = sorted(fills, key=lambda item: item["fill_id"])
            values = sorted(values, key=lambda item: item["fill_id"])
            expected_id = _valuation_id(
                record.get("portfolio_version", ""),
                record.get("market_session_date", ""),
                position_state["snapshot_id"],
            )
            boundary = (
                record.get("schema_version") == DAILY_PORTFOLIO_VALUATION_SCHEMA_VERSION
                and record.get("calculation_version") == DAILY_PORTFOLIO_VALUATION_CALCULATION_VERSION
                and record.get("valuation_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_GROSS_PRE_TAX_DAILY_PORTFOLIO_VALUATION"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("fill_inclusion_policy") == FILL_INCLUSION_POLICY
                and record.get("position_snapshot_id") == position_state["snapshot_id"]
                and record.get("position_snapshot_record_hash") == position_state["record_hash"]
                and record.get("supporting_fill_ids") == [item["fill_id"] for item in fills]
                and record.get("supporting_fill_hashes") == [item["record_hash"] for item in fills]
                and record.get("supporting_position_value_ids") == [item["result_id"] for item in values]
                and record.get("supporting_position_value_hashes") == [item["record_hash"] for item in values]
                and record.get("supporting_cash_flow_ids") == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes") == [item["record_hash"] for item in flows]
                and record.get("effective_at") == values[0]["effective_at"]
                and all(item["market_session_date"] == record.get("market_session_date") and item["effective_at"] == record.get("effective_at") for item in values)
                and record.get("contains_backfilled_close_evidence") == any(item["contains_backfilled_close_evidence"] for item in values)
                and record.get("contains_post_close_corporate_action_evidence") == any(item["contains_post_close_corporate_action_evidence"] for item in values)
                and record.get("daily_portfolio_valuation_calculated") is True
                and record.get("broker_cash_reconciled") is False
                and record.get("tax_and_withholding_modelled") is False
                and record.get("portfolio_return_calculated") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == fills[0]["strategy_version"]
                and record.get("model_versions") == fills[0]["model_versions"]
                and record.get("git_revision") == fills[0]["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-valuation record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["valuation_id"] == result["valuation_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Daily portfolio valuation {result['valuation_id']} already exists."
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

    def repair_incomplete_tail(self) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix, tail = raw[:complete_end], raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(self.path.suffix + f".incomplete-tail-{uuid4().hex}")
                backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    _write_all(backup_descriptor, tail)
                    os.fsync(backup_descriptor)
                finally:
                    os.close(backup_descriptor)
                target = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    _write_all(target, prefix)
                    os.fsync(target)
                finally:
                    os.close(target)
                self.verify()
                return backup
            target = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(target, b"\n")
                os.fsync(target)
            finally:
                os.close(target)
            self.verify()
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
