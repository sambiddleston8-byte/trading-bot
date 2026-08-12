from __future__ import annotations

"""Immutable point-in-time security factor-exposure evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance.portfolio_valuation import (
    SimulatedPortfolioValuationLedger,
    _as_datetime,
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


FACTOR_EVIDENCE_SCHEMA_VERSION = "1.0"
FACTOR_EVIDENCE_POLICY_VERSION = "provider-point-in-time-factor-evidence-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY = {
    "label_policy": "PROVIDER_FACTOR_DEFINITIONS_PRESERVED_WITHOUT_TRANSLATION",
    "missing_policy": "MISSING_OR_UNUSABLE_EVIDENCE_IS_UNCERTAIN",
    "effective_time_policy": "FACTOR_EFFECTIVE_NO_LATER_THAN_VALUATION",
    "aggregation_policy": "PORTFOLIO_FACTOR_EXPOSURE_NOT_CALCULATED",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTPS URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} cannot contain credentials")
    return resolved


def _factor_values(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    resolved = []
    codes = set()
    names = set()
    for value in values:
        code = _required(value.get("factor_code"), "factor_code")
        name = _required(value.get("factor_name"), "factor_name")
        unit = _required(value.get("unit"), "unit")
        if code.casefold() in {"unknown", "n/a", "na"} or name.casefold() in {
            "unknown",
            "n/a",
            "na",
        }:
            raise ValueError("Factor definitions cannot use missing-data placeholders")
        if code in codes or name in names:
            raise ValueError("Factor codes and names must each be unique")
        try:
            decimal_value = Decimal(str(value.get("exposure")))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("factor exposure must be a finite decimal") from error
        if not decimal_value.is_finite():
            raise ValueError("factor exposure must be a finite decimal")
        exposure = Fraction(decimal_value)
        codes.add(code)
        names.add(name)
        resolved.append(
            {
                "factor_code": code,
                "factor_name": name,
                "unit": unit,
                "exposure": _decimal_string(exposure),
                "exact_exposure": _fraction_material(exposure),
            }
        )
    resolved.sort(key=lambda item: (item["factor_code"], item["factor_name"]))
    return resolved


def _evidence_id(material: Mapping[str, Any]) -> str:
    identity = {
        key: material.get(key)
        for key in (
            "valuation_id",
            "ticker",
            "completeness_status",
            "uncertainty_reasons",
            "provider",
            "factor_model_name",
            "factor_model_version",
            "factor_effective_at",
            "retrieved_at",
            "methodology_uri",
            "methodology_sha256",
            "source_uri",
            "source_input_sha256",
            "factors",
        )
    }
    identity["policy_version"] = FACTOR_EVIDENCE_POLICY_VERSION
    return "FEV-" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32].upper()


def _source_evidence_hash(record: Mapping[str, Any]) -> str:
    material = {
        key: record.get(key)
        for key in (
            "portfolio_version",
            "valuation_id",
            "ticker",
            "valuation_effective_at",
            "factor_effective_at",
            "retrieved_at",
            "completeness_status",
            "uncertainty_reasons",
            "provider",
            "factor_model_name",
            "factor_model_version",
            "methodology_uri",
            "methodology_sha256",
            "source_uri",
            "source_input_sha256",
            "factors",
        )
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class FactorExposureEvidenceLedger:
    """Append-only provider factor evidence; no portfolio aggregation."""

    def __init__(
        self, path: str | Path, valuation_ledger: SimulatedPortfolioValuationLedger
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Factor-evidence ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank factor-evidence line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at factor-evidence line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Factor-evidence line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def observe(
        self,
        *,
        portfolio_version: str,
        horizon: str,
        ticker: str,
        provider: str,
        factor_model_name: str,
        factor_model_version: str,
        methodology_uri: str,
        methodology_sha256: str,
        source_uri: str,
        source_input_sha256: str,
        factor_effective_at: str | datetime,
        retrieved_at: str | datetime,
        factors: Sequence[Mapping[str, Any]] = (),
        completeness_status: str = "COMPLETE",
        uncertainty_reasons: Sequence[str] = (),
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = _required(portfolio_version, "portfolio_version")
        resolved_horizon = _required(horizon, "horizon").upper()
        resolved_ticker = _required(ticker, "ticker").upper()
        valuation = next(
            (
                item
                for item in self.valuation_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("horizon") == resolved_horizon
            ),
            None,
        )
        if valuation is None:
            raise ValueError("A verified portfolio valuation is required")
        positions = {
            str(item.get("ticker") or "").upper(): item
            for item in valuation.get("positions") or []
        }
        position = positions.get(resolved_ticker)
        if position is None:
            raise ValueError("Ticker must be a position in the verified valuation")

        valuation_at = _as_datetime(valuation["outcome_asset_price_effective_at"])
        effective = _as_datetime(factor_effective_at)
        retrieved = _as_datetime(retrieved_at)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if effective > valuation_at:
            raise ValueError("factor_effective_at cannot exceed the valuation boundary")
        if retrieved < effective:
            raise ValueError("retrieved_at cannot predate factor_effective_at")
        if recorded < max(retrieved, _as_datetime(valuation["calculated_at"])):
            raise ValueError("recorded_at cannot predate retrieval or valuation calculation")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")

        completeness = str(completeness_status or "").upper()
        if completeness not in {"COMPLETE", "UNCERTAIN"}:
            raise ValueError("completeness_status must be COMPLETE or UNCERTAIN")
        reasons = sorted(
            {_required(item, "uncertainty reason") for item in uncertainty_reasons}
        )
        resolved_factors = _factor_values(factors)
        if completeness == "COMPLETE":
            if not resolved_factors or reasons:
                raise ValueError("COMPLETE evidence requires factors and no uncertainty")
            status = "OBSERVED"
        else:
            if resolved_factors or not reasons:
                raise ValueError("UNCERTAIN evidence requires reasons and no factors")
            status = "UNCERTAIN"

        evidence = {
            "schema_version": FACTOR_EVIDENCE_SCHEMA_VERSION,
            "policy_version": FACTOR_EVIDENCE_POLICY_VERSION,
            "record_type": "POINT_IN_TIME_SECURITY_FACTOR_EXPOSURE_EVIDENCE",
            "status": status,
            "simulation_only": True,
            "portfolio_factor_exposure_calculated": False,
            "recommendation_provided": False,
            "performance_claim": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": valuation["horizon_label"],
            "ticker": resolved_ticker,
            "valuation_id": valuation["valuation_id"],
            "valuation_record_hash": valuation["record_hash"],
            "position_total_return_result_id": position[
                "total_return_result_id"
            ],
            "position_total_return_result_hash": position[
                "total_return_result_hash"
            ],
            "valuation_effective_at": valuation_at.isoformat(),
            "factor_effective_at": effective.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "recorded_at": recorded.isoformat(),
            "availability_at_valuation": (
                "AVAILABLE_BY_BOUNDARY"
                if retrieved <= valuation_at
                else "BACKFILLED_AFTER_BOUNDARY"
            ),
            "completeness_status": completeness,
            "uncertainty_reasons": reasons,
            "provider": _required(provider, "provider"),
            "factor_model_name": _required(factor_model_name, "factor_model_name"),
            "factor_model_version": _required(
                factor_model_version, "factor_model_version"
            ),
            "methodology_uri": _https_uri(methodology_uri, "methodology_uri"),
            "methodology_sha256": _sha256(
                methodology_sha256, "methodology_sha256"
            ),
            "source_uri": _https_uri(source_uri, "source_uri"),
            "source_input_sha256": _sha256(
                source_input_sha256, "source_input_sha256"
            ),
            "factors": resolved_factors,
            **POLICY,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
        }
        evidence["evidence_id"] = _evidence_id(evidence)
        evidence["source_evidence_sha256"] = _source_evidence_hash(evidence)
        return self._append(evidence, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        valuations = {
            item["valuation_id"]: item for item in self.valuation_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids = set()
        completed_keys = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Factor-evidence chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Factor-evidence record {index} has been modified."
                )
            valuation = valuations.get(record.get("valuation_id"))
            if valuation is None:
                raise LedgerIntegrityError(
                    f"Factor-evidence record {index} lost its valuation."
                )
            ticker = str(record.get("ticker") or "").upper()
            positions = {
                str(item.get("ticker") or "").upper(): item
                for item in valuation.get("positions") or []
            }
            position = positions.get(ticker)
            if position is None:
                raise LedgerIntegrityError(
                    f"Factor-evidence record {index} lost its position."
                )
            try:
                valuation_at = _as_datetime(record.get("valuation_effective_at"))
                effective = _as_datetime(record.get("factor_effective_at"))
                retrieved = _as_datetime(record.get("retrieved_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                provider = _required(record.get("provider"), "provider")
                model_name = _required(
                    record.get("factor_model_name"), "factor_model_name"
                )
                model_version = _required(
                    record.get("factor_model_version"), "factor_model_version"
                )
                _https_uri(record.get("methodology_uri"), "methodology_uri")
                _sha256(record.get("methodology_sha256"), "methodology_sha256")
                _https_uri(record.get("source_uri"), "source_uri")
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
                factors = _factor_values(record.get("factors") or [])
                reasons = sorted(
                    {
                        _required(item, "uncertainty reason")
                        for item in record.get("uncertainty_reasons") or []
                    }
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Factor-evidence record {index} has invalid values."
                ) from error
            completeness = str(record.get("completeness_status") or "")
            key = (
                valuation["valuation_id"],
                ticker,
                provider,
                model_name,
                model_version,
            )
            expected_availability = (
                "AVAILABLE_BY_BOUNDARY"
                if retrieved <= valuation_at
                else "BACKFILLED_AFTER_BOUNDARY"
            )
            complete = completeness == "COMPLETE" and bool(factors) and not reasons
            uncertain = completeness == "UNCERTAIN" and not factors and bool(reasons)
            linked = (
                record.get("portfolio_version") == valuation["portfolio_version"]
                and record.get("horizon") == valuation["horizon"]
                and record.get("horizon_label") == valuation["horizon_label"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("position_total_return_result_id")
                == position["total_return_result_id"]
                and record.get("position_total_return_result_hash")
                == position["total_return_result_hash"]
                and record.get("strategy_version") == valuation["strategy_version"]
                and record.get("model_versions") == valuation["model_versions"]
                and record.get("git_revision") == valuation["git_revision"]
            )
            boundary = (
                record.get("schema_version") == FACTOR_EVIDENCE_SCHEMA_VERSION
                and record.get("policy_version") == FACTOR_EVIDENCE_POLICY_VERSION
                and record.get("evidence_id") == _evidence_id(record)
                and record.get("evidence_id") not in seen_ids
                and record.get("record_type")
                == "POINT_IN_TIME_SECURITY_FACTOR_EXPOSURE_EVIDENCE"
                and record.get("status")
                == ("OBSERVED" if completeness == "COMPLETE" else "UNCERTAIN")
                and record.get("simulation_only") is True
                and record.get("portfolio_factor_exposure_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("performance_claim") is False
                and record.get("alpha_calculated") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and (complete or uncertain)
                and not (complete and key in completed_keys)
                and not (uncertain and key in completed_keys)
                and record.get("factors") == factors
                and record.get("uncertainty_reasons") == reasons
                and valuation_at
                == _as_datetime(valuation["outcome_asset_price_effective_at"])
                and effective <= valuation_at
                and retrieved >= effective
                and recorded
                >= max(retrieved, _as_datetime(valuation["calculated_at"]))
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("availability_at_valuation") == expected_availability
                and all(record.get(name) == value for name, value in POLICY.items())
                and record.get("source_evidence_sha256")
                == _source_evidence_hash(record)
            )
            if not linked or not boundary:
                raise LedgerIntegrityError(
                    f"Factor-evidence record {index} violates its boundary."
                )
            seen_ids.add(record["evidence_id"])
            if complete:
                completed_keys.add(key)
            previous_hash = record["record_hash"]
        return records

    def _append(
        self, evidence: dict[str, Any], *, allow_existing: bool
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
                    if item["evidence_id"] == evidence["evidence_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                comparable = {
                    key: value for key, value in existing.items() if key not in ignored
                }
                proposed = {
                    key: value for key, value in evidence.items() if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Factor evidence {evidence['evidence_id']} already exists."
                )
            key = (
                evidence["valuation_id"],
                evidence["ticker"],
                evidence["provider"],
                evidence["factor_model_name"],
                evidence["factor_model_version"],
            )
            if evidence["completeness_status"] == "COMPLETE" and any(
                (
                    item["valuation_id"],
                    item["ticker"],
                    item["provider"],
                    item["factor_model_name"],
                    item["factor_model_version"],
                )
                == key
                and item["completeness_status"] == "COMPLETE"
                for item in records
            ):
                raise LedgerIntegrityError(
                    "Conflicting complete factor evidence requires an explicit "
                    "new model version or future supersession mechanism."
                )
            if evidence["completeness_status"] == "UNCERTAIN" and any(
                (
                    item["valuation_id"],
                    item["ticker"],
                    item["provider"],
                    item["factor_model_name"],
                    item["factor_model_version"],
                )
                == key
                and item["completeness_status"] == "COMPLETE"
                for item in records
            ):
                raise LedgerIntegrityError(
                    "Complete factor evidence cannot regress to UNCERTAIN."
                )
            material = {
                **evidence,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
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
