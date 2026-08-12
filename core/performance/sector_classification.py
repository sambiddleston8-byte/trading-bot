from __future__ import annotations

"""Immutable point-in-time sector evidence for verified portfolio positions."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance.portfolio_valuation import (
    SimulatedPortfolioValuationLedger,
    _as_datetime,
    _canonical_json,
    _record_hash,
    _write_all,
)


SECTOR_CLASSIFICATION_SCHEMA_VERSION = "1.0"
SECTOR_CLASSIFICATION_POLICY_VERSION = "provider-point-in-time-sector-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POLICY = {
    "label_policy": "PROVIDER_LABELS_PRESERVED_WITHOUT_TRANSLATION",
    "missing_policy": "MISSING_OR_UNKNOWN_IS_UNCERTAIN_NOT_A_SECTOR",
    "effective_time_policy": "CLASSIFICATION_EFFECTIVE_NO_LATER_THAN_VALUATION",
    "exposure_policy": "NOT_CALCULATED_BY_THIS_EVIDENCE_LEDGER",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _optional(value: Any) -> str | None:
    resolved = str(value or "").strip()
    return resolved or None


def _classification_value(value: Any, name: str) -> str | None:
    resolved = _optional(value)
    if resolved is not None and resolved.casefold() in {
        "unknown",
        "unclassified",
        "not classified",
        "n/a",
        "na",
    }:
        raise ValueError(f"{name} cannot use a missing-data placeholder")
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


def _evidence_id(
    valuation_id: str,
    ticker: str,
    completeness: str,
    provider: str,
    taxonomy_name: str,
    taxonomy_version: str,
    classification_effective_at: str,
    retrieved_at: str,
    uncertainty_reasons: Sequence[str],
    source_uri: str,
    source_input_sha256: str,
    sector_code: str | None,
    sector_name: str | None,
    industry_code: str | None,
    industry_name: str | None,
) -> str:
    material = [
        valuation_id,
        ticker,
        completeness,
        provider,
        taxonomy_name,
        taxonomy_version,
        classification_effective_at,
        retrieved_at,
        list(uncertainty_reasons),
        source_uri,
        source_input_sha256,
        sector_code,
        sector_name,
        industry_code,
        industry_name,
        SECTOR_CLASSIFICATION_POLICY_VERSION,
    ]
    return "SCF-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _source_evidence_hash(record: dict[str, Any]) -> str:
    material = {
        "portfolio_version": record.get("portfolio_version"),
        "valuation_id": record.get("valuation_id"),
        "ticker": record.get("ticker"),
        "valuation_effective_at": record.get("valuation_effective_at"),
        "classification_effective_at": record.get("classification_effective_at"),
        "completeness_status": record.get("completeness_status"),
        "uncertainty_reasons": record.get("uncertainty_reasons"),
        "provider": record.get("provider"),
        "taxonomy_name": record.get("taxonomy_name"),
        "taxonomy_version": record.get("taxonomy_version"),
        "sector_code": record.get("sector_code"),
        "sector_name": record.get("sector_name"),
        "industry_code": record.get("industry_code"),
        "industry_name": record.get("industry_name"),
        "source_uri": record.get("source_uri"),
        "source_input_sha256": record.get("source_input_sha256"),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class SectorClassificationEvidenceLedger:
    """Append-only provider classifications; exposure is calculated elsewhere."""

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
                "Sector-classification ledger has an incomplete final line; "
                "run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank sector-classification line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at sector-classification line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Sector-classification line {line_number} is not an object."
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
        taxonomy_name: str,
        taxonomy_version: str,
        source_uri: str,
        source_input_sha256: str,
        classification_effective_at: str | datetime,
        retrieved_at: str | datetime,
        recorded_at: str | datetime | None = None,
        sector_code: str | None = None,
        sector_name: str | None = None,
        industry_code: str | None = None,
        industry_name: str | None = None,
        completeness_status: str = "COMPLETE",
        uncertainty_reasons: Sequence[str] = (),
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = _required(portfolio_version, "portfolio_version")
        resolved_horizon = _required(horizon, "horizon").upper()
        resolved_ticker = _required(ticker, "ticker").upper()
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item
                for item in valuations
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

        valuation_effective = _as_datetime(
            valuation["outcome_asset_price_effective_at"]
        )
        classification_effective = _as_datetime(classification_effective_at)
        retrieved = _as_datetime(retrieved_at)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if classification_effective > valuation_effective:
            raise ValueError(
                "classification_effective_at cannot be later than the valuation boundary"
            )
        if retrieved < classification_effective:
            raise ValueError("retrieved_at cannot predate classification_effective_at")
        if recorded < max(retrieved, _as_datetime(valuation["calculated_at"])):
            raise ValueError("recorded_at cannot predate retrieval or valuation calculation")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")

        resolved_provider = _required(provider, "provider")
        resolved_taxonomy = _required(taxonomy_name, "taxonomy_name")
        resolved_taxonomy_version = _required(
            taxonomy_version, "taxonomy_version"
        )
        resolved_source_uri = _https_uri(source_uri, "source_uri")
        resolved_source_hash = _sha256(
            source_input_sha256, "source_input_sha256"
        )
        completeness = str(completeness_status or "").upper()
        if completeness not in {"COMPLETE", "UNCERTAIN"}:
            raise ValueError("completeness_status must be COMPLETE or UNCERTAIN")
        reasons = sorted(
            {_required(item, "uncertainty reason") for item in uncertainty_reasons}
        )
        resolved_sector_code = _classification_value(sector_code, "sector_code")
        resolved_sector_name = _classification_value(sector_name, "sector_name")
        resolved_industry_code = _classification_value(
            industry_code, "industry_code"
        )
        resolved_industry_name = _classification_value(
            industry_name, "industry_name"
        )
        classification_values = (
            resolved_sector_code,
            resolved_sector_name,
            resolved_industry_code,
            resolved_industry_name,
        )
        if completeness == "COMPLETE":
            if reasons:
                raise ValueError("COMPLETE evidence cannot have uncertainty reasons")
            if not resolved_sector_code or not resolved_sector_name:
                raise ValueError("COMPLETE evidence requires sector_code and sector_name")
            if (resolved_industry_code is None) != (resolved_industry_name is None):
                raise ValueError(
                    "industry_code and industry_name must both be supplied or both omitted"
                )
            status = "OBSERVED"
        else:
            if not reasons:
                raise ValueError("UNCERTAIN evidence requires reasons")
            if any(value is not None for value in classification_values):
                raise ValueError("UNCERTAIN evidence cannot assert classification values")
            status = "UNCERTAIN"

        evidence = {
            "schema_version": SECTOR_CLASSIFICATION_SCHEMA_VERSION,
            "policy_version": SECTOR_CLASSIFICATION_POLICY_VERSION,
            "evidence_id": _evidence_id(
                valuation["valuation_id"],
                resolved_ticker,
                completeness,
                resolved_provider,
                resolved_taxonomy,
                resolved_taxonomy_version,
                classification_effective.isoformat(),
                retrieved.isoformat(),
                reasons,
                resolved_source_uri,
                resolved_source_hash,
                resolved_sector_code,
                resolved_sector_name,
                resolved_industry_code,
                resolved_industry_name,
            ),
            "record_type": "POINT_IN_TIME_SECTOR_CLASSIFICATION_EVIDENCE",
            "status": status,
            "simulation_only": True,
            "exposure_calculated": False,
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
            "position_total_return_result_id": position["total_return_result_id"],
            "position_total_return_result_hash": position["total_return_result_hash"],
            "valuation_effective_at": valuation_effective.isoformat(),
            "classification_effective_at": classification_effective.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "recorded_at": recorded.isoformat(),
            "availability_at_valuation": (
                "AVAILABLE_BY_BOUNDARY"
                if retrieved <= valuation_effective
                else "BACKFILLED_AFTER_BOUNDARY"
            ),
            "completeness_status": completeness,
            "uncertainty_reasons": reasons,
            "provider": resolved_provider,
            "taxonomy_name": resolved_taxonomy,
            "taxonomy_version": resolved_taxonomy_version,
            "sector_code": resolved_sector_code,
            "sector_name": resolved_sector_name,
            "industry_code": resolved_industry_code,
            "industry_name": resolved_industry_name,
            "source_uri": resolved_source_uri,
            "source_input_sha256": resolved_source_hash,
            **POLICY,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
        }
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
                    f"Sector-classification chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} has been modified."
                )
            valuation = valuations.get(record.get("valuation_id"))
            if valuation is None:
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} lost its valuation."
                )
            ticker = str(record.get("ticker") or "").upper()
            positions = {
                str(item.get("ticker") or "").upper(): item
                for item in valuation.get("positions") or []
            }
            position = positions.get(ticker)
            if position is None:
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} lost its position."
                )
            completeness = str(record.get("completeness_status") or "")
            try:
                valuation_effective = _as_datetime(
                    record.get("valuation_effective_at")
                )
                classification_effective = _as_datetime(
                    record.get("classification_effective_at")
                )
                retrieved = _as_datetime(record.get("retrieved_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                provider = _required(record.get("provider"), "provider")
                taxonomy = _required(record.get("taxonomy_name"), "taxonomy_name")
                taxonomy_version = _required(
                    record.get("taxonomy_version"), "taxonomy_version"
                )
                _https_uri(record.get("source_uri"), "source_uri")
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
                reasons = sorted(
                    {
                        _required(item, "uncertainty reason")
                        for item in record.get("uncertainty_reasons", [])
                    }
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} has invalid values."
                ) from error
            key = (
                valuation["valuation_id"],
                ticker,
                provider,
                taxonomy,
                taxonomy_version,
            )
            try:
                sector_code = _classification_value(
                    record.get("sector_code"), "sector_code"
                )
                sector_name = _classification_value(
                    record.get("sector_name"), "sector_name"
                )
                industry_code = _classification_value(
                    record.get("industry_code"), "industry_code"
                )
                industry_name = _classification_value(
                    record.get("industry_name"), "industry_name"
                )
            except ValueError as error:
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} has invalid values."
                ) from error
            expected_id = _evidence_id(
                valuation["valuation_id"],
                ticker,
                completeness,
                provider,
                taxonomy,
                taxonomy_version,
                classification_effective.isoformat(),
                retrieved.isoformat(),
                reasons,
                str(record.get("source_uri") or ""),
                str(record.get("source_input_sha256") or ""),
                sector_code,
                sector_name,
                industry_code,
                industry_name,
            )
            complete_values = (
                bool(sector_code)
                and bool(sector_name)
                and ((industry_code is None) == (industry_name is None))
                and not reasons
            )
            uncertain_values = (
                bool(reasons)
                and sector_code is None
                and sector_name is None
                and industry_code is None
                and industry_name is None
            )
            expected_availability = (
                "AVAILABLE_BY_BOUNDARY"
                if retrieved <= valuation_effective
                else "BACKFILLED_AFTER_BOUNDARY"
            )
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
                record.get("schema_version")
                == SECTOR_CLASSIFICATION_SCHEMA_VERSION
                and record.get("policy_version")
                == SECTOR_CLASSIFICATION_POLICY_VERSION
                and record.get("evidence_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type")
                == "POINT_IN_TIME_SECTOR_CLASSIFICATION_EVIDENCE"
                and record.get("status")
                == ("OBSERVED" if completeness == "COMPLETE" else "UNCERTAIN")
                and record.get("simulation_only") is True
                and record.get("exposure_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("performance_claim") is False
                and record.get("alpha_calculated") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and completeness in {"COMPLETE", "UNCERTAIN"}
                and not (completeness == "UNCERTAIN" and key in completed_keys)
                and (
                    (completeness == "COMPLETE" and complete_values)
                    or (completeness == "UNCERTAIN" and uncertain_values)
                )
                and record.get("uncertainty_reasons") == reasons
                and valuation_effective
                == _as_datetime(valuation["outcome_asset_price_effective_at"])
                and classification_effective <= valuation_effective
                and retrieved >= classification_effective
                and recorded
                >= max(retrieved, _as_datetime(valuation["calculated_at"]))
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("availability_at_valuation")
                == expected_availability
                and all(record.get(name) == value for name, value in POLICY.items())
                and record.get("source_evidence_sha256")
                == _source_evidence_hash(record)
            )
            if not linked or not boundary:
                raise LedgerIntegrityError(
                    f"Sector-classification record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            if completeness == "COMPLETE":
                completed_keys.add(key)
            previous_hash = record["record_hash"]
        return records

    def complete_evidence(
        self,
        *,
        valuation_id: str,
        ticker: str,
        provider: str,
        taxonomy_name: str,
        taxonomy_version: str,
    ) -> dict[str, Any] | None:
        key = (
            str(valuation_id),
            str(ticker).upper(),
            str(provider),
            str(taxonomy_name),
            str(taxonomy_version),
        )
        return next(
            (
                item
                for item in self.verify()
                if (
                    item["valuation_id"],
                    item["ticker"],
                    item["provider"],
                    item["taxonomy_name"],
                    item["taxonomy_version"],
                )
                == key
                and item["completeness_status"] == "COMPLETE"
            ),
            None,
        )

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
                    key: value
                    for key, value in existing.items()
                    if key not in ignored
                }
                proposed = {
                    key: value
                    for key, value in evidence.items()
                    if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Sector-classification evidence {evidence['evidence_id']} already exists."
                )
            key = (
                evidence["valuation_id"],
                evidence["ticker"],
                evidence["provider"],
                evidence["taxonomy_name"],
                evidence["taxonomy_version"],
            )
            if evidence["completeness_status"] == "UNCERTAIN" and any(
                (
                    item["valuation_id"],
                    item["ticker"],
                    item["provider"],
                    item["taxonomy_name"],
                    item["taxonomy_version"],
                )
                == key
                and item["completeness_status"] == "COMPLETE"
                for item in records
            ):
                raise LedgerIntegrityError(
                    "Complete sector-classification evidence cannot regress to UNCERTAIN."
                )
            material = {
                **evidence,
                "previous_hash": (
                    records[-1]["record_hash"] if records else GENESIS_HASH
                ),
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                _write_all(
                    target, (_canonical_json(record) + "\n").encode("utf-8")
                )
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
