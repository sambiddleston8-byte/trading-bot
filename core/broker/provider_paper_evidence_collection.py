from __future__ import annotations

"""Read-only Alpaca PAPER collection with private, locally attested retention."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from core.broker.alpaca_paper import AlpacaPaperConfiguration, TICKER_PATTERN
from core.broker.paper_broker_capture import (
    MAX_CAPTURE_BYTES,
    MAX_LEDGER_BYTES,
    _account_hash,
    _check_regular_descriptor,
    _fsync_directory,
    _no_follow,
    _payload_object,
    _private_directory,
    _read_private_file,
    _write_all,
)
from core.data_sources.provider_configuration import ProviderConfiguration
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-readonly-collection-bundle-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_COLLECTION_WINDOW = timedelta(seconds=30)
PART_KINDS = ("ACCOUNT_OPEN", "POSITIONS", "OPEN_ORDERS", "ACCOUNT_CLOSE")
PART_REQUESTS = {
    "ACCOUNT_OPEN": ("/v2/account", ""),
    "POSITIONS": ("/v2/positions", ""),
    "OPEN_ORDERS": (
        "/v2/orders",
        "direction=asc&limit=500&nested=false&status=open",
    ),
    "ACCOUNT_CLOSE": ("/v2/account", ""),
}
OPEN_ORDER_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "held",
        "pending_cancel",
        "pending_replace",
        "stopped",
        "suspended",
        "done_for_day",
    }
)
TRUE_FIELDS = {
    "collection_bytes_retained": True,
    "payload_hash_verified": True,
    "local_collector_attestation_present": True,
    "local_collector_attests_official_paper_https_endpoint": True,
    "local_collector_attests_read_only_get_paths": True,
    "account_identity_bracketed": True,
    "single_collection_window_bound": True,
    "local_collector_attests_credentials_present": True,
    "local_collector_attests_get_calls_completed": True,
}
FALSE_FIELDS = {
    "transport_authentication_evidence_retained": False,
    "broker_signature_present": False,
    "broker_origin_nonrepudiable": False,
    "local_hash_proves_broker_authenticity": False,
    "positions_orders_account_field_present": False,
    "semantic_claim_validated": False,
    "broker_reconciliation_complete": False,
    "settlement_breakdown_available": False,
    "previous_close_available": False,
    "external_head_anchor_present": False,
    "credentials_stored": False,
    "credentials_returned": False,
    "source_payload_returned": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "order_route_exists": False,
    "order_submitted": False,
    "paper_order_submission_allowed": False,
    "recommendation_provided": False,
    "human_review_eligible": False,
    "live_trading_enabled": False,
}
BOOLEAN_FIELDS = {**TRUE_FIELDS, **FALSE_FIELDS}
PART_FIELDS = frozenset(
    {
        "part_kind",
        "request_path",
        "request_query",
        "http_status",
        "fetched_at",
        "payload_sha256",
        "byte_length",
        "blob_relative_path",
    }
)
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "bundle_id",
        "record_type",
        "status",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "collection_started_at",
        "collection_finished_at",
        "recorded_at",
        "monotonic_elapsed_microseconds",
        "parts",
        "next_authority",
        "previous_hash",
        "record_hash",
    }
    | set(BOOLEAN_FIELDS)
)


class PaperEvidenceCollectionError(RuntimeError):
    """Secret-safe failure from the paper-only read path."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime:
    resolved = datetime.fromisoformat(canonical_timestamp(value))
    if resolved.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return resolved


def _exact_string(value: Any, name: str, maximum: int = 300) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{name} must be an exact non-empty string")
    return value


def _validate_account(payload: bytes) -> tuple[dict[str, Any], str]:
    parsed = _payload_object(payload)
    if parsed.get("status") != "ACTIVE" or not isinstance(parsed.get("status"), str):
        raise ValueError("paper account status must be exact ACTIVE")
    if parsed.get("currency") != "USD" or not isinstance(parsed.get("currency"), str):
        raise ValueError("paper account currency must be exact USD")
    return parsed, _account_hash(payload)


