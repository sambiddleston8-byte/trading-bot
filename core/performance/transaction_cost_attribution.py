from __future__ import annotations

"""Exact attribution of recorded simulated entry fees and slippage."""

from datetime import datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
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


TRANSACTION_COST_ATTRIBUTION_SCHEMA_VERSION = "1.0"
TRANSACTION_COST_ATTRIBUTION_CALCULATION_VERSION = (
    "recorded-simulated-entry-cost-attribution-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
TEN_THOUSAND = Fraction(10_000)
FORMULA = {
    "simulated_fill_notional": "recorded_entry_cost - recorded_entry_fee",
    "reference_notional": (
        "simulated_fill_notional - signed_recorded_entry_slippage_amount"
    ),
    "adverse_slippage_cost": "max(signed_recorded_entry_slippage_amount, 0)",
    "favourable_slippage_benefit": (
        "max(-signed_recorded_entry_slippage_amount, 0)"
    ),
    "net_recorded_entry_execution_cost": (
        "recorded_entry_fee + signed_recorded_entry_slippage_amount"
    ),
    "basis_points": "cost_amount / reference_notional * 10000",
    "double_count_policy": "ATTRIBUTION_ONLY_COST_ALREADY_EMBEDDED_NO_REDEDUCTION",
    "unobserved_cost_policy": (
        "NO_EXIT_SPREAD_MARKET_IMPACT_OR_LATENCY_COST_INVENTED"
    ),
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _result_id(portfolio_version: str, horizon: str) -> str:
    material = [
        portfolio_version,
        horizon,
        TRANSACTION_COST_ATTRIBUTION_CALCULATION_VERSION,
    ]
    return "TCOST-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(valuation: Mapping[str, Any]) -> dict[str, Any]:
    raw_positions = list(valuation.get("positions") or [])
    if not raw_positions:
        raise ValueError("At least one verified position is required")
    tickers = [str(item.get("ticker") or "").upper() for item in raw_positions]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        raise ValueError("Transaction-cost positions require unique tickers")

    positions = []
    totals = {
        "reference_notional": Fraction(0),
        "simulated_fill_notional": Fraction(0),
        "recorded_entry_fees": Fraction(0),
        "signed_recorded_entry_slippage": Fraction(0),
        "adverse_slippage_cost": Fraction(0),
        "favourable_slippage_benefit": Fraction(0),
        "net_recorded_entry_execution_cost": Fraction(0),
        "recorded_entry_cost": Fraction(0),
    }
    for position, ticker in zip(raw_positions, tickers):
        exact = position.get("exact_fractions") or {}
        entry_cost = _fraction(exact.get("recorded_entry_cost") or {}, "entry cost")
        fee = _fraction(exact.get("recorded_entry_fee") or {}, "entry fee")
        slippage = _fraction(
            exact.get("recorded_entry_slippage_amount") or {},
            "entry slippage amount",
        )
        if entry_cost <= 0 or fee < 0:
            raise ValueError("Entry cost must be positive and fee cannot be negative")
        fill_notional = entry_cost - fee
        reference_notional = fill_notional - slippage
        if fill_notional <= 0 or reference_notional <= 0:
            raise ValueError("Fill and reference notional must be positive")
        adverse = max(slippage, Fraction(0))
        favourable = max(-slippage, Fraction(0))
        net_cost = fee + slippage
        values = {
            "reference_notional": reference_notional,
            "simulated_fill_notional": fill_notional,
            "recorded_entry_fee": fee,
            "signed_recorded_entry_slippage": slippage,
            "adverse_slippage_cost": adverse,
            "favourable_slippage_benefit": favourable,
            "net_recorded_entry_execution_cost": net_cost,
            "recorded_entry_cost": entry_cost,
            "fee_bps_of_reference_notional": fee / reference_notional * TEN_THOUSAND,
            "signed_slippage_bps_of_reference_notional": (
                slippage / reference_notional * TEN_THOUSAND
            ),
            "net_cost_bps_of_reference_notional": (
                net_cost / reference_notional * TEN_THOUSAND
            ),
        }
        for name in totals:
            position_name = {
                "recorded_entry_fees": "recorded_entry_fee",
                "signed_recorded_entry_slippage": (
                    "signed_recorded_entry_slippage"
                ),
            }.get(name, name)
            totals[name] += values[position_name]
        positions.append(
            {
                "ticker": ticker,
                "order_id": position["order_id"],
                "fill_id": position["fill_id"],
                "execution_record_hash": position["execution_record_hash"],
                **{key: _decimal_string(value) for key, value in values.items()},
                "exact_fractions": {
                    key: _fraction_material(value) for key, value in values.items()
                },
            }
        )
    positions.sort(key=lambda item: item["ticker"])

    exact_valuation = valuation.get("exact_fractions") or {}
    if totals["recorded_entry_cost"] != _fraction(
        exact_valuation.get("total_recorded_entry_cost") or {},
        "valuation entry cost",
    ) or totals["recorded_entry_fees"] != _fraction(
        exact_valuation.get("total_recorded_entry_fees") or {},
        "valuation entry fees",
    ) or totals["signed_recorded_entry_slippage"] != _fraction(
        exact_valuation.get("total_recorded_entry_slippage_amount") or {},
        "valuation entry slippage",
    ):
        raise ValueError("Position costs do not reconcile to the verified valuation")
    reference = totals["reference_notional"]
    if reference <= 0:
        raise ValueError("Portfolio reference notional must be positive")
    aggregate_bps = {
        "fee_bps_of_reference_notional": (
            totals["recorded_entry_fees"] / reference * TEN_THOUSAND
        ),
        "signed_slippage_bps_of_reference_notional": (
            totals["signed_recorded_entry_slippage"] / reference * TEN_THOUSAND
        ),
        "net_cost_bps_of_reference_notional": (
            totals["net_recorded_entry_execution_cost"] / reference * TEN_THOUSAND
        ),
    }
    all_values = {**totals, **aggregate_bps}
    return {
        "position_count": len(positions),
        "positions": positions,
        **{key: _decimal_string(value) for key, value in all_values.items()},
        "exact_fractions": {
            key: _fraction_material(value) for key, value in all_values.items()
        },
    }


class EntryTransactionCostAttributionLedger:
    """Append-only recorded entry-cost attribution; no invented costs."""

    def __init__(
        self, path: str | Path, valuation_ledger: SimulatedPortfolioValuationLedger
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Transaction-cost ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank transaction-cost line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at transaction-cost line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Transaction-cost line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(
        portfolio_version: str, horizon: str, reasons: list[str]
    ) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "horizon": str(horizon).upper(),
            "reasons": reasons,
            "record_appended": False,
            "simulation_only": True,
            "entry_transaction_cost_attribution_calculated": False,
            "portfolio_return_calculated": False,
            "turnover_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _valuation(
        self, portfolio_version: str, horizon: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.valuation_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            None,
        )

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
        valuation = self._valuation(version, resolved_horizon)
        if resolved_horizon == "ENTRY":
            return self.not_calculable(
                version, resolved_horizon, ["ENTRY is not a portfolio valuation horizon."]
            )
        if valuation is None:
            return self.not_calculable(
                version, resolved_horizon, ["Verified portfolio valuation is missing."]
            )
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        if calculated < _as_datetime(valuation["calculated_at"]):
            return self.not_calculable(
                version,
                resolved_horizon,
                ["calculated_at cannot predate the verified valuation."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(valuation)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": TRANSACTION_COST_ATTRIBUTION_SCHEMA_VERSION,
            "calculation_version": TRANSACTION_COST_ATTRIBUTION_CALCULATION_VERSION,
            "result_id": _result_id(version, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_RECORDED_ENTRY_TRANSACTION_COST_ATTRIBUTION",
            "simulation_only": True,
            "currency": "USD",
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": valuation["horizon_label"],
            "effective_at": valuation["outcome_asset_price_effective_at"],
            "calculated_at": calculated.isoformat(),
            "valuation_id": valuation["valuation_id"],
            "valuation_record_hash": valuation["record_hash"],
            "entry_transaction_cost_attribution_calculated": True,
            "costs_already_embedded_no_rededuction": True,
            "exit_cost_included": False,
            "bid_ask_spread_separately_included": False,
            "market_impact_included": False,
            "latency_cost_included": False,
            "turnover_calculated": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        valuations = {
            item["valuation_id"]: item for item in self.valuation_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Transaction-cost chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Transaction-cost record {index} has been modified."
                )
            valuation = valuations.get(record.get("valuation_id"))
            if valuation is None:
                raise LedgerIntegrityError(
                    f"Transaction-cost record {index} lost its valuation."
                )
            try:
                economics = _economics(valuation)
                calculated = _as_datetime(record.get("calculated_at"))
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Transaction-cost record {index} has invalid values."
                ) from error
            expected_id = _result_id(
                str(record.get("portfolio_version") or ""),
                str(record.get("horizon") or ""),
            )
            boundary = (
                record.get("schema_version")
                == TRANSACTION_COST_ATTRIBUTION_SCHEMA_VERSION
                and record.get("calculation_version")
                == TRANSACTION_COST_ATTRIBUTION_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_RECORDED_ENTRY_TRANSACTION_COST_ATTRIBUTION"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("portfolio_version") == valuation["portfolio_version"]
                and record.get("horizon") == valuation["horizon"]
                and record.get("horizon_label") == valuation["horizon_label"]
                and record.get("effective_at")
                == valuation["outcome_asset_price_effective_at"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("entry_transaction_cost_attribution_calculated") is True
                and record.get("costs_already_embedded_no_rededuction") is True
                and record.get("exit_cost_included") is False
                and record.get("bid_ask_spread_separately_included") is False
                and record.get("market_impact_included") is False
                and record.get("latency_cost_included") is False
                and record.get("turnover_calculated") is False
                and record.get("portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuation["strategy_version"]
                and record.get("model_versions") == valuation["model_versions"]
                and record.get("git_revision") == valuation["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= _as_datetime(valuation["calculated_at"])
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Transaction-cost record {index} violates its boundary."
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
                (item for item in records if item["result_id"] == result["result_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                comparable = {
                    key: value for key, value in existing.items() if key not in ignored
                }
                proposed = {
                    key: value for key, value in result.items() if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Transaction cost result {result['result_id']} already exists."
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
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
