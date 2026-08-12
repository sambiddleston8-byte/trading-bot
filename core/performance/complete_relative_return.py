from __future__ import annotations

"""Benchmark-relative return for complete fixed-horizon simulated outcomes."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.benchmark_total_return import BenchmarkTotalReturnLedger
from core.performance.fixed_horizon_round_trip import FixedHorizonRoundTripOutcomeLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


COMPLETE_RELATIVE_RETURN_SCHEMA_VERSION = "1.0"
COMPLETE_RELATIVE_RETURN_CALCULATION_VERSION = "complete-net-asset-minus-gross-benchmark-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "complete_benchmark_relative_return": (
        "asset_net_total_return_after_entry_and_exit_fees - benchmark_gross_cash_total_return"
    ),
    "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
    "asset_cost_basis": "BOTH_RECORDED_SIMULATED_EXECUTION_FEES_INCLUDED",
    "benchmark_cost_basis": "NO_HYPOTHETICAL_BENCHMARK_EXECUTION_COST_DEDUCTED",
    "distribution_basis": "BOTH_SIDES_GROSS_CASH_PRE_TAX_NO_REINVESTMENT",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _result_id(asset_result_id: str, benchmark_result_id: str) -> str:
    material = [asset_result_id, benchmark_result_id, COMPLETE_RELATIVE_RETURN_CALCULATION_VERSION]
    return "CRRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(asset: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    asset_return = _fraction(
        asset["exact_fractions"]["net_total_return_after_entry_and_exit_fees"],
        "complete asset total return",
    )
    benchmark_return = _fraction(
        benchmark["exact_fractions"]["benchmark_gross_cash_total_return"],
        "benchmark gross cash total return",
    )
    relative = asset_return - benchmark_return
    exact = {
        "asset_net_total_return_after_entry_and_exit_fees": asset_return,
        "benchmark_gross_cash_total_return": benchmark_return,
        "complete_benchmark_relative_return": relative,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class CompleteFixedHorizonRelativeReturnLedger:
    """Append-only arithmetic comparison; explicitly not alpha or a success score."""

    def __init__(
        self,
        path: str | Path,
        asset_outcome_ledger: FixedHorizonRoundTripOutcomeLedger,
        benchmark_return_ledger: BenchmarkTotalReturnLedger,
    ) -> None:
        self.path = Path(path)
        self.asset_outcome_ledger = asset_outcome_ledger
        self.benchmark_return_ledger = benchmark_return_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Complete-relative-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank complete-relative-return line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at complete-relative-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Complete-relative-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(asset_result_id: str, benchmark_result_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "asset_outcome_result_id": str(asset_result_id),
            "benchmark_total_return_result_id": str(benchmark_result_id),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "relative_total_return_calculated": False,
            "alpha_calculated": False,
            "success_rule_applied": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, asset_result_id: str, benchmark_result_id: str):
        asset = next(
            (item for item in self.asset_outcome_ledger.verify() if item.get("result_id") == asset_result_id),
            None,
        )
        benchmark = next(
            (
                item for item in self.benchmark_return_ledger.verify()
                if item.get("result_id") == benchmark_result_id
            ),
            None,
        )
        reasons = []
        if asset is None:
            reasons.append("Verified complete fixed-horizon asset outcome is missing.")
        if benchmark is None:
            reasons.append("Verified matched benchmark total return is missing.")
        if asset is not None and benchmark is not None:
            identity_fields = (
                "decision_id", "portfolio_version", "ticker", "horizon", "horizon_label",
                "strategy_version", "model_versions", "git_revision",
            )
            if any(asset.get(field) != benchmark.get(field) for field in identity_fields):
                reasons.append("Asset and benchmark must share fixed-horizon identity.")
            if asset.get("entry_fill_id") != benchmark.get("fill_id"):
                reasons.append("Benchmark must be linked to the asset entry fill.")
            if (
                asset.get("outcome_observation_id") != benchmark.get("outcome_observation_id")
                or asset.get("outcome_observation_record_hash")
                != benchmark.get("outcome_observation_hash")
            ):
                reasons.append("Asset and benchmark must share exact outcome observation evidence.")
            try:
                _economics(asset, benchmark)
            except (KeyError, TypeError, ValueError) as error:
                reasons.append(str(error))
        return asset, benchmark, sorted(set(reasons))

    def calculate(
        self,
        *,
        asset_outcome_result_id: str,
        benchmark_total_return_result_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        asset_id = str(asset_outcome_result_id or "").strip()
        benchmark_id = str(benchmark_total_return_result_id or "").strip()
        asset, benchmark, reasons = self._support(asset_id, benchmark_id)
        if reasons or asset is None or benchmark is None:
            return self.not_calculable(asset_id, benchmark_id, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(_as_datetime(asset["paired_at"]), _as_datetime(benchmark["calculated_at"]))
        if calculated < latest:
            return self.not_calculable(
                asset_id, benchmark_id, ["calculated_at cannot predate supporting results."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(asset_id, benchmark_id, ["calculated_at cannot be in the future."])
        economics = _economics(asset, benchmark)
        result = {
            "schema_version": COMPLETE_RELATIVE_RETURN_SCHEMA_VERSION,
            "calculation_version": COMPLETE_RELATIVE_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(asset_id, benchmark_id),
            "status": "CALCULATED",
            "scope": "SIMULATED_COMPLETE_FIXED_HORIZON_BENCHMARK_RELATIVE_RETURN",
            "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "asset_outcome_result_id": asset_id,
            "asset_outcome_record_hash": asset["record_hash"],
            "benchmark_total_return_result_id": benchmark_id,
            "benchmark_total_return_record_hash": benchmark["record_hash"],
            "entry_fill_id": asset["entry_fill_id"],
            "decision_id": asset["decision_id"],
            "portfolio_version": asset["portfolio_version"],
            "ticker": asset["ticker"],
            "horizon": asset["horizon"],
            "horizon_label": asset["horizon_label"],
            "benchmark_family": benchmark["benchmark_family"],
            "benchmark_ticker": benchmark["benchmark_ticker"],
            "outcome_observation_id": asset["outcome_observation_id"],
            "outcome_observation_record_hash": asset["outcome_observation_record_hash"],
            "asset_entry_and_exit_fees_included": True,
            "benchmark_execution_cost_deducted": False,
            "relative_total_return_calculated": True,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "success_rule_applied": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": asset["strategy_version"],
            "model_versions": asset["model_versions"],
            "git_revision": asset["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Complete-relative-return record {index} has been modified.")
            asset, benchmark, reasons = self._support(
                str(record.get("asset_outcome_result_id") or ""),
                str(record.get("benchmark_total_return_result_id") or ""),
            )
            if reasons or asset is None or benchmark is None:
                raise LedgerIntegrityError(f"Complete-relative-return record {index} lost support.")
            assets, asset_reasons = resolve_pinned_records(
                self.asset_outcome_ledger.verify(),
                [record.get("asset_outcome_result_id")],
                [record.get("asset_outcome_record_hash")],
                id_field="result_id",
                label="asset-outcome",
            )
            benchmarks, benchmark_reasons = resolve_pinned_records(
                self.benchmark_return_ledger.verify(),
                [record.get("benchmark_total_return_result_id")],
                [record.get("benchmark_total_return_record_hash")],
                id_field="result_id",
                label="benchmark-return",
            )
            if asset_reasons or benchmark_reasons or assets != [asset] or benchmarks != [benchmark]:
                raise LedgerIntegrityError(f"Complete-relative-return record {index} lost pinned support.")
            try:
                economics = _economics(asset, benchmark)
                calculated = _as_datetime(record["calculated_at"])
                latest = max(_as_datetime(asset["paired_at"]), _as_datetime(benchmark["calculated_at"]))
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Complete-relative-return record {index} has invalid values.") from error
            expected_id = _result_id(asset["result_id"], benchmark["result_id"])
            boundary = (
                record.get("schema_version") == COMPLETE_RELATIVE_RETURN_SCHEMA_VERSION
                and record.get("calculation_version") == COMPLETE_RELATIVE_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_COMPLETE_FIXED_HORIZON_BENCHMARK_RELATIVE_RETURN"
                and record.get("comparison_method") == "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("asset_outcome_record_hash") == asset["record_hash"]
                and record.get("benchmark_total_return_record_hash") == benchmark["record_hash"]
                and all(record.get(field) == asset.get(field) for field in (
                    "entry_fill_id", "decision_id", "portfolio_version", "ticker", "horizon",
                    "horizon_label", "outcome_observation_id", "outcome_observation_record_hash",
                    "strategy_version", "model_versions", "git_revision",
                ))
                and record.get("benchmark_family") == benchmark["benchmark_family"]
                and record.get("benchmark_ticker") == benchmark["benchmark_ticker"]
                and record.get("asset_entry_and_exit_fees_included") is True
                and record.get("benchmark_execution_cost_deducted") is False
                and record.get("relative_total_return_calculated") is True
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("success_rule_applied") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Complete-relative-return record {index} violates its boundary.")
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Complete relative return {result['result_id']} already exists.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