def _strict_array(payload: bytes, label: str) -> list[Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"{label} payload contains duplicate JSON keys")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"{label} payload contains non-standard JSON constant {value}")

    try:
        parsed = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} payload must be a UTF-8 JSON array") from error
    if not isinstance(parsed, list):
        raise ValueError(f"{label} payload must be a UTF-8 JSON array")
    return parsed


def _validate_positions(payload: bytes) -> None:
    positions = _strict_array(payload, "positions")
    symbols: set[str] = set()
    for entry in positions:
        if not isinstance(entry, Mapping):
            raise ValueError("each paper position must be an object")
        symbol = _exact_string(entry.get("symbol"), "position symbol", 15)
        if TICKER_PATTERN.fullmatch(symbol) is None or symbol in symbols:
            raise ValueError("paper positions must have unique valid symbols")
        if entry.get("side") != "long" or not isinstance(entry.get("side"), str):
            raise ValueError("paper positions must be long-only")
        symbols.add(symbol)


def _validate_open_orders(payload: bytes) -> None:
    orders = _strict_array(payload, "open-orders")
    if len(orders) >= 500:
        raise ValueError("ORDER_PAGE_INCOMPLETE")
    identities: set[str] = set()
    for entry in orders:
        if not isinstance(entry, Mapping):
            raise ValueError("each open paper order must be an object")
        identity = _exact_string(entry.get("id"), "open order id", 200)
        symbol = _exact_string(entry.get("symbol"), "open order symbol", 15)
        status_value = entry.get("status")
        if identity in identities:
            raise ValueError("open paper order ids must be unique")
        if TICKER_PATTERN.fullmatch(symbol) is None:
            raise ValueError("open paper order symbol is invalid")
        if not isinstance(status_value, str) or status_value not in OPEN_ORDER_STATUSES:
            raise ValueError("open paper order status is not open")
        identities.add(identity)


def _validate_part(kind: str, payload: bytes) -> str | None:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CAPTURE_BYTES:
        raise ValueError("each paper evidence payload must contain 1 byte to 2 MiB")
    if kind in {"ACCOUNT_OPEN", "ACCOUNT_CLOSE"}:
        _, account_hash = _validate_account(payload)
        return account_hash
    if kind == "POSITIONS":
        _validate_positions(payload)
        return None
    if kind == "OPEN_ORDERS":
        _validate_open_orders(payload)
        return None
    raise ValueError("paper evidence part kind is not permitted")


