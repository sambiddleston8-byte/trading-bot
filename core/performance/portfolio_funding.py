from __future__ import annotations

"""Immutable initial funding for simulated portfolio valuation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.broker import PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


PORTFOLIO_FUNDING_SCHEMA_VERSION = "1.0"
PORTFOLIO_FUNDING_POLICY_VERSION = "initial-funding-only-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only portfolio-funding write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _positive_fraction(value: Any, name: str) -> Fraction:
    try:
        resolved = Fraction(Decimal(str(value)))
    except (InvalidOperation, OverflowError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} must be a positive exact decimal") from error
    if resolved <= 0:
        raise ValueError(f"{name} must be a positive exact decimal")
    return resolved


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _funding_id(portfolio_version: str) -> str:
    return "PFUND-" + hashlib.sha256(
        str(portfolio_version).encode("utf-8")
    ).hexdigest()[:32].upper()


def _portfolio_proposals(
    proposal_ledger: PaperOrderProposalLedger, portfolio_version: str
) -> list[dict[str, Any]]:
    return sorted(
        (
            item
            for item in proposal_ledger.verify()
            if item.get("portfolio_version") == portfolio_version
        ),
        key=lambda item: item["order_id"],
    )


def _common_identity(proposals: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not proposals:
        raise ValueError("At least one verified portfolio proposal is required")
    identity = {
        "strategy_version": proposals[0].get("strategy_version"),
        "model_versions": proposals[0].get("model_versions"),
        "git_revision": proposals[0].get("git_revision"),
    }
    if any(
        any(item.get(key) != value for key, value in identity.items())
        for item in proposals
    ):
        raise ValueError("Portfolio proposals must share strategy, model and Git identity")
    return identity


class PortfolioFundingLedger:
    """Append-only initial capital; later external cash flows are blocked."""

    def __init__(self, path: str | Path, proposal_ledger: PaperOrderProposalLedger) -> None:
        self.path = Path(path)
        self.proposal_ledger = proposal_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio-funding ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio-funding line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio-funding line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio-funding line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record_initial_funding(
        self,
        *,
        portfolio_version: str,
        amount: Any,
        effective_at: str | datetime,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_version = str(portfolio_version or "").strip()
        if not resolved_version:
            raise ValueError("portfolio_version is required")
        proposals = _portfolio_proposals(self.proposal_ledger, resolved_version)
        identity = _common_identity(proposals)
        funding = _positive_fraction(amount, "amount")
        effective = _as_datetime(effective_at)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        earliest_proposal = min(_as_datetime(item["created_at"]) for item in proposals)
        if effective > recorded:
            raise ValueError("effective_at cannot be later than recorded_at")
        if recorded > earliest_proposal:
            raise ValueError("Initial funding must be recorded before portfolio proposals")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        result = {
            "schema_version": PORTFOLIO_FUNDING_SCHEMA_VERSION,
            "policy_version": PORTFOLIO_FUNDING_POLICY_VERSION,
            "funding_id": _funding_id(resolved_version),
            "status": "RECORDED",
            "record_type": "SIMULATED_PORTFOLIO_INITIAL_FUNDING",
            "event_type": "INITIAL_FUNDING",
            "simulation_only": True,
            "currency": "USD",
            "amount": _decimal_string(funding),
            "exact_amount": _fraction_material(funding),
            "effective_at": effective.isoformat(),
            "recorded_at": recorded.isoformat(),
            "portfolio_version": resolved_version,
            "proposal_order_ids": [item["order_id"] for item in proposals],
            "proposal_record_hashes": [item["record_hash"] for item in proposals],
            "cash_flow_policy": "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED",
            "external_contributions_supported": False,
            "external_withdrawals_supported": False,
            "portfolio_return_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
            **identity,
        }
        return self._append(result, allow_existing=allow_existing)

    def funding_for(self, portfolio_version: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.verify()
                if item["portfolio_version"] == portfolio_version
            ),
            None,
        )

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_versions = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio-funding chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio-funding record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            proposals = _portfolio_proposals(self.proposal_ledger, version)
            try:
                identity = _common_identity(proposals)
                amount = _positive_fraction(record.get("amount"), "amount")
                exact_amount = record.get("exact_amount") or {}
                exact_denominator = int(exact_amount["denominator"])
                stored_fraction = Fraction(int(exact_amount["numerator"]), exact_denominator)
                effective = _as_datetime(record.get("effective_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                earliest_proposal = min(
                    _as_datetime(item["created_at"]) for item in proposals
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio-funding record {index} has invalid values."
                ) from error
            boundary = (
                bool(version)
                and version not in seen_versions
                and record.get("schema_version") == PORTFOLIO_FUNDING_SCHEMA_VERSION
                and record.get("policy_version") == PORTFOLIO_FUNDING_POLICY_VERSION
                and record.get("funding_id") == _funding_id(version)
                and record.get("status") == "RECORDED"
                and record.get("record_type") == "SIMULATED_PORTFOLIO_INITIAL_FUNDING"
                and record.get("event_type") == "INITIAL_FUNDING"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and exact_denominator > 0
                and stored_fraction == amount
                and record.get("exact_amount") == _fraction_material(amount)
                and record.get("amount") == _decimal_string(amount)
                and effective <= recorded <= earliest_proposal
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("proposal_order_ids")
                == [item["order_id"] for item in proposals]
                and record.get("proposal_record_hashes")
                == [item["record_hash"] for item in proposals]
                and all(record.get(key) == value for key, value in identity.items())
                and record.get("cash_flow_policy")
                == "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED"
                and record.get("external_contributions_supported") is False
                and record.get("external_withdrawals_supported") is False
                and record.get("portfolio_return_calculated") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio-funding record {index} violates its boundary."
                )
            seen_versions.add(version)
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
                (item for item in records if item["funding_id"] == result["funding_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio funding {result['funding_id']} already exists."
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
