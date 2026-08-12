from __future__ import annotations

"""Like-for-like S&P gross-cash valuation of a simulated portfolio."""

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
from core.performance.portfolio_valuation import SimulatedPortfolioValuationLedger


PORTFOLIO_BENCHMARK_VALUATION_SCHEMA_VERSION = "1.0"
PORTFOLIO_BENCHMARK_VALUATION_CALCULATION_VERSION = (
    "matched-capital-sp500-gross-cash-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
PORTFOLIO_BENCHMARK_VALUATION_FORMULA = {
    "matched_benchmark_capital": "asset_position_recorded_entry_cost",
    "benchmark_position_ending_value": (
        "matched_benchmark_capital * (1 + benchmark_gross_cash_total_return)"
    ),
    "benchmark_cash_reserve": (
        "initial_funding - sum(asset_position_recorded_entry_cost)"
    ),
    "benchmark_total_equity": (
        "benchmark_cash_reserve + sum(benchmark_position_ending_value)"
    ),
    "capital_policy": "MATCH_ASSET_RECORDED_ENTRY_COST_INCLUDING_ENTRY_FEE_BASIS",
    "benchmark_cost_policy": "NO_BENCHMARK_TRANSACTION_COST_INVENTED",
    "distribution_policy": "GROSS_ORDINARY_CASH_POINTS_NO_REINVESTMENT",
    "cash_policy": "UNDEPLOYED_INITIAL_FUNDING_HELD_AS_ZERO_RETURN_CASH",
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
            raise OSError("Unable to complete append-only benchmark-valuation write")
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


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    if resolved == 0:
        return "0"
    return format(resolved.normalize(), "f")


def _valuation_id(portfolio_version: str, horizon: str) -> str:
    material = [
        portfolio_version,
        horizon,
        PORTFOLIO_BENCHMARK_VALUATION_CALCULATION_VERSION,
    ]
    return "PBVAL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    asset_valuation: Mapping[str, Any],
    benchmark_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    initial_funding = _fraction(
        asset_valuation["exact_fractions"]["initial_funding"], "initial funding"
    )
    result_by_fill = {item["fill_id"]: item for item in benchmark_results}
    deployed_capital = Fraction(0)
    benchmark_ending_value = Fraction(0)
    positions = []
    for position in sorted(asset_valuation["positions"], key=lambda item: item["ticker"]):
        capital = _fraction(
            position["exact_fractions"]["recorded_entry_cost"], "matched capital"
        )
        benchmark = result_by_fill[position["fill_id"]]
        benchmark_return = _fraction(
            benchmark["exact_fractions"]["benchmark_gross_cash_total_return"],
            "benchmark total return",
        )
        ending_value = capital * (1 + benchmark_return)
        if capital <= 0 or ending_value <= 0:
            raise ValueError("Matched benchmark capital and ending value must be positive")
        deployed_capital += capital
        benchmark_ending_value += ending_value
        positions.append(
            {
                "ticker": position["ticker"],
                "order_id": position["order_id"],
                "fill_id": position["fill_id"],
                "decision_id": position["decision_id"],
                "asset_total_return_result_id": position["total_return_result_id"],
                "asset_total_return_result_hash": position["total_return_result_hash"],
                "benchmark_total_return_result_id": benchmark["result_id"],
                "benchmark_total_return_result_hash": benchmark["record_hash"],
                "matched_benchmark_capital": _decimal_string(capital),
                "benchmark_gross_cash_total_return": _decimal_string(
                    benchmark_return
                ),
                "benchmark_position_ending_value": _decimal_string(ending_value),
                "exact_fractions": {
                    "matched_benchmark_capital": _fraction_material(capital),
                    "benchmark_gross_cash_total_return": _fraction_material(
                        benchmark_return
                    ),
                    "benchmark_position_ending_value": _fraction_material(
                        ending_value
                    ),
                },
            }
        )
    cash_reserve = initial_funding - deployed_capital
    if cash_reserve < 0:
        raise ValueError("Matched benchmark capital exceeds initial funding")
    total_equity = cash_reserve + benchmark_ending_value
    if total_equity <= 0:
        raise ValueError("Benchmark portfolio equity must be positive")
    weighted_positions = []
    for position in positions:
        ending_value = _fraction(
            position["exact_fractions"]["benchmark_position_ending_value"],
            "benchmark position ending value",
        )
        weight = ending_value / total_equity
        weighted_positions.append(
            {
                **position,
                "actual_weight": _decimal_string(weight),
                "exact_fractions": {
                    **position["exact_fractions"],
                    "actual_weight": _fraction_material(weight),
                },
            }
        )
    cash_weight = cash_reserve / total_equity
    return {
        "positions": weighted_positions,
        "initial_funding": _decimal_string(initial_funding),
        "total_matched_benchmark_capital": _decimal_string(deployed_capital),
        "benchmark_cash_reserve": _decimal_string(cash_reserve),
        "total_benchmark_position_ending_value": _decimal_string(
            benchmark_ending_value
        ),
        "benchmark_total_equity": _decimal_string(total_equity),
        "benchmark_cash_weight": _decimal_string(cash_weight),
        "benchmark_weight_total": _decimal_string(
            cash_weight
            + sum(
                (
                    _fraction(item["exact_fractions"]["actual_weight"], "weight")
                    for item in weighted_positions
                ),
                Fraction(0),
            )
        ),
        "exact_fractions": {
            "initial_funding": _fraction_material(initial_funding),
            "total_matched_benchmark_capital": _fraction_material(deployed_capital),
            "benchmark_cash_reserve": _fraction_material(cash_reserve),
            "total_benchmark_position_ending_value": _fraction_material(
                benchmark_ending_value
            ),
            "benchmark_total_equity": _fraction_material(total_equity),
            "benchmark_cash_weight": _fraction_material(cash_weight),
            "benchmark_weight_total": _fraction_material(Fraction(1)),
        },
    }


class SimulatedPortfolioBenchmarkValuationLedger:
    """Append-only S&P counterfactual valuations; no relative return or alpha."""

    def __init__(
        self,
        path: str | Path,
        asset_valuation_ledger: SimulatedPortfolioValuationLedger,
        benchmark_return_ledger: BenchmarkTotalReturnLedger,
    ) -> None:
        self.path = Path(path)
        self.asset_valuation_ledger = asset_valuation_ledger
        self.benchmark_return_ledger = benchmark_return_ledger
        self.funding_ledger = asset_valuation_ledger.funding_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio benchmark-valuation ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio benchmark-valuation line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at benchmark-valuation line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Benchmark-valuation line {line_number} is not an object."
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
            "benchmark_portfolio_valuation_calculated": False,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, horizon: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
        asset_valuations = self.asset_valuation_ledger.verify()
        asset_valuation = next(
            (
                item
                for item in asset_valuations
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            None,
        )
        reasons = []
        if horizon == "ENTRY":
            reasons.append("ENTRY is a funding baseline, not a benchmark valuation horizon.")
        if asset_valuation is None:
            reasons.append("Verified asset portfolio valuation is missing.")
            return None, [], reasons
        all_benchmarks = self.benchmark_return_ledger.verify()
        benchmark_results = sorted(
            (
                item
                for item in all_benchmarks
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            key=lambda item: item["ticker"],
        )
        expected_fills = {item["fill_id"] for item in asset_valuation["positions"]}
        if {item["fill_id"] for item in benchmark_results} != expected_fills:
            reasons.append(
                "Every asset portfolio position must have one verified benchmark return."
            )
        asset_results = {
            item["result_id"]: item
            for item in self.asset_valuation_ledger.total_return_ledger.verify()
        }
        benchmark_by_fill = {item["fill_id"]: item for item in benchmark_results}
        identity_fields = (
            "fill_id",
            "order_id",
            "decision_id",
            "portfolio_version",
            "ticker",
            "horizon",
            "horizon_label",
            "entry_observation_hash",
            "outcome_observation_hash",
            "strategy_version",
            "model_versions",
            "git_revision",
        )
        for position in asset_valuation["positions"]:
            asset = asset_results.get(position["total_return_result_id"])
            benchmark = benchmark_by_fill.get(position["fill_id"])
            if asset is None or benchmark is None or any(
                asset.get(field) != benchmark.get(field) for field in identity_fields
            ):
                reasons.append(
                    "Asset and benchmark position evidence must share exact identity."
                )
                break
        return asset_valuation, benchmark_results, reasons

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
        asset_valuation, benchmarks, reasons = self._support(version, resolved_horizon)
        if reasons:
            return self.not_calculable(version, resolved_horizon, reasons)
        assert asset_valuation is not None and benchmarks
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(asset_valuation["calculated_at"])]
            + [_as_datetime(item["calculated_at"]) for item in benchmarks]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot predate supporting evidence."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(asset_valuation, benchmarks)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_BENCHMARK_VALUATION_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_BENCHMARK_VALUATION_CALCULATION_VERSION,
            "valuation_id": _valuation_id(version, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_LIKE_FOR_LIKE_SP500_PORTFOLIO_BENCHMARK_VALUATION",
            "simulation_only": True,
            "currency": "USD",
            "benchmark_family": "S&P 500",
            "benchmark_ticker": "^GSPC",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": asset_valuation["horizon_label"],
            "outcome_asset_price_effective_at": asset_valuation[
                "outcome_asset_price_effective_at"
            ],
            "outcome_benchmark_price_effective_at": asset_valuation[
                "outcome_benchmark_price_effective_at"
            ],
            "position_count": len(benchmarks),
            "asset_portfolio_valuation_id": asset_valuation["valuation_id"],
            "asset_portfolio_valuation_hash": asset_valuation["record_hash"],
            "supporting_benchmark_return_ids": [item["result_id"] for item in benchmarks],
            "supporting_benchmark_return_hashes": [item["record_hash"] for item in benchmarks],
            "benchmark_portfolio_valuation_calculated": True,
            "benchmark_portfolio_return_calculated": False,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": asset_valuation["strategy_version"],
            "model_versions": asset_valuation["model_versions"],
            "git_revision": asset_valuation["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_BENCHMARK_VALUATION_FORMULA),
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
                    f"Portfolio benchmark-valuation chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-valuation record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("horizon") or "")
            asset_valuation, benchmarks, reasons = self._support(version, horizon)
            if reasons or asset_valuation is None or not benchmarks:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-valuation record {index} lost evidence."
                )
            try:
                economics = _economics(asset_valuation, benchmarks)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(asset_valuation["calculated_at"])]
                    + [_as_datetime(item["calculated_at"]) for item in benchmarks]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-valuation record {index} has invalid values."
                ) from error
            expected_id = _valuation_id(version, horizon)
            boundary = (
                record.get("schema_version")
                == PORTFOLIO_BENCHMARK_VALUATION_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_BENCHMARK_VALUATION_CALCULATION_VERSION
                and record.get("valuation_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_LIKE_FOR_LIKE_SP500_PORTFOLIO_BENCHMARK_VALUATION"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("benchmark_family") == "S&P 500"
                and record.get("benchmark_ticker") == "^GSPC"
                and record.get("horizon_label") == asset_valuation["horizon_label"]
                and record.get("outcome_asset_price_effective_at")
                == asset_valuation["outcome_asset_price_effective_at"]
                and record.get("outcome_benchmark_price_effective_at")
                == asset_valuation["outcome_benchmark_price_effective_at"]
                and record.get("position_count") == len(benchmarks)
                and record.get("asset_portfolio_valuation_id")
                == asset_valuation["valuation_id"]
                and record.get("asset_portfolio_valuation_hash")
                == asset_valuation["record_hash"]
                and record.get("supporting_benchmark_return_ids")
                == [item["result_id"] for item in benchmarks]
                and record.get("supporting_benchmark_return_hashes")
                == [item["record_hash"] for item in benchmarks]
                and record.get("benchmark_portfolio_valuation_calculated") is True
                and record.get("benchmark_portfolio_return_calculated") is False
                and record.get("relative_portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == asset_valuation["strategy_version"]
                and record.get("model_versions") == asset_valuation["model_versions"]
                and record.get("git_revision") == asset_valuation["git_revision"]
                and record.get("formula") == PORTFOLIO_BENCHMARK_VALUATION_FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-valuation record {index} violates its boundary."
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
                    f"Portfolio benchmark valuation {result['valuation_id']} already exists."
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
