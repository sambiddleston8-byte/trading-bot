from __future__ import annotations

"""Evidence-gated CAGR from one verified time-weighted portfolio return."""

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
from core.performance.metric_readiness import PerformanceMetricReadinessGate
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


ANNUALIZED_RETURN_SCHEMA_VERSION = "1.0"
ANNUALIZED_RETURN_CALCULATION_VERSION = "gated-tropical-year-cagr-v1"
TROPICAL_YEAR_SECONDS = 31_556_952  # 365.2425 * 86,400, exact by policy.
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "gross_growth": "1 + verified_time_weighted_portfolio_return",
    "annualization_exponent": "31556952 / exact_elapsed_seconds",
    "cagr": "gross_growth ** annualization_exponent - 1",
    "year_basis": "FIXED_TROPICAL_YEAR_365.2425_CALENDAR_DAYS",
    "arithmetic_policy": "EXACT_INPUT_FRACTIONS_WITH_34_DIGIT_DECIMAL_POWER_PRESENTATION",
    "return_basis": "SIMULATED_GROSS_PRE_TAX_CASH_FLOW_NEUTRAL",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _result_id(portfolio_version: str, through_horizon: str) -> str:
    material = [portfolio_version, through_horizon, ANNUALIZED_RETURN_CALCULATION_VERSION]
    return "CAGR-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    funding: Mapping[str, Any],
    target: Mapping[str, Any],
    portfolio_return: Mapping[str, Any],
) -> dict[str, Any]:
    started = _as_datetime(funding["effective_at"])
    ended = _as_datetime(target["outcome_asset_price_effective_at"])
    elapsed = ended - started
    if elapsed.microseconds:
        raise ValueError("CAGR evidence timestamps must use whole-second precision")
    elapsed_seconds = int(elapsed.total_seconds())
    if elapsed_seconds <= 0:
        raise ValueError("CAGR elapsed time must be positive")
    twr = _fraction(
        portfolio_return["exact_fractions"]["time_weighted_portfolio_return"],
        "time-weighted portfolio return",
    )
    gross_growth = 1 + twr
    if gross_growth < 0:
        raise ValueError("Time-weighted return cannot imply negative gross growth")
    exponent = Fraction(TROPICAL_YEAR_SECONDS, elapsed_seconds)
    try:
        with localcontext(CONTEXT):
            decimal_growth = Decimal(gross_growth.numerator) / Decimal(gross_growth.denominator)
            decimal_exponent = Decimal(exponent.numerator) / Decimal(exponent.denominator)
            cagr = decimal_growth.__pow__(decimal_exponent) - Decimal(1)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("CAGR cannot be resolved from the verified return and elapsed time") from error
    with localcontext(CONTEXT):
        elapsed_years = Decimal(elapsed_seconds) / Decimal(TROPICAL_YEAR_SECONDS)
    return {
        "elapsed_seconds": elapsed_seconds,
        "elapsed_years": _decimal_string(elapsed_years),
        "time_weighted_portfolio_return": portfolio_return[
            "time_weighted_portfolio_return"
        ],
        "gross_growth": _decimal_string(decimal_growth),
        "annualization_exponent": _decimal_string(decimal_exponent),
        "compound_annual_growth_rate": _decimal_string(cagr),
        "exact_fractions": {
            "time_weighted_portfolio_return": _fraction_material(twr),
            "gross_growth": _fraction_material(gross_growth),
            "annualization_exponent": _fraction_material(exponent),
        },
    }


