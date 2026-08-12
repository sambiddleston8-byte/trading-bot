from __future__ import annotations

"""Immutable initial-funded valuation of a complete simulated portfolio."""

from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.broker import LocalPaperExecutionLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_funding import PortfolioFundingLedger
from core.performance.total_return import TotalReturnLedger


PORTFOLIO_VALUATION_SCHEMA_VERSION = "1.0"
PORTFOLIO_VALUATION_CALCULATION_VERSION = "initial-funded-buy-and-hold-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
PORTFOLIO_VALUATION_FORMULA = {
    "remaining_cash": (
        "initial_funding - sum(recorded_entry_cost) + sum(gross_dividend_cash)"
    ),
    "position_market_value": "sum(outcome_position_value)",
    "total_equity": "remaining_cash + position_market_value",
    "actual_position_weight": "outcome_position_value / total_equity",
    "actual_cash_weight": "remaining_cash / total_equity",
    "cash_flow_policy": "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED",
    "position_policy": "COMPLETE_PROPOSAL_SET_LONG_BUY_AND_HOLD_ONLY",
    "valuation_alignment": "EXACT_SHARED_ASSET_AND_BENCHMARK_EFFECTIVE_TIMES",
    "exit_policy": "NO_EXIT_EXECUTION_OR_EXIT_COST_INVENTED",
    "slippage_policy": "EMBEDDED_IN_FILL_PRICE_DISCLOSED_NOT_SUBTRACTED_TWICE",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only portfolio-valuation write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _fraction(material: Mapping[str, Any], name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} exact fraction denominator must be positive")
    return value


def _number_fraction(value: Any, name: str) -> Fraction:
    try:
        resolved = Fraction(Decimal(str(value)))
    except (InvalidOperation, OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact decimal") from error
    return resolved


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    if resolved == 0:
        return "0"
    return format(resolved.normalize(), "f")


def _valuation_id(portfolio_version: str, horizon: str) -> str:
    material = [portfolio_version, horizon, PORTFOLIO_VALUATION_CALCULATION_VERSION]
    return "PVAL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    funding: Mapping[str, Any],
    proposals: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    proposal_by_order = {item["order_id"]: item for item in proposals}
    fill_by_id = {item["fill_id"]: item for item in fills}
    initial_funding = _fraction(funding.get("exact_amount") or {}, "initial funding")
    target_weights = [
        _number_fraction(item["target_weight"], "target_weight") for item in proposals
    ]
    if any(value < 0 or value > 1 for value in target_weights):
        raise ValueError("Each portfolio proposal target weight must be between 0 and 100%")
    target_total = sum(target_weights, Fraction(0))
    if target_total > 1:
        raise ValueError("Portfolio proposal target weights exceed 100%")

    total_entry_cost = Fraction(0)
    total_entry_fees = Fraction(0)
    total_entry_slippage = Fraction(0)
    total_position_value = Fraction(0)
    total_dividend_cash = Fraction(0)
    raw_positions = []
    for result in sorted(results, key=lambda item: item["ticker"]):
        exact = result.get("exact_fractions") or {}
        entry_cost = _fraction(exact.get("recorded_entry_cost") or {}, "entry cost")
        entry_fee = _fraction(exact.get("recorded_entry_fee") or {}, "entry fee")
        position_value = _fraction(
            exact.get("outcome_position_value") or {}, "outcome position value"
        )
        dividend_cash = _fraction(
            exact.get("gross_dividend_cash") or {}, "gross dividend cash"
        )
        quantity = _fraction(
            exact.get("split_adjusted_quantity") or {}, "split-adjusted quantity"
        )
        proposal = proposal_by_order[result["order_id"]]
        fill = fill_by_id[result["fill_id"]]
        target_weight = _number_fraction(proposal["target_weight"], "target_weight")
        slippage_amount = _number_fraction(
            fill["slippage_amount"], "recorded entry slippage amount"
        )
        total_entry_cost += entry_cost
        total_entry_fees += entry_fee
        total_entry_slippage += slippage_amount
        total_position_value += position_value
        total_dividend_cash += dividend_cash
        raw_positions.append(
            {
                "ticker": result["ticker"],
                "order_id": result["order_id"],
                "fill_id": result["fill_id"],
                "decision_id": result["decision_id"],
                "total_return_result_id": result["result_id"],
                "total_return_result_hash": result["record_hash"],
                "execution_record_hash": fill["record_hash"],
                "target_weight": _decimal_string(target_weight),
                "split_adjusted_quantity": _decimal_string(quantity),
                "recorded_entry_cost": _decimal_string(entry_cost),
                "recorded_entry_fee": _decimal_string(entry_fee),
                "recorded_entry_slippage_bps": str(fill["slippage_bps"]),
                "recorded_entry_slippage_amount": _decimal_string(slippage_amount),
                "outcome_position_value": _decimal_string(position_value),
                "gross_dividend_cash": _decimal_string(dividend_cash),
                "exact_fractions": {
                    "target_weight": _fraction_material(target_weight),
                    "split_adjusted_quantity": _fraction_material(quantity),
                    "recorded_entry_cost": _fraction_material(entry_cost),
                    "recorded_entry_fee": _fraction_material(entry_fee),
                    "recorded_entry_slippage_amount": _fraction_material(slippage_amount),
                    "outcome_position_value": _fraction_material(position_value),
                    "gross_dividend_cash": _fraction_material(dividend_cash),
                },
            }
        )
    remaining_cash = initial_funding - total_entry_cost + total_dividend_cash
    if remaining_cash < 0:
        raise ValueError("Initial funding is insufficient for recorded entry costs")
    total_equity = remaining_cash + total_position_value
    if total_equity <= 0:
        raise ValueError("Portfolio total equity must remain positive")

    positions = []
    for position in raw_positions:
        market_value = _fraction(
            position["exact_fractions"]["outcome_position_value"], "market value"
        )
        actual_weight = market_value / total_equity
        position = dict(position)
        position["actual_weight"] = _decimal_string(actual_weight)
        position["exact_fractions"] = {
            **position["exact_fractions"],
            "actual_weight": _fraction_material(actual_weight),
        }
        positions.append(position)
    actual_cash_weight = remaining_cash / total_equity
    target_cash_weight = Fraction(1) - target_total
    return {
        "positions": positions,
        "initial_funding": _decimal_string(initial_funding),
        "total_recorded_entry_cost": _decimal_string(total_entry_cost),
        "total_recorded_entry_fees": _decimal_string(total_entry_fees),
        "total_recorded_entry_slippage_amount": _decimal_string(total_entry_slippage),
        "total_gross_dividend_cash": _decimal_string(total_dividend_cash),
        "remaining_cash": _decimal_string(remaining_cash),
        "total_position_market_value": _decimal_string(total_position_value),
        "total_equity": _decimal_string(total_equity),
        "target_position_weight_total": _decimal_string(target_total),
        "target_cash_weight": _decimal_string(target_cash_weight),
        "actual_cash_weight": _decimal_string(actual_cash_weight),
        "actual_weight_total": _decimal_string(
            actual_cash_weight
            + sum(
                (
                    _fraction(item["exact_fractions"]["actual_weight"], "actual weight")
                    for item in positions
                ),
                Fraction(0),
            )
        ),
        "exact_fractions": {
            "initial_funding": _fraction_material(initial_funding),
            "total_recorded_entry_cost": _fraction_material(total_entry_cost),
            "total_recorded_entry_fees": _fraction_material(total_entry_fees),
            "total_recorded_entry_slippage_amount": _fraction_material(
                total_entry_slippage
            ),
            "total_gross_dividend_cash": _fraction_material(total_dividend_cash),
            "remaining_cash": _fraction_material(remaining_cash),
            "total_position_market_value": _fraction_material(total_position_value),
            "total_equity": _fraction_material(total_equity),
            "target_position_weight_total": _fraction_material(target_total),
            "target_cash_weight": _fraction_material(target_cash_weight),
            "actual_cash_weight": _fraction_material(actual_cash_weight),
            "actual_weight_total": _fraction_material(Fraction(1)),
        },
    }


class SimulatedPortfolioValuationLedger:
    """Complete initial-funded portfolio snapshots; no performance series yet."""

    def __init__(
        self,
        path: str | Path,
        execution_ledger: LocalPaperExecutionLedger,
        total_return_ledger: TotalReturnLedger,
        funding_ledger: PortfolioFundingLedger,
    ) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger
        self.total_return_ledger = total_return_ledger
        self.funding_ledger = funding_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio-valuation ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio-valuation line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio-valuation line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio-valuation line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(
        portfolio_version: str, horizon: str, reasons: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "portfolio_valuation_calculated": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, horizon: str
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[str],
    ]:
        proposals = sorted(
            (
                item
                for item in self.execution_ledger.proposal_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
            ),
            key=lambda item: item["order_id"],
        )
        all_fills = self.execution_ledger.verify()
        fills = sorted(
            (item for item in all_fills if item.get("portfolio_version") == portfolio_version),
            key=lambda item: item["order_id"],
        )
        all_returns = self.total_return_ledger.verify()
        returns = sorted(
            (
                item
                for item in all_returns
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            key=lambda item: item["ticker"],
        )
        observations = {
            item["observation_id"]: item
            for item in self.total_return_ledger.observation_ledger.verify()
        }
        outcomes = [
            observations[item["outcome_observation_id"]]
            for item in returns
            if item.get("outcome_observation_id") in observations
        ]
        funding = self.funding_ledger.funding_for(portfolio_version)
        reasons = []
        if horizon == "ENTRY":
            reasons.append("ENTRY is a baseline, not a portfolio valuation horizon.")
        if not proposals:
            reasons.append("Verified portfolio proposals are missing.")
        if any(item.get("side") != "BUY" for item in proposals):
            reasons.append("Initial portfolio valuation supports long BUY proposals only.")
        if len({item.get("ticker") for item in proposals}) != len(proposals):
            reasons.append("Each initial portfolio ticker must have one proposal.")
        expected_orders = [item["order_id"] for item in proposals]
        if [item["order_id"] for item in fills] != expected_orders:
            reasons.append("Every portfolio proposal must have exactly one verified fill.")
        expected_fill_ids = {item["fill_id"] for item in fills}
        if {item["fill_id"] for item in returns} != expected_fill_ids:
            reasons.append("Every portfolio fill must have one verified total-return result.")
        if len(outcomes) != len(returns):
            reasons.append("Every total-return result must retain its outcome observation.")
        if outcomes and (
            len({item["asset_price_effective_at"] for item in outcomes}) != 1
            or len({item["benchmark_price_effective_at"] for item in outcomes}) != 1
        ):
            reasons.append("All positions must share exact valuation effective times.")
        if funding is None:
            reasons.append("Verified initial portfolio funding is missing.")
        elif (
            funding.get("proposal_order_ids") != expected_orders
            or funding.get("proposal_record_hashes")
            != [item["record_hash"] for item in proposals]
        ):
            reasons.append("Initial funding must cover the complete proposal set.")
        if proposals:
            identity_fields = ("strategy_version", "model_versions", "git_revision")
            reference = proposals[0]
            if any(
                any(item.get(field) != reference.get(field) for field in identity_fields)
                for item in [*proposals, *fills, *returns]
            ) or (
                funding is not None
                and any(funding.get(field) != reference.get(field) for field in identity_fields)
            ):
                reasons.append("Portfolio evidence must share strategy, model and Git identity.")
        return funding, proposals, fills, returns, outcomes, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        resolved_horizon = str(horizon or "").upper()
        funding, proposals, fills, returns, outcomes, reasons = self._support(
            version, resolved_horizon
        )
        if reasons:
            return self.not_calculable(version, resolved_horizon, reasons)
        assert funding is not None and proposals and outcomes
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(funding["recorded_at"])]
            + [_as_datetime(item["calculated_at"]) for item in returns]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version,
                resolved_horizon,
                ["calculated_at cannot predate supporting portfolio evidence."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(funding, proposals, fills, returns)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_VALUATION_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_VALUATION_CALCULATION_VERSION,
            "valuation_id": _valuation_id(version, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_INITIAL_FUNDED_PORTFOLIO_VALUATION",
            "simulation_only": True,
            "position_policy": "COMPLETE_PROPOSAL_SET_LONG_BUY_AND_HOLD_ONLY",
            "cash_flow_policy": "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED",
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": returns[0]["horizon_label"],
            "outcome_asset_price_effective_at": outcomes[0]["asset_price_effective_at"],
            "outcome_benchmark_price_effective_at": outcomes[0][
                "benchmark_price_effective_at"
            ],
            "position_count": len(returns),
            "funding_id": funding["funding_id"],
            "funding_record_hash": funding["record_hash"],
            "supporting_total_return_result_ids": [item["result_id"] for item in returns],
            "supporting_total_return_result_hashes": [item["record_hash"] for item in returns],
            "portfolio_valuation_calculated": True,
            "portfolio_return_calculated": False,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": proposals[0]["strategy_version"],
            "model_versions": proposals[0]["model_versions"],
            "git_revision": proposals[0]["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_VALUATION_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio-valuation chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio-valuation record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("horizon") or "")
            funding, proposals, fills, returns, outcomes, reasons = self._support(
                version, horizon
            )
            if reasons or funding is None or not proposals or not outcomes:
                raise LedgerIntegrityError(
                    f"Portfolio-valuation record {index} lost supporting evidence."
                )
            try:
                economics = _economics(funding, proposals, fills, returns)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(funding["recorded_at"])]
                    + [_as_datetime(item["calculated_at"]) for item in returns]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio-valuation record {index} has invalid values."
                ) from error
            expected_id = _valuation_id(version, horizon)
            boundary = (
                record.get("schema_version") == PORTFOLIO_VALUATION_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_VALUATION_CALCULATION_VERSION
                and record.get("valuation_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_INITIAL_FUNDED_PORTFOLIO_VALUATION"
                and record.get("simulation_only") is True
                and record.get("position_policy")
                == "COMPLETE_PROPOSAL_SET_LONG_BUY_AND_HOLD_ONLY"
                and record.get("cash_flow_policy")
                == "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED"
                and record.get("currency") == "USD"
                and record.get("horizon_label") == returns[0]["horizon_label"]
                and record.get("outcome_asset_price_effective_at")
                == outcomes[0]["asset_price_effective_at"]
                and record.get("outcome_benchmark_price_effective_at")
                == outcomes[0]["benchmark_price_effective_at"]
                and record.get("position_count") == len(returns)
                and record.get("funding_id") == funding["funding_id"]
                and record.get("funding_record_hash") == funding["record_hash"]
                and record.get("supporting_total_return_result_ids")
                == [item["result_id"] for item in returns]
                and record.get("supporting_total_return_result_hashes")
                == [item["record_hash"] for item in returns]
                and record.get("portfolio_valuation_calculated") is True
                and record.get("portfolio_return_calculated") is False
                and record.get("relative_portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == proposals[0]["strategy_version"]
                and record.get("model_versions") == proposals[0]["model_versions"]
                and record.get("git_revision") == proposals[0]["git_revision"]
                and record.get("formula") == PORTFOLIO_VALUATION_FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio-valuation record {index} violates its boundary."
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
            existing = next(
                (item for item in records if item["valuation_id"] == result["valuation_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio valuation {result['valuation_id']} already exists."
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
                backup = self.path.with_suffix(
                    self.path.suffix + f".incomplete-tail-{uuid4().hex}"
                )
                backup_descriptor = os.open(
                    backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
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
