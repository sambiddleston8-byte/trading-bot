from __future__ import annotations

"""Evidence-gated annualized Sortino ratio from a preregistered downside target."""

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
from core.performance.sortino_readiness import SortinoMetricReadinessGate


SORTINO_RATIO_SCHEMA_VERSION = "1.0"
SORTINO_RATIO_CALCULATION_VERSION = "preregistered-total-count-downside-sortino-v1"
ANNUALIZATION_SESSIONS = 252
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "target_relative_daily_return": "daily_portfolio_return - preregistered_minimum_acceptable_return",
    "mean_target_relative_daily_return": "sum(target_relative_daily_return) / total_observation_count",
    "downside_deviation": "sqrt(sum(min(0, target_relative_daily_return)^2) / total_observation_count)",
    "annualized_sortino_ratio": "sqrt(252) * mean_target_relative_daily_return / downside_deviation",
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


def _result_id(portfolio_version: str, horizon: str, policy_id: str) -> str:
    material = [
        portfolio_version,
        horizon,
        policy_id,
        SORTINO_RATIO_CALCULATION_VERSION,
    ]
    return "SORTINO-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _statistics(
    daily_returns: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    risk_free_by_daily_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not daily_returns:
        raise ValueError("At least one daily return is required")
    relative_returns: list[Fraction] = []
    path = []
    for daily in daily_returns:
        portfolio_value = _fraction(
            daily["exact_fractions"]["daily_portfolio_return"],
            "daily portfolio return",
        )
        target_value = Fraction(0)
        support: dict[str, Any] = {}
        if policy["target_basis"] == "MATCHED_DAILY_SOFR":
            risk_free = risk_free_by_daily_id.get(daily["result_id"])
            if risk_free is None or (
                risk_free.get("daily_portfolio_return_record_hash")
                != daily.get("record_hash")
            ):
                raise ValueError("Matched SOFR evidence is not pinned to its daily return")
            target_value = _fraction(
                risk_free["exact_fractions"]["daily_risk_free_return"],
                "daily risk-free return",
            )
            support = {
                "daily_risk_free_return_id": risk_free["result_id"],
                "daily_risk_free_return": _fraction_decimal(target_value),
            }
        relative = portfolio_value - target_value
        relative_returns.append(relative)
        path.append(
            {
                "daily_portfolio_return_id": daily["result_id"],
                "current_market_session_date": daily["current_market_session_date"],
                "daily_portfolio_return": _fraction_decimal(portfolio_value),
                "minimum_acceptable_return": _fraction_decimal(target_value),
                "target_relative_daily_return": _fraction_decimal(relative),
                "downside_shortfall": _fraction_decimal(min(Fraction(0), relative)),
                "exact_fractions": {
                    "daily_portfolio_return": _fraction_material(portfolio_value),
                    "minimum_acceptable_return": _fraction_material(target_value),
                    "target_relative_daily_return": _fraction_material(relative),
                    "downside_shortfall": _fraction_material(
                        min(Fraction(0), relative)
                    ),
                },
                **support,
            }
        )
    count = len(relative_returns)
    mean = sum(relative_returns, Fraction(0)) / count
    downside_sum_squares = sum(
        (min(Fraction(0), item) ** 2 for item in relative_returns), Fraction(0)
    )
    downside_variance = downside_sum_squares / count
    downside_count = sum(item < 0 for item in relative_returns)
    if downside_variance <= 0:
        raise ValueError("Sortino ratio is undefined when downside deviation is zero")
    with localcontext(CONTEXT):
        mean_decimal = Decimal(mean.numerator) / Decimal(mean.denominator)
        variance_decimal = (
            Decimal(downside_variance.numerator)
            / Decimal(downside_variance.denominator)
        )
        deviation = variance_decimal.sqrt()
        annualized = Decimal(ANNUALIZATION_SESSIONS).sqrt() * mean_decimal / deviation
    return {
        "observation_count": count,
        "downside_observation_count": downside_count,
        "mean_target_relative_daily_return": _fraction_decimal(mean),
        "downside_sum_of_squares": _fraction_decimal(downside_sum_squares),
        "downside_variance": _fraction_decimal(downside_variance),
        "downside_deviation": _decimal_string(deviation),
        "annualized_sortino_ratio": _decimal_string(annualized),
        "exact_fractions": {
            "mean_target_relative_daily_return": _fraction_material(mean),
            "downside_sum_of_squares": _fraction_material(downside_sum_squares),
            "downside_variance": _fraction_material(downside_variance),
        },
        "daily_target_relative_return_path": path,
    }


