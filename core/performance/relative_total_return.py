from __future__ import annotations

"""Exact position-level relative total returns from verified Phase 5 results."""

from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.benchmark_total_return import BenchmarkTotalReturnLedger
from core.performance.total_return import TotalReturnLedger


RELATIVE_RETURN_SCHEMA_VERSION = "1.0"
RELATIVE_RETURN_CALCULATION_VERSION = "position-relative-total-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
RELATIVE_RETURN_FORMULA = {
    "position_relative_total_return": (
        "asset_gross_total_return_after_entry_fee_excl_exit - "
        "benchmark_gross_cash_total_return"
    ),
    "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
    "asset_distribution_policy": "GROSS_USD_CASH_NO_REINVESTMENT_BEFORE_TAX",
    "benchmark_distribution_policy": "GROSS_ORDINARY_CASH_POINTS_NO_REINVESTMENT",
    "entry_fee_policy": "AS_RECORDED_IN_ASSET_RETURN",
    "exit_cost_policy": "NOT_INCLUDED_NO_EXIT_EXECUTION_RECORDED",
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
            raise OSError("Unable to complete append-only relative-return write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _fraction(material: Mapping[str, Any], name: str) -> Fraction:
    try:
        numerator = int(material["numerator"])
        denominator = int(material["denominator"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator == 0:
        raise ValueError(f"{name} exact fraction denominator cannot be zero")
    return Fraction(numerator, denominator)


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    if resolved == 0:
        return "0"
    return format(resolved.normalize(), "f")


def _result_id(fill_id: str, horizon: str) -> str:
    material = [fill_id, horizon, RELATIVE_RETURN_CALCULATION_VERSION]
    return "RRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    asset_result: Mapping[str, Any], benchmark_result: Mapping[str, Any]
) -> dict[str, Any]:
    asset = _fraction(
        asset_result.get("exact_fractions", {}).get(
            "gross_total_return_after_entry_fee_excl_exit", {}
        ),
        "asset total return",
    )
    benchmark = _fraction(
        benchmark_result.get("exact_fractions", {}).get(
            "benchmark_gross_cash_total_return", {}
        ),
        "benchmark total return",
    )
    relative = asset - benchmark
    return {
        "asset_gross_total_return_after_entry_fee_excl_exit": _decimal_string(asset),
        "benchmark_gross_cash_total_return": _decimal_string(benchmark),
        "position_relative_total_return": _decimal_string(relative),
        "exact_fractions": {
            "asset_gross_total_return_after_entry_fee_excl_exit": _fraction_material(asset),
            "benchmark_gross_cash_total_return": _fraction_material(benchmark),
            "position_relative_total_return": _fraction_material(relative),
        },
    }


class RelativeTotalReturnLedger:
    """Append-only arithmetic differences; explicitly not alpha or a track record."""

    def __init__(
        self,
        path: str | Path,
        asset_return_ledger: TotalReturnLedger,
        benchmark_return_ledger: BenchmarkTotalReturnLedger,
    ) -> None:
        self.path = Path(path)
        self.asset_return_ledger = asset_return_ledger
        self.benchmark_return_ledger = benchmark_return_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Relative-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank relative-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at relative-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Relative-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(fill_id: str, horizon: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "fill_id": str(fill_id),
            "horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "relative_total_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
        }

    def calculate(
        self,
        *,
        fill_id: str,
        horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        asset_records = self.asset_return_ledger.verify()
        benchmark_records = self.benchmark_return_ledger.verify()
        resolved_horizon = str(horizon or "").upper()
        asset = next(
            (
                item for item in asset_records
                if item.get("fill_id") == fill_id and item.get("horizon") == resolved_horizon
            ),
            None,
        )
        benchmark = next(
            (
                item for item in benchmark_records
                if item.get("fill_id") == fill_id and item.get("horizon") == resolved_horizon
            ),
            None,
        )
        reasons = []
        if resolved_horizon == "ENTRY":
            reasons.append("ENTRY is a baseline, not a relative-return horizon.")
        if asset is None:
            reasons.append("Verified asset total-return result is missing.")
        if benchmark is None:
            reasons.append("Verified benchmark total-return result is missing.")
        if asset is not None and benchmark is not None:
            identity_fields = (
                "fill_id", "order_id", "decision_id", "portfolio_version", "ticker",
                "horizon", "horizon_label", "strategy_version", "model_versions",
                "git_revision", "entry_observation_hash", "outcome_observation_hash",
            )
            if any(asset.get(field) != benchmark.get(field) for field in identity_fields):
                reasons.append(
                    "Asset and benchmark returns must share the exact fill, horizon and evidence identity."
                )
        if reasons:
            return self.not_calculable(fill_id, resolved_horizon, reasons)

        assert asset is not None and benchmark is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_input_at = max(
            _as_datetime(asset["calculated_at"]),
            _as_datetime(benchmark["calculated_at"]),
        )
        if calculated < latest_input_at:
            return self.not_calculable(
                fill_id, resolved_horizon,
                ["calculated_at cannot predate supporting return results."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                fill_id, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(asset, benchmark)
        except ValueError as error:
            return self.not_calculable(fill_id, resolved_horizon, [str(error)])
        result = {
            "schema_version": RELATIVE_RETURN_SCHEMA_VERSION,
            "calculation_version": RELATIVE_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(fill_id, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_POSITION_BENCHMARK_RELATIVE_TOTAL_RETURN",
            "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
            "return_unit": "DECIMAL_STRING",
            "currency": "USD",
            "simulation_only": True,
            "position_level_only": True,
            "relative_total_return_calculated": True,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
            "learning_eligible": False,
            "calculated_at": calculated.isoformat(),
            "fill_id": fill_id,
            "order_id": asset["order_id"],
            "decision_id": asset["decision_id"],
            "portfolio_version": asset["portfolio_version"],
            "ticker": asset["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": asset["horizon_label"],
            "benchmark_family": benchmark["benchmark_family"],
            "benchmark_ticker": benchmark["benchmark_ticker"],
            "asset_total_return_result_id": asset["result_id"],
            "asset_total_return_result_hash": asset["record_hash"],
            "benchmark_total_return_result_id": benchmark["result_id"],
            "benchmark_total_return_result_hash": benchmark["record_hash"],
            "entry_observation_hash": asset["entry_observation_hash"],
            "outcome_observation_hash": asset["outcome_observation_hash"],
            "strategy_version": asset["strategy_version"],
            "model_versions": asset["model_versions"],
            "git_revision": asset["git_revision"],
            **economics,
            "formula": dict(RELATIVE_RETURN_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        assets = {
            item["result_id"]: item for item in self.asset_return_ledger.verify()
        }
        benchmarks = {
            item["result_id"]: item for item in self.benchmark_return_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Relative-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Relative-return record {index} has been modified."
                )
            asset = assets.get(record.get("asset_total_return_result_id"))
            benchmark = benchmarks.get(record.get("benchmark_total_return_result_id"))
            if asset is None or benchmark is None:
                raise LedgerIntegrityError(
                    f"Relative-return record {index} lost supporting results."
                )
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            expected_id = _result_id(fill_id, horizon)
            identity_fields = (
                "fill_id", "order_id", "decision_id", "portfolio_version", "ticker",
                "horizon", "horizon_label", "strategy_version", "model_versions",
                "git_revision", "entry_observation_hash", "outcome_observation_hash",
            )
            linked = (
                record.get("asset_total_return_result_hash") == asset.get("record_hash")
                and record.get("benchmark_total_return_result_hash") == benchmark.get("record_hash")
                and all(asset.get(field) == benchmark.get(field) for field in identity_fields)
                and all(record.get(field) == asset.get(field) for field in identity_fields)
                and record.get("benchmark_family") == benchmark.get("benchmark_family")
                and record.get("benchmark_ticker") == benchmark.get("benchmark_ticker")
            )
            boundary = (
                record.get("schema_version") == RELATIVE_RETURN_SCHEMA_VERSION
                and record.get("calculation_version") == RELATIVE_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_POSITION_BENCHMARK_RELATIVE_TOTAL_RETURN"
                and record.get("comparison_method") == "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
                and record.get("return_unit") == "DECIMAL_STRING"
                and record.get("currency") == "USD"
                and record.get("simulation_only") is True
                and record.get("position_level_only") is True
                and record.get("relative_total_return_calculated") is True
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("portfolio_performance_claim") is False
                and record.get("track_record_claim") is False
                and record.get("learning_eligible") is False
                and record.get("formula") == RELATIVE_RETURN_FORMULA
            )
            try:
                calculated = _as_datetime(record.get("calculated_at"))
                economics = _economics(asset, benchmark)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Relative-return record {index} has invalid values."
                ) from error
            calculations = (
                calculated >= max(
                    _as_datetime(asset["calculated_at"]),
                    _as_datetime(benchmark["calculated_at"]),
                )
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(key) == value for key, value in economics.items())
            )
            if not linked or not boundary or not calculations:
                raise LedgerIntegrityError(
                    f"Relative-return record {index} violates its boundary."
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
                (item for item in records if item["result_id"] == result["result_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                comparable = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Relative-return result {result['result_id']} already exists."
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
