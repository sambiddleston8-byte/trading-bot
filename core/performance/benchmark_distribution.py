from __future__ import annotations

"""Immutable S&P 500 dividend-point evidence; no return is calculated here."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.outcome_observation import OutcomeObservationLedger


BENCHMARK_DISTRIBUTION_SCHEMA_VERSION = "1.1"
BENCHMARK_DEFINITION_VERSION = "sp500-gross-cash-dividend-points-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CONTEMPORANEOUS_DELAY = timedelta(hours=72)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY = {
    "benchmark_family": "S&P 500",
    "benchmark_price_ticker": "^GSPC",
    "distribution_series": "S&P 500 DIVIDEND POINTS",
    "currency": "USD",
    "distribution_basis": "GROSS_ORDINARY_CASH_DIVIDEND_POINTS",
    "withholding_tax_policy": "NO_WITHHOLDING_DEDUCTION",
    "reinvestment_policy": "NOT_REINVESTED",
    "basket_composition_policy": "CURRENT_INDEX_WEIGHTS_NOT_FROZEN_AT_ENTRY",
    "interval_convention": "EX_DATE_AFTER_START_THROUGH_END",
    "interval_notation": "(START,END]",
    "temporal_alignment_policy": "SAME_UTC_MARKET_DATE_AT_ENTRY_AND_OUTCOME",
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
            raise OSError("Unable to complete append-only benchmark-distribution write")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _nonnegative_decimal(value: Any, name: str) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    if resolved == 0:
        return "0"
    exact = format(resolved, "f")
    if "." in exact:
        exact = exact.rstrip("0").rstrip(".")
    return exact


def _methodology_uri(value: Any) -> str:
    resolved = _required(value, "methodology_uri")
    parsed = urlsplit(resolved)
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "spglobal.com" or hostname.endswith(".spglobal.com")
    ):
        raise ValueError("methodology_uri must be an official HTTPS spglobal.com URL")
    return resolved


def _evidence_id(fill_id: str, horizon: str, completeness_status: str) -> str:
    key = _canonical_json(
        [fill_id, horizon, completeness_status, BENCHMARK_DEFINITION_VERSION]
    )
    return "BDP-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()


def _definition_material(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **POLICY,
        "definition_version": BENCHMARK_DEFINITION_VERSION,
        "methodology_name": record.get("methodology_name"),
        "methodology_version": record.get("methodology_version"),
        "methodology_uri": record.get("methodology_uri"),
        "methodology_sha256": record.get("methodology_sha256"),
    }


def _definition_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(_definition_material(record)).encode("utf-8")
    ).hexdigest()


def _source_evidence_hash(record: dict[str, Any]) -> str:
    material = {
        "benchmark_definition_sha256": record.get("benchmark_definition_sha256"),
        "period_start_at": record.get("period_start_at"),
        "period_end_at": record.get("period_end_at"),
        "completeness_status": record.get("completeness_status"),
        "uncertainty_reasons": record.get("uncertainty_reasons"),
        "gross_dividend_points": record.get("gross_dividend_points"),
        "data_source": record.get("data_source"),
        "source_version": record.get("source_version"),
        "source_input_sha256": record.get("source_input_sha256"),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class BenchmarkDistributionLedger:
    """Append-only S&P dividend-point evidence linked to outcome observations."""

    def __init__(
        self, path: str | Path, observation_ledger: OutcomeObservationLedger
    ) -> None:
        self.path = Path(path)
        self.observation_ledger = observation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Benchmark-distribution ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank benchmark-distribution line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at benchmark-distribution line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Benchmark-distribution line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def observe(
        self,
        *,
        fill_id: str,
        horizon: str,
        data_source: str,
        source_version: str,
        source_input_sha256: str,
        methodology_name: str,
        methodology_version: str,
        methodology_uri: str,
        methodology_sha256: str,
        gross_dividend_points: Any | None = None,
        completeness_status: str = "COMPLETE",
        uncertainty_reasons: Sequence[str] = (),
        retrieved_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        observations = self.observation_ledger.verify()
        resolved_horizon = str(horizon or "").upper()
        if resolved_horizon == "ENTRY":
            raise ValueError("ENTRY is a baseline, not a distribution interval")
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
        if entry is None or outcome is None:
            raise ValueError("Verified ENTRY and due-horizon observations are required")
        if (
            entry.get("benchmark_ticker") != "^GSPC"
            or outcome.get("benchmark_ticker") != "^GSPC"
            or entry.get("benchmark_price_basis") != "UNADJUSTED_CLOSE"
            or outcome.get("benchmark_price_basis") != "UNADJUSTED_CLOSE"
            or outcome.get("asset_price_basis") != "UNADJUSTED_CLOSE"
        ):
            raise ValueError("Unadjusted S&P 500 and asset observations are required")
        period_start = _as_datetime(entry["benchmark_price_effective_at"])
        period_end = _as_datetime(outcome["benchmark_price_effective_at"])
        entry_asset_at = _as_datetime(entry["asset_price_effective_at"])
        outcome_asset_at = _as_datetime(outcome["asset_price_effective_at"])
        if period_start.date() != entry_asset_at.date():
            raise ValueError(
                "Entry asset and benchmark must share the same UTC market date"
            )
        if period_end.date() != outcome_asset_at.date():
            raise ValueError(
                "Outcome asset and benchmark must share the same UTC market date"
            )
        if period_end <= period_start:
            raise ValueError("Benchmark distribution interval must end after it starts")
        retrieved = _as_datetime(retrieved_at or datetime.now(timezone.utc))
        if retrieved < period_end:
            raise ValueError("retrieved_at cannot predate the distribution interval end")
        if retrieved > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("retrieved_at cannot be in the future")
        completeness = str(completeness_status or "").upper()
        if completeness not in {"COMPLETE", "UNCERTAIN"}:
            raise ValueError("completeness_status must be COMPLETE or UNCERTAIN")
        reasons = sorted({_required(item, "uncertainty reason") for item in uncertainty_reasons})
        if completeness == "COMPLETE":
            if reasons:
                raise ValueError("COMPLETE evidence cannot have uncertainty reasons")
            points = _nonnegative_decimal(gross_dividend_points, "gross_dividend_points")
            status = "OBSERVED"
        else:
            if not reasons:
                raise ValueError("UNCERTAIN evidence requires reasons")
            if gross_dividend_points is not None:
                raise ValueError("UNCERTAIN evidence cannot assert dividend points")
            points = None
            status = "UNCERTAIN"
        record = {
            "schema_version": BENCHMARK_DISTRIBUTION_SCHEMA_VERSION,
            "definition_version": BENCHMARK_DEFINITION_VERSION,
            "evidence_id": _evidence_id(
                fill_id, resolved_horizon, completeness
            ),
            "record_type": "BENCHMARK_DISTRIBUTION_EVIDENCE",
            "status": status,
            "performance_claim": False,
            "benchmark_total_return_calculated": False,
            "relative_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "fill_id": fill_id,
            "entry_observation_id": entry["observation_id"],
            "entry_observation_hash": entry["record_hash"],
            "outcome_observation_id": outcome["observation_id"],
            "outcome_observation_hash": outcome["record_hash"],
            "order_id": outcome["order_id"],
            "decision_id": outcome["decision_id"],
            "portfolio_version": outcome["portfolio_version"],
            "ticker": outcome["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": outcome["horizon_label"],
            **POLICY,
            "period_start_at": period_start.isoformat(),
            "period_start_inclusive": False,
            "period_end_at": period_end.isoformat(),
            "period_end_inclusive": True,
            "entry_asset_at": entry_asset_at.isoformat(),
            "entry_alignment_seconds": str(
                abs(int((entry_asset_at - period_start).total_seconds()))
            ),
            "outcome_asset_at": outcome_asset_at.isoformat(),
            "outcome_alignment_seconds": str(
                abs(int((outcome_asset_at - period_end).total_seconds()))
            ),
            "gross_dividend_points": points,
            "completeness_status": completeness,
            "uncertainty_reasons": reasons,
            "retrieved_at": retrieved.isoformat(),
            "retrieval_mode": (
                "CONTEMPORANEOUS"
                if retrieved - period_end <= CONTEMPORANEOUS_DELAY
                else "BACKFILLED"
            ),
            "data_source": _required(data_source, "data_source"),
            "source_version": _required(source_version, "source_version"),
            "source_input_sha256": _sha256(
                source_input_sha256, "source_input_sha256"
            ),
            "methodology_name": _required(methodology_name, "methodology_name"),
            "methodology_version": _required(
                methodology_version, "methodology_version"
            ),
            "methodology_uri": _methodology_uri(methodology_uri),
            "methodology_sha256": _sha256(
                methodology_sha256, "methodology_sha256"
            ),
            "strategy_version": outcome["strategy_version"],
            "model_versions": outcome["model_versions"],
            "git_revision": outcome["git_revision"],
        }
        record["benchmark_definition_sha256"] = _definition_hash(record)
        record["source_evidence_sha256"] = _source_evidence_hash(record)
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        observations = {
            item["observation_id"]: item for item in self.observation_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        completed_intervals: set[tuple[str, str]] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Benchmark-distribution chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Benchmark-distribution record {index} has been modified."
                )
            entry = observations.get(record.get("entry_observation_id"))
            outcome = observations.get(record.get("outcome_observation_id"))
            if entry is None or outcome is None:
                raise LedgerIntegrityError(
                    f"Benchmark-distribution record {index} lost linked observations."
                )
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            completeness = str(record.get("completeness_status") or "")
            expected_id = _evidence_id(fill_id, horizon, completeness)
            try:
                period_start = _as_datetime(record.get("period_start_at"))
                period_end = _as_datetime(record.get("period_end_at"))
                entry_asset_at = _as_datetime(record.get("entry_asset_at"))
                outcome_asset_at = _as_datetime(record.get("outcome_asset_at"))
                retrieved = _as_datetime(record.get("retrieved_at"))
                reasons = sorted(
                    {
                        _required(item, "uncertainty reason")
                        for item in record.get("uncertainty_reasons", [])
                    }
                )
                points = (
                    _nonnegative_decimal(
                        record.get("gross_dividend_points"), "gross_dividend_points"
                    )
                    if completeness == "COMPLETE"
                    else None
                )
                _required(record.get("data_source"), "data_source")
                _required(record.get("source_version"), "source_version")
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
                _required(record.get("methodology_name"), "methodology_name")
                _required(record.get("methodology_version"), "methodology_version")
                _methodology_uri(record.get("methodology_uri"))
                _sha256(record.get("methodology_sha256"), "methodology_sha256")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Benchmark-distribution record {index} has invalid values."
                ) from error
            expected_status = "OBSERVED" if completeness == "COMPLETE" else "UNCERTAIN"
            expected_retrieval_mode = (
                "CONTEMPORANEOUS"
                if retrieved - period_end <= CONTEMPORANEOUS_DELAY
                else "BACKFILLED"
            )
            linked = (
                entry.get("fill_id") == fill_id
                and entry.get("horizon") == "ENTRY"
                and outcome.get("fill_id") == fill_id
                and outcome.get("horizon") == horizon
                and record.get("entry_observation_hash") == entry.get("record_hash")
                and record.get("outcome_observation_hash") == outcome.get("record_hash")
                and record.get("order_id") == outcome.get("order_id")
                and record.get("decision_id") == outcome.get("decision_id")
                and record.get("portfolio_version") == outcome.get("portfolio_version")
                and record.get("ticker") == outcome.get("ticker")
                and record.get("horizon_label") == outcome.get("horizon_label")
                and record.get("strategy_version") == outcome.get("strategy_version")
                and record.get("model_versions") == outcome.get("model_versions")
                and record.get("git_revision") == outcome.get("git_revision")
                and period_start == _as_datetime(entry["benchmark_price_effective_at"])
                and period_end == _as_datetime(outcome["benchmark_price_effective_at"])
                and entry_asset_at == _as_datetime(entry["asset_price_effective_at"])
                and outcome_asset_at == _as_datetime(outcome["asset_price_effective_at"])
            )
            boundary = (
                record.get("schema_version") == BENCHMARK_DISTRIBUTION_SCHEMA_VERSION
                and record.get("definition_version") == BENCHMARK_DEFINITION_VERSION
                and record.get("evidence_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type") == "BENCHMARK_DISTRIBUTION_EVIDENCE"
                and record.get("status") == expected_status
                and record.get("performance_claim") is False
                and record.get("benchmark_total_return_calculated") is False
                and record.get("relative_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("learning_eligible") is False
                and all(record.get(key) == value for key, value in POLICY.items())
                and record.get("period_start_inclusive") is False
                and record.get("period_end_inclusive") is True
                and period_end > period_start
                and period_start.date() == entry_asset_at.date()
                and period_end.date() == outcome_asset_at.date()
                and record.get("entry_alignment_seconds")
                == str(abs(int((entry_asset_at - period_start).total_seconds())))
                and record.get("outcome_alignment_seconds")
                == str(abs(int((outcome_asset_at - period_end).total_seconds())))
                and retrieved >= period_end
                and retrieved <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("retrieval_mode") == expected_retrieval_mode
                and completeness in {"COMPLETE", "UNCERTAIN"}
                and not (
                    completeness == "UNCERTAIN"
                    and (fill_id, horizon) in completed_intervals
                )
                and ((completeness == "UNCERTAIN") == bool(reasons))
                and record.get("uncertainty_reasons") == reasons
                and record.get("gross_dividend_points") == points
                and record.get("benchmark_definition_sha256")
                == _definition_hash(record)
                and record.get("source_evidence_sha256")
                == _source_evidence_hash(record)
                and entry.get("benchmark_ticker") == "^GSPC"
                and outcome.get("benchmark_ticker") == "^GSPC"
                and entry.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and outcome.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and outcome.get("asset_price_basis") == "UNADJUSTED_CLOSE"
            )
            if not linked or not boundary:
                raise LedgerIntegrityError(
                    f"Benchmark-distribution record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            if completeness == "COMPLETE":
                completed_intervals.add((fill_id, horizon))
            previous_hash = record["record_hash"]
        return records

    def complete_evidence(self, *, fill_id: str, horizon: str) -> dict[str, Any] | None:
        resolved_horizon = str(horizon or "").upper()
        return next(
            (
                item
                for item in self.verify()
                if item["fill_id"] == fill_id
                and item["horizon"] == resolved_horizon
                and item["completeness_status"] == "COMPLETE"
            ),
            None,
        )

    def _append(self, evidence: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["evidence_id"] == evidence["evidence_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "retrieved_at", "retrieval_mode"}
                comparable = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in evidence.items() if key not in ignored}
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Benchmark-distribution evidence {evidence['evidence_id']} already exists."
                )
            if evidence["completeness_status"] == "UNCERTAIN" and any(
                item["fill_id"] == evidence["fill_id"]
                and item["horizon"] == evidence["horizon"]
                and item["completeness_status"] == "COMPLETE"
                for item in records
            ):
                raise LedgerIntegrityError(
                    "Complete benchmark-distribution evidence cannot regress "
                    "to UNCERTAIN."
                )
            material = {
                **evidence,
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
