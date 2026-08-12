from __future__ import annotations

"""Evidence-gated annualized Sharpe ratio from exact paired daily returns."""

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
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)
from core.performance.sharpe_readiness import SharpeMetricReadinessGate


SHARPE_RATIO_SCHEMA_VERSION = "1.0"
SHARPE_RATIO_CALCULATION_VERSION = "gated-daily-excess-sample-sharpe-v1"
ANNUALIZATION_SESSIONS = 252
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "daily_excess_return": "daily_portfolio_return - matched_daily_risk_free_return",
    "sample_mean_daily_excess_return": "sum(daily_excess_return) / observation_count",
    "sample_daily_excess_variance": "sum((daily_excess_return - mean)^2) / (observation_count - 1)",
    "sample_daily_excess_volatility": "sqrt(sample_daily_excess_variance)",
    "annualized_sharpe_ratio": "sqrt(252) * sample_mean_daily_excess_return / sample_daily_excess_volatility",
    "annualization_policy": "FIXED_252_REGULAR_SESSIONS",
    "arithmetic_policy": "EXACT_RATIONAL_INPUTS_WITH_34_DIGIT_DECIMAL_ROOT_AND_RATIO",
    "return_basis": "SIMULATED_GROSS_PRE_TAX_CASH_FLOW_NEUTRAL",
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


def _result_id(portfolio_version: str, through_horizon: str) -> str:
    material = [portfolio_version, through_horizon, SHARPE_RATIO_CALCULATION_VERSION]
    return "SHARPE-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _statistics(
    daily_returns: Sequence[Mapping[str, Any]],
    risk_free_returns: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(daily_returns) < 2 or len(daily_returns) != len(risk_free_returns):
        raise ValueError("At least two exactly paired daily returns are required")
    identity_fields = ("strategy_version", "model_versions", "git_revision")
    if any(
        any(item.get(field) != daily_returns[0].get(field) for field in identity_fields)
        for item in [*daily_returns, *risk_free_returns]
    ):
        raise ValueError("Paired returns span multiple strategy, model or Git identities")
    excess_returns = []
    path = []
    for daily, risk_free in zip(daily_returns, risk_free_returns):
        if (
            risk_free.get("daily_portfolio_return_id") != daily.get("result_id")
            or risk_free.get("daily_portfolio_return_record_hash")
            != daily.get("record_hash")
        ):
            raise ValueError("Risk-free return is not pinned to its paired daily return")
        portfolio_value = _fraction(
            daily["exact_fractions"]["daily_portfolio_return"],
            "daily portfolio return",
        )
        risk_free_value = _fraction(
            risk_free["exact_fractions"]["daily_risk_free_return"],
            "daily risk-free return",
        )
        excess = portfolio_value - risk_free_value
        excess_returns.append(excess)
        path.append(
            {
                "daily_portfolio_return_id": daily["result_id"],
                "daily_risk_free_return_id": risk_free["result_id"],
                "current_market_session_date": daily[
                    "current_market_session_date"
                ],
                "daily_portfolio_return": _fraction_decimal(portfolio_value),
                "daily_risk_free_return": _fraction_decimal(risk_free_value),
                "daily_excess_return": _fraction_decimal(excess),
                "exact_fractions": {
                    "daily_portfolio_return": _fraction_material(portfolio_value),
                    "daily_risk_free_return": _fraction_material(risk_free_value),
                    "daily_excess_return": _fraction_material(excess),
                },
            }
        )
    mean = sum(excess_returns, Fraction(0)) / len(excess_returns)
    variance = sum(
        ((item - mean) ** 2 for item in excess_returns), Fraction(0)
    ) / (len(excess_returns) - 1)
    if variance <= 0:
        raise ValueError("Sharpe ratio is undefined when sample excess-return variance is zero")
    with localcontext(CONTEXT):
        mean_decimal = Decimal(mean.numerator) / Decimal(mean.denominator)
        variance_decimal = Decimal(variance.numerator) / Decimal(variance.denominator)
        volatility = variance_decimal.sqrt()
        annualized = Decimal(ANNUALIZATION_SESSIONS).sqrt() * mean_decimal / volatility
    return {
        "observation_count": len(excess_returns),
        "sample_mean_daily_excess_return": _fraction_decimal(mean),
        "sample_daily_excess_variance": _fraction_decimal(variance),
        "sample_daily_excess_volatility": _decimal_string(volatility),
        "annualized_sharpe_ratio": _decimal_string(annualized),
        "exact_fractions": {
            "sample_mean_daily_excess_return": _fraction_material(mean),
            "sample_daily_excess_variance": _fraction_material(variance),
        },
        "daily_excess_return_path": path,
    }


