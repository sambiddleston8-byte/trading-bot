from __future__ import annotations

"""Immutable human approval boundary for historical replay data sources."""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.portfolio.historical_provider_qualification import HistoricalProviderQualificationLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "human-approved-historical-data-source-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_UNIVERSES = {"SP500", "NASDAQ100"}
PERMITTED_USES = {"LOCAL_RESEARCH", "HISTORICAL_REPLAY", "INTERNAL_DERIVED_RESULTS"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete historical-data approval append")
        written += count


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _date(value: str | date) -> date:
    if isinstance(value, datetime):
        raise ValueError("coverage dates must not include time")
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("coverage dates must use YYYY-MM-DD") from error


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    return resolved


def _hosts(values: Sequence[str]) -> list[str]:
    hosts = sorted({_required(value, "approved_data_host", 253).lower() for value in values})
    if not hosts:
        raise ValueError("approved_data_hosts cannot be empty")
    for host in hosts:
        parsed = urlsplit(f"https://{host}")
        if parsed.hostname != host or parsed.port or "/" in host or "@" in host:
            raise ValueError("approved_data_hosts must contain bare lowercase hostnames")
    return hosts


def _set(values: Sequence[str], name: str, allowed: set[str]) -> list[str]:
    resolved = sorted({_required(value, name, 100).upper() for value in values})
    if not resolved or any(value not in allowed for value in resolved):
        raise ValueError(f"{name} contains unsupported or empty values")
    return resolved


def _approval_id(provider: str, plan: str, terms_hash: str, approved_at: str) -> str:
    return "DSA-" + hashlib.sha256(
        _canonical_json([provider, plan, terms_hash, approved_at, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class HistoricalDataSourceApprovalLedger:
    """Records human approval; it does not subscribe, fetch, certify, or run data."""

    def __init__(
        self,
        path: str | Path,
        qualification_ledger: HistoricalProviderQualificationLedger,
    ) -> None:
        self.path = Path(path)
        self.qualification_ledger = qualification_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Data-source approval ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank approval line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid approval JSON at {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Approval line {line_number} is not an object.")
                records.append(record)
        return records

    def approve(
        self,
        *,
        provider_name: str,
        product_or_plan: str,
        qualification_id: str,
        qualification_record_hash: str,
        provider_url: str,
        terms_url: str,
        terms_content_sha256: str,
        terms_review_reference: str,
        approved_data_hosts: Sequence[str],
        approved_universes: Sequence[str],
        permitted_uses: Sequence[str],
        coverage_not_before: str | date,
        coverage_not_after: str | date,
        historical_constituents_included: bool,
        removed_securities_included: bool,
        delisted_securities_included: bool,
        corporate_actions_included: bool,
        terminal_outcome_support_documented: bool,
        redistribution_allowed: bool,
        approved_by: str,
        approved_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        approved = _timestamp(approved_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= approved <= now + MAX_CLOCK_SKEW:
            raise ValueError("approved_at must match the actual append time")
        start = _date(coverage_not_before)
        end = _date(coverage_not_after)
        if end <= start:
            raise ValueError("coverage_not_after must be later than coverage_not_before")
        required_true = {
            "historical_constituents_included": historical_constituents_included,
            "removed_securities_included": removed_securities_included,
            "delisted_securities_included": delisted_securities_included,
            "corporate_actions_included": corporate_actions_included,
            "terminal_outcome_support_documented": terminal_outcome_support_documented,
        }
        if any(value is not True for value in required_true.values()):
            raise ValueError("approved replay data must cover constituents, removals, delistings, actions and outcomes")
        provider = _required(provider_name, "provider_name", 200)
        plan = _required(product_or_plan, "product_or_plan", 200)
        terms_hash = _sha256(terms_content_sha256, "terms_content_sha256")
        resolved_provider_url = _https(provider_url, "provider_url")
        resolved_terms_url = _https(terms_url, "terms_url")
        resolved_hosts = _hosts(approved_data_hosts)
        resolved_universes = _set(
            approved_universes, "approved_universes", SUPPORTED_UNIVERSES
        )
        resolved_uses = _set(permitted_uses, "permitted_uses", PERMITTED_USES)
        qualification = self._qualification(
            qualification_id=qualification_id,
            qualification_record_hash=qualification_record_hash,
            provider=provider,
            product=plan,
            terms_url=resolved_terms_url,
            terms_hash=terms_hash,
            hosts=resolved_hosts,
            universes=resolved_universes,
            uses=resolved_uses,
            coverage_start=start,
            coverage_end=end,
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "approval_id": _approval_id(provider, plan, terms_hash, approved.isoformat()),
            "record_type": "HUMAN_HISTORICAL_DATA_SOURCE_APPROVAL",
            "status": "APPROVED_FOR_BOUNDED_LOCAL_REPLAY_INPUTS",
            "approved_at": approved.isoformat(),
            "approved_by": _required(approved_by, "approved_by", 100),
            "provider_name": provider,
            "product_or_plan": plan,
            "qualification_id": qualification["qualification_id"],
            "qualification_record_hash": qualification["record_hash"],
            "provider_url": resolved_provider_url,
            "terms_url": resolved_terms_url,
            "terms_content_sha256": terms_hash,
            "terms_review_reference": _required(terms_review_reference, "terms_review_reference"),
            "approved_data_hosts": resolved_hosts,
            "approved_universes": resolved_universes,
            "permitted_uses": resolved_uses,
            "coverage_not_before": start.isoformat(),
            "coverage_not_after": end.isoformat(),
            **required_true,
            "redistribution_allowed": redistribution_allowed is True,
            "source_quality_independently_proven": False,
            "subscription_purchased": False,
            "credentials_recorded": False,
            "data_fetched": False,
            "coverage_certificate_created": False,
            "replay_executed": False,
            "performance_claim_allowed": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _qualification(
        self,
        *,
        qualification_id: Any,
        qualification_record_hash: Any,
        provider: str,
        product: str,
        terms_url: Any,
        terms_hash: str,
        hosts: Sequence[str],
        universes: Sequence[str],
        uses: Sequence[str],
        coverage_start: date,
        coverage_end: date,
    ) -> dict[str, Any]:
        identifier = _required(qualification_id, "qualification_id")
        expected_hash = _sha256(qualification_record_hash, "qualification_record_hash")
        qualification = next(
            (
                item for item in self.qualification_ledger.verify()
                if item["qualification_id"] == identifier
            ),
            None,
        )
        if qualification is None or qualification["record_hash"] != expected_hash:
            raise ValueError("an exact verified provider qualification is required")
        if not qualification["qualification_passed"]:
            raise ValueError("provider qualification must pass before approval")
        terms_ref = next(
            item for item in qualification["evidence_references"]
            if item["evidence_kind"] == "TERMS"
        )
        terms = next(
            item for item in self.qualification_ledger.content_ledger.verify()
            if item["content_evidence_id"] == terms_ref["content_evidence_id"]
        )
        boundary = (
            qualification["provider_name"] == provider
            and qualification["product_or_plan"] == product
            and qualification["provider_hosts"] == _hosts(hosts)
            and set(_set(universes, "approved_universes", SUPPORTED_UNIVERSES))
            .issubset(qualification["evaluated_universes"])
            and set(_set(uses, "permitted_uses", PERMITTED_USES))
            .issubset(qualification["permitted_uses"])
            and _date(qualification["coverage_not_before"]) <= coverage_start
            and _date(qualification["coverage_not_after"]) >= coverage_end
            and terms["source_uri"] == _https(terms_url, "terms_url")
            and terms["source_input_sha256"] == terms_hash
            and terms["record_hash"] == terms_ref["content_record_hash"]
        )
        if not boundary:
            raise ValueError("approval scope must exactly derive from the passing qualification")
        return qualification

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Data-source approval {index} was modified.")
            try:
                provider = _required(record.get("provider_name"), "provider", 200)
                plan = _required(record.get("product_or_plan"), "plan", 200)
                approved = _timestamp(record.get("approved_at"))
                terms_hash = _sha256(record.get("terms_content_sha256"), "terms")
                _https(record.get("provider_url"), "provider_url")
                _https(record.get("terms_url"), "terms_url")
                _required(record.get("terms_review_reference"), "terms review")
                hosts = _hosts(record.get("approved_data_hosts") or [])
                _required(record.get("approved_by"), "approved_by", 100)
                universes = _set(record.get("approved_universes") or [], "universes", SUPPORTED_UNIVERSES)
                uses = _set(record.get("permitted_uses") or [], "uses", PERMITTED_USES)
                start = _date(record.get("coverage_not_before"))
                end = _date(record.get("coverage_not_after"))
                qualification = self._qualification(
                    qualification_id=record.get("qualification_id"),
                    qualification_record_hash=record.get("qualification_record_hash"),
                    provider=provider, product=plan,
                    terms_url=record.get("terms_url"), terms_hash=terms_hash,
                    hosts=hosts, universes=universes, uses=uses,
                    coverage_start=start, coverage_end=end,
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Data-source approval {index} is invalid.") from error
            expected = _approval_id(provider, plan, terms_hash, approved.isoformat())
            fixed_true = (
                "historical_constituents_included", "removed_securities_included",
                "delisted_securities_included", "corporate_actions_included",
                "terminal_outcome_support_documented",
            )
            fixed_false = (
                "source_quality_independently_proven", "subscription_purchased",
                "credentials_recorded", "data_fetched", "coverage_certificate_created",
                "replay_executed", "performance_claim_allowed", "broker_submission_enabled",
                "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("approval_id") == expected
                and expected not in seen
                and record.get("record_type") == "HUMAN_HISTORICAL_DATA_SOURCE_APPROVAL"
                and record.get("status") == "APPROVED_FOR_BOUNDED_LOCAL_REPLAY_INPUTS"
                and universes == record.get("approved_universes")
                and uses == record.get("permitted_uses")
                and hosts == record.get("approved_data_hosts")
                and record.get("qualification_id") == qualification["qualification_id"]
                and record.get("qualification_record_hash") == qualification["record_hash"]
                and end > start
                # Historical records remain verifiable after the append-time
                # freshness window has elapsed; only future-dated records fail here.
                and approved <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and isinstance(record.get("redistribution_allowed"), bool)
                and all(record.get(field) is True for field in fixed_true)
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Data-source approval {index} violates its boundary.")
            seen.add(expected)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["approval_id"] == result["approval_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Historical data-source approval already exists.")
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
