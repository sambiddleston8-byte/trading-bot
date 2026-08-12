from __future__ import annotations

"""Immutable human-preregistered risk-adjusted alpha model policy."""

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
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


ALPHA_POLICY_SCHEMA_VERSION = "1.0"
ALPHA_POLICY_VERSION = "preregistered-daily-risk-adjusted-alpha-v1"
MINIMUM_OBSERVATIONS = 756
MINIMUM_EVALUATION_SPAN = timedelta(days=1095)
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MODEL_DEFINITIONS = {
    "CAPM_SP500_SOFR": {
        "factor_names": ["SP500_EXCESS_RETURN_OVER_MATCHED_SOFR"],
        "risk_free_basis": "MATCHED_DAILY_SOFR_INDEX_RETURN",
        "factor_source_policy": "VERIFIED_INTERNAL_SP500_TOTAL_RETURN_AND_SOFR_EVIDENCE",
        "official_ken_french_data_required": False,
    },
    "KEN_FRENCH_US_3_FACTOR": {
        "factor_names": ["MKT_RF", "SMB", "HML"],
        "risk_free_basis": "KEN_FRENCH_DATASET_RF",
        "factor_source_policy": "OFFICIAL_KEN_FRENCH_US_RESEARCH_DATA_LIBRARY",
        "official_ken_french_data_required": True,
    },
    "KEN_FRENCH_US_5_FACTOR": {
        "factor_names": ["MKT_RF", "SMB", "HML", "RMW", "CMA"],
        "risk_free_basis": "KEN_FRENCH_DATASET_RF",
        "factor_source_policy": "OFFICIAL_KEN_FRENCH_US_RESEARCH_DATA_LIBRARY",
        "official_ken_french_data_required": True,
    },
}


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
    model_family: str,
    evaluation_start: str,
    evaluation_end: str,
    strategy_version: str,
) -> str:
    material = [
        portfolio_version,
        model_family,
        evaluation_start,
        evaluation_end,
        strategy_version,
        ALPHA_POLICY_VERSION,
    ]
    return "ALPOL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class AlphaModelPolicyLedger:
    """Append-only model choices that must predate a fixed future window."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Alpha-model-policy ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank alpha-model-policy line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at alpha-model-policy line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Alpha-model-policy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        portfolio_version: str,
        model_family: str,
        evaluation_not_before: str | datetime,
        evaluation_not_after: str | datetime,
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
        family = _required(model_family, "model_family").upper()
        if family not in MODEL_DEFINITIONS:
            raise ValueError("model_family must be CAPM_SP500_SOFR, KEN_FRENCH_US_3_FACTOR or KEN_FRENCH_US_5_FACTOR")
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        start = _as_datetime(evaluation_not_before)
        end = _as_datetime(evaluation_not_after)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if start < recorded + MINIMUM_LEAD_TIME:
            raise ValueError("evaluation_not_before must be at least five minutes after preregistration")
        if end < start + MINIMUM_EVALUATION_SPAN:
            raise ValueError("evaluation window must be fixed for at least 1095 days")
        strategy = _required(strategy_version, "strategy_version")
        models = _models(model_versions)
        git = _required(git_revision, "git_revision")
        definition = MODEL_DEFINITIONS[family]
        record = {
            "schema_version": ALPHA_POLICY_SCHEMA_VERSION,
            "policy_version": ALPHA_POLICY_VERSION,
            "policy_id": _policy_id(version, family, start.isoformat(), end.isoformat(), strategy),
            "status": "PREREGISTERED",
            "record_type": "HUMAN_APPROVED_RISK_ADJUSTED_ALPHA_MODEL_POLICY",
            "portfolio_version": version,
            "model_family": family,
            "evaluation_not_before": start.isoformat(),
            "evaluation_not_after": end.isoformat(),
            "recorded_at": recorded.isoformat(),
            "decided_by": _required(decided_by, "decided_by"),
            "decision_reference": _required(decision_reference, "decision_reference"),
            "human_decision_confirmed": True,
            "return_frequency": "DAILY",
            "dependent_variable": "PORTFOLIO_DAILY_TOTAL_RETURN_MINUS_MODEL_MATCHED_RISK_FREE_RETURN",
            **definition,
            "regression_method": "OLS_WITH_INTERCEPT",
            "alpha_definition": "REGRESSION_INTERCEPT",
            "inference_covariance": "NEWEY_WEST_HAC",
            "hac_lag_policy": "floor(4*(observation_count/100)^(2/9))",
            "minimum_observations": MINIMUM_OBSERVATIONS,
            "missing_observation_imputation_allowed": False,
            "complete_date_intersection_required": True,
            "cross_strategy_pooling_allowed": False,
            "cross_model_version_pooling_allowed": False,
            "annualized_alpha_formula": "daily_alpha_intercept * 252",
            "annualization_days": 252,
            "model_selection_after_outcomes_allowed": False,
            "optional_stopping_allowed": False,
            "policy_selection_precedes_evaluation_window": True,
            "retrospective_application_allowed": False,
            "alpha_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": strategy,
            "model_versions": models,
            "git_revision": git,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_windows = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Alpha-model-policy record {index} has been modified.")
            try:
                version = _required(record.get("portfolio_version"), "portfolio_version")
                family = _required(record.get("model_family"), "model_family").upper()
                if family not in MODEL_DEFINITIONS:
                    raise ValueError("unsupported alpha model")
                start = _as_datetime(record.get("evaluation_not_before"))
                end = _as_datetime(record.get("evaluation_not_after"))
                recorded = _as_datetime(record.get("recorded_at"))
                strategy = _required(record.get("strategy_version"), "strategy_version")
                models = _models(record.get("model_versions") or [])
                git = _required(record.get("git_revision"), "git_revision")
                _required(record.get("decided_by"), "decided_by")
                _required(record.get("decision_reference"), "decision_reference")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Alpha-model-policy record {index} has invalid values.") from error
            expected_id = _policy_id(version, family, start.isoformat(), end.isoformat(), strategy)
            window = (version, start.isoformat(), end.isoformat())
            boundary = (
                record.get("schema_version") == ALPHA_POLICY_SCHEMA_VERSION
                and record.get("policy_version") == ALPHA_POLICY_VERSION
                and record.get("policy_id") == expected_id
                and expected_id not in seen_ids
                and window not in seen_windows
                and record.get("status") == "PREREGISTERED"
                and record.get("record_type") == "HUMAN_APPROVED_RISK_ADJUSTED_ALPHA_MODEL_POLICY"
                and record.get("human_decision_confirmed") is True
                and start >= recorded + MINIMUM_LEAD_TIME
                and end >= start + MINIMUM_EVALUATION_SPAN
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(key) == value for key, value in MODEL_DEFINITIONS[family].items())
                and record.get("return_frequency") == "DAILY"
                and record.get("dependent_variable") == "PORTFOLIO_DAILY_TOTAL_RETURN_MINUS_MODEL_MATCHED_RISK_FREE_RETURN"
                and record.get("regression_method") == "OLS_WITH_INTERCEPT"
                and record.get("alpha_definition") == "REGRESSION_INTERCEPT"
                and record.get("inference_covariance") == "NEWEY_WEST_HAC"
                and record.get("hac_lag_policy") == "floor(4*(observation_count/100)^(2/9))"
                and record.get("minimum_observations") == MINIMUM_OBSERVATIONS
                and record.get("missing_observation_imputation_allowed") is False
                and record.get("complete_date_intersection_required") is True
                and record.get("cross_strategy_pooling_allowed") is False
                and record.get("cross_model_version_pooling_allowed") is False
                and record.get("annualized_alpha_formula") == "daily_alpha_intercept * 252"
                and record.get("annualization_days") == 252
                and record.get("model_selection_after_outcomes_allowed") is False
                and record.get("optional_stopping_allowed") is False
                and record.get("policy_selection_precedes_evaluation_window") is True
                and record.get("retrospective_application_allowed") is False
                and record.get("alpha_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("model_versions") == models
                and record.get("git_revision") == git
            )
            if not boundary:
                raise LedgerIntegrityError(f"Alpha-model-policy record {index} violates its boundary.")
            seen_ids.add(expected_id)
            seen_windows.add(window)
            previous_hash = record["record_hash"]
        return records

    def _append(self, policy: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["policy_id"] == policy["policy_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in policy.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Alpha model policy {policy['policy_id']} already exists.")
            if any(
                item["portfolio_version"] == policy["portfolio_version"]
                and item["evaluation_not_before"] == policy["evaluation_not_before"]
                and item["evaluation_not_after"] == policy["evaluation_not_after"]
                for item in records
            ):
                raise LedgerIntegrityError("A different alpha model is already registered for this window.")
            material = {**policy, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