def _bundle_id(
    account_hash: str,
    parts: list[dict[str, Any]],
    started: str,
    finished: str,
    monotonic_elapsed_microseconds: int,
) -> str:
    material = [
        account_hash,
        parts,
        started,
        finished,
        monotonic_elapsed_microseconds,
        POLICY_VERSION,
    ]
    return "PPRC-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class PaperReadOnlyCollectionBundleLedger:
    """Retain one complete local three-part collection without claiming broker proof."""

    def __init__(self, path: str | Path, blob_directory: str | Path) -> None:
        self.path = Path(path)
        self.blob_directory = Path(blob_directory)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name="Paper read-only collection ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper collection ledger is unsafe.") from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Paper collection ledger has an incomplete final line.")
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError("Paper collection ledger is not UTF-8.") from error
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(f"Blank paper collection at line {index}.")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper collection JSON at line {index}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(f"Paper collection {index} is not an object.")
            records.append(record)
        return records

    def _admit(
        self,
        *,
        payloads: Mapping[str, bytes],
        fetched_at: Mapping[str, str | datetime],
        collection_started_at: str | datetime,
        collection_finished_at: str | datetime,
        recorded_at: str | datetime,
        monotonic_elapsed_microseconds: int,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        """Internal writer for observations made by ``AlpacaPaperReadOnlyCollector``."""

        if set(payloads) != set(PART_KINDS) or set(fetched_at) != set(PART_KINDS):
            raise ValueError("a complete collection requires exactly four paper responses")
        if type(monotonic_elapsed_microseconds) is not int or not (
            0 <= monotonic_elapsed_microseconds <= 30_000_000
        ):
            raise ValueError("monotonic collection window must not exceed 30 seconds")
        started = _timestamp(collection_started_at)
        finished = _timestamp(collection_finished_at)
        recorded = _timestamp(recorded_at)
        now = datetime.now(timezone.utc)
        if not started <= finished <= recorded <= now + MAX_CLOCK_SKEW:
            raise ValueError("paper collection times are not chronological")
        if finished - started > MAX_COLLECTION_WINDOW:
            raise ValueError("paper collection wall-clock window exceeds 30 seconds")
        fetched = {kind: _timestamp(fetched_at[kind]) for kind in PART_KINDS}
        if any(not started <= value <= finished for value in fetched.values()):
            raise ValueError("paper part timestamp is outside its collection window")
        account_hash = _validate_part("ACCOUNT_OPEN", payloads["ACCOUNT_OPEN"])
        closing_hash = _validate_part("ACCOUNT_CLOSE", payloads["ACCOUNT_CLOSE"])
        assert account_hash is not None and closing_hash is not None
        if closing_hash != account_hash:
            raise ValueError("the account identity must bracket the paper collection")
        for kind in ("POSITIONS", "OPEN_ORDERS"):
            _validate_part(kind, payloads[kind])
        parts: list[dict[str, Any]] = []
        for kind in PART_KINDS:
            body = payloads[kind]
            digest = hashlib.sha256(body).hexdigest()
            path, query = PART_REQUESTS[kind]
            parts.append(
                {
                    "part_kind": kind,
                    "request_path": path,
                    "request_query": query,
                    "http_status": 200,
                    "fetched_at": fetched[kind].isoformat(),
                    "payload_sha256": digest,
                    "byte_length": len(body),
                    "blob_relative_path": f"sha256/{digest[:2]}/{digest}.blob",
                }
            )
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "bundle_id": _bundle_id(
                account_hash,
                parts,
                started.isoformat(),
                finished.isoformat(),
                monotonic_elapsed_microseconds,
            ),
            "record_type": "PROVIDER_PAPER_READONLY_COLLECTION_BUNDLE",
            "status": "COLLECTION_BYTES_RETAINED_LOCAL_ATTESTATION_ONLY",
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": account_hash,
            "collection_started_at": started.isoformat(),
            "collection_finished_at": finished.isoformat(),
            "recorded_at": recorded.isoformat(),
            "monotonic_elapsed_microseconds": monotonic_elapsed_microseconds,
            "parts": parts,
            "next_authority": "AUTHENTICATED_SEMANTIC_RECONCILIATION_REQUIRED",
            **TRUE_FIELDS,
            **FALSE_FIELDS,
        }
        return self._append(result, dict(payloads), allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        prior_by_account: dict[str, datetime] = {}
        records = self.records()
        root = self.blob_directory.resolve()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Paper collection {index} was modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("paper collection field alphabet is invalid")
                if any(record.get(key) is not value for key, value in BOOLEAN_FIELDS.items()):
                    raise ValueError("paper collection authority flags are invalid")
                parts = record.get("parts")
                if not isinstance(parts, list) or len(parts) != 4:
                    raise ValueError("paper collection parts are incomplete")
                payloads: dict[str, bytes] = {}
                expected_kinds: list[str] = []
                for part in parts:
                    if not isinstance(part, dict) or set(part) != PART_FIELDS:
                        raise ValueError("paper collection part is invalid")
                    kind = part.get("part_kind")
                    if kind not in PART_KINDS or kind in expected_kinds:
                        raise ValueError("paper collection part kind is invalid")
                    expected_kinds.append(kind)
                    digest = _exact_string(part.get("payload_sha256"), "payload hash", 64)
                    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        raise ValueError("payload hash must be lowercase SHA-256")
                    relative = Path(_exact_string(part.get("blob_relative_path"), "blob path"))
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("paper collection blob path is unsafe")
                    if relative != Path(f"sha256/{digest[:2]}/{digest}.blob"):
                        raise ValueError("paper collection blob path is not content-addressed")
                    blob = self.blob_directory / relative
                    if blob.resolve() != root / relative:
                        raise ValueError("paper collection blob escaped its content root")
                    body = _read_private_file(
                        blob,
                        mode=0o400,
                        name="Paper collection blob",
                        maximum_bytes=MAX_CAPTURE_BYTES,
                    )
                    if hashlib.sha256(body).hexdigest() != digest:
                        raise ValueError("paper collection blob hash changed")
                    if type(part.get("byte_length")) is not int or len(body) != part["byte_length"]:
                        raise ValueError("paper collection blob length changed")
                    path, query = PART_REQUESTS[kind]
                    if (
                        part.get("request_path") != path
                        or part.get("request_query") != query
                        or type(part.get("http_status")) is not int
                        or part.get("http_status") != 200
                    ):
                        raise ValueError("paper collection endpoint metadata changed")
                    payloads[kind] = body
                if expected_kinds != list(PART_KINDS):
                    raise ValueError("paper collection part ordering is invalid")
                account_hash = _validate_part("ACCOUNT_OPEN", payloads["ACCOUNT_OPEN"])
                closing_hash = _validate_part("ACCOUNT_CLOSE", payloads["ACCOUNT_CLOSE"])
                if closing_hash != account_hash:
                    raise ValueError("paper account bracket changed")
                _validate_part("POSITIONS", payloads["POSITIONS"])
                _validate_part("OPEN_ORDERS", payloads["OPEN_ORDERS"])
                started = _timestamp(record.get("collection_started_at"))
                finished = _timestamp(record.get("collection_finished_at"))
                recorded = _timestamp(record.get("recorded_at"))
                fetched_values = [_timestamp(part["fetched_at"]) for part in parts]
                if not started <= finished <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                    raise ValueError("paper collection chronology changed")
                if finished - started > MAX_COLLECTION_WINDOW or any(
                    not started <= value <= finished for value in fetched_values
                ):
                    raise ValueError("paper collection window changed")
                elapsed = record.get("monotonic_elapsed_microseconds")
                if type(elapsed) is not int or not 0 <= elapsed <= 30_000_000:
                    raise ValueError("paper collection monotonic window changed")
                expected_id = _bundle_id(
                    account_hash,
                    parts,
                    started.isoformat(),
                    finished.isoformat(),
                    elapsed,
                )
                prior = prior_by_account.get(account_hash)
                boundary = (
                    record.get("schema_version") == SCHEMA_VERSION
                    and record.get("policy_version") == POLICY_VERSION
                    and record.get("bundle_id") == expected_id
                    and expected_id not in seen
                    and record.get("record_type") == "PROVIDER_PAPER_READONLY_COLLECTION_BUNDLE"
                    and record.get("status") == "COLLECTION_BYTES_RETAINED_LOCAL_ATTESTATION_ONLY"
                    and record.get("broker") == "Alpaca"
                    and record.get("broker_environment") == "PAPER"
                    and record.get("account_reference_sha256") == account_hash
                    and record.get("next_authority")
                    == "AUTHENTICATED_SEMANTIC_RECONCILIATION_REQUIRED"
                    and (prior is None or started > prior)
                )
            except (KeyError, LedgerIntegrityError, OSError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper collection {index} violates its boundary."
                ) from error
            if not boundary:
                raise LedgerIntegrityError(f"Paper collection {index} violates its boundary.")
            seen.add(expected_id)
            prior_by_account[account_hash] = started
            previous_hash = record["record_hash"]
        return records

    def read_verified(self, bundle_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        identity = _exact_string(bundle_id, "bundle_id")
        record = next((item for item in self.verify() if item["bundle_id"] == identity), None)
        if record is None:
            raise ValueError("paper collection bundle was not found")
        payloads = {
            part["part_kind"]: _read_private_file(
                self.blob_directory / part["blob_relative_path"],
                mode=0o400,
                name="Paper collection blob",
                maximum_bytes=MAX_CAPTURE_BYTES,
            )
            for part in record["parts"]
        }
        expected_hashes = {
            part["part_kind"]: part["payload_sha256"] for part in record["parts"]
        }
        if any(
            hashlib.sha256(payload).hexdigest() != expected_hashes[kind]
            for kind, payload in payloads.items()
        ):
            raise LedgerIntegrityError("Paper collection bytes changed after verification.")
        return record, payloads

    def _store_blob(self, path: Path, payload: bytes) -> None:
        _private_directory(self.blob_directory / "sha256")
        _private_directory(path.parent)
        if path.exists():
            existing = _read_private_file(
                path,
                mode=0o400,
                name="Paper collection blob",
                maximum_bytes=MAX_CAPTURE_BYTES,
            )
            if existing != payload:
                raise LedgerIntegrityError("Paper collection blob conflicts with its hash.")
            return
        temporary = path.parent / f".capture-{os.getpid()}-{time.monotonic_ns()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(), 0o400)
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            existing = _read_private_file(
                path,
                mode=0o400,
                name="Paper collection blob",
                maximum_bytes=MAX_CAPTURE_BYTES,
            )
            if existing != payload:
                raise LedgerIntegrityError("Paper collection blob conflicts with its hash.")
        finally:
            temporary.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise LedgerIntegrityError("Paper collection blob has unsafe links.")

    def _append(
        self, result: dict[str, Any], payloads: dict[str, bytes], *, allow_existing: bool
    ) -> dict[str, Any]:
        _private_directory(self.path.parent)
        _private_directory(self.blob_directory)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        except OSError as error:
            raise LedgerIntegrityError("Paper collection lock is unsafe.") from error
        try:
            _check_regular_descriptor(descriptor, mode=0o600, name="Paper collection lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            same_window = next(
                (
                    item
                    for item in records
                    if item["account_reference_sha256"] == result["account_reference_sha256"]
                    and item["collection_started_at"] == result["collection_started_at"]
                ),
                None,
            )
            if same_window is not None:
                if allow_existing and same_window["bundle_id"] == result["bundle_id"]:
                    return same_window
                raise LedgerIntegrityError("Paper collection conflicts at the same window.")
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"] == result["account_reference_sha256"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["collection_started_at"]) <= _timestamp(
                prior["collection_started_at"]
            ):
                raise ValueError("collection_started_at must move forward for each account")
            for part in result["parts"]:
                self._store_blob(
                    self.blob_directory / part["blob_relative_path"],
                    payloads[part["part_kind"]],
                )
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
                    target, mode=0o600, name="Paper read-only collection ledger"
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


class AlpacaPaperReadOnlyCollector:
    """Explicit injected-session GET collector; it exposes no order mutation method."""

    KEY_ENV = "ALPACA_PAPER_API_KEY"
    SECRET_ENV = "ALPACA_PAPER_API_SECRET"

    def __init__(
        self,
        *,
        ledger: PaperReadOnlyCollectionBundleLedger,
        session: Any,
        configuration: AlpacaPaperConfiguration | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if session is None:
            raise ValueError("an explicit read-only session is required")
        self.ledger = ledger
        self.session = session
        self.configuration = configuration or AlpacaPaperConfiguration.from_environment()
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    @property
    def configured(self) -> bool:
        ProviderConfiguration.load_local_environment()
        return bool(os.getenv(self.KEY_ENV) and os.getenv(self.SECRET_ENV))

    def collect_and_admit(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "NOT_CONFIGURED",
                "broker": "Alpaca",
                "broker_environment": "PAPER",
                "collection_recorded": False,
                "credentials_returned": False,
                "source_payload_returned": False,
                "order_submitted": False,
                "live_trading_enabled": False,
            }
        started = self.wall_clock()
        monotonic_started = self.monotonic_clock()
        payloads: dict[str, bytes] = {}
        fetched: dict[str, datetime] = {}
        account_body = self._get("ACCOUNT_OPEN")
        _, account_hash = _validate_account(account_body)
        payloads["ACCOUNT_OPEN"] = account_body
        fetched["ACCOUNT_OPEN"] = self.wall_clock()
        payloads["POSITIONS"] = self._get("POSITIONS")
        _validate_positions(payloads["POSITIONS"])
        fetched["POSITIONS"] = self.wall_clock()
        payloads["OPEN_ORDERS"] = self._get("OPEN_ORDERS")
        _validate_open_orders(payloads["OPEN_ORDERS"])
        fetched["OPEN_ORDERS"] = self.wall_clock()
        closing = self._get("ACCOUNT_CLOSE")
        _, closing_hash = _validate_account(closing)
        if closing_hash != account_hash:
            raise PaperEvidenceCollectionError("ACCOUNT identity changed during collection")
        finished = self.wall_clock()
        payloads["ACCOUNT_CLOSE"] = closing
        fetched["ACCOUNT_CLOSE"] = finished
        monotonic_finished = self.monotonic_clock()
        elapsed = monotonic_finished - monotonic_started
        if elapsed < 0 or elapsed > MAX_COLLECTION_WINDOW.total_seconds():
            raise PaperEvidenceCollectionError("COLLECTION monotonic window exceeded")
        if started > finished or finished - started > MAX_COLLECTION_WINDOW:
            raise PaperEvidenceCollectionError("COLLECTION wall-clock window exceeded")
        record = self.ledger._admit(
            payloads=payloads,
            fetched_at=fetched,
            collection_started_at=started,
            collection_finished_at=finished,
            recorded_at=finished,
            monotonic_elapsed_microseconds=int(round(elapsed * 1_000_000)),
        )
        return {
            "status": "RECORDED_LOCAL_ATTESTATION_ONLY",
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "collection_recorded": True,
            "bundle_id": record["bundle_id"],
            "record_hash": record["record_hash"],
            "credentials_returned": False,
            "source_payload_returned": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }

    def _get(self, kind: str) -> bytes:
        path, query = PART_REQUESTS[kind]
        url = f"{self.configuration.endpoint}{path}" + (f"?{query}" if query else "")
        for attempt in range(2):
            try:
                response = self.session.get(
                    url,
                    headers={
                        "APCA-API-KEY-ID": str(os.getenv(self.KEY_ENV)),
                        "APCA-API-SECRET-KEY": str(os.getenv(self.SECRET_ENV)),
                    },
                    timeout=20,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout) as error:
                error.request = None
                error.response = None
                error.args = ("paper collection connection failure",)
                if attempt == 0:
                    self.sleeper(1.0)
                    continue
                raise PaperEvidenceCollectionError(f"{kind} connection failed") from None
            except requests.RequestException as error:
                error.request = None
                error.response = None
                error.args = ("paper collection request failure",)
                raise PaperEvidenceCollectionError(f"{kind} request failed") from None
            status_code = getattr(response, "status_code", None)
            final_url = getattr(response, "url", None)
            if type(status_code) is not int:
                raise PaperEvidenceCollectionError(f"{kind} response status is unavailable")
            if 300 <= status_code < 400:
                raise PaperEvidenceCollectionError(f"{kind} redirect rejected (HTTP {status_code})")
            if status_code == 429 or 500 <= status_code <= 599:
                if attempt == 0:
                    self.sleeper(1.0)
                    continue
                raise PaperEvidenceCollectionError(f"{kind} retry failed (HTTP {status_code})")
            if status_code != 200:
                raise PaperEvidenceCollectionError(f"{kind} request rejected (HTTP {status_code})")
            if final_url != url:
                raise PaperEvidenceCollectionError(f"{kind} final endpoint changed")
            parsed = urlsplit(str(final_url))
            if (
                parsed.scheme != "https"
                or parsed.hostname != "paper-api.alpaca.markets"
                or parsed.port is not None
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise PaperEvidenceCollectionError(f"{kind} final endpoint is not official paper")
            body = getattr(response, "content", None)
            if not isinstance(body, bytes) or not body or len(body) > MAX_CAPTURE_BYTES:
                raise PaperEvidenceCollectionError(f"{kind} payload size is invalid")
            return body
        raise PaperEvidenceCollectionError(f"{kind} collection failed")