class AnnualizedPortfolioReturnLedger:
    """Append-only CAGR results that fail closed unless v2 evidence is ready."""

    def __init__(self, path: str | Path, readiness_gate: PerformanceMetricReadinessGate) -> None:
        self.path = Path(path)
        self.readiness_gate = readiness_gate

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Annualized-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank annualized-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at annualized-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Annualized-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(version: str, horizon: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": version,
            "through_horizon": horizon,
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "cagr_calculated": False,
            "risk_adjusted": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, version: str, horizon: str):
        readiness = self.readiness_gate.assess(
            portfolio_version=version, through_horizon=horizon
        )
        reasons = list(readiness["metrics"]["CAGR"]["reasons"])
        target = next(
            (
                item
                for item in self.readiness_gate.valuation_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("horizon") == horizon
            ),
            None,
        )
        funding = self.readiness_gate.valuation_ledger.funding_ledger.funding_for(version)
        result_id = readiness.get("verified_time_weighted_return_id")
        portfolio_return = next(
            (
                item
                for item in self.readiness_gate.portfolio_return_ledger.verify()
                if item.get("result_id") == result_id
            ),
            None,
        )
        if readiness["metrics"]["CAGR"]["status"] != "EVIDENCE_READY":
            reasons.extend(readiness["metrics"]["CAGR"]["reasons"])
        if target is None:
            reasons.append("Through-horizon valuation is missing.")
        if funding is None:
            reasons.append("Initial funding evidence is missing.")
        if portfolio_return is None:
            reasons.append("Verified time-weighted portfolio return is missing.")
        if target is not None and portfolio_return is not None:
            identity_fields = ("strategy_version", "model_versions", "git_revision")
            if any(
                portfolio_return.get(field) != target.get(field)
                for field in identity_fields
            ):
                reasons.append(
                    "Return and through-horizon valuation must share strategy, model and Git identity."
                )
            if funding is not None and any(
                funding.get(field) != target.get(field) for field in identity_fields
            ):
                reasons.append(
                    "Funding and through-horizon valuation must share strategy, model and Git identity."
                )
        return readiness, funding, target, portfolio_return, sorted(set(reasons))

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
        readiness, funding, target, portfolio_return, reasons = self._support(
            version, horizon
        )
        if reasons or funding is None or target is None or portfolio_return is None:
            return self.not_calculable(version, horizon, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            _as_datetime(target["calculated_at"]),
            _as_datetime(portfolio_return["calculated_at"]),
        )
        if calculated < latest:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot predate supporting evidence."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(funding, target, portfolio_return)
        except (KeyError, TypeError, ValueError) as error:
            return self.not_calculable(version, horizon, [str(error)])
        result = {
            "schema_version": ANNUALIZED_RETURN_SCHEMA_VERSION,
            "calculation_version": ANNUALIZED_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_PRE_TAX_COMPOUND_ANNUAL_GROWTH_RATE",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "funding_id": funding["funding_id"],
            "funding_record_hash": funding["record_hash"],
            "through_valuation_id": target["valuation_id"],
            "through_valuation_record_hash": target["record_hash"],
            "portfolio_return_id": portfolio_return["result_id"],
            "portfolio_return_record_hash": portfolio_return["record_hash"],
            "readiness_policy_version": readiness["policy_version"],
            "readiness_evidence_snapshot_sha256": readiness[
                "evidence_snapshot_sha256"
            ],
            "cagr_calculated": True,
            "annualized": True,
            "risk_adjusted": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": target["strategy_version"],
            "model_versions": target["model_versions"],
            "git_revision": target["git_revision"],
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
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Annualized-return ledger is modified at record {index}."
                )
            readiness, funding, target, expected_return, reasons = self._support(
                str(record.get("portfolio_version") or ""),
                str(record.get("through_horizon") or ""),
            )
            returns, pin_reasons = resolve_pinned_records(
                self.readiness_gate.portfolio_return_ledger.verify(),
                [record.get("portfolio_return_id")],
                [record.get("portfolio_return_record_hash")],
                id_field="result_id",
                label="portfolio return",
            )
            if (
                reasons
                or pin_reasons
                or funding is None
                or target is None
                or expected_return is None
                or returns != [expected_return]
            ):
                raise LedgerIntegrityError(
                    f"Annualized-return record {index} lost readiness support."
                )
            try:
                economics = _economics(funding, target, expected_return)
                calculated = _as_datetime(record["calculated_at"])
                latest = max(
                    _as_datetime(target["calculated_at"]),
                    _as_datetime(expected_return["calculated_at"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Annualized-return record {index} has invalid values."
                ) from error
            expected_id = _result_id(record["portfolio_version"], record["through_horizon"])
            boundary = (
                record.get("schema_version") == ANNUALIZED_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == ANNUALIZED_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_GROSS_PRE_TAX_COMPOUND_ANNUAL_GROWTH_RATE"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("funding_id") == funding["funding_id"]
                and record.get("funding_record_hash") == funding["record_hash"]
                and record.get("through_valuation_id") == target["valuation_id"]
                and record.get("through_valuation_record_hash") == target["record_hash"]
                and record.get("readiness_policy_version") == readiness["policy_version"]
                and record.get("readiness_evidence_snapshot_sha256")
                == readiness["evidence_snapshot_sha256"]
                and record.get("cagr_calculated") is True
                and record.get("annualized") is True
                and record.get("risk_adjusted") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("strategy_version") == target["strategy_version"]
                and record.get("model_versions") == target["model_versions"]
                and record.get("git_revision") == target["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Annualized-return record {index} violates its boundary."
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
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Annualized return {result['result_id']} already exists."
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
