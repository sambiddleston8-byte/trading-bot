from __future__ import annotations

"""Exact portfolio benchmark-relative return from verified Phase 5 ledgers."""

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
from core.performance.portfolio_benchmark_return import (
    TimeWeightedPortfolioBenchmarkReturnLedger,
)
from core.performance.portfolio_return import (
    TimeWeightedPortfolioReturnLedger,
    _as_datetime,
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


PORTFOLIO_RELATIVE_RETURN_SCHEMA_VERSION = "1.0"
PORTFOLIO_RELATIVE_RETURN_CALCULATION_VERSION = (
    "portfolio-relative-twr-arithmetic-difference-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
PORTFOLIO_RELATIVE_RETURN_FORMULA = {
    "portfolio_benchmark_relative_return": (
        "time_weighted_portfolio_return - "
        "time_weighted_benchmark_portfolio_return"
    ),
    "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
    "cash_flow_policy": "EXACT_MATCHED_BOUNDARIES_AND_EXTERNAL_FLOWS",
    "annualization_policy": "NOT_ANNUALIZED",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _result_id(portfolio_version: str, horizon: str) -> str:
    material = [
        portfolio_version,
        horizon,
        PORTFOLIO_RELATIVE_RETURN_CALCULATION_VERSION,
    ]
    return "PRREL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    asset_result: Mapping[str, Any], benchmark_result: Mapping[str, Any]
) -> dict[str, Any]:
    asset = _fraction(
        asset_result.get("exact_fractions", {}).get(
            "time_weighted_portfolio_return", {}
        ),
        "asset portfolio return",
    )
    benchmark = _fraction(
        benchmark_result.get("exact_fractions", {}).get(
            "time_weighted_benchmark_portfolio_return", {}
        ),
        "benchmark portfolio return",
    )
    relative = asset - benchmark
    return {
        "time_weighted_portfolio_return": _decimal_string(asset),
        "time_weighted_benchmark_portfolio_return": _decimal_string(benchmark),
        "portfolio_benchmark_relative_return": _decimal_string(relative),
        "exact_fractions": {
            "time_weighted_portfolio_return": _fraction_material(asset),
            "time_weighted_benchmark_portfolio_return": _fraction_material(benchmark),
            "portfolio_benchmark_relative_return": _fraction_material(relative),
        },
    }


def _evidence_matches(
    asset: Mapping[str, Any], benchmark: Mapping[str, Any]
) -> bool:
    shared_fields = (
        "portfolio_version",
        "through_horizon",
        "through_horizon_label",
        "currency",
        "funding_id",
        "funding_record_hash",
        "supporting_cash_flow_ids",
        "supporting_cash_flow_hashes",
        "strategy_version",
        "model_versions",
        "git_revision",
    )
    return (
        all(asset.get(field) == benchmark.get(field) for field in shared_fields)
        and asset.get("supporting_valuation_ids")
        == benchmark.get("supporting_asset_valuation_ids")
        and asset.get("supporting_valuation_hashes")
        == benchmark.get("supporting_asset_valuation_hashes")
    )


class PortfolioRelativeReturnLedger:
    """Append-only portfolio return difference; explicitly not alpha."""

    def __init__(
        self,
        path: str | Path,
        asset_return_ledger: TimeWeightedPortfolioReturnLedger,
        benchmark_return_ledger: TimeWeightedPortfolioBenchmarkReturnLedger,
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
                "Portfolio relative-return ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio relative-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio relative-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio relative-return line {line_number} is not an object."
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
            "through_horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, through_horizon: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
        assets = self.asset_return_ledger.verify()
        benchmarks = self.benchmark_return_ledger.verify()
        asset = next(
            (
                item
                for item in assets
                if item.get("portfolio_version") == portfolio_version
                and item.get("through_horizon") == through_horizon
            ),
            None,
        )
        benchmark = next(
            (
                item
                for item in benchmarks
                if item.get("portfolio_version") == portfolio_version
                and item.get("through_horizon") == through_horizon
            ),
            None,
        )
        reasons = []
        if through_horizon == "ENTRY":
            reasons.append("ENTRY is the funding baseline, not a return horizon.")
        if asset is None:
            reasons.append("Verified asset portfolio-return result is missing.")
        if benchmark is None:
            reasons.append("Verified benchmark portfolio-return result is missing.")
        if asset is not None and benchmark is not None and not _evidence_matches(
            asset, benchmark
        ):
            reasons.append(
                "Asset and benchmark portfolio returns must share the exact funding, "
                "valuation, cash-flow, horizon and methodology identity."
            )
        return asset, benchmark, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        through_horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        asset, benchmark, reasons = self._support(version, horizon)
        if reasons:
            return self.not_calculable(version, horizon, reasons)
        assert asset is not None and benchmark is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            _as_datetime(asset["calculated_at"]),
            _as_datetime(benchmark["calculated_at"]),
        )
        if calculated < latest_support:
            return self.not_calculable(
                version,
                horizon,
                ["calculated_at cannot predate supporting portfolio returns."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(asset, benchmark)
        except ValueError as error:
            return self.not_calculable(version, horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_RELATIVE_RETURN_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_RELATIVE_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_PORTFOLIO_BENCHMARK_RELATIVE_TIME_WEIGHTED_RETURN",
            "comparison_method": "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA",
            "return_unit": "DECIMAL_STRING",
            "currency": "USD",
            "simulation_only": True,
            "portfolio_level_only": True,
            "relative_portfolio_return_calculated": True,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_horizon_label": asset["through_horizon_label"],
            "benchmark_family": benchmark["benchmark_family"],
            "benchmark_ticker": benchmark["benchmark_ticker"],
            "asset_portfolio_return_result_id": asset["result_id"],
            "asset_portfolio_return_result_hash": asset["record_hash"],
            "benchmark_portfolio_return_result_id": benchmark["result_id"],
            "benchmark_portfolio_return_result_hash": benchmark["record_hash"],
            "funding_id": asset["funding_id"],
            "funding_record_hash": asset["funding_record_hash"],
            "supporting_asset_valuation_ids": asset["supporting_valuation_ids"],
            "supporting_asset_valuation_hashes": asset[
                "supporting_valuation_hashes"
            ],
            "supporting_benchmark_valuation_ids": benchmark[
                "supporting_benchmark_valuation_ids"
            ],
            "supporting_benchmark_valuation_hashes": benchmark[
                "supporting_benchmark_valuation_hashes"
            ],
            "supporting_cash_flow_ids": asset["supporting_cash_flow_ids"],
            "supporting_cash_flow_hashes": asset["supporting_cash_flow_hashes"],
            "strategy_version": asset["strategy_version"],
            "model_versions": asset["model_versions"],
            "git_revision": asset["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_RELATIVE_RETURN_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        assets = {
            item["result_id"]: item for item in self.asset_return_ledger.verify()
        }
        benchmarks = {
            item["result_id"]: item
            for item in self.benchmark_return_ledger.verify()
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
                    f"Portfolio relative-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio relative-return record {index} has been modified."
                )
            asset = assets.get(record.get("asset_portfolio_return_result_id"))
            benchmark = benchmarks.get(
                record.get("benchmark_portfolio_return_result_id")
            )
            if asset is None or benchmark is None:
                raise LedgerIntegrityError(
                    f"Portfolio relative-return record {index} lost supporting results."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("through_horizon") or "")
            expected_id = _result_id(version, horizon)
            try:
                economics = _economics(asset, benchmark)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    _as_datetime(asset["calculated_at"]),
                    _as_datetime(benchmark["calculated_at"]),
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio relative-return record {index} has invalid values."
                ) from error
            linked = (
                _evidence_matches(asset, benchmark)
                and record.get("asset_portfolio_return_result_hash")
                == asset.get("record_hash")
                and record.get("benchmark_portfolio_return_result_hash")
                == benchmark.get("record_hash")
                and record.get("portfolio_version") == asset.get("portfolio_version")
                and record.get("through_horizon") == asset.get("through_horizon")
                and record.get("through_horizon_label")
                == asset.get("through_horizon_label")
                and record.get("funding_id") == asset.get("funding_id")
                and record.get("funding_record_hash")
                == asset.get("funding_record_hash")
                and record.get("supporting_asset_valuation_ids")
                == asset.get("supporting_valuation_ids")
                and record.get("supporting_asset_valuation_hashes")
                == asset.get("supporting_valuation_hashes")
                and record.get("supporting_benchmark_valuation_ids")
                == benchmark.get("supporting_benchmark_valuation_ids")
                and record.get("supporting_benchmark_valuation_hashes")
                == benchmark.get("supporting_benchmark_valuation_hashes")
                and record.get("supporting_cash_flow_ids")
                == asset.get("supporting_cash_flow_ids")
                and record.get("supporting_cash_flow_hashes")
                == asset.get("supporting_cash_flow_hashes")
                and record.get("strategy_version") == asset.get("strategy_version")
                and record.get("model_versions") == asset.get("model_versions")
                and record.get("git_revision") == asset.get("git_revision")
                and record.get("benchmark_family")
                == benchmark.get("benchmark_family")
                and record.get("benchmark_ticker")
                == benchmark.get("benchmark_ticker")
            )
            boundary = (
                record.get("schema_version")
                == PORTFOLIO_RELATIVE_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_RELATIVE_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_PORTFOLIO_BENCHMARK_RELATIVE_TIME_WEIGHTED_RETURN"
                and record.get("comparison_method")
                == "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
                and record.get("return_unit") == "DECIMAL_STRING"
                and record.get("currency") == "USD"
                and record.get("simulation_only") is True
                and record.get("portfolio_level_only") is True
                and record.get("relative_portfolio_return_calculated") is True
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("formula") == PORTFOLIO_RELATIVE_RETURN_FORMULA
            )
            calculations = (
                calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(key) == value for key, value in economics.items())
            )
            if not linked or not boundary or not calculations:
                raise LedgerIntegrityError(
                    f"Portfolio relative-return record {index} violates its boundary."
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
                    f"Portfolio relative-return result {result['result_id']} already exists."
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
