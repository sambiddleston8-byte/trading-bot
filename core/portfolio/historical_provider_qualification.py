from __future__ import annotations

"""Provider-neutral qualification before historical replay data can be approved."""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "historical-provider-qualification-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
REQUIRED_UNIVERSES = {"SP500", "NASDAQ100"}
REQUIRED_USES = {"LOCAL_RESEARCH", "HISTORICAL_REPLAY", "INTERNAL_DERIVED_RESULTS"}
REQUIRED_CAPABILITIES = {
    "HISTORICAL_CONSTITUENTS",
    "ADDITIONS_AND_REMOVALS",
    "DELISTED_SECURITIES",
    "TERMINAL_OUTCOMES",
    "CORPORATE_ACTIONS",
    "TOTAL_RETURN_PRICES",
    "POINT_IN_TIME_FUNDAMENTALS",
    "MARKET_CALENDARS_AND_HALTS",
    "STABLE_SECURITY_IDENTIFIERS",
    "REPRODUCIBLE_VERSIONED_EXPORT",
}
REQUIRED_CORPORATE_ACTIONS = {
    "CASH_DIVIDENDS", "STOCK_DIVIDENDS", "SPLITS", "MERGERS_AND_ACQUISITIONS",
    "SPINOFFS", "SYMBOL_CHANGES", "BANKRUPTCY_OR_LIQUIDATION",
}
REQUIRED_TIME_FIELDS = {
    "EFFECTIVE_AT", "PUBLICLY_AVAILABLE_AT", "RETRIEVED_AT",
}
EVIDENCE_KINDS = {"TERMS", "DOCUMENTATION", "SAMPLE"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        raise ValueError("coverage dates must not include time")
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("coverage dates must use YYYY-MM-DD") from error


def _values(
    values: Sequence[Any],
    name: str,
    allowed: set[str],
    *,
    allow_empty: bool = False,
) -> list[str]:
    result = sorted({_required(value, name, 100).upper() for value in values})
    if (not result and not allow_empty) or any(value not in allowed for value in result):
        raise ValueError(f"{name} contains unsupported or empty values")
    return result


def _hosts(values: Sequence[Any]) -> list[str]:
    hosts = sorted({_required(value, "provider_host", 253).lower() for value in values})
    if not hosts:
        raise ValueError("provider_hosts cannot be empty")
    for host in hosts:
        parsed = urlsplit(f"https://{host}")
        if parsed.hostname != host or parsed.port or "/" in host or "@" in host:
            raise ValueError("provider_hosts must contain bare lowercase hostnames")
    return hosts


def _evidence(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        identifier = _required(value.get("content_evidence_id"), "content_evidence_id")
        kind = _required(value.get("evidence_kind"), "evidence_kind", 30).upper()
        purpose = _required(value.get("purpose"), "evidence purpose", 300)
        if identifier in seen or kind not in EVIDENCE_KINDS:
            raise ValueError("evidence references contain a duplicate or unsupported kind")
        result.append({
            "content_evidence_id": identifier,
            "evidence_kind": kind,
            "purpose": purpose,
        })
        seen.add(identifier)
    if {item["evidence_kind"] for item in result} != EVIDENCE_KINDS:
        raise ValueError("terms, documentation and sample evidence are all required")
    return sorted(result, key=lambda item: (item["evidence_kind"], item["content_evidence_id"]))


def _capability_evidence(
    values: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    kinds = {item["content_evidence_id"]: item["evidence_kind"] for item in evidence}
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        capability = _required(value.get("capability"), "capability", 100).upper()
        documentation_id = _required(
            value.get("documentation_content_evidence_id"),
            "documentation_content_evidence_id",
        )
        sample_id = _required(
            value.get("sample_content_evidence_id"), "sample_content_evidence_id"
        )
        if capability not in REQUIRED_CAPABILITIES or capability in seen:
            raise ValueError("capability evidence contains an unsupported or duplicate capability")
        if kinds.get(documentation_id) != "DOCUMENTATION" or kinds.get(sample_id) != "SAMPLE":
            raise ValueError("each capability requires exact documentation and sample evidence")
        result.append({
            "capability": capability,
            "documentation_content_evidence_id": documentation_id,
            "sample_content_evidence_id": sample_id,
        })
        seen.add(capability)
    return sorted(result, key=lambda item: item["capability"])


def _limitations(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        description = _required(value.get("description"), "limitation description", 500)
        mitigation = _required(value.get("mitigation"), "limitation mitigation", 500)
        blocking = value.get("blocks_required_capability")
        if not isinstance(blocking, bool):
            raise ValueError("blocks_required_capability must be boolean")
        key = description.casefold()
        if key in seen:
            raise ValueError("known limitations must be unique")
        result.append({
            "description": description,
            "mitigation": mitigation,
            "blocks_required_capability": blocking,
        })
        seen.add(key)
    return sorted(result, key=lambda item: item["description"].casefold())


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete provider-qualification append")
        offset += count


class HistoricalProviderQualificationLedger:
    """Human-attested qualification; it neither approves, purchases nor fetches data."""

    def __init__(
        self,
        path: str | Path,
        content_ledger: AuthenticatedSourceContentLedger,
    ) -> None:
        self.path = Path(path)
        self.content_ledger = content_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Provider-qualification ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank qualification line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid qualification JSON at {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Qualification line {line_number} is not an object.")
                records.append(record)
        return records

    def qualify(
        self,
        *,
        provider_name: str,
        product_or_plan: str,
        provider_hosts: Sequence[str],
        evidence_references: Sequence[Mapping[str, Any]],
        permitted_uses: Sequence[str],
        evaluated_universes: Sequence[str],
        coverage_not_before: str | date,
        coverage_not_after: str | date,
        capability_evidence: Sequence[Mapping[str, Any]],
        corporate_action_types: Sequence[str],
        point_in_time_fields: Sequence[str],
        corrections_and_revisions_preserved: bool,
        delisted_history_retained: bool,
        terminal_zero_recovery_representable: bool,
        prices_adjustment_method_documented: bool,
        observed_sample_matches_documentation: bool,
        reproducible_export_available: bool,
        redistribution_allowed: bool,
        known_limitations: Sequence[Mapping[str, Any]],
        assessed_by: str,
        assessed_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        assessed = _timestamp(assessed_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= assessed <= now + MAX_CLOCK_SKEW:
            raise ValueError("assessed_at must match the actual append time")
        start = _date(coverage_not_before)
        end = _date(coverage_not_after)
        if end <= start:
            raise ValueError("coverage_not_after must be later than coverage_not_before")
        hosts = _hosts(provider_hosts)
        references = _evidence(evidence_references)
        evidence = self._resolve_evidence(references, hosts, assessed)
        universes = _values(
            evaluated_universes,
            "evaluated_universes",
            REQUIRED_UNIVERSES,
            allow_empty=True,
        )
        uses = _values(
            permitted_uses,
            "permitted_uses",
            REQUIRED_USES,
            allow_empty=True,
        )
        resolved_capability_evidence = _capability_evidence(capability_evidence, references)
        resolved_capabilities = [
            item["capability"] for item in resolved_capability_evidence
        ]
        actions = _values(
            corporate_action_types,
            "corporate_action_types",
            REQUIRED_CORPORATE_ACTIONS,
            allow_empty=True,
        )
        time_fields = _values(
            point_in_time_fields,
            "point_in_time_fields",
            REQUIRED_TIME_FIELDS,
            allow_empty=True,
        )
        limitations = _limitations(known_limitations)
        checks = {
            "required_internal_uses_permitted": set(uses) == REQUIRED_USES,
            "both_roadmap_universes_evaluated": set(universes) == REQUIRED_UNIVERSES,
            "all_required_capabilities_documented_and_sampled": (
                set(resolved_capabilities) == REQUIRED_CAPABILITIES
            ),
            "corporate_action_scope_complete": set(actions) == REQUIRED_CORPORATE_ACTIONS,
            "point_in_time_fields_complete": set(time_fields) == REQUIRED_TIME_FIELDS,
            "corrections_and_revisions_preserved": corrections_and_revisions_preserved is True,
            "delisted_history_retained": delisted_history_retained is True,
            "terminal_zero_recovery_representable": terminal_zero_recovery_representable is True,
            "prices_adjustment_method_documented": prices_adjustment_method_documented is True,
            "observed_sample_matches_documentation": observed_sample_matches_documentation is True,
            "reproducible_export_available": reproducible_export_available is True,
            "no_limitation_blocks_a_required_capability": not any(
                item["blocks_required_capability"] for item in limitations
            ),
        }
        passed = all(checks.values())
        identity = {
            "provider_name": _required(provider_name, "provider_name", 200),
            "product_or_plan": _required(product_or_plan, "product_or_plan", 200),
            "provider_hosts": hosts,
            "evidence_references": [
                {**reference, "content_record_hash": evidence[
                    reference["content_evidence_id"]
                ]["record_hash"]}
                for reference in references
            ],
            "permitted_uses": uses,
            "evaluated_universes": universes,
            "coverage_not_before": start.isoformat(),
            "coverage_not_after": end.isoformat(),
            "capabilities": resolved_capabilities,
            "capability_evidence": resolved_capability_evidence,
            "corporate_action_types": actions,
            "point_in_time_fields": time_fields,
            "corrections_and_revisions_preserved": corrections_and_revisions_preserved is True,
            "delisted_history_retained": delisted_history_retained is True,
            "terminal_zero_recovery_representable": terminal_zero_recovery_representable is True,
            "prices_adjustment_method_documented": prices_adjustment_method_documented is True,
            "observed_sample_matches_documentation": observed_sample_matches_documentation is True,
            "reproducible_export_available": reproducible_export_available is True,
            "redistribution_allowed": redistribution_allowed is True,
            "known_limitations": limitations,
            "qualification_checks": checks,
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "qualification_id": "HPQ-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper(),
            "record_type": "HISTORICAL_PROVIDER_QUALIFICATION_REPORT",
            "status": "PASSED_AWAITING_HUMAN_APPROVAL" if passed else "FAILED",
            "assessed_at": assessed.isoformat(),
            "assessed_by": _required(assessed_by, "assessed_by", 100),
            **identity,
            "qualification_passed": passed,
            "eligible_for_separate_human_approval": passed,
            "source_semantics_human_attested": True,
            "source_quality_independently_proven": False,
            "provider_approved": False,
            "subscription_purchased": False,
            "credentials_recorded": False,
            "data_fetched_by_qualification": False,
            "evaluation_dataset_opened": False,
            "replay_executed": False,
            "performance_claim_allowed": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
        return self._append(record, allow_existing=allow_existing)

    def _resolve_evidence(
        self,
        references: Sequence[Mapping[str, str]],
        hosts: Sequence[str],
        assessed_at: datetime,
    ) -> dict[str, dict[str, Any]]:
        records = {
            item["content_evidence_id"]: item for item in self.content_ledger.verify()
        }
        selected: dict[str, dict[str, Any]] = {}
        for reference in references:
            identifier = reference["content_evidence_id"]
            record = records.get(identifier)
            if record is None:
                raise ValueError("every qualification reference must use authenticated evidence")
            if urlsplit(record["source_uri"]).hostname not in hosts:
                raise ValueError("qualification evidence host is outside the assessed provider")
            if _timestamp(record["retrieved_at"]) > assessed_at:
                raise ValueError("qualification evidence cannot come from the future")
            selected[identifier] = record
        return selected

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
                raise LedgerIntegrityError(f"Provider qualification {index} was modified.")
            try:
                assessed = _timestamp(record.get("assessed_at"))
                hosts = _hosts(record.get("provider_hosts") or [])
                references = _evidence(record.get("evidence_references") or [])
                evidence = self._resolve_evidence(references, hosts, assessed)
                universes = _values(
                    record.get("evaluated_universes") or [],
                    "evaluated_universes", REQUIRED_UNIVERSES,
                    allow_empty=True,
                )
                uses = _values(
                    record.get("permitted_uses") or [],
                    "permitted_uses",
                    REQUIRED_USES,
                    allow_empty=True,
                )
                capability_evidence = _capability_evidence(
                    record.get("capability_evidence") or [], references
                )
                capabilities = [item["capability"] for item in capability_evidence]
                actions = _values(
                    record.get("corporate_action_types") or [],
                    "corporate_action_types", REQUIRED_CORPORATE_ACTIONS,
                    allow_empty=True,
                )
                time_fields = _values(
                    record.get("point_in_time_fields") or [],
                    "point_in_time_fields", REQUIRED_TIME_FIELDS,
                    allow_empty=True,
                )
                start = _date(record.get("coverage_not_before"))
                end = _date(record.get("coverage_not_after"))
                limitations = _limitations(record.get("known_limitations") or [])
                _required(record.get("provider_name"), "provider_name", 200)
                _required(record.get("product_or_plan"), "product_or_plan", 200)
                _required(record.get("assessed_by"), "assessed_by", 100)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Provider qualification {index} is invalid.") from error
            evidence_refs = [
                {
                    **reference,
                    "content_record_hash": evidence[reference["content_evidence_id"]]["record_hash"],
                }
                for reference in references
            ]
            checks = {
                "required_internal_uses_permitted": set(uses) == REQUIRED_USES,
                "both_roadmap_universes_evaluated": set(universes) == REQUIRED_UNIVERSES,
                "all_required_capabilities_documented_and_sampled": (
                    set(capabilities) == REQUIRED_CAPABILITIES
                ),
                "corporate_action_scope_complete": set(actions) == REQUIRED_CORPORATE_ACTIONS,
                "point_in_time_fields_complete": set(time_fields) == REQUIRED_TIME_FIELDS,
                **{
                    field: record.get(field) is True
                    for field in (
                        "corrections_and_revisions_preserved", "delisted_history_retained",
                        "terminal_zero_recovery_representable",
                        "prices_adjustment_method_documented",
                        "observed_sample_matches_documentation",
                        "reproducible_export_available",
                    )
                },
                "no_limitation_blocks_a_required_capability": not any(
                    item["blocks_required_capability"] for item in limitations
                ),
            }
            passed = all(checks.values())
            identity = {
                key: record.get(key) for key in (
                    "provider_name", "product_or_plan", "provider_hosts",
                    "permitted_uses", "evaluated_universes", "coverage_not_before",
                    "coverage_not_after", "capabilities", "corporate_action_types",
                    "capability_evidence",
                    "point_in_time_fields", "corrections_and_revisions_preserved",
                    "delisted_history_retained", "terminal_zero_recovery_representable",
                    "prices_adjustment_method_documented", "observed_sample_matches_documentation",
                    "reproducible_export_available", "redistribution_allowed", "known_limitations",
                    "qualification_checks",
                )
            }
            identity["evidence_references"] = evidence_refs
            expected = "HPQ-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper()
            fixed_false = (
                "source_quality_independently_proven", "provider_approved",
                "subscription_purchased", "credentials_recorded",
                "data_fetched_by_qualification", "evaluation_dataset_opened",
                "replay_executed", "performance_claim_allowed",
                "broker_submission_enabled", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("record_type") == "HISTORICAL_PROVIDER_QUALIFICATION_REPORT"
                and record.get("qualification_id") == expected
                and expected not in seen
                and record.get("qualification_checks") == checks
                and record.get("qualification_passed") is passed
                and record.get("eligible_for_separate_human_approval") is passed
                and record.get("status") == (
                    "PASSED_AWAITING_HUMAN_APPROVAL" if passed else "FAILED"
                )
                and record.get("source_semantics_human_attested") is True
                and end > start
                and assessed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and limitations == record.get("known_limitations")
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Provider qualification {index} violates its boundary.")
            seen.add(expected)
            previous_hash = record["record_hash"]
        return records

    def _append(self, record: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["qualification_id"] == record["qualification_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "assessed_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in record.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Historical provider qualification already exists.")
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            result = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(result) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
