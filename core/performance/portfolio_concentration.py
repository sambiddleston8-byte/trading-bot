from __future__ import annotations

"""Exact concentration statistics at verified simulated portfolio boundaries."""

from datetime import datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.portfolio_valuation import (
    SimulatedPortfolioValuationLedger,
    _as_datetime,
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


PORTFOLIO_CONCENTRATION_SCHEMA_VERSION = "1.0"
PORTFOLIO_CONCENTRATION_CALCULATION_VERSION = (
    "post-flow-portfolio-concentration-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
PORTFOLIO_CONCENTRATION_FORMULA = {
    "post_flow_cash": "base_remaining_cash + cumulative_external_cash_flow",
    "post_flow_total_equity": (
        "base_total_equity + cumulative_external_cash_flow"
    ),
    "allocation_weight": "allocation_value / post_flow_total_equity",
    "cash_inclusive_hhi": "cash_weight^2 + sum(position_weight^2)",
    "invested_position_weight": (
        "position_weight / invested_position_weight_total"
    ),
    "invested_position_hhi": "sum(invested_position_weight^2)",
    "effective_invested_positions": "1 / invested_position_hhi",
    "cash_flow_timing": "AFTER_MARKET_VALUATION_END_OF_SUBPERIOD",
    "classification_policy": "NO_THRESHOLD_OR_RECOMMENDATION",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _result_id(portfolio_version: str, horizon: str) -> str:
    material = [
        portfolio_version,
        horizon,
        PORTFOLIO_CONCENTRATION_CALCULATION_VERSION,
    ]
    return "PCONC-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    valuation: Mapping[str, Any], flows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    base_cash = _fraction(
        valuation.get("exact_fractions", {}).get("remaining_cash", {}),
        "base remaining cash",
    )
    base_equity = _fraction(
        valuation.get("exact_fractions", {}).get("total_equity", {}),
        "base total equity",
    )
    cumulative_flow = sum(
        (
            _fraction(item.get("exact_signed_amount", {}), "external cash flow")
            for item in flows
        ),
        Fraction(0),
    )
    cash = base_cash + cumulative_flow
    equity = base_equity + cumulative_flow
    if cash < 0:
        raise ValueError("Post-flow cash cannot be negative")
    if equity <= 0:
        raise ValueError("Post-flow total equity must be positive")

    raw_positions = list(valuation.get("positions") or [])
    if not raw_positions:
        raise ValueError("At least one verified invested position is required")
    tickers = [str(item.get("ticker") or "").upper() for item in raw_positions]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        raise ValueError("Verified concentration positions require unique tickers")

    values = []
    for item, ticker in zip(raw_positions, tickers):
        value = _fraction(
            item.get("exact_fractions", {}).get("outcome_position_value", {}),
            f"{ticker} position value",
        )
        if value < 0:
            raise ValueError("Position values cannot be negative")
        values.append((ticker, value))
    invested_value = sum((value for _, value in values), Fraction(0))
    expected_invested_value = equity - cash
    if invested_value <= 0:
        raise ValueError("Invested position value must be positive")
    if invested_value != expected_invested_value:
        raise ValueError("Position, cash and total-equity values must reconcile exactly")

    cash_weight = cash / equity
    invested_weight_total = invested_value / equity
    positions = []
    for ticker, value in values:
        weight = value / equity
        invested_weight = value / invested_value
        positions.append(
            {
                "ticker": ticker,
                "position_value": _decimal_string(value),
                "portfolio_weight": _decimal_string(weight),
                "invested_position_weight": _decimal_string(invested_weight),
                "exact_fractions": {
                    "position_value": _fraction_material(value),
                    "portfolio_weight": _fraction_material(weight),
                    "invested_position_weight": _fraction_material(invested_weight),
                },
            }
        )
    positions.sort(
        key=lambda item: (
            -_fraction(item["exact_fractions"]["portfolio_weight"], "weight"),
            item["ticker"],
        )
    )

    total_weight = cash_weight + sum(
        (
            _fraction(item["exact_fractions"]["portfolio_weight"], "weight")
            for item in positions
        ),
        Fraction(0),
    )
    invested_normalized_total = sum(
        (
            _fraction(
                item["exact_fractions"]["invested_position_weight"],
                "invested weight",
            )
            for item in positions
        ),
        Fraction(0),
    )
    if total_weight != 1 or invested_normalized_total != 1:
        raise ValueError("Concentration weights must reconcile exactly")

    cash_inclusive_hhi = cash_weight**2 + sum(
        (
            _fraction(item["exact_fractions"]["portfolio_weight"], "weight") ** 2
            for item in positions
        ),
        Fraction(0),
    )
    invested_hhi = sum(
        (
            _fraction(
                item["exact_fractions"]["invested_position_weight"],
                "invested weight",
            )
            ** 2
            for item in positions
        ),
        Fraction(0),
    )
    if invested_hhi <= 0:
        raise ValueError("Invested-position HHI must be positive")
    effective_positions = 1 / invested_hhi
    largest_weight = _fraction(
        positions[0]["exact_fractions"]["portfolio_weight"], "largest weight"
    )
    largest_invested_weight = _fraction(
        positions[0]["exact_fractions"]["invested_position_weight"],
        "largest invested weight",
    )
    top_five = positions[:5]
    top_five_weight = sum(
        (
            _fraction(item["exact_fractions"]["portfolio_weight"], "weight")
            for item in top_five
        ),
        Fraction(0),
    )
    top_five_invested_weight = sum(
        (
            _fraction(
                item["exact_fractions"]["invested_position_weight"],
                "invested weight",
            )
            for item in top_five
        ),
        Fraction(0),
    )
    exact = {
        "base_remaining_cash": base_cash,
        "base_total_equity": base_equity,
        "cumulative_external_cash_flow": cumulative_flow,
        "post_flow_cash": cash,
        "post_flow_total_equity": equity,
        "cash_weight": cash_weight,
        "invested_position_weight_total": invested_weight_total,
        "cash_inclusive_allocation_hhi": cash_inclusive_hhi,
        "invested_position_hhi": invested_hhi,
        "effective_number_of_invested_positions": effective_positions,
        "largest_position_weight": largest_weight,
        "largest_invested_position_weight": largest_invested_weight,
        "top_five_position_weight": top_five_weight,
        "top_five_invested_position_weight": top_five_invested_weight,
        "allocation_weight_total": total_weight,
        "invested_position_weight_normalized_total": invested_normalized_total,
    }
    return {
        "positions": positions,
        "position_count": len(positions),
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {
            key: _fraction_material(value) for key, value in exact.items()
        },
    }


class PortfolioConcentrationLedger:
    """Append-only concentration evidence; no threshold or recommendation."""

    def __init__(
        self,
        path: str | Path,
        valuation_ledger: SimulatedPortfolioValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio-concentration ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio-concentration line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio-concentration line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio-concentration line {line_number} is not an object."
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
            "concentration_calculated": False,
            "concentration_classification_provided": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, horizon: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item
                for item in valuations
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            None,
        )
        reasons = []
        if horizon == "ENTRY":
            reasons.append("ENTRY is the funding baseline, not a valuation horizon.")
        if valuation is None:
            reasons.append("Verified portfolio valuation is missing.")
            return None, [], reasons
        target_at = _as_datetime(valuation["outcome_asset_price_effective_at"])
        included_valuations = {
            item["valuation_id"]: item
            for item in valuations
            if item.get("portfolio_version") == portfolio_version
            and _as_datetime(item["outcome_asset_price_effective_at"]) <= target_at
        }
        flows = sorted(
            (
                item
                for item in self.cash_flow_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and _as_datetime(item["effective_at"]) <= target_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        if any(item.get("valuation_id") not in included_valuations for item in flows):
            reasons.append(
                "Every included cash flow must retain an included valuation boundary."
            )
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if any(
            any(item.get(field) != valuation.get(field) for field in identity_fields)
            for item in flows
        ):
            reasons.append(
                "Concentration evidence must share strategy, model and Git identity."
            )
        return valuation, flows, reasons

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
        valuation, flows, reasons = self._support(version, resolved_horizon)
        if reasons:
            return self.not_calculable(version, resolved_horizon, reasons)
        assert valuation is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(valuation["calculated_at"])]
            + [_as_datetime(item["recorded_at"]) for item in flows]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version,
                resolved_horizon,
                ["calculated_at cannot predate supporting concentration evidence."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(valuation, flows)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_CONCENTRATION_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_CONCENTRATION_CALCULATION_VERSION,
            "result_id": _result_id(version, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_POST_FLOW_PORTFOLIO_CONCENTRATION",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": valuation["horizon_label"],
            "effective_at": valuation["outcome_asset_price_effective_at"],
            "valuation_id": valuation["valuation_id"],
            "valuation_record_hash": valuation["record_hash"],
            "supporting_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_cash_flow_hashes": [item["record_hash"] for item in flows],
            "concentration_calculated": True,
            "concentration_classification_provided": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_CONCENTRATION_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio-concentration chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio-concentration record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("horizon") or "")
            valuation, flows, reasons = self._support(version, horizon)
            if reasons or valuation is None:
                raise LedgerIntegrityError(
                    f"Portfolio-concentration record {index} lost supporting evidence."
                )
            try:
                economics = _economics(valuation, flows)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(valuation["calculated_at"])]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio-concentration record {index} has invalid values."
                ) from error
            expected_id = _result_id(version, horizon)
            boundary = (
                record.get("schema_version")
                == PORTFOLIO_CONCENTRATION_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_CONCENTRATION_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_POST_FLOW_PORTFOLIO_CONCENTRATION"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("horizon_label") == valuation["horizon_label"]
                and record.get("effective_at")
                == valuation["outcome_asset_price_effective_at"]
                and record.get("valuation_id") == valuation["valuation_id"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("supporting_cash_flow_ids")
                == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes")
                == [item["record_hash"] for item in flows]
                and record.get("concentration_calculated") is True
                and record.get("concentration_classification_provided") is False
                and record.get("portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuation["strategy_version"]
                and record.get("model_versions") == valuation["model_versions"]
                and record.get("git_revision") == valuation["git_revision"]
                and record.get("formula") == PORTFOLIO_CONCENTRATION_FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio-concentration record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(
        self, result: dict[str, Any], *, allow_existing: bool
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["result_id"] == result["result_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                comparable = {
                    key: value
                    for key, value in existing.items()
                    if key not in ignored
                }
                proposed = {
                    key: value
                    for key, value in result.items()
                    if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio concentration result {result['result_id']} already exists."
                )
            material = {
                **result,
                "previous_hash": (
                    records[-1]["record_hash"] if records else GENESIS_HASH
                ),
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                _write_all(
                    target, (_canonical_json(record) + "\n").encode("utf-8")
                )
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
