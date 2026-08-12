from __future__ import annotations

"""Exact one-period returns between consecutive daily portfolio valuations."""

from datetime import date, datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.daily_portfolio_valuation import DailyPortfolioValuationLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


DAILY_PORTFOLIO_RETURN_SCHEMA_VERSION = "1.0"
DAILY_PORTFOLIO_RETURN_CALCULATION_VERSION = "boundary-flow-neutral-daily-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "current_pre_flow_equity": "current_post_flow_equity - current_boundary_external_cash_flow",
    "daily_return": "current_pre_flow_equity / previous_post_flow_equity - 1",
    "cash_flow_timing": "EXACTLY_AT_CURRENT_REGULAR_SESSION_CLOSE_AFTER_RETURN_MEASUREMENT",
    "session_adjacency": (
        "NEXT_WEEKDAY_REGULAR_SESSION_WITH_WEEKENDS_ONLY; HOLIDAYS_BLOCKED_PENDING_CALENDAR_EVIDENCE"
    ),
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _result_id(portfolio_version: str, current_valuation_id: str) -> str:
    material = [portfolio_version, current_valuation_id, DAILY_PORTFOLIO_RETURN_CALCULATION_VERSION]
    return "DPRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _regular_session_successor(previous: str, current: str) -> bool:
    """Fail-closed weekday adjacency; exchange holidays need future evidence."""
    try:
        start = date.fromisoformat(previous)
        end = date.fromisoformat(current)
    except ValueError:
        return False
    candidate = start + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return end == candidate and end.weekday() < 5


def _interval_reasons(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    boundary_flows: Sequence[Mapping[str, Any]],
) -> list[str]:
    reasons = []
    previous_at = _as_datetime(previous["effective_at"])
    current_at = _as_datetime(current["effective_at"])
    if current_at <= previous_at:
        reasons.append("Daily valuation effective times must strictly increase.")
    if not _regular_session_successor(
        str(previous.get("market_session_date") or ""),
        str(current.get("market_session_date") or ""),
    ):
        reasons.append(
            "Valuations must be adjacent regular weekday sessions; holiday intervals require calendar evidence."
        )
    identity_fields = ("strategy_version", "model_versions", "git_revision")
    if any(previous.get(field) != current.get(field) for field in identity_fields):
        reasons.append("Consecutive valuations must share strategy, model and Git identity.")
    if any(_as_datetime(item["effective_at"]) != current_at for item in boundary_flows):
        reasons.append("Boundary cash flows must be timestamped exactly at the current valuation close.")
    if any(
        any(item.get(field) != current.get(field) for field in identity_fields)
        for item in boundary_flows
    ):
        reasons.append("Boundary cash flows must share valuation identity.")
    current_flow_ids = set(current.get("supporting_cash_flow_ids") or [])
    previous_flow_ids = set(previous.get("supporting_cash_flow_ids") or [])
    if {item["flow_id"] for item in boundary_flows} != current_flow_ids - previous_flow_ids:
        reasons.append("Boundary cash flows must reconcile with consecutive valuation support.")
    return reasons


