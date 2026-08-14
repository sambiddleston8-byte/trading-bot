from __future__ import annotations

"""Resolve only provider-supported PAPER evidence; retain every unresolved gap."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.broker.paper_broker_capture import (
    MAX_LEDGER_BYTES,
    _check_regular_descriptor,
    _fsync_directory,
    _no_follow,
    _payload_object,
    _private_directory,
    _read_private_file,
    _write_all,
)
from core.broker.provider_paper_bundle_normalization import (
    ProviderPaperBundleNormalizationLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-external-evidence-resolution-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SETTLEMENT_REASON = "ALPACA_ACCOUNT_CASH_IS_NOT_SETTLEMENT_BREAKDOWN"
BINDING_REASON = "POSITION_ORDER_PAYLOADS_LACK_ACCOUNT_ID_OR_SIGNED_RECEIPT"
PREVIOUS_CLOSE_REASON = "EXACT_PREVIOUS_TRADING_DAY_CLOSE_TIMESTAMP_UNPROVEN"
TRUE_FIELDS = {
    "normalization_verified": True,
    "collection_bundle_reverified": True,
    "source_payload_hashes_reverified": True,
    "upstream_local_collector_attests_credentials_present": True,
    "provider_endpoints_documented_as_account_scoped": True,
}
FALSE_FIELDS = {
    "settlement_breakdown_available": False,
    "settled_cash_derived_from_account_cash": False,
    "positions_orders_account_id_present": False,
    "positions_orders_account_binding_proven": False,
    "transport_authentication_evidence_retained": False,
    "broker_signature_present": False,
    "broker_origin_nonrepudiable": False,
    "cryptographic_authentication_present": False,
    "broker_reconciliation_complete": False,
    "downstream_ledger_written": False,
    "account_snapshot_adoption_eligible": False,
    "risk_snapshot_adoption_eligible": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "order_submitted": False,
    "recommendation_provided": False,
    "live_trading_enabled": False,
    "external_head_anchor_present": False,
}
BOOLEAN_FIELDS = {**TRUE_FIELDS, **FALSE_FIELDS}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "resolution_id",
        "record_type",
        "status",
        "normalization_id",
        "normalization_record_hash",
        "collection_bundle_id",
        "collection_bundle_record_hash",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "account_open_payload_sha256",
        "account_close_payload_sha256",
        "positions_source_payload_sha256",
        "open_orders_source_payload_sha256",
        "observed_at",
        "recorded_at",
        "currency",
        "previous_close_status",
        "previous_close_available",
        "official_account_last_equity_semantics_applied",
        "previous_close_equity_usd",
        "previous_close_effective_at",
        "previous_close_source_field",
        "previous_close_reason_codes",
        "settlement_status",
        "settlement_reason_codes",
        "account_binding_status",
        "account_binding_reason_codes",
        "downstream_blocking_reasons",
        "exact_fractions",
        "next_authority",
        "previous_hash",
        "record_hash",
    }
    | set(BOOLEAN_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _amount(value: Any) -> Fraction:
    if isinstance(value, (bool, float)) or not isinstance(value, str):
        raise ValueError("last_equity must be an exact decimal string")
    if not value or value != value.strip():
        raise ValueError("last_equity must be an exact decimal string")
    try:
        resolved = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("last_equity must be an exact decimal string") from error
    parts = resolved.as_tuple()
    if (
        not resolved.is_finite()
        or resolved < 0
        or len(parts.digits) > 30
        or not -18 <= parts.exponent <= 18
    ):
        raise ValueError("last_equity must be a bounded non-negative decimal")
    return Fraction(resolved)


def _previous_close(
    opening: Mapping[str, Any], closing: Mapping[str, Any], _observed: datetime
) -> dict[str, Any]:
    if opening.get("last_equity") is None or closing.get("last_equity") is None:
        return {
            "status": "UNRESOLVED_SOURCE_FIELD_MISSING",
            "equity": None,
            "effective_at": None,
            "source_field": None,
            "reasons": ["LAST_EQUITY_MISSING"],
        }
    try:
        opened = _amount(opening.get("last_equity"))
        closed = _amount(closing.get("last_equity"))
    except ValueError:
        return {
            "status": "UNRESOLVED_SOURCE_FIELD_INVALID",
            "equity": None,
            "effective_at": None,
            "source_field": None,
            "reasons": ["PREVIOUS_CLOSE_SOURCE_FIELD_INVALID"],
        }
    if opened != closed:
        return {
            "status": "UNRESOLVED_BRACKET_VALUES_DISAGREE",
            "equity": None,
            "effective_at": None,
            "source_field": None,
            "reasons": ["PREVIOUS_CLOSE_BRACKET_MISMATCH"],
        }
    return {
        "status": "PROVIDER_VALUE_SUPPORTED_EFFECTIVE_TIME_UNRESOLVED",
        "equity": None,
        "effective_at": None,
        "source_field": "last_equity",
        "reasons": [PREVIOUS_CLOSE_REASON],
    }


def _resolution_id(material: list[Any]) -> str:
    return "PPER-" + hashlib.sha256(
        _canonical_json([*material, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperExternalEvidenceResolutionLedger:
    """Assess external-evidence gaps without fabricating settlement or binding."""

    def __init__(
        self,
        path: str | Path,
        normalization_ledger: ProviderPaperBundleNormalizationLedger,
    ) -> None:
        self.path = Path(path)
        self.normalization_ledger = normalization_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name="Paper external evidence resolution ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError(
                "Paper external evidence resolution ledger is unsafe."
            ) from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Paper external evidence resolution has an incomplete final line."
            )
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError(
                "Paper external evidence resolution ledger is not UTF-8."
            ) from error
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(
                    f"Blank paper external evidence resolution at line {index}."
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper external evidence resolution JSON at line {index}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(
                    f"Paper external evidence resolution {index} is not an object."
                )
            records.append(record)
        return records

    def record(
        self,
        *,
        normalization_id: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        result = self._build(normalization_id, recorded_at or datetime.now(timezone.utc))
        return self._append(result, allow_existing=allow_existing)

    def _build(self, normalization_id: Any, recorded_at: Any) -> dict[str, Any]:
        if (
            not isinstance(normalization_id, str)
            or not normalization_id
            or normalization_id != normalization_id.strip()
        ):
            raise ValueError("normalization_id must be an exact non-empty string")
        normalizations = self.normalization_ledger.verify()
        normalization = next(
            (item for item in normalizations if item["normalization_id"] == normalization_id),
            None,
        )
        if normalization is None:
            raise ValueError("normalization_id is not verified")
        if not normalization["normalization_succeeded"]:
            raise ValueError("only a successful normalization can be assessed")
        bundle, payloads = self.normalization_ledger.bundle_ledger.read_verified(
            normalization["collection_bundle_id"]
        )
        if bundle["record_hash"] != normalization["collection_bundle_record_hash"]:
            raise LedgerIntegrityError("Normalization collection binding changed.")
        opening = _payload_object(payloads["ACCOUNT_OPEN"])
        closing = _payload_object(payloads["ACCOUNT_CLOSE"])
        observed = _timestamp(normalization["observed_at"])
        recorded = _timestamp(recorded_at)
        if observed > recorded or recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("resolution times are reverse ordered or future dated")
        previous_close = _previous_close(opening, closing, observed)
        previous_close_available = False
        blockers = [
            "SETTLEMENT_BREAKDOWN_UNRESOLVED_PROVIDER_SEMANTICS",
            "POSITIONS_ORDERS_ACCOUNT_BINDING_UNRESOLVED",
        ]
        if not previous_close_available:
            blockers.append("PREVIOUS_CLOSE_EVIDENCE_UNRESOLVED")
        blockers.extend(
            reason
            for reason in normalization["downstream_blocking_reasons"]
            if reason == "NOTIONAL_ORDER_QUANTITY_EVIDENCE_REQUIRED"
        )
        blockers = sorted(set(blockers))
        hashes = {part["part_kind"]: part["payload_sha256"] for part in bundle["parts"]}
        identity = [
            normalization_id,
            normalization["record_hash"],
            previous_close["status"],
            blockers,
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "resolution_id": _resolution_id(identity),
            "record_type": "PROVIDER_PAPER_EXTERNAL_EVIDENCE_RESOLUTION",
            "status": "EXTERNAL_EVIDENCE_UNRESOLVED",
            "normalization_id": normalization_id,
            "normalization_record_hash": normalization["record_hash"],
            "collection_bundle_id": bundle["bundle_id"],
            "collection_bundle_record_hash": bundle["record_hash"],
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": normalization["account_reference_sha256"],
            "account_open_payload_sha256": hashes["ACCOUNT_OPEN"],
            "account_close_payload_sha256": hashes["ACCOUNT_CLOSE"],
            "positions_source_payload_sha256": hashes["POSITIONS"],
            "open_orders_source_payload_sha256": hashes["OPEN_ORDERS"],
            "observed_at": observed.isoformat(),
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            "previous_close_status": previous_close["status"],
            "previous_close_equity_usd": None,
            "previous_close_effective_at": None,
            "previous_close_source_field": previous_close["source_field"],
            "previous_close_reason_codes": previous_close["reasons"],
            "official_account_last_equity_semantics_applied": previous_close_available,
            "settlement_status": "UNRESOLVED_PROVIDER_SEMANTICS",
            "settlement_reason_codes": [SETTLEMENT_REASON],
            "account_binding_status": "UPSTREAM_LOCAL_CREDENTIAL_PRESENCE_ATTESTATION_ONLY",
            "account_binding_reason_codes": [BINDING_REASON],
            "downstream_blocking_reasons": blockers,
            "exact_fractions": {},
            "next_authority": "AUTHENTICATED_SETTLEMENT_AND_ACCOUNT_BINDING_EVIDENCE_REQUIRED",
            "previous_close_available": previous_close_available,
            **TRUE_FIELDS,
            **FALSE_FIELDS,
        }

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        previous_observed: dict[str, datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Paper external evidence resolution {index} was modified."
                )
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("external evidence field alphabet is invalid")
                if any(record.get(name) is not value for name, value in BOOLEAN_FIELDS.items()):
                    raise ValueError("external evidence authority flags changed")
                expected = self._build(record.get("normalization_id"), record.get("recorded_at"))
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("external evidence no longer matches source payloads")
                identity = record["resolution_id"]
                account = record["account_reference_sha256"]
                observed = _timestamp(record["observed_at"])
                if identity in seen:
                    raise ValueError("external evidence resolution identity is duplicated")
                if account in previous_observed and observed <= previous_observed[account]:
                    raise ValueError("external evidence observation did not move forward")
            except (KeyError, LedgerIntegrityError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper external evidence resolution {index} violates its boundary."
                ) from error
            seen.add(identity)
            previous_observed[account] = observed
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        except OSError as error:
            raise LedgerIntegrityError("Paper external evidence lock is unsafe.") from error
        try:
            _check_regular_descriptor(descriptor, mode=0o600, name="Paper external evidence lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["normalization_id"] == result["normalization_id"]
                ),
                None,
            )
            if existing is not None:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                left = {key: value for key, value in existing.items() if key not in ignored}
                right = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and left == right:
                    return existing
                raise LedgerIntegrityError("Paper external evidence conflicts with its source.")
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"] == result["account_reference_sha256"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior["observed_at"]
            ):
                raise ValueError("observed_at must move forward for each paper account")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(), 0o600
            )
            try:
                _check_regular_descriptor(
                    target, mode=0o600, name="Paper external evidence ledger"
                )
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.path.parent)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
