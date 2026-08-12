from __future__ import annotations

"""Gated volatility and drawdown from exact cash-flow-neutral daily returns."""

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
from core.performance.metric_readiness import PerformanceMetricReadinessGate
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


DAILY_RISK_SCHEMA_VERSION = "1.0"
DAILY_RISK_CALCULATION_VERSION = "gated-sample-volatility-wealth-drawdown-v1"
ANNUALIZATION_SESSIONS = 252
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "daily_mean_return": "sum(daily_return) / observation_count",
    "sample_daily_variance": "sum((daily_return - daily_mean_return)^2) / (observation_count - 1)",
    "sample_daily_volatility": "sqrt(sample_daily_variance)",
    "annualized_volatility": "sqrt(sample_daily_variance * 252)",
    "wealth_index": "cumulative_product(1 + daily_return), starting at 1",
    "drawdown": "wealth_index / running_peak_wealth_index - 1",
    "maximum_drawdown": "minimum(drawdown), retained as a non-positive decimal",
    "return_basis": "SIMULATED_GROSS_PRE_TAX_CASH_FLOW_NEUTRAL",
    "annualization_policy": "FIXED_252_REGULAR_SESSIONS_NOT_CALENDAR_TIME",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _fraction_decimal(value: Fraction) -> str:
    with localcontext(CONTEXT):
        return _decimal_string(Decimal(value.numerator) / Decimal(value.denominator))


def _sqrt(value: Fraction) -> str:
    if value < 0:
        raise ValueError("Variance cannot be negative")
    with localcontext(CONTEXT):
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return _decimal_string(decimal.sqrt())


def _result_id(portfolio_version: str, through_horizon: str) -> str:
    material = [portfolio_version, through_horizon, DAILY_RISK_CALCULATION_VERSION]
    return "DRISK-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _statistics(returns: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(returns) < 2:
        raise ValueError("At least two daily returns are required")
    identity_fields = ("strategy_version", "model_versions", "git_revision")
    if any(
        any(item.get(field) != returns[0].get(field) for field in identity_fields)
        for item in returns
    ):
        raise ValueError("Supporting daily returns span multiple strategy, model or Git identities")
    values = [
        _fraction(item["exact_fractions"]["daily_portfolio_return"], "daily return")
        for item in returns
    ]
    mean = sum(values, Fraction(0)) / len(values)
    variance = sum(((item - mean) ** 2 for item in values), Fraction(0)) / (len(values) - 1)
    daily_volatility = _sqrt(variance)
    annualized_variance = variance * ANNUALIZATION_SESSIONS
    annualized_volatility = _sqrt(annualized_variance)

    wealth = Fraction(1)
    peak = Fraction(1)
    maximum_drawdown = Fraction(0)
    peak_date = returns[0]["previous_market_session_date"]
    maximum_peak_date = peak_date
    trough_date = peak_date
    path = []
    for item, daily_return in zip(returns, values):
        growth = 1 + daily_return
        if growth <= 0:
            raise ValueError("Daily return wealth growth must remain positive")
        wealth *= growth
        if wealth > peak:
            peak = wealth
            peak_date = item["current_market_session_date"]
        drawdown = wealth / peak - 1
        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
            maximum_peak_date = peak_date
            trough_date = item["current_market_session_date"]
        path.append(
            {
                "daily_return_id": item["result_id"],
                "market_session_date": item["current_market_session_date"],
                "wealth_index": _fraction_decimal(wealth),
                "running_peak_wealth_index": _fraction_decimal(peak),
                "drawdown": _fraction_decimal(drawdown),
                "exact_fractions": {
                    "wealth_index": _fraction_material(wealth),
                    "running_peak_wealth_index": _fraction_material(peak),
                    "drawdown": _fraction_material(drawdown),
                },
            }
        )
    recovered = wealth >= peak if maximum_drawdown < 0 else True
    exact = {
        "daily_mean_return": mean,
        "sample_daily_variance": variance,
        "maximum_drawdown": maximum_drawdown,
        "ending_wealth_index": wealth,
        "ending_running_peak_wealth_index": peak,
    }
    return {
        "observation_count": len(values),
        "daily_mean_return": _fraction_decimal(mean),
        "sample_daily_variance": _fraction_decimal(variance),
        "sample_daily_volatility": daily_volatility,
        "annualized_volatility": annualized_volatility,
        "maximum_drawdown": _fraction_decimal(maximum_drawdown),
        "maximum_drawdown_peak_date": maximum_peak_date,
        "maximum_drawdown_trough_date": trough_date,
        "maximum_drawdown_recovered_by_end": recovered,
        "ending_wealth_index": _fraction_decimal(wealth),
        "ending_running_peak_wealth_index": _fraction_decimal(peak),
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
        "wealth_path": path,
    }


