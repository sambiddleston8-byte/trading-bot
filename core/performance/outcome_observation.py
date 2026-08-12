from __future__ import annotations

"""Immutable raw price observations; this module makes no performance claim."""

import calendar
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from core.broker.local_paper_execution import LocalPaperExecutionLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


OBSERVATION_SCHEMA_VERSION = "1.0"
MAX_MARKET_DATE_DISTANCE = timedelta(days=7)
MAX_ENTRY_BENCHMARK_AGE = timedelta(days=4)
CONTEMPORANEOUS_DELAY = timedelta(hours=72)

OUTCOME_HORIZONS = {
    "ENTRY": {"label": "Entry", "days": 0, "months": 0},
    "1_DAY": {"label": "1 day", "days": 1, "months": 0},
    "1_WEEK": {"label": "1 week", "days": 7, "months": 0},
    "1_MONTH": {"label": "1 month", "days": 0, "months": 1},
    "3_MONTHS": {"label": "3 months", "days": 0, "months": 3},
    "6_MONTHS": {"label": "6 months", "days": 0, "months": 6},
    "12_MONTHS": {"label": "12 months", "days": 0, "months": 12},
    "24_MONTHS": {"label": "24 months", "days": 0, "months": 24},
}

MARKET_PRICE_BASES = {"ADJUSTED_CLOSE", "UNADJUSTED_CLOSE"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only observation write")
        written += count


def _positive_number(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive finite number") from error
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return resolved


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _target_at(filled_at: str, horizon: str) -> str:
    definition = OUTCOME_HORIZONS[horizon]
    target = _as_datetime(filled_at) + timedelta(days=definition["days"])
    target = _add_months(target, definition["months"])
    return target.isoformat()


def _observation_id(fill_id: str, horizon: str) -> str:
    logical_key = _canonical_json([fill_id, horizon])
    return "OBS-" + hashlib.sha256(logical_key.encode("utf-8")).hexdigest()[:32].upper()


def _source_observation_hash(record: dict[str, Any]) -> str:
    material = {
        "ticker": record.get("ticker"),
        "asset_price": record.get("asset_price"),
        "asset_price_basis": record.get("asset_price_basis"),
        "benchmark_ticker": record.get("benchmark_ticker"),
        "benchmark_price": record.get("benchmark_price"),
        "benchmark_price_basis": record.get("benchmark_price_basis"),
        "asset_price_effective_at": record.get("asset_price_effective_at"),
        "benchmark_price_effective_at": record.get("benchmark_price_effective_at"),
        "data_source": record.get("data_source"),
        "source_version": record.get("source_version"),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class OutcomeObservationLedger:
    """Append-only asset/benchmark observations linked to verified local fills."""

    def __init__(
        self,
        path: str | Path,
        execution_ledger: LocalPaperExecutionLedger,
    ) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Outcome observation ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank outcome observation line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at outcome observation line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Outcome observation line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def verify(self) -> list[dict[str, Any]]:
        fills = {record["fill_id"]: record for record in self.execution_ledger.verify()}
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_fill_horizons: set[tuple[str, str]] = set()
        entry_basis_by_fill: dict[str, str] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Outcome observation chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has been modified."
                )
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            observation_id = str(record.get("observation_id") or "")
            fill = fills.get(fill_id)
            if fill is None:
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has no verified fill."
                )
            if horizon not in OUTCOME_HORIZONS:
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has an invalid horizon."
                )
            if observation_id != _observation_id(fill_id, horizon):
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has an invalid identity."
                )
            fill_horizon = (fill_id, horizon)
            if observation_id in seen_ids or fill_horizon in seen_fill_horizons:
                raise LedgerIntegrityError(
                    f"Duplicate outcome observation at record {index}."
                )
            expected_identity = {
                "execution_record_hash": fill.get("record_hash"),
                "order_id": fill.get("order_id"),
                "decision_id": fill.get("decision_id"),
                "portfolio_version": fill.get("portfolio_version"),
                "ticker": fill.get("ticker"),
                "strategy_version": fill.get("strategy_version"),
                "model_versions": fill.get("model_versions"),
                "git_revision": fill.get("git_revision"),
                "target_at": _target_at(fill["filled_at"], horizon),
            }
            if any(record.get(key) != value for key, value in expected_identity.items()):
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} changed linked identity."
                )
            try:
                asset_price = _positive_number(record.get("asset_price"), "asset_price")
                _positive_number(record.get("benchmark_price"), "benchmark_price")
                asset_at = _as_datetime(record.get("asset_price_effective_at"))
                benchmark_at = _as_datetime(
                    record.get("benchmark_price_effective_at")
                )
                retrieved_at = _as_datetime(record.get("retrieved_at"))
                target_at = _as_datetime(record.get("target_at"))
                filled_at = _as_datetime(fill["filled_at"])
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has invalid values."
                ) from error
            expected_mode = (
                "CONTEMPORANEOUS"
                if retrieved_at - min(asset_at, benchmark_at)
                <= CONTEMPORANEOUS_DELAY
                else "BACKFILLED"
            )
            if horizon == "ENTRY":
                timing_clear = (
                    asset_at == filled_at
                    and filled_at - MAX_ENTRY_BENCHMARK_AGE
                    <= benchmark_at
                    <= filled_at
                )
                entry_basis_by_fill[fill_id] = str(
                    record.get("benchmark_price_basis") or ""
                )
            else:
                timing_clear = (
                    target_at <= asset_at <= target_at + MAX_MARKET_DATE_DISTANCE
                    and target_at
                    <= benchmark_at
                    <= target_at + MAX_MARKET_DATE_DISTANCE
                )
                timing_clear = timing_clear and (
                    fill_id in entry_basis_by_fill
                    and record.get("benchmark_price_basis")
                    == entry_basis_by_fill[fill_id]
                )
            timing_clear = timing_clear and retrieved_at >= max(asset_at, benchmark_at)
            boundary_clear = (
                record.get("schema_version") == OBSERVATION_SCHEMA_VERSION
                and record.get("status") == "OBSERVED"
                and record.get("record_type") == "RAW_PRICE_OBSERVATION"
                and record.get("performance_claim") is False
                and record.get("retrieval_mode") == expected_mode
                and record.get("target_alignment") == "WITHIN_DECLARED_WINDOW"
                and record.get("benchmark_ticker") == "^GSPC"
                and record.get("benchmark_price_basis") in MARKET_PRICE_BASES
                and record.get("asset_price_basis")
                == ("SIMULATED_FILL" if horizon == "ENTRY" else record.get("benchmark_price_basis"))
                and (horizon != "ENTRY" or asset_price == float(fill["fill_price"]))
                and record.get("source_observation_sha256")
                == _source_observation_hash(record)
                and timing_clear
            )
            if not boundary_clear:
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} violates its boundary."
                )
            try:
                _required(record.get("data_source"), "data_source")
                _required(record.get("source_version"), "source_version")
            except ValueError as error:
                raise LedgerIntegrityError(
                    f"Outcome observation record {index} has missing provenance."
                ) from error
            seen_ids.add(observation_id)
            seen_fill_horizons.add(fill_horizon)
            previous_hash = record["record_hash"]
        return records

    def observe(
        self,
        *,
        fill_id: str,
        horizon: str,
        benchmark_price: float,
        benchmark_price_effective_at: str | datetime,
        data_source: str,
        source_version: str,
        asset_price: float | None = None,
        asset_price_effective_at: str | datetime | None = None,
        benchmark_ticker: str = "^GSPC",
        market_price_basis: str = "ADJUSTED_CLOSE",
        retrieved_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        fills = {record["fill_id"]: record for record in self.execution_ledger.verify()}
        fill = fills.get(str(fill_id))
        if fill is None:
            raise ValueError("A verified local simulated fill is required")
        resolved_horizon = str(horizon or "").upper()
        if resolved_horizon not in OUTCOME_HORIZONS:
            raise ValueError(f"horizon must be one of: {', '.join(OUTCOME_HORIZONS)}")
        resolved_benchmark = str(benchmark_ticker or "").upper()
        if resolved_benchmark != "^GSPC":
            raise ValueError("The initial Phase 5 benchmark must be ^GSPC")
        resolved_basis = str(market_price_basis or "").upper()
        if resolved_basis not in MARKET_PRICE_BASES:
            raise ValueError("market_price_basis is unsupported")

        benchmark_at = _as_datetime(benchmark_price_effective_at)
        retrieval = _as_datetime(retrieved_at or datetime.now(timezone.utc))
        existing_records = self.verify()
        if resolved_horizon == "ENTRY":
            asset_at = _as_datetime(fill["filled_at"])
        else:
            if asset_price_effective_at is None:
                raise ValueError("asset_price_effective_at is required")
            asset_at = _as_datetime(asset_price_effective_at)
        if retrieval < max(asset_at, benchmark_at):
            raise ValueError("retrieved_at cannot be earlier than market price time")
        if retrieval > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("retrieved_at cannot be in the future")
        target_at = _as_datetime(_target_at(fill["filled_at"], resolved_horizon))
        filled_at = _as_datetime(fill["filled_at"])
        if resolved_horizon == "ENTRY":
            if not filled_at - MAX_ENTRY_BENCHMARK_AGE <= benchmark_at <= filled_at:
                raise ValueError(
                    "entry benchmark price must be at or within four days before the fill"
                )
            resolved_asset_price = float(fill["fill_price"])
            asset_basis = "SIMULATED_FILL"
        else:
            entry = next(
                (
                    record
                    for record in existing_records
                    if record["fill_id"] == fill["fill_id"]
                    and record["horizon"] == "ENTRY"
                ),
                None,
            )
            if entry is None:
                raise ValueError("ENTRY observation is required before later horizons")
            if resolved_basis != entry["benchmark_price_basis"]:
                raise ValueError("market price basis must match the ENTRY observation")
            if not (
                target_at <= asset_at <= target_at + MAX_MARKET_DATE_DISTANCE
                and target_at
                <= benchmark_at
                <= target_at + MAX_MARKET_DATE_DISTANCE
            ):
                raise ValueError("market price is outside the horizon observation window")
            resolved_asset_price = _positive_number(asset_price, "asset_price")
            asset_basis = resolved_basis
        retrieval_mode = (
            "CONTEMPORANEOUS"
            if retrieval - min(asset_at, benchmark_at) <= CONTEMPORANEOUS_DELAY
            else "BACKFILLED"
        )
        observation = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observation_id": _observation_id(fill["fill_id"], resolved_horizon),
            "record_type": "RAW_PRICE_OBSERVATION",
            "status": "OBSERVED",
            "performance_claim": False,
            "fill_id": fill["fill_id"],
            "execution_record_hash": fill["record_hash"],
            "order_id": fill["order_id"],
            "decision_id": fill["decision_id"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": OUTCOME_HORIZONS[resolved_horizon]["label"],
            "target_at": target_at.isoformat(),
            "asset_price_effective_at": asset_at.isoformat(),
            "benchmark_price_effective_at": benchmark_at.isoformat(),
            "retrieved_at": retrieval.isoformat(),
            "retrieval_mode": retrieval_mode,
            "target_alignment": "WITHIN_DECLARED_WINDOW",
            "asset_price": resolved_asset_price,
            "asset_price_basis": asset_basis,
            "benchmark_ticker": resolved_benchmark,
            "benchmark_price": _positive_number(benchmark_price, "benchmark_price"),
            "benchmark_price_basis": resolved_basis,
            "data_source": _required(data_source, "data_source"),
            "source_version": _required(source_version, "source_version"),
            "strategy_version": fill["strategy_version"],
            "model_versions": fill["model_versions"],
            "git_revision": fill["git_revision"],
        }
        observation["source_observation_sha256"] = _source_observation_hash(
            observation
        )
        return self._append(observation, allow_existing=allow_existing)

    def _append(
        self, observation: dict[str, Any], *, allow_existing: bool
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
                    if item["observation_id"] == observation["observation_id"]
                ),
                None,
            )
            if existing:
                ignored = {
                    "previous_hash",
                    "record_hash",
                    "retrieved_at",
                    "retrieval_mode",
                }
                comparable = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in observation.items() if key not in ignored}
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Observation ID {observation['observation_id']} already exists."
                )
            material = {
                **observation,
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