def _economics(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    boundary_flows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    previous_equity = _fraction(previous["exact_fractions"]["total_equity"], "previous equity")
    current_post_flow = _fraction(current["exact_fractions"]["total_equity"], "current equity")
    boundary_flow = sum(
        (_fraction(item["exact_signed_amount"], "boundary cash flow") for item in boundary_flows),
        Fraction(0),
    )
    current_pre_flow = current_post_flow - boundary_flow
    if previous_equity <= 0 or current_pre_flow <= 0 or current_post_flow <= 0:
        raise ValueError("Daily pre-flow and post-flow equity must remain positive")
    daily_return = current_pre_flow / previous_equity - 1
    exact = {
        "previous_post_flow_equity": previous_equity,
        "current_boundary_external_cash_flow": boundary_flow,
        "current_pre_flow_equity": current_pre_flow,
        "current_post_flow_equity": current_post_flow,
        "daily_portfolio_return": daily_return,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class DailyPortfolioReturnLedger:
    """Append-only daily returns; no annualization or risk metric is calculated."""

    def __init__(
        self,
        path: str | Path,
        valuation_ledger: DailyPortfolioValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-portfolio-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank daily-portfolio-return line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-portfolio-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-portfolio-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(portfolio_version: str, current_valuation_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "current_valuation_id": str(current_valuation_id),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "daily_return_calculated": False,
            "annualized": False,
            "risk_adjusted": False,
            "performance_metric_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(self, portfolio_version: str, current_valuation_id: str):
        values = sorted(
            (
                item for item in self.valuation_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        current_index = next(
            (index for index, item in enumerate(values) if item.get("valuation_id") == current_valuation_id),
            None,
        )
        reasons = []
        if current_index is None:
            reasons.append("Verified current daily portfolio valuation is missing.")
            return None, None, [], reasons
        if current_index == 0:
            reasons.append("A previous daily portfolio valuation is required.")
            return None, values[current_index], [], reasons
        previous, current = values[current_index - 1], values[current_index]
        previous_at = _as_datetime(previous["effective_at"])
        current_at = _as_datetime(current["effective_at"])
        all_flows = self.cash_flow_ledger.verify()
        boundary_flows = sorted(
            (
                item for item in all_flows
                if item.get("portfolio_version") == portfolio_version
                and previous_at < _as_datetime(item["effective_at"]) <= current_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        reasons.extend(_interval_reasons(previous, current, boundary_flows))
        return previous, current, boundary_flows, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        current_valuation_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        current_id = str(current_valuation_id or "").strip()
        previous, current, flows, reasons = self._support(version, current_id)
        if reasons:
            return self.not_calculable(version, current_id, reasons)
        assert previous is not None and current is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            [_as_datetime(previous["calculated_at"]), _as_datetime(current["calculated_at"])]
            + [_as_datetime(item["recorded_at"]) for item in flows]
        )
        if calculated < latest:
            return self.not_calculable(version, current_id, ["calculated_at cannot predate support."])
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(version, current_id, ["calculated_at cannot be in the future."])
        try:
            economics = _economics(previous, current, flows)
        except (TypeError, ValueError) as error:
            return self.not_calculable(version, current_id, [str(error)])
        result = {
            "schema_version": DAILY_PORTFOLIO_RETURN_SCHEMA_VERSION,
            "calculation_version": DAILY_PORTFOLIO_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, current_id),
            "status": "CALCULATED",
            "scope": "SIMULATED_ONE_PERIOD_CASH_FLOW_NEUTRAL_DAILY_RETURN",
            "simulation_only": True,
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "previous_market_session_date": previous["market_session_date"],
            "current_market_session_date": current["market_session_date"],
            "previous_effective_at": previous["effective_at"],
            "current_effective_at": current["effective_at"],
            "regular_session_interval_verified": True,
            "exchange_holiday_calendar_evidence_used": False,
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current["valuation_id"],
            "current_valuation_record_hash": current["record_hash"],
            "supporting_boundary_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_boundary_cash_flow_hashes": [item["record_hash"] for item in flows],
            "gross_pre_tax_basis": True,
            "broker_cash_reconciled": False,
            "daily_return_calculated": True,
            "annualized": False,
            "risk_adjusted": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": current["strategy_version"],
            "model_versions": current["model_versions"],
            "git_revision": current["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(self, record: Mapping[str, Any]):
        valuations = self.valuation_ledger.verify()
        previous, previous_reasons = resolve_pinned_records(
            valuations,
            [record.get("previous_valuation_id")],
            [record.get("previous_valuation_record_hash")],
            id_field="valuation_id",
            label="previous-valuation",
        )
        current, current_reasons = resolve_pinned_records(
            valuations,
            [record.get("current_valuation_id")],
            [record.get("current_valuation_record_hash")],
            id_field="valuation_id",
            label="current-valuation",
        )
        flows, flow_reasons = resolve_pinned_records(
            self.cash_flow_ledger.verify(),
            record.get("supporting_boundary_cash_flow_ids"),
            record.get("supporting_boundary_cash_flow_hashes"),
            id_field="flow_id",
            label="boundary-cash-flow",
        )
        return (
            previous[0] if previous else None,
            current[0] if current else None,
            list(flows),
            [*previous_reasons, *current_reasons, *flow_reasons],
        )

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Daily-portfolio-return chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Daily-portfolio-return record {index} has been modified.")
            previous, current, flows, reasons = self._pinned_support(record)
            if reasons or previous is None or current is None:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-return record {index} lost supporting evidence."
                )
            try:
                interval_reasons = _interval_reasons(previous, current, flows)
                if interval_reasons:
                    raise ValueError(" ".join(interval_reasons))
                economics = _economics(previous, current, flows)
                calculated = _as_datetime(record.get("calculated_at"))
                latest = max(
                    [_as_datetime(previous["calculated_at"]), _as_datetime(current["calculated_at"])]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-return record {index} has invalid values."
                ) from error
            expected_id = _result_id(record.get("portfolio_version", ""), current["valuation_id"])
            boundary = (
                record.get("schema_version") == DAILY_PORTFOLIO_RETURN_SCHEMA_VERSION
                and record.get("calculation_version") == DAILY_PORTFOLIO_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_ONE_PERIOD_CASH_FLOW_NEUTRAL_DAILY_RETURN"
                and record.get("simulation_only") is True
                and record.get("portfolio_version") == current["portfolio_version"] == previous["portfolio_version"]
                and record.get("previous_market_session_date") == previous["market_session_date"]
                and record.get("current_market_session_date") == current["market_session_date"]
                and record.get("previous_effective_at") == previous["effective_at"]
                and record.get("current_effective_at") == current["effective_at"]
                and record.get("regular_session_interval_verified") is True
                and record.get("exchange_holiday_calendar_evidence_used") is False
                and record.get("gross_pre_tax_basis") is True
                and record.get("broker_cash_reconciled") is False
                and record.get("daily_return_calculated") is True
                and record.get("annualized") is False
                and record.get("risk_adjusted") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == current["strategy_version"]
                and record.get("model_versions") == current["model_versions"]
                and record.get("git_revision") == current["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Daily-portfolio-return record {index} violates its boundary."
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
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                if allow_existing and (
                    {key: value for key, value in existing.items() if key not in ignored}
                    == {key: value for key, value in result.items() if key not in ignored}
                ):
                    return existing
                raise LedgerIntegrityError(f"Daily return {result['result_id']} already exists.")
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