class SortinoRatioLedger:
    """Append-only Sortino results, impossible without preregistered readiness."""

    def __init__(self, path: str | Path, readiness_gate: SortinoMetricReadinessGate):
        self.path = Path(path)
        self.readiness_gate = readiness_gate

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Sortino-ratio ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank Sortino-ratio line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at Sortino-ratio line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Sortino-ratio line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(version: str, horizon: str, policy_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": version,
            "through_horizon": horizon,
            "downside_policy_id": policy_id,
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "sortino_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, version: str, horizon: str, policy_id: str):
        readiness = self.readiness_gate.assess(
            portfolio_version=version,
            through_horizon=horizon,
            downside_policy_id=policy_id,
        )
        reasons = list(readiness["reasons"])
        if readiness["status"] != "EVIDENCE_READY":
            reasons.append("Sortino preregistered-evidence readiness is not approved.")
        policy = next(
            (
                item
                for item in self.readiness_gate.downside_policy_ledger.verify()
                if item.get("policy_id") == policy_id
            ),
            None,
        )
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
        if policy is not None and target is not None and base_gate.daily_return_ledger:
            start = _as_datetime(policy["evaluation_not_before"])
            end = _as_datetime(target["outcome_asset_price_effective_at"])
            daily_returns = sorted(
                (
                    item
                    for item in base_gate.daily_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and item.get("daily_return_calculated") is True
                    and start <= _as_datetime(item["current_effective_at"]) <= end
                ),
                key=lambda item: _as_datetime(item["current_effective_at"]),
            )
        expected_ids = {item["result_id"] for item in daily_returns}
        risk_free_by_daily_id = {}
        if policy is not None and policy.get("target_basis") == "MATCHED_DAILY_SOFR":
            if self.readiness_gate.risk_free_return_ledger is not None:
                risk_free_by_daily_id = {
                    item["daily_portfolio_return_id"]: item
                    for item in self.readiness_gate.risk_free_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and item.get("daily_portfolio_return_id") in expected_ids
                }
        return (
            readiness,
            policy,
            target,
            daily_returns,
            risk_free_by_daily_id,
            sorted(set(reasons)),
        )

    def calculate(
        self,
        *,
        portfolio_version: str,
        through_horizon: str,
        downside_policy_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        policy_id = str(downside_policy_id or "").strip()
        readiness, policy, target, daily, risk_free, reasons = self._support(
            version, horizon, policy_id
        )
        if reasons or policy is None or target is None:
            return self.not_calculable(version, horizon, policy_id, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            [_as_datetime(item["calculated_at"]) for item in daily]
            + [
                _as_datetime(item["calculated_at"])
                for item in risk_free.values()
            ]
        )
        if calculated < latest:
            return self.not_calculable(
                version, horizon, policy_id, ["calculated_at cannot predate daily returns."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, policy_id, ["calculated_at cannot be in the future."]
            )
        try:
            statistics = _statistics(daily, policy, risk_free)
        except (KeyError, TypeError, ValueError) as error:
            return self.not_calculable(version, horizon, policy_id, [str(error)])
        risk_free_values = list(risk_free.values())
        result = {
            "schema_version": SORTINO_RATIO_SCHEMA_VERSION,
            "calculation_version": SORTINO_RATIO_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon, policy_id),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SORTINO_RATIO",
            "simulation_only": True,
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_effective_at": target["outcome_asset_price_effective_at"],
            "downside_policy_id": policy_id,
            "downside_policy_record_hash": policy["record_hash"],
            "target_basis": policy["target_basis"],
            "evaluation_not_before": policy["evaluation_not_before"],
            "readiness_policy_version": readiness["policy_version"],
            "readiness_evidence_snapshot_sha256": readiness["evidence_snapshot_sha256"],
            "supporting_daily_return_ids": [item["result_id"] for item in daily],
            "supporting_daily_return_hashes": [item["record_hash"] for item in daily],
            "supporting_risk_free_return_ids": [item["result_id"] for item in risk_free_values],
            "supporting_risk_free_return_hashes": [item["record_hash"] for item in risk_free_values],
            "sortino_calculated": True,
            "annualized": True,
            "risk_adjusted": True,
            "sharpe_calculated": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": daily[0]["strategy_version"],
            "model_versions": daily[0]["model_versions"],
            "git_revision": daily[0]["git_revision"],
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
                raise LedgerIntegrityError(f"Sortino-ratio record {index} has been modified.")
            readiness, policy, target, expected_daily, expected_risk_free, reasons = self._support(
                str(record.get("portfolio_version") or ""),
                str(record.get("through_horizon") or ""),
                str(record.get("downside_policy_id") or ""),
            )
            daily, daily_reasons = resolve_pinned_records(
                self.readiness_gate.metric_readiness_gate.daily_return_ledger.verify(),
                record.get("supporting_daily_return_ids"),
                record.get("supporting_daily_return_hashes"),
                id_field="result_id",
                label="daily portfolio return",
            )
            risk_free_source = (
                self.readiness_gate.risk_free_return_ledger.verify()
                if self.readiness_gate.risk_free_return_ledger is not None
                else []
            )
            risk_free, risk_free_reasons = resolve_pinned_records(
                risk_free_source,
                record.get("supporting_risk_free_return_ids"),
                record.get("supporting_risk_free_return_hashes"),
                id_field="result_id",
                label="daily risk-free return",
            )
            expected_risk_free_values = list(expected_risk_free.values())
            if (
                reasons
                or daily_reasons
                or risk_free_reasons
                or policy is None
                or target is None
                or list(daily) != expected_daily
                or list(risk_free) != expected_risk_free_values
            ):
                raise LedgerIntegrityError(
                    f"Sortino-ratio record {index} lost readiness support."
                )
            try:
                statistics = _statistics(daily, policy, expected_risk_free)
                calculated = _as_datetime(record["calculated_at"])
                latest = max(
                    [_as_datetime(item["calculated_at"]) for item in daily]
                    + [
                        _as_datetime(item["calculated_at"])
                        for item in expected_risk_free.values()
                    ]
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Sortino-ratio record {index} has invalid values."
                ) from error
            expected_id = _result_id(
                record["portfolio_version"],
                record["through_horizon"],
                record["downside_policy_id"],
            )
            boundary = (
                record.get("schema_version") == SORTINO_RATIO_SCHEMA_VERSION
                and record.get("calculation_version") == SORTINO_RATIO_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SORTINO_RATIO"
                and record.get("simulation_only") is True
                and record.get("through_effective_at") == target["outcome_asset_price_effective_at"]
                and record.get("downside_policy_record_hash") == policy["record_hash"]
                and record.get("target_basis") == policy["target_basis"]
                and record.get("evaluation_not_before") == policy["evaluation_not_before"]
                and record.get("readiness_policy_version") == readiness["policy_version"]
                and record.get("readiness_evidence_snapshot_sha256") == readiness["evidence_snapshot_sha256"]
                and record.get("sortino_calculated") is True
                and record.get("annualized") is True
                and record.get("risk_adjusted") is True
                and record.get("sharpe_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("strategy_version") == daily[0]["strategy_version"]
                and record.get("model_versions") == daily[0]["model_versions"]
                and record.get("git_revision") == daily[0]["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in statistics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Sortino-ratio record {index} violates its boundary."
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
                raise LedgerIntegrityError(f"Sortino result {result['result_id']} already exists.")
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
