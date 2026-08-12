from __future__ import annotations

"""Fail-closed Alpaca paper boundary with no network or submission capability."""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    current_git_revision,
    normalize_model_version,
)


ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
ORDER_LEDGER_SCHEMA_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AlpacaPaperConfiguration:
    """Configuration that cannot represent Alpaca's live trading endpoint."""

    def __init__(self, endpoint: str = ALPACA_PAPER_ENDPOINT) -> None:
        parsed = urlsplit(str(endpoint))
        if (
            parsed.scheme != "https"
            or parsed.hostname != "paper-api.alpaca.markets"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Only Alpaca's official paper trading endpoint is supported")
        self.endpoint = ALPACA_PAPER_ENDPOINT

    @classmethod
    def from_environment(cls) -> "AlpacaPaperConfiguration":
        return cls(os.getenv("ALPACA_PAPER_API_BASE_URL", ALPACA_PAPER_ENDPOINT))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only ledger write")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _positive_number(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive finite number") from error
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return resolved


def _weight(value: Any) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("target_weight must be between 0 and 1") from error
    if not math.isfinite(resolved) or not 0 <= resolved <= 1:
        raise ValueError("target_weight must be between 0 and 1")
    return resolved


class PaperOrderProposalLedger:
    """Append-only proposed orders; this class cannot submit or route an order."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Order ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank order ledger line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at order ledger line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Order ledger line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        records = self.records()
        seen_ids: set[str] = set()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if material.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Order ledger chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Order ledger record {index} has been modified.")
            order_id = str(record.get("order_id") or "")
            if not order_id or order_id in seen_ids:
                raise LedgerIntegrityError(f"Invalid or duplicate order ID at record {index}.")
            seen_ids.add(order_id)
            previous_hash = record["record_hash"]
        return records

    def repair_incomplete_tail(self) -> Path | None:
        """Explicitly preserve and remove only a malformed final partial line."""
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

    def propose(
        self,
        *,
        decision_id: str,
        portfolio_version: str,
        ticker: str,
        side: str,
        quantity: float,
        reference_price: float,
        target_weight: float,
        strategy_version: str,
        model_versions: Iterable[Mapping[str, Any]],
        created_at: str | datetime | None = None,
        git_revision: str | None = None,
        order_id: str | None = None,
        execution_mode: str = "PAPER_ONLY",
        allow_existing: bool = False,
    ) -> dict[str, Any]:
        resolved_mode = str(execution_mode).upper()
        if resolved_mode != "PAPER_ONLY":
            raise ValueError("Paper order proposals require PAPER_ONLY execution mode")
        resolved_ticker = str(ticker or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", resolved_ticker):
            raise ValueError("ticker is invalid")
        resolved_side = str(side or "").upper()
        if resolved_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        timestamp = canonical_timestamp(created_at or datetime.now(timezone.utc))
        logical_key = _canonical_json(
            [
                _required(decision_id, "decision_id"),
                _required(portfolio_version, "portfolio_version"),
                resolved_ticker,
                resolved_side,
            ]
        )
        resolved_id = order_id or (
            "PORD-" + hashlib.sha256(logical_key.encode("utf-8")).hexdigest()[:32].upper()
        )
        if not re.fullmatch(r"PORD-[A-Z0-9-]{1,59}", str(resolved_id)):
            raise ValueError("order_id must use the PORD- safe identifier format")
        client_order_id = "SP-" + hashlib.sha256(str(resolved_id).encode("utf-8")).hexdigest()[:32]
        versions = [normalize_model_version(item) for item in model_versions]
        if not versions or any(
            not str(item.get("component") or "").strip()
            or not str(item.get("version") or "").strip()
            for item in versions
        ):
            raise ValueError("model_versions requires nonblank component and version values")
        proposal = {
            "schema_version": ORDER_LEDGER_SCHEMA_VERSION,
            "order_id": str(resolved_id),
            "client_order_id": client_order_id,
            "status": "PROPOSED",
            "execution_mode": resolved_mode,
            "created_at": timestamp,
            "decision_id": _required(decision_id, "decision_id"),
            "portfolio_version": _required(portfolio_version, "portfolio_version"),
            "ticker": resolved_ticker,
            "side": resolved_side,
            "quantity": _positive_number(quantity, "quantity"),
            "reference_price": _positive_number(reference_price, "reference_price"),
            "target_weight": _weight(target_weight),
            "order_type": "MARKET",
            "time_in_force": "DAY",
            "strategy_version": _required(strategy_version, "strategy_version"),
            "model_versions": versions,
            "git_revision": git_revision or current_git_revision(PROJECT_ROOT),
        }
        return self._append(proposal, allow_existing=allow_existing)

    def _append(self, proposal: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["order_id"] == proposal["order_id"]),
                None,
            )
            if existing:
                comparable = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"previous_hash", "record_hash", "created_at"}
                }
                proposed_comparable = {
                    key: value for key, value in proposal.items() if key != "created_at"
                }
                if allow_existing and comparable == proposed_comparable:
                    return existing
                raise LedgerIntegrityError(f"Order ID {proposal['order_id']} already exists.")
            material = {
                **proposal,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            target = os.open(self.path, flags, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
