from __future__ import annotations

"""Deterministic S&P 500 gross cash total returns from immutable evidence."""

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

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.benchmark_distribution import BenchmarkDistributionLedger
from core.performance.outcome_observation import OutcomeObservationLedger


BENCHMARK_RETURN_SCHEMA_VERSION = "1.0"
BENCHMARK_RETURN_CALCULATION_VERSION = "sp500-gross-cash-total-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
COMPOSITION_POLICY = "CURRENT_INDEX_WEIGHTS_NOT_FROZEN_AT_ENTRY"
BENCHMARK_RETURN_FORMULA = {
    "gross_cash_ending_value": "outcome_unadjusted_price + gross_dividend_points",
    "gross_cash_total_return": (
        "(gross_cash_ending_value - entry_unadjusted_price) / "
        "entry_unadjusted_price"
    ),
    "price_return_context": (
        "(outcome_unadjusted_price - entry_unadjusted_price) / "
        "entry_unadjusted_price"
    ),
    "distribution_return_component": (
        "gross_dividend_points / entry_unadjusted_price"
    ),
    "distribution_policy": "GROSS_ORDINARY_CASH_DIVIDEND_POINTS",
    "withholding_tax_policy": "NO_WITHHOLDING_DEDUCTION",
    "reinvestment_policy": "NOT_REINVESTED",
    "interval_policy": "EX_DATE_AFTER_START_THROUGH_END_(START,END]",
    "composition_policy": COMPOSITION_POLICY,
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
            raise OSError("Unable to complete append-only benchmark-return write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (resolved == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return resolved


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _fraction_decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    return _decimal_string(resolved)


def _result_id(fill_id: str, horizon: str) -> str:
    key = _canonical_json([fill_id, horizon, BENCHMARK_RETURN_CALCULATION_VERSION])
    return "BTR-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()


def _economics(
    *,
    entry: Mapping[str, Any],
    outcome: Mapping[str, Any],
    distribution: Mapping[str, Any],
) -> dict[str, Any]:
    if distribution.get("completeness_status") != "COMPLETE":
        raise ValueError("Benchmark distribution evidence must be COMPLETE.")
    if distribution.get("basket_composition_policy") != COMPOSITION_POLICY:
        raise ValueError("Benchmark composition policy is unsupported.")
    if distribution.get("reinvestment_policy") != "NOT_REINVESTED":
        raise ValueError("Benchmark distributions must not be reinvested.")
    if distribution.get("distribution_basis") != (
        "GROSS_ORDINARY_CASH_DIVIDEND_POINTS"
    ):
        raise ValueError("Benchmark distribution basis is unsupported.")
    with localcontext(CALCULATION_CONTEXT):
        entry_price = _fraction(
            _decimal(entry["benchmark_price"], "entry_benchmark_price")
        )
        outcome_price = _fraction(
            _decimal(outcome["benchmark_price"], "outcome_benchmark_price")
        )
        dividend_points = _fraction(
            _decimal(
                distribution["gross_dividend_points"],
                "gross_dividend_points",
                allow_zero=True,
            )
        )
        ending_value = outcome_price + dividend_points
        price_return = (outcome_price - entry_price) / entry_price
        distribution_component = dividend_points / entry_price
        cash_total_return = (ending_value - entry_price) / entry_price
        exact = {
            "entry_benchmark_price": _fraction_material(entry_price),
            "outcome_benchmark_price": _fraction_material(outcome_price),
            "gross_dividend_points": _fraction_material(dividend_points),
            "gross_cash_ending_value": _fraction_material(ending_value),
            "benchmark_price_return_context": _fraction_material(price_return),
            "benchmark_distribution_return_component": _fraction_material(
                distribution_component
            ),
            "benchmark_gross_cash_total_return": _fraction_material(
                cash_total_return
            ),
        }
        return {
            "entry_benchmark_price": _fraction_decimal_string(entry_price),
            "outcome_benchmark_price": _fraction_decimal_string(outcome_price),
            "gross_dividend_points": _fraction_decimal_string(dividend_points),
            "gross_cash_ending_value": _fraction_decimal_string(ending_value),
            "benchmark_price_return_context": _fraction_decimal_string(price_return),
            "benchmark_distribution_return_component": _fraction_decimal_string(
                distribution_component
            ),
            "benchmark_gross_cash_total_return": _fraction_decimal_string(
                cash_total_return
            ),
            "exact_fractions": exact,
        }


class BenchmarkTotalReturnLedger:
    """Append-only S&P cash returns; never relative return, alpha or a track record."""

    def __init__(
        self,
        path: str | Path,
        observation_ledger: OutcomeObservationLedger,
        distribution_ledger: BenchmarkDistributionLedger,
    ) -> None:
        self.path = Path(path)
        self.observation_ledger = observation_ledger
        self.distribution_ledger = distribution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Benchmark-return ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank benchmark-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at benchmark-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Benchmark-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(
        fill_id: str, horizon: str, reasons: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "fill_id": str(fill_id),
            "horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "benchmark_total_return_calculated": False,
            "learning_eligible": False,
            "relative_total_return_calculated": False,
            "alpha_calculated": False,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
        }

    def calculate(
        self,
        *,
        fill_id: str,
        horizon: str,
        accept_current_index_composition: bool = False,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        observations = self.observation_ledger.verify()
        distributions = self.distribution_ledger.verify()
        resolved_horizon = str(horizon or "").upper()
        reasons: list[str] = []
        if resolved_horizon == "ENTRY":
            reasons.append("ENTRY is a baseline, not a benchmark-return horizon.")
        entry = next(
            (
                item
                for item in observations
                if item["fill_id"] == fill_id and item["horizon"] == "ENTRY"
            ),
            None,
        )
        outcome = next(
            (
                item
                for item in observations
                if item["fill_id"] == fill_id
                and item["horizon"] == resolved_horizon
            ),
            None,
        )
        distribution = next(
            (
                item
                for item in distributions
                if item["fill_id"] == fill_id
                and item["horizon"] == resolved_horizon
                and item["completeness_status"] == "COMPLETE"
            ),
            None,
        )
        uncertain_distribution = next(
            (
                item
                for item in distributions
                if item["fill_id"] == fill_id
                and item["horizon"] == resolved_horizon
                and item["completeness_status"] == "UNCERTAIN"
            ),
            None,
        )
        if entry is None:
            reasons.append("Verified ENTRY observation is missing.")
        if outcome is None and resolved_horizon != "ENTRY":
            reasons.append("Verified due-horizon observation is missing.")
        if (
            distribution is None
            and uncertain_distribution is None
            and resolved_horizon != "ENTRY"
        ):
            reasons.append("Complete benchmark distribution evidence is missing.")
        elif distribution is None and uncertain_distribution is not None:
            details = "; ".join(uncertain_distribution["uncertainty_reasons"])
            reasons.append(f"Benchmark distribution evidence is UNCERTAIN: {details}")
        if entry is not None and outcome is not None and distribution is not None:
            evidence_matches = (
                distribution.get("entry_observation_id")
                == entry.get("observation_id")
                and distribution.get("entry_observation_hash")
                == entry.get("record_hash")
                and distribution.get("outcome_observation_id")
                == outcome.get("observation_id")
                and distribution.get("outcome_observation_hash")
                == outcome.get("record_hash")
            )
            if not evidence_matches:
                reasons.append(
                    "Benchmark distribution evidence must link to the exact "
                    "price observations."
                )
        if accept_current_index_composition is not True:
            reasons.append(
                "Current index membership/weights approximation must be "
                "explicitly accepted."
            )
        if entry is not None and entry.get("benchmark_price_basis") != (
            "UNADJUSTED_CLOSE"
        ):
            reasons.append("ENTRY benchmark basis must be UNADJUSTED_CLOSE.")
        if outcome is not None and outcome.get("benchmark_price_basis") != (
            "UNADJUSTED_CLOSE"
        ):
            reasons.append("Outcome benchmark basis must be UNADJUSTED_CLOSE.")
        if reasons:
            return self.not_calculable(fill_id, resolved_horizon, reasons)

        assert entry is not None and outcome is not None and distribution is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_evidence_at = max(
            _as_datetime(entry["retrieved_at"]),
            _as_datetime(outcome["retrieved_at"]),
            _as_datetime(distribution["retrieved_at"]),
        )
        if calculated < latest_evidence_at:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["calculated_at cannot predate supporting evidence."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["calculated_at cannot be in the future."],
            )
        try:
            economics = _economics(
                entry=entry, outcome=outcome, distribution=distribution
            )
        except ValueError as error:
            return self.not_calculable(fill_id, resolved_horizon, [str(error)])
        result = {
            "schema_version": BENCHMARK_RETURN_SCHEMA_VERSION,
            "calculation_version": BENCHMARK_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(fill_id, resolved_horizon),
            "status": "CALCULATED",
            "scope": "S&P_500_GROSS_CASH_TOTAL_RETURN",
            "return_unit": "DECIMAL_STRING",
            "currency": "USD",
            "simulation_only": True,
            "benchmark_total_return_calculated": True,
            "relative_total_return_calculated": False,
            "alpha_calculated": False,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
            "learning_eligible": False,
            "calculated_at": calculated.isoformat(),
            "fill_id": fill_id,
            "entry_observation_id": entry["observation_id"],
            "entry_observation_hash": entry["record_hash"],
            "outcome_observation_id": outcome["observation_id"],
            "outcome_observation_hash": outcome["record_hash"],
            "benchmark_distribution_evidence_id": distribution["evidence_id"],
            "benchmark_distribution_evidence_hash": distribution["record_hash"],
            "benchmark_definition_sha256": distribution[
                "benchmark_definition_sha256"
            ],
            "source_evidence_sha256": distribution["source_evidence_sha256"],
            "order_id": outcome["order_id"],
            "decision_id": outcome["decision_id"],
            "portfolio_version": outcome["portfolio_version"],
            "ticker": outcome["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": outcome["horizon_label"],
            "benchmark_family": "S&P 500",
            "benchmark_ticker": "^GSPC",
            "benchmark_price_basis": "UNADJUSTED_CLOSE",
            "period_start_at": distribution["period_start_at"],
            "period_end_at": distribution["period_end_at"],
            "interval_notation": "(START,END]",
            "composition_approximation": COMPOSITION_POLICY,
            "composition_approximation_accepted": True,
            "strategy_version": outcome["strategy_version"],
            "model_versions": outcome["model_versions"],
            "git_revision": outcome["git_revision"],
            "formula": dict(BENCHMARK_RETURN_FORMULA),
            **economics,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        observations = {
            item["observation_id"]: item for item in self.observation_ledger.verify()
        }
        distributions = {
            item["evidence_id"]: item for item in self.distribution_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Benchmark-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Benchmark-return record {index} has been modified."
                )
            entry = observations.get(record.get("entry_observation_id"))
            outcome = observations.get(record.get("outcome_observation_id"))
            distribution = distributions.get(
                record.get("benchmark_distribution_evidence_id")
            )
            if entry is None or outcome is None or distribution is None:
                raise LedgerIntegrityError(
                    f"Benchmark-return record {index} lost supporting evidence."
                )
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            result_id = _result_id(fill_id, horizon)
            try:
                calculated = _as_datetime(record.get("calculated_at"))
                economics = _economics(
                    entry=entry, outcome=outcome, distribution=distribution
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Benchmark-return record {index} has invalid values."
                ) from error
            latest_evidence_at = max(
                _as_datetime(entry["retrieved_at"]),
                _as_datetime(outcome["retrieved_at"]),
                _as_datetime(distribution["retrieved_at"]),
            )
            linked = (
                entry.get("fill_id") == fill_id
                and entry.get("horizon") == "ENTRY"
                and outcome.get("fill_id") == fill_id
                and outcome.get("horizon") == horizon
                and distribution.get("fill_id") == fill_id
                and distribution.get("horizon") == horizon
                and distribution.get("completeness_status") == "COMPLETE"
                and distribution.get("entry_observation_id")
                == entry.get("observation_id")
                and distribution.get("entry_observation_hash")
                == entry.get("record_hash")
                and distribution.get("outcome_observation_id")
                == outcome.get("observation_id")
                and distribution.get("outcome_observation_hash")
                == outcome.get("record_hash")
                and record.get("entry_observation_hash") == entry.get("record_hash")
                and record.get("outcome_observation_hash") == outcome.get("record_hash")
                and record.get("benchmark_distribution_evidence_hash")
                == distribution.get("record_hash")
                and record.get("benchmark_definition_sha256")
                == distribution.get("benchmark_definition_sha256")
                and record.get("source_evidence_sha256")
                == distribution.get("source_evidence_sha256")
                and record.get("period_start_at")
                == distribution.get("period_start_at")
                and record.get("period_end_at") == distribution.get("period_end_at")
                and record.get("order_id") == outcome.get("order_id")
                and record.get("decision_id") == outcome.get("decision_id")
                and record.get("portfolio_version")
                == outcome.get("portfolio_version")
                and record.get("ticker") == outcome.get("ticker")
                and record.get("horizon_label") == outcome.get("horizon_label")
                and record.get("strategy_version") == outcome.get("strategy_version")
                and record.get("model_versions") == outcome.get("model_versions")
                and record.get("git_revision") == outcome.get("git_revision")
            )
            boundary = (
                record.get("schema_version") == BENCHMARK_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == BENCHMARK_RETURN_CALCULATION_VERSION
                and record.get("result_id") == result_id
                and result_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "S&P_500_GROSS_CASH_TOTAL_RETURN"
                and record.get("return_unit") == "DECIMAL_STRING"
                and record.get("currency") == "USD"
                and record.get("simulation_only") is True
                and record.get("benchmark_total_return_calculated") is True
                and record.get("relative_total_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("portfolio_performance_claim") is False
                and record.get("track_record_claim") is False
                and record.get("learning_eligible") is False
                and record.get("benchmark_family") == "S&P 500"
                and record.get("benchmark_ticker") == "^GSPC"
                and record.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and entry.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and outcome.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and record.get("interval_notation") == "(START,END]"
                and record.get("composition_approximation") == COMPOSITION_POLICY
                and record.get("composition_approximation_accepted") is True
                and record.get("formula") == BENCHMARK_RETURN_FORMULA
                and calculated >= latest_evidence_at
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            calculations = all(
                record.get(key) == value for key, value in economics.items()
            )
            if not linked or not boundary or not calculations:
                raise LedgerIntegrityError(
                    f"Benchmark-return record {index} violates its boundary."
                )
            seen_ids.add(result_id)
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
                comparable = {
                    key: value for key, value in existing.items() if key not in ignored
                }
                proposed = {
                    key: value for key, value in result.items() if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Benchmark-return result {result['result_id']} already exists."
                )
            material = {
                **result,
                "previous_hash": (
                    records[-1]["record_hash"] if records else GENESIS_HASH
                ),
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
