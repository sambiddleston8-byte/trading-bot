from __future__ import annotations

"""Immutable human-preregistered downside target; no Sortino is calculated."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    normalize_model_version,
)
from core.performance.portfolio_valuation import (
    _canonical_json,
    _record_hash,
    _write_all,
)


DOWNSIDE_POLICY_SCHEMA_VERSION = "1.0"
DOWNSIDE_POLICY_VERSION = "preregistered-sortino-target-v1"
ALLOWED_TARGET_BASES = {"MATCHED_DAILY_SOFR", "ZERO_DAILY_RETURN"}
MINIMUM_TOTAL_OBSERVATIONS = 252
MINIMUM_DOWNSIDE_OBSERVATIONS = 30
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _models(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    resolved = [normalize_model_version(item) for item in values]
    if not resolved or any(
        not item["component"].strip() or not item["version"].strip()
        for item in resolved
    ):
        raise ValueError("model_versions require at least one named component and version")
    return resolved


def _policy_id(
    portfolio_version: str,
    evaluation_not_before: str,
    target_basis: str,
    strategy_version: str,
) -> str:
    material = [
        portfolio_version,
        evaluation_not_before,
        target_basis,
        strategy_version,
        DOWNSIDE_POLICY_VERSION,
    ]
    return "DSPOL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _target_definition(target_basis: str) -> dict[str, Any]:
    if target_basis == "MATCHED_DAILY_SOFR":
        return {
            "minimum_acceptable_return_formula": "matched_daily_risk_free_return",
            "fixed_daily_target": None,
            "risk_free_pairing_required": True,
        }
    return {
        "minimum_acceptable_return_formula": "0",
        "fixed_daily_target": {
            "decimal": "0",
            "exact_fraction": {"numerator": "0", "denominator": "1"},
        },
        "risk_free_pairing_required": False,
    }


class DownsideTargetPolicyLedger:
    """Append-only human choices that must predate their evaluation window."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Downside-policy ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank downside-policy line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at downside-policy line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Downside-policy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        portfolio_version: str,
        target_basis: str,
        evaluation_not_before: str | datetime,
        decided_by: str,
        decision_reference: str,
        human_decision_confirmed: bool,
        strategy_version: str,
        model_versions: Sequence[Mapping[str, Any]],
        git_revision: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = _required(portfolio_version, "portfolio_version")
        basis = _required(target_basis, "target_basis").upper()
        if basis not in ALLOWED_TARGET_BASES:
            raise ValueError("target_basis must be MATCHED_DAILY_SOFR or ZERO_DAILY_RETURN")
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        evaluator_start = _as_datetime(evaluation_not_before)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if evaluator_start < recorded + MINIMUM_LEAD_TIME:
            raise ValueError(
                "evaluation_not_before must be at least five minutes after preregistration"
            )
        strategy = _required(strategy_version, "strategy_version")
        models = _models(model_versions)
        git = _required(git_revision, "git_revision")
        record = {
            "schema_version": DOWNSIDE_POLICY_SCHEMA_VERSION,
            "policy_version": DOWNSIDE_POLICY_VERSION,
            "policy_id": _policy_id(version, evaluator_start.isoformat(), basis, strategy),
            "status": "PREREGISTERED",
            "record_type": "HUMAN_APPROVED_SORTINO_DOWNSIDE_TARGET_POLICY",
            "portfolio_version": version,
            "target_basis": basis,
            "evaluation_not_before": evaluator_start.isoformat(),
            "recorded_at": recorded.isoformat(),
            "decided_by": _required(decided_by, "decided_by"),
            "decision_reference": _required(decision_reference, "decision_reference"),
            "human_decision_confirmed": True,
            "minimum_total_observations": MINIMUM_TOTAL_OBSERVATIONS,
            "minimum_downside_observations": MINIMUM_DOWNSIDE_OBSERVATIONS,
            "downside_definition": "daily_return < minimum_acceptable_return",
            "downside_deviation_formula": (
                "sqrt(sum(min(0, daily_return - minimum_acceptable_return)^2) "
                "/ total_observation_count)"
            ),
            "immutable_before_observation": True,
            "retrospective_application_allowed": False,
            "sortino_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": strategy,
            "model_versions": models,
            "git_revision": git,
            **_target_definition(basis),
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_windows = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Downside-policy record {index} has been modified.")
            try:
                version = _required(record.get("portfolio_version"), "portfolio_version")
                basis = _required(record.get("target_basis"), "target_basis").upper()
                if basis not in ALLOWED_TARGET_BASES:
                    raise ValueError("unsupported target basis")
                evaluator_start = _as_datetime(record.get("evaluation_not_before"))
                recorded = _as_datetime(record.get("recorded_at"))
                strategy = _required(record.get("strategy_version"), "strategy_version")
                models = _models(record.get("model_versions") or [])
                git = _required(record.get("git_revision"), "git_revision")
                _required(record.get("decided_by"), "decided_by")
                _required(record.get("decision_reference"), "decision_reference")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Downside-policy record {index} has invalid values."
                ) from error
            expected_id = _policy_id(version, evaluator_start.isoformat(), basis, strategy)
            window_key = (version, evaluator_start.isoformat())
            definition = _target_definition(basis)
            boundary = (
                record.get("schema_version") == DOWNSIDE_POLICY_SCHEMA_VERSION
                and record.get("policy_version") == DOWNSIDE_POLICY_VERSION
                and record.get("policy_id") == expected_id
                and expected_id not in seen_ids
                and window_key not in seen_windows
                and record.get("status") == "PREREGISTERED"
                and record.get("record_type")
                == "HUMAN_APPROVED_SORTINO_DOWNSIDE_TARGET_POLICY"
                and record.get("human_decision_confirmed") is True
                and evaluator_start >= recorded + MINIMUM_LEAD_TIME
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("minimum_total_observations")
                == MINIMUM_TOTAL_OBSERVATIONS
                and record.get("minimum_downside_observations")
                == MINIMUM_DOWNSIDE_OBSERVATIONS
                and record.get("downside_definition")
                == "daily_return < minimum_acceptable_return"
                and record.get("downside_deviation_formula")
                == "sqrt(sum(min(0, daily_return - minimum_acceptable_return)^2) / total_observation_count)"
                and record.get("immutable_before_observation") is True
                and record.get("retrospective_application_allowed") is False
                and record.get("sortino_calculated") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("model_versions") == models
                and record.get("git_revision") == git
                and all(record.get(key) == value for key, value in definition.items())
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Downside-policy record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_windows.add(window_key)
            previous_hash = record["record_hash"]
        return records

    def _append(self, policy: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["policy_id"] == policy["policy_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in policy.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Downside policy {policy['policy_id']} already exists.")
            if any(
                item["portfolio_version"] == policy["portfolio_version"]
                and item["evaluation_not_before"] == policy["evaluation_not_before"]
                for item in records
            ):
                raise LedgerIntegrityError(
                    "A different downside target is already preregistered for this evaluation window."
                )
            material = {
                **policy,
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
