from __future__ import annotations

"""Tamper-evident local simulations derived from verified paper proposals.

This module records what a hypothetical fill would look like. It contains no
HTTP client, account credentials or broker submission capability.
"""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.broker.alpaca_paper import PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SIMULATED_EXECUTION_SCHEMA_VERSION = "1.0"
SIMULATED_EXECUTION_MODE = "LOCAL_SIMULATION_ONLY"
MAX_ABS_SIMULATED_SLIPPAGE_BPS = 5_000.0


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _fill_id(order_id: str) -> str:
    return (
        "SFILL-"
        + hashlib.sha256(str(order_id).encode("utf-8")).hexdigest()[:32].upper()
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only simulated execution write")
        written += count


def _positive_number(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive finite number") from error
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return resolved


def _nonnegative_number(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative finite number") from error
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return resolved


class LocalPaperExecutionLedger:
    """Append-only local fill simulations linked to verified proposals."""

    def __init__(
        self,
        path: str | Path,
        proposal_ledger: PaperOrderProposalLedger,
    ) -> None:
        self.path = Path(path)
        self.proposal_ledger = proposal_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Simulated execution ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank simulated execution ledger line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at simulated execution ledger line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Simulated execution ledger line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def verify(self) -> list[dict[str, Any]]:
        proposals = {
            record["order_id"]: record for record in self.proposal_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_fill_ids: set[str] = set()
        seen_order_ids: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if material.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Simulated execution chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Simulated execution record {index} has been modified."
                )
            fill_id = str(record.get("fill_id") or "")
            order_id = str(record.get("order_id") or "")
            if not fill_id or fill_id in seen_fill_ids:
                raise LedgerIntegrityError(
                    f"Invalid or duplicate simulated fill ID at record {index}."
                )
            if not order_id or order_id in seen_order_ids:
                raise LedgerIntegrityError(
                    f"Invalid or duplicate proposal order ID at record {index}."
                )
            proposal = proposals.get(order_id)
            if proposal is None:
                raise LedgerIntegrityError(
                    f"Simulated execution record {index} has no verified proposal."
                )
            if record.get("proposal_record_hash") != proposal.get("record_hash"):
                raise LedgerIntegrityError(
                    f"Simulated execution record {index} does not match its proposal."
                )
            expected_identity = {
                "decision_id": proposal.get("decision_id"),
                "portfolio_version": proposal.get("portfolio_version"),
                "ticker": proposal.get("ticker"),
                "side": proposal.get("side"),
                "requested_quantity": proposal.get("quantity"),
                "reference_price": proposal.get("reference_price"),
                "strategy_version": proposal.get("strategy_version"),
                "model_versions": proposal.get("model_versions"),
                "git_revision": proposal.get("git_revision"),
            }
            if any(record.get(key) != value for key, value in expected_identity.items()):
                raise LedgerIntegrityError(
                    f"Simulated execution record {index} changed proposal identity."
                )
            if record.get("schema_version") != SIMULATED_EXECUTION_SCHEMA_VERSION:
                raise LedgerIntegrityError(
                    f"Unsupported simulated execution schema at record {index}."
                )
            try:
                filled_at = canonical_timestamp(record.get("filled_at"))
                proposal_created_at = canonical_timestamp(proposal.get("created_at"))
                filled_quantity = _positive_number(
                    record.get("filled_quantity"), "filled_quantity"
                )
                fill_price = _positive_number(record.get("fill_price"), "fill_price")
                fees = _nonnegative_number(record.get("fees"), "fees")
                gross_value = _positive_number(record.get("gross_value"), "gross_value")
                slippage_bps = float(record.get("slippage_bps"))
                slippage_amount = float(record.get("slippage_amount"))
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Invalid simulated execution values at record {index}."
                ) from error
            direction = 1.0 if proposal["side"] == "BUY" else -1.0
            expected_slippage_per_share = direction * (
                fill_price - float(proposal["reference_price"])
            )
            expected_slippage_bps = (
                expected_slippage_per_share
                / float(proposal["reference_price"])
                * 10_000.0
            )
            expected_slippage_amount = expected_slippage_per_share * filled_quantity
            semantic_values_clear = (
                fill_id == _fill_id(order_id)
                and filled_at == record.get("filled_at")
                and filled_at >= proposal_created_at
                and filled_quantity == float(proposal["quantity"])
                and math.isclose(gross_value, fill_price * filled_quantity)
                and math.isfinite(slippage_bps)
                and math.isfinite(slippage_amount)
                and abs(slippage_bps) <= MAX_ABS_SIMULATED_SLIPPAGE_BPS
                and math.isclose(slippage_bps, expected_slippage_bps)
                and math.isclose(slippage_amount, expected_slippage_amount)
                and record.get("slippage_convention")
                == "positive values are adverse to the portfolio"
                and 0 <= fees <= gross_value
            )
            if not semantic_values_clear:
                raise LedgerIntegrityError(
                    f"Inconsistent simulated execution values at record {index}."
                )
            if (
                record.get("execution_mode") != SIMULATED_EXECUTION_MODE
                or record.get("status") != "SIMULATED_FILLED"
                or record.get("source") != "DETERMINISTIC_LOCAL_SIMULATION"
            ):
                raise LedgerIntegrityError(
                    f"Invalid simulated execution boundary at record {index}."
                )
            seen_fill_ids.add(fill_id)
            seen_order_ids.add(order_id)
            previous_hash = record["record_hash"]
        return records

    def simulate_full_fill(
        self,
        *,
        order_id: str,
        fill_price: float,
        fees: float = 0.0,
        filled_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        proposals = {
            record["order_id"]: record for record in self.proposal_ledger.verify()
        }
        proposal = proposals.get(str(order_id))
        if proposal is None:
            raise ValueError("A verified paper-order proposal is required")

        resolved_fill_price = _positive_number(fill_price, "fill_price")
        resolved_fees = _nonnegative_number(fees, "fees")
        timestamp = canonical_timestamp(filled_at or datetime.now(timezone.utc))
        if timestamp < canonical_timestamp(proposal["created_at"]):
            raise ValueError("filled_at cannot be earlier than proposal creation")

        side = proposal["side"]
        reference_price = float(proposal["reference_price"])
        requested_quantity = float(proposal["quantity"])
        direction = 1.0 if side == "BUY" else -1.0
        signed_slippage_per_share = direction * (resolved_fill_price - reference_price)
        slippage_bps = signed_slippage_per_share / reference_price * 10_000.0
        slippage_amount = signed_slippage_per_share * requested_quantity
        gross_value = resolved_fill_price * requested_quantity
        if abs(slippage_bps) > MAX_ABS_SIMULATED_SLIPPAGE_BPS:
            raise ValueError(
                "fill_price is more than 50% from the proposal reference price"
            )
        if resolved_fees > gross_value:
            raise ValueError("fees cannot exceed simulated gross value")
        fill_id = _fill_id(proposal["order_id"])
        fill = {
            "schema_version": SIMULATED_EXECUTION_SCHEMA_VERSION,
            "fill_id": fill_id,
            "order_id": proposal["order_id"],
            "proposal_record_hash": proposal["record_hash"],
            "status": "SIMULATED_FILLED",
            "execution_mode": SIMULATED_EXECUTION_MODE,
            "source": "DETERMINISTIC_LOCAL_SIMULATION",
            "filled_at": timestamp,
            "decision_id": proposal["decision_id"],
            "portfolio_version": proposal["portfolio_version"],
            "ticker": proposal["ticker"],
            "side": side,
            "requested_quantity": requested_quantity,
            "filled_quantity": requested_quantity,
            "reference_price": reference_price,
            "fill_price": resolved_fill_price,
            "gross_value": gross_value,
            "fees": resolved_fees,
            "slippage_bps": slippage_bps,
            "slippage_amount": slippage_amount,
            "slippage_convention": "positive values are adverse to the portfolio",
            "strategy_version": proposal["strategy_version"],
            "model_versions": proposal["model_versions"],
            "git_revision": proposal["git_revision"],
        }
        return self._append(fill, allow_existing=allow_existing)

    def _append(self, fill: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["fill_id"] == fill["fill_id"]),
                None,
            )
            if existing:
                comparable = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"previous_hash", "record_hash", "filled_at"}
                }
                proposed_comparable = {
                    key: value for key, value in fill.items() if key != "filled_at"
                }
                if allow_existing and comparable == proposed_comparable:
                    return existing
                raise LedgerIntegrityError(
                    f"Simulated fill ID {fill['fill_id']} already exists."
                )
            material = {
                **fill,
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
        """Preserve and remove only a malformed final partial line."""
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


METHODOLOGY_GATE_LABELS = {
    "point_in_time_availability": "Point-in-time input availability",
    "forecast_confidence_accuracy": "Forecast-confidence accuracy",
    "single_factor_lineage": "Single-count factor lineage",
    "historical_universe_membership": "Historical universe membership",
    "active_pipeline_out_of_sample": "Active-pipeline out-of-sample validation",
}


class PaperSubmissionPreflight:
    """Report submission readiness without possessing power to enable it."""

    @classmethod
    def evaluate(cls, gates: Mapping[str, bool] | None = None) -> dict[str, Any]:
        supplied = dict(gates or {})
        results = {
            key: {"label": label, "passed": supplied.get(key) is True}
            for key, label in METHODOLOGY_GATE_LABELS.items()
        }
        open_gates = [key for key, result in results.items() if not result["passed"]]
        methodology_status = "CLEARED" if not open_gates else "BLOCKED"
        return {
            "status": methodology_status,
            "broker_submission_enabled": False,
            "next_authority": (
                "SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED"
                if not open_gates
                else "RESOLVE_METHODOLOGY_GATES"
            ),
            "gates": results,
            "open_gates": open_gates,
            "note": "This check cannot submit an order or enable a broker route.",
        }
