from __future__ import annotations

"""Reconcile retained paper-capture bytes against a normalized paper snapshot.

The reconciliation proves only that locally retained bytes and one verified
snapshot agree on five account fields.  It never authenticates the broker, the
transport or the account, and it never stores the account identifier or any
cash, buying-power or equity amount.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from core.broker.paper_broker_capture import (
    CAPTURE_KIND,
    MAX_LEDGER_BYTES,
    _check_regular_descriptor,
    _fsync_directory,
    _no_follow,
    _read_private_file,
    _write_all,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "paper-broker-account-reconciliation-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
LEDGER_NAME = "Paper-broker account reconciliation ledger"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DECIMAL_PATTERN = re.compile(r"^\d+(\.\d+)?$")
RECONCILED_AMOUNTS = ("cash", "buying_power", "equity")
TRUE_FIELDS = {
    "capture_bytes_locally_verified": True,
    "snapshot_record_verified": True,
    "account_identity_matched": True,
    "observed_at_matched": True,
    "account_status_matched": True,
    "account_currency_matched": True,
    "cash_matched": True,
    "buying_power_matched": True,
    "equity_matched": True,
    "canonical_payload_hash_matched": True,
}
FIXED_FIELDS = {
    "source_payload_copied": False,
    "transport_authenticated": False,
    "broker_origin_authenticated": False,
    "settlement_breakdown_reconciled": False,
    "positions_reconciled": False,
    "open_orders_reconciled": False,
    "previous_close_reconciled": False,
    "broker_reconciliation_complete": False,
    "cryptographic_authentication_present": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "order_route_exists": False,
    "credentials_accessed": False,
    "network_request_performed": False,
    "order_submitted": False,
    "paper_order_submission_allowed": False,
    "recommendation_provided": False,
    "human_review_eligible": False,
    "live_trading_enabled": False,
}
BOOLEAN_FIELDS = {**TRUE_FIELDS, **FIXED_FIELDS}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "reconciliation_id",
        "record_type",
        "status",
        "capture_kind",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "observed_at",
        "reconciled_at",
        "recorded_at",
        "capture_evidence_id",
        "capture_record_hash",
        "capture_payload_sha256",
        "captured_object_canonical_sha256",
        "snapshot_id",
        "snapshot_record_hash",
        "snapshot_source_payload_sha256",
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
    resolved = datetime.fromisoformat(canonical_timestamp(value))
    if resolved.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return resolved


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = value if isinstance(value, str) else ""
    if not resolved or resolved != resolved.strip() or len(resolved) > maximum:
        raise ValueError(f"{name} must be an exact non-empty string")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = value if isinstance(value, str) else ""
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _exact_string(source: Mapping[str, Any], field: str, expected: str, label: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or value != value.strip() or value != expected:
        raise ValueError(f"{label} {field} must be the exact string {expected}")
    return value


def _private_directory(path: Path) -> None:
    if not path.exists():
        parent = path.parent
        parent_details = parent.lstat()
        if (
            parent.is_symlink()
            or not stat.S_ISDIR(parent_details.st_mode)
            or parent_details.st_uid != os.geteuid()
            or stat.S_IMODE(parent_details.st_mode) & 0o022
        ):
            raise LedgerIntegrityError(
                "Paper-broker account reconciliation parent directory is unsafe."
            )
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise LedgerIntegrityError(
            "Paper-broker account reconciliation directory is unsafe."
        )
    if stat.S_IMODE(details.st_mode) != 0o700:
        raise LedgerIntegrityError(
            "Paper-broker account reconciliation directory must be owner-only."
        )


def _payload_object(payload: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("captured payload must not contain duplicate JSON keys")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"captured payload contains non-standard JSON constant {value}")

    try:
        parsed = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("captured payload must be a UTF-8 JSON object") from error
    if not isinstance(parsed, dict):
        raise ValueError("captured payload must be a UTF-8 JSON object")
    return parsed


def _captured_amount(source: Mapping[str, Any], field: str) -> Fraction:
    value = source.get(field)
    if (
        not isinstance(value, str)
        or value != value.strip()
        or DECIMAL_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(
            f"captured {field} must be an exact finite non-negative decimal string"
        )
    try:
        resolved = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            f"captured {field} must be an exact finite non-negative decimal string"
        ) from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(
            f"captured {field} must be an exact finite non-negative decimal string"
        )
    return Fraction(resolved)


def _snapshot_fraction(snapshot: Mapping[str, Any], field: str) -> Fraction:
    material = snapshot.get("exact_fractions")
    material = material.get(field) if isinstance(material, Mapping) else None
    if not isinstance(material, Mapping) or set(material) != {"numerator", "denominator"}:
        raise ValueError(f"snapshot {field} exact fraction has an invalid field alphabet")
    numerator = material["numerator"]
    denominator = material["denominator"]
    if (
        isinstance(numerator, (bool, float))
        or isinstance(denominator, (bool, float))
        or not isinstance(numerator, (int, str))
        or not isinstance(denominator, (int, str))
    ):
        raise ValueError(f"snapshot {field} exact fraction is invalid")
    numerator_text = str(numerator)
    denominator_text = str(denominator)
    if (
        not numerator_text.removeprefix("-").isdigit()
        or not denominator_text.isdigit()
        or int(denominator_text) <= 0
    ):
        raise ValueError(f"snapshot {field} exact fraction is invalid")
    return Fraction(int(numerator_text), int(denominator_text))


def _pin(
    records: Sequence[Mapping[str, Any]],
    identity: Any,
    record_hash: Any,
    *,
    id_field: str,
    label: str,
) -> Mapping[str, Any]:
    resolved, reasons = resolve_pinned_records(
        records, [identity], [record_hash], id_field=id_field, label=label
    )
    if reasons or len(resolved) != 1:
        raise ValueError(reasons[0] if reasons else f"{label} is not uniquely pinned")
    return resolved[0]


def _reconciliation_id(
    capture_evidence_id: str,
    capture_record_hash: str,
    snapshot_id: str,
    snapshot_record_hash: str,
) -> str:
    material = [
        capture_evidence_id,
        capture_record_hash,
        snapshot_id,
        snapshot_record_hash,
        POLICY_VERSION,
    ]
    return "PBARC-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class PaperBrokerAccountReconciliationLedger:
    """Prove five account fields agree between retained bytes and one snapshot."""

    def __init__(
        self,
        path: str | Path,
        capture_ledger: Any,
        snapshot_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.capture_ledger = capture_ledger
        self.snapshot_ledger = snapshot_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name=LEDGER_NAME,
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError(f"{LEDGER_NAME} is unsafe.") from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Paper-broker account reconciliation has an incomplete final line."
            )
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError(f"{LEDGER_NAME} is not UTF-8.") from error
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(
                    f"Blank paper-broker account reconciliation at line {line_number}."
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper-broker account reconciliation JSON at line {line_number}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(
                    f"Paper-broker account reconciliation {line_number} is not an object."
                )
            records.append(record)
        return records

    def reconcile(
        self,
        *,
        capture_evidence_id: str,
        capture_record_hash: str,
        snapshot_id: str,
        snapshot_record_hash: str,
        reconciled_at: str | datetime,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        links = (
            _required(capture_evidence_id, "capture_evidence_id"),
            _sha256(capture_record_hash, "capture_record_hash"),
            _required(snapshot_id, "snapshot_id"),
            _sha256(snapshot_record_hash, "snapshot_record_hash"),
        )
        reconciled = _timestamp(reconciled_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        self._resolve_and_build(links, reconciled, recorded)
        return self._append(links, reconciled, recorded, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_sources: set[tuple[str, str]] = set()
        previous_observed: dict[str, datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Paper-broker account reconciliation {index} was modified."
                )
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                if any(
                    record.get(field) is not value
                    for field, value in BOOLEAN_FIELDS.items()
                ):
                    raise ValueError("record semantic flags are not exact booleans")
                links = (
                    _required(record.get("capture_evidence_id"), "capture_evidence_id"),
                    _sha256(record.get("capture_record_hash"), "capture_record_hash"),
                    _required(record.get("snapshot_id"), "snapshot_id"),
                    _sha256(record.get("snapshot_record_hash"), "snapshot_record_hash"),
                )
                expected = self._resolve_and_build(
                    links,
                    _timestamp(record.get("reconciled_at")),
                    _timestamp(record.get("recorded_at")),
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("record does not match its pinned reconciliation")
                identity = record["reconciliation_id"]
                sources = (links[0], links[2])
                if identity in seen_ids:
                    raise ValueError("reconciliation identity is duplicated")
                if sources[0] in {item[0] for item in seen_sources} or sources[1] in {
                    item[1] for item in seen_sources
                }:
                    raise ValueError("pinned reconciliation sources are re-used")
                account = record["account_reference_sha256"]
                observed = _timestamp(record["observed_at"])
                if account in previous_observed and observed <= previous_observed[account]:
                    raise ValueError("observed_at does not move forward for this account")
            except (AttributeError, KeyError, LedgerIntegrityError, OSError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper-broker account reconciliation {index} violates its boundary."
                ) from error
            seen_ids.add(identity)
            seen_sources.add(sources)
            previous_observed[account] = observed
            previous_hash = record["record_hash"]
        return records

    def _resolve_and_build(
        self,
        links: tuple[str, str, str, str],
        reconciled: datetime,
        recorded: datetime,
    ) -> dict[str, Any]:
        capture_evidence_id, capture_record_hash, snapshot_id, snapshot_record_hash = links
        capture = _pin(
            self.capture_ledger.verify(),
            capture_evidence_id,
            capture_record_hash,
            id_field="capture_evidence_id",
            label="paper-broker capture",
        )
        snapshot = _pin(
            self.snapshot_ledger.verify(),
            snapshot_id,
            snapshot_record_hash,
            id_field="snapshot_id",
            label="paper-account snapshot",
        )
        verified_capture, payload = self.capture_ledger.read_verified(capture_evidence_id)
        if verified_capture != capture:
            raise ValueError("Retained capture record changed during reconciliation")
        return self._build(capture, payload, snapshot, reconciled, recorded)

    def _build(
        self,
        capture: Mapping[str, Any],
        payload: bytes,
        snapshot: Mapping[str, Any],
        reconciled: datetime,
        recorded: datetime,
    ) -> dict[str, Any]:
        _exact_string(capture, "capture_kind", CAPTURE_KIND, "capture")
        _exact_string(capture, "broker", "Alpaca", "capture")
        _exact_string(capture, "broker_environment", "PAPER", "capture")
        _exact_string(snapshot, "broker", "Alpaca", "snapshot")
        _exact_string(snapshot, "broker_environment", "PAPER", "snapshot")
        _exact_string(snapshot, "currency", "USD", "snapshot")
        if snapshot.get("paper_account_confirmed") is not True:
            raise ValueError("snapshot must confirm an explicitly paper account")
        account = _sha256(
            capture.get("account_reference_sha256"), "capture account_reference_sha256"
        )
        if account != _sha256(
            snapshot.get("account_reference_sha256"), "snapshot account_reference_sha256"
        ):
            raise ValueError("Capture and snapshot account references do not match")
        observed_at = _required(capture.get("observed_at"), "capture observed_at")
        if observed_at != _required(
            snapshot.get("observed_at"), "snapshot observed_at"
        ) or _timestamp(observed_at) != _timestamp(snapshot["observed_at"]):
            raise ValueError("Capture and snapshot observed_at must be identical")
        observed = _timestamp(observed_at)

        parsed = _payload_object(payload)
        _exact_string(parsed, "status", "ACTIVE", "captured account")
        _exact_string(parsed, "currency", "USD", "captured account")
        captured = {name: _captured_amount(parsed, name) for name in RECONCILED_AMOUNTS}
        expected = {name: _snapshot_fraction(snapshot, name) for name in RECONCILED_AMOUNTS}
        for name in RECONCILED_AMOUNTS:
            if captured[name] != expected[name]:
                raise ValueError(f"Captured {name} does not match the snapshot exact fraction")
        total_cash = _snapshot_fraction(snapshot, "settled_cash") + _snapshot_fraction(
            snapshot, "unsettled_cash"
        )
        if captured["cash"] != total_cash:
            raise ValueError("Captured cash does not match the snapshot total cash")

        payload_hash = _sha256(capture.get("payload_sha256"), "payload_sha256")
        if hashlib.sha256(payload).hexdigest() != payload_hash:
            raise ValueError("Retained capture bytes do not match their payload hash")
        canonical_hash = hashlib.sha256(
            _canonical_json(parsed).encode("utf-8")
        ).hexdigest()
        source_hash = _sha256(
            snapshot.get("source_payload_sha256"), "source_payload_sha256"
        )
        if canonical_hash != source_hash:
            raise ValueError(
                "Canonical captured object hash does not match the snapshot source hash"
            )

        capture_recorded = _timestamp(capture.get("recorded_at"))
        snapshot_recorded = _timestamp(snapshot.get("recorded_at"))
        if reconciled < max(capture_recorded, snapshot_recorded, observed):
            raise ValueError("reconciled_at must follow every pinned source record")
        if reconciled > recorded:
            raise ValueError("reconciled_at cannot exceed recorded_at")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "reconciliation_id": _reconciliation_id(
                capture["capture_evidence_id"],
                capture["record_hash"],
                snapshot["snapshot_id"],
                snapshot["record_hash"],
            ),
            "record_type": "PAPER_BROKER_ACCOUNT_FIELD_RECONCILIATION",
            "status": "ACCOUNT_FIELDS_RECONCILED_UNAUTHENTICATED",
            "capture_kind": CAPTURE_KIND,
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": account,
            "observed_at": observed.isoformat(),
            "reconciled_at": reconciled.isoformat(),
            "recorded_at": recorded.isoformat(),
            "capture_evidence_id": capture["capture_evidence_id"],
            "capture_record_hash": capture["record_hash"],
            "capture_payload_sha256": payload_hash,
            "captured_object_canonical_sha256": canonical_hash,
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_record_hash": snapshot["record_hash"],
            "snapshot_source_payload_sha256": source_hash,
            "next_authority": "AUTHENTICATED_BROKER_RECONCILIATION_REQUIRED",
            **TRUE_FIELDS,
            **FIXED_FIELDS,
        }

    def _append(
        self,
        links: tuple[str, str, str, str],
        reconciled: datetime,
        recorded: datetime,
        *,
        allow_existing: bool,
    ) -> dict[str, Any]:
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        except OSError as error:
            raise LedgerIntegrityError(
                "Paper-broker account reconciliation lock is unsafe."
            ) from error
        try:
            _check_regular_descriptor(
                descriptor,
                mode=0o600,
                name="Paper-broker account reconciliation lock",
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            result = self._resolve_and_build(links, reconciled, recorded)
            existing = next(
                (
                    item
                    for item in records
                    if item["reconciliation_id"] == result["reconciliation_id"]
                    or item["capture_evidence_id"] == result["capture_evidence_id"]
                    or item["snapshot_id"] == result["snapshot_id"]
                ),
                None,
            )
            if existing is not None:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError(
                    "Paper-broker account reconciliation conflicts with an existing record."
                )
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
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
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                _check_regular_descriptor(target, mode=0o600, name=LEDGER_NAME)
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.path.parent)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
