from __future__ import annotations

"""Append-only, tamper-evident records for investment decisions.

The ledger stores one canonical JSON object per line.  Each record contains the
hash of the previous record and its own SHA-256 hash, so edits, deletions and
reordering can be detected.  It deliberately records decisions only; it does
not place or route orders.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable
from uuid import uuid4


LEDGER_SCHEMA_VERSION = "1.0"
GENESIS_HASH = "0" * 64


class LedgerIntegrityError(RuntimeError):
    """Raised when an existing ledger is malformed or its hash chain breaks."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_git_revision(project_root: Path | None = None) -> str:
    """Return the checked-out commit without failing outside a Git checkout."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


@dataclass(frozen=True)
class ModelVersion:
    component: str
    version: str
    provider: str = "rules-based"
    model: str | None = None
    prompt_version: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "version": self.version,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class InvestmentDecision:
    """Versioned Phase 1 decision schema required by the master roadmap.

    Fields may be ``None`` when research has not established an answer, but the
    field itself is always present. This distinguishes missing analysis from a
    silently changing record format.
    """

    ticker: str
    decision: str
    price_at_decision: float | None
    target_portfolio_weight: float | None
    expected_return: float | None
    expected_return_horizon_days: int | None
    confidence: float | None
    bull_case: str | None
    base_case: str | None
    bear_case: str | None
    catalysts: list[Any]
    risks: list[Any]
    investment_thesis: str | None
    thesis_invalidation_conditions: list[str]
    expected_catalyst_window: str | None
    reassessment_date: str | None
    thesis_expiry_date: str | None

    @classmethod
    def from_canonical(
        cls,
        canonical: dict[str, Any],
        holding: dict[str, Any] | None = None,
    ) -> "InvestmentDecision":
        holding = holding or {}
        master = canonical.get("master_decision") or {}
        horizon_years = canonical.get("valuation_horizon_years")
        horizon_days = round(float(horizon_years) * 365) if horizon_years is not None else None
        monitoring = canonical.get("monitoring_conditions") or []
        risk_signals = canonical.get("market_signals") or {}
        risks = risk_signals.get("risk_components") or []
        thesis = canonical.get("decision_reason")
        catalysts = canonical.get("catalysts") or []
        if isinstance(catalysts, dict):
            catalysts = catalysts.get("validated_catalysts") or catalysts.get("catalysts") or []
        if not isinstance(catalysts, list):
            catalysts = [catalysts]
        return cls(
            ticker=str(canonical.get("ticker") or holding.get("ticker") or "").upper(),
            decision=str(canonical.get("decision") or "UNAVAILABLE").upper(),
            price_at_decision=canonical.get("current_price"),
            target_portfolio_weight=holding.get("weight"),
            expected_return=canonical.get("expected_return"),
            expected_return_horizon_days=horizon_days,
            confidence=master.get("confidence") or holding.get("research_confidence"),
            bull_case=canonical.get("bull_case"),
            base_case=thesis,
            bear_case=canonical.get("bear_case"),
            catalysts=catalysts,
            risks=list(risks) if isinstance(risks, list) else [risks],
            investment_thesis=thesis,
            thesis_invalidation_conditions=[str(item) for item in monitoring],
            expected_catalyst_window=None,
            reassessment_date=None,
            thesis_expiry_date=None,
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "price_at_decision": self.price_at_decision,
            "target_portfolio_weight": self.target_portfolio_weight,
            "expected_return": self.expected_return,
            "expected_return_horizon_days": self.expected_return_horizon_days,
            "confidence": self.confidence,
            "bull_case": self.bull_case,
            "base_case": self.base_case,
            "bear_case": self.bear_case,
            "catalysts": self.catalysts,
            "risks": self.risks,
            "investment_thesis": self.investment_thesis,
            "thesis_invalidation_conditions": self.thesis_invalidation_conditions,
            "expected_catalyst_window": self.expected_catalyst_window,
            "reassessment_date": self.reassessment_date,
            "thesis_expiry_date": self.thesis_expiry_date,
        }


class InvestmentDecisionLedger:
    """Write-once JSONL ledger with a verifiable SHA-256 hash chain."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as ledger:
            for line_number, line in enumerate(ledger, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank ledger line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at ledger line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Ledger line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def repair_incomplete_tail(self) -> Path | None:
        """Remove only a malformed final partial line, preserving a backup.

        This is an explicit recovery operation, never an automatic mutation.
        A malformed line anywhere except the tail remains an integrity error.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix = raw[:complete_end]
            tail = raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(self.path.suffix + ".incomplete-tail")
                backup.write_bytes(tail)
                descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    os.write(descriptor, prefix)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self.verify()
                return backup
            return None
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)

    @staticmethod
    def _record_hash(record_without_hash: dict[str, Any]) -> str:
        return hashlib.sha256(
            _canonical_json(record_without_hash).encode("utf-8")
        ).hexdigest()

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        records = self.records()
        for index, record in enumerate(records, start=1):
            stored_hash = record.get("record_hash")
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if material.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Ledger chain is broken at record {index}."
                )
            if stored_hash != self._record_hash(material):
                raise LedgerIntegrityError(
                    f"Ledger record {index} has been modified."
                )
            previous_hash = stored_hash
        return records

    def append(
        self,
        *,
        ticker: str,
        decision: str,
        decision_payload: dict[str, Any],
        model_versions: Iterable[ModelVersion | dict[str, Any]],
        data_as_of: str,
        portfolio_version: str | None = None,
        git_revision: str | None = None,
        decided_at: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return self.append_batch(
            [{
                "ticker": ticker,
                "decision": decision,
                "decision_payload": decision_payload,
                "model_versions": model_versions,
                "data_as_of": data_as_of,
                "portfolio_version": portfolio_version,
                "git_revision": git_revision,
                "decided_at": decided_at,
                "decision_id": decision_id,
            }]
        )[0]

    def append_batch(
        self,
        entries: Iterable[dict[str, Any]],
        *,
        allow_existing: bool = False,
    ) -> list[dict[str, Any]]:
        """Append a portfolio's decisions under one writer lock.

        ``allow_existing`` is reserved for transaction recovery. Existing IDs
        are accepted only when their immutable content matches exactly.
        """
        pending = [dict(entry) for entry in entries]
        if not pending:
            return []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            existing = self.verify()
            existing_by_id = {
                str(item.get("decision_id")): item
                for item in existing
                if item.get("decision_id")
            }
            previous_hash = existing[-1]["record_hash"] if existing else GENESIS_HASH
            resolved: list[dict[str, Any]] = []
            new_records: list[dict[str, Any]] = []
            for entry in pending:
                resolved_id = entry.get("decision_id") or f"DEC-{uuid4().hex.upper()}"
                versions = [
                    version.as_dict() if isinstance(version, ModelVersion) else dict(version)
                    for version in entry.get("model_versions", [])
                ]
                comparable = {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "decision_id": resolved_id,
                    "decided_at": entry.get("decided_at") or _utc_now(),
                    "data_as_of": entry["data_as_of"],
                    "ticker": str(entry["ticker"]).strip().upper(),
                    "decision": str(entry["decision"]).strip().upper(),
                    "portfolio_version": entry.get("portfolio_version"),
                    "git_revision": entry.get("git_revision")
                    or current_git_revision(self.path.parent),
                    "model_versions": versions,
                    "decision_payload": entry["decision_payload"],
                    "execution_mode": "RECORD_ONLY",
                }
                prior = existing_by_id.get(str(resolved_id))
                if prior is not None:
                    prior_comparable = {
                        key: value
                        for key, value in prior.items()
                        if key not in {"previous_hash", "record_hash"}
                    }
                    if not allow_existing or prior_comparable != comparable:
                        raise LedgerIntegrityError(
                            f"Decision ID {resolved_id} already exists."
                        )
                    resolved.append(prior)
                    continue
                material = {**comparable, "previous_hash": previous_hash}
                record = {**material, "record_hash": self._record_hash(material)}
                previous_hash = record["record_hash"]
                new_records.append(record)
                resolved.append(record)
                existing_by_id[str(resolved_id)] = record

            if new_records:
                encoded = "".join(
                    _canonical_json(record) + "\n" for record in new_records
                ).encode("utf-8")
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    written = 0
                    while written < len(encoded):
                        count = os.write(descriptor, encoded[written:])
                        if count <= 0:
                            raise OSError("Ledger append made no forward progress.")
                        written += count
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return resolved
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)

    def append_investment_decision(
        self,
        investment_decision: InvestmentDecision,
        *,
        model_versions: Iterable[ModelVersion | dict[str, Any]],
        data_as_of: str,
        portfolio_version: str | None = None,
        git_revision: str | None = None,
        decided_at: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return self.append(
            ticker=investment_decision.ticker,
            decision=investment_decision.decision,
            decision_payload=investment_decision.as_payload(),
            model_versions=model_versions,
            data_as_of=data_as_of,
            portfolio_version=portfolio_version,
            git_revision=git_revision,
            decided_at=decided_at,
            decision_id=decision_id,
        )