class SharpeRatioLedger:
    """Append-only Sharpe results, impossible without complete paired readiness."""

    def __init__(self, path: str | Path, readiness_gate: SharpeMetricReadinessGate):
        self.path = Path(path)
        self.readiness_gate = readiness_gate

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Sharpe-ratio ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank Sharpe-ratio line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at Sharpe-ratio line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Sharpe-ratio line {line_number} is not an object."
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
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, version: str, horizon: str):
        readiness = self.readiness_gate.assess(
            portfolio_version=version, through_horizon=horizon
        )
        reasons = list(readiness["reasons"])
        if readiness["status"] != "EVIDENCE_READY":
            reasons.append("Sharpe paired-evidence readiness is not approved.")
        base_gate = self.readiness_gate.metric_readiness_gate
        target = next(
            (
                item
                for item in base_gate.valuation_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("horizon") == horizon
            ),
            None,
        )
        daily_returns = []
        if target is not None and base_gate.daily_return_ledger is not None:
            target_at = _as_datetime(target["outcome_asset_price_effective_at"])
            daily_returns = sorted(
                (
                    item
                    for item in base_gate.daily_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and item.get("daily_return_calculated") is True
                    and _as_datetime(item["current_effective_at"]) <= target_at
                ),
                key=lambda item: _as_datetime(item["current_effective_at"]),
            )
        by_daily_id = {
            item["daily_portfolio_return_id"]: item
            for item in self.readiness_gate.risk_free_return_ledger.verify()
            if item.get("portfolio_version") == version
            and item.get("daily_risk_free_return_calculated") is True
        }
        risk_free_returns = [
            by_daily_id[item["result_id"]]
            for item in daily_returns
            if item["result_id"] in by_daily_id
        ]
        if len(risk_free_returns) != len(daily_returns):
            reasons.append("Complete ordered paired returns are unavailable.")
        return readiness, target, daily_returns, risk_free_returns, sorted(set(reasons))

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
        readiness, target, daily_returns, risk_free_returns, reasons = self._support(
            version, horizon
        )
        if reasons or target is None:
            return self.not_calculable(version, horizon, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            [_as_datetime(item["calculated_at"]) for item in daily_returns]
            + [_as_datetime(item["calculated_at"]) for item in risk_free_returns]
        )
        if calculated < latest:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot predate paired returns."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot be in the future."]
            )
        try:
            statistics = _statistics(daily_returns, risk_free_returns)
        except (KeyError, TypeError, ValueError) as error:
            return self.not_calculable(version, horizon, [str(error)])
        result = {
            "schema_version": SHARPE_RATIO_SCHEMA_VERSION,
            "calculation_version": SHARPE_RATIO_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SHARPE_RATIO",
            "simulation_only": True,
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_effective_at": target["outcome_asset_price_effective_at"],
            "readiness_policy_version": readiness["policy_version"],
            "readiness_evidence_snapshot_sha256": readiness[
                "evidence_snapshot_sha256"
            ],
            "supporting_daily_return_ids": [item["result_id"] for item in daily_returns],
            "supporting_daily_return_hashes": [item["record_hash"] for item in daily_returns],
            "supporting_risk_free_return_ids": [
                item["result_id"] for item in risk_free_returns
            ],
            "supporting_risk_free_return_hashes": [
                item["record_hash"] for item in risk_free_returns
            ],
            "source_backfilled": any(
                item["source_backfilled"] for item in risk_free_returns
            ),
            "sharpe_calculated": True,
            "annualized": True,
            "risk_adjusted": True,
            "sortino_calculated": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": daily_returns[0]["strategy_version"],
            "model_versions": daily_returns[0]["model_versions"],
            "git_revision": daily_returns[0]["git_revision"],
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
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Sharpe-ratio record {index} has been modified.")
            readiness, target, expected_daily, expected_risk_free, reasons = self._support(
                str(record.get("portfolio_version") or ""),
                str(record.get("through_horizon") or ""),
            )
            daily_returns, daily_reasons = resolve_pinned_records(
                self.readiness_gate.metric_readiness_gate.daily_return_ledger.verify(),
                record.get("supporting_daily_return_ids"),
                record.get("supporting_daily_return_hashes"),
                id_field="result_id",
                label="daily portfolio return",
            )
            risk_free_returns, risk_free_reasons = resolve_pinned_records(
                self.readiness_gate.risk_free_return_ledger.verify(),
                record.get("supporting_risk_free_return_ids"),
                record.get("supporting_risk_free_return_hashes"),
                id_field="result_id",
                label="daily risk-free return",
            )
            if (
                reasons
                or daily_reasons
                or risk_free_reasons
                or target is None
                or list(daily_returns) != expected_daily
                or list(risk_free_returns) != expected_risk_free
            ):
                raise LedgerIntegrityError(
                    f"Sharpe-ratio record {index} lost readiness support."
                )
            try:
                statistics = _statistics(daily_returns, risk_free_returns)
                calculated = _as_datetime(record["calculated_at"])
                latest = max(
                    [_as_datetime(item["calculated_at"]) for item in daily_returns]
                    + [_as_datetime(item["calculated_at"]) for item in risk_free_returns]
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Sharpe-ratio record {index} has invalid values."
                ) from error
            expected_id = _result_id(record["portfolio_version"], record["through_horizon"])
            boundary = (
                record.get("schema_version") == SHARPE_RATIO_SCHEMA_VERSION
                and record.get("calculation_version") == SHARPE_RATIO_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SHARPE_RATIO"
                and record.get("simulation_only") is True
                and record.get("through_effective_at")
                == target["outcome_asset_price_effective_at"]
                and record.get("readiness_policy_version") == readiness["policy_version"]
                and record.get("readiness_evidence_snapshot_sha256")
                == readiness["evidence_snapshot_sha256"]
                and record.get("source_backfilled")
                == any(item["source_backfilled"] for item in risk_free_returns)
                and record.get("sharpe_calculated") is True
                and record.get("annualized") is True
                and record.get("risk_adjusted") is True
                and record.get("sortino_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("strategy_version") == daily_returns[0]["strategy_version"]
                and record.get("model_versions") == daily_returns[0]["model_versions"]
                and record.get("git_revision") == daily_returns[0]["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in statistics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Sharpe-ratio record {index} violates its boundary."
                )
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
                raise LedgerIntegrityError(f"Sharpe result {result['result_id']} already exists.")
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