class DailyRiskStatisticsLedger:
    """Append-only risk statistics, impossible without v2 readiness approval."""

    def __init__(self, path: str | Path, readiness_gate: PerformanceMetricReadinessGate) -> None:
        self.path = Path(path)
        self.readiness_gate = readiness_gate

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-risk-statistics ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank daily-risk-statistics line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-risk-statistics line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-risk-statistics line {line_number} is not an object."
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
            "volatility_calculated": False,
            "maximum_drawdown_calculated": False,
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(self, version: str, horizon: str):
        readiness = self.readiness_gate.assess(portfolio_version=version, through_horizon=horizon)
        reasons = []
        for metric in ("VOLATILITY", "MAXIMUM_DRAWDOWN"):
            result = readiness["metrics"][metric]
            if result["status"] != "EVIDENCE_READY":
                reasons.extend(result["reasons"])
        target = next(
            (
                item for item in self.readiness_gate.valuation_ledger.verify()
                if item.get("portfolio_version") == version and item.get("horizon") == horizon
            ),
            None,
        )
        returns = []
        if target is not None and self.readiness_gate.daily_return_ledger is not None:
            target_at = _as_datetime(target["outcome_asset_price_effective_at"])
            returns = sorted(
                (
                    item for item in self.readiness_gate.daily_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and _as_datetime(item["current_effective_at"]) <= target_at
                ),
                key=lambda item: _as_datetime(item["current_effective_at"]),
            )
        return readiness, target, returns, sorted(set(reasons))

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
        readiness, target, returns, reasons = self._support(version, horizon)
        if reasons or target is None:
            return self.not_calculable(version, horizon, reasons or ["Through-horizon valuation is missing."])
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(_as_datetime(item["calculated_at"]) for item in returns)
        if calculated < latest:
            return self.not_calculable(version, horizon, ["calculated_at cannot predate daily returns."])
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(version, horizon, ["calculated_at cannot be in the future."])
        try:
            statistics = _statistics(returns)
        except (TypeError, ValueError) as error:
            return self.not_calculable(version, horizon, [str(error)])
        result = {
            "schema_version": DAILY_RISK_SCHEMA_VERSION,
            "calculation_version": DAILY_RISK_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_PRE_TAX_DAILY_VOLATILITY_AND_DRAWDOWN",
            "simulation_only": True,
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_effective_at": target["outcome_asset_price_effective_at"],
            "readiness_policy_version": readiness["policy_version"],
            "readiness_evidence_snapshot_sha256": readiness["evidence_snapshot_sha256"],
            "supporting_daily_return_ids": [item["result_id"] for item in returns],
            "supporting_daily_return_hashes": [item["record_hash"] for item in returns],
            "volatility_calculated": True,
            "maximum_drawdown_calculated": True,
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": returns[0]["strategy_version"],
            "model_versions": returns[0]["model_versions"],
            "git_revision": returns[0]["git_revision"],
            **statistics,
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
                raise LedgerIntegrityError(f"Daily-risk-statistics ledger is modified at record {index}.")
            readiness, target, expected_returns, reasons = self._support(
                str(record.get("portfolio_version") or ""), str(record.get("through_horizon") or "")
            )
            returns, pin_reasons = resolve_pinned_records(
                self.readiness_gate.daily_return_ledger.verify(),
                record.get("supporting_daily_return_ids"),
                record.get("supporting_daily_return_hashes"),
                id_field="result_id",
                label="daily-return",
            )
            if reasons or pin_reasons or target is None or list(returns) != expected_returns:
                raise LedgerIntegrityError(f"Daily-risk-statistics record {index} lost readiness support.")
            try:
                statistics = _statistics(returns)
                calculated = _as_datetime(record.get("calculated_at"))
                latest = max(_as_datetime(item["calculated_at"]) for item in returns)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Daily-risk-statistics record {index} has invalid values.") from error
            expected_id = _result_id(record["portfolio_version"], record["through_horizon"])
            boundary = (
                record.get("schema_version") == DAILY_RISK_SCHEMA_VERSION
                and record.get("calculation_version") == DAILY_RISK_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_GROSS_PRE_TAX_DAILY_VOLATILITY_AND_DRAWDOWN"
                and record.get("simulation_only") is True
                and record.get("through_effective_at") == target["outcome_asset_price_effective_at"]
                and record.get("readiness_policy_version") == readiness["policy_version"]
                and record.get("readiness_evidence_snapshot_sha256") == readiness["evidence_snapshot_sha256"]
                and record.get("volatility_calculated") is True
                and record.get("maximum_drawdown_calculated") is True
                and record.get("sharpe_calculated") is False
                and record.get("sortino_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == returns[0]["strategy_version"]
                and record.get("model_versions") == returns[0]["model_versions"]
                and record.get("git_revision") == returns[0]["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in statistics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Daily-risk-statistics record {index} violates its boundary.")
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
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                if allow_existing and ({k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}):
                    return existing
                raise LedgerIntegrityError(f"Daily risk result {result['result_id']} already exists.")
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
