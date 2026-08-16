"""Private, byte-bound account-entitlement evidence for Campaign v2 R2.

The boundary accepts only an exact JSON export captured from an authenticated
Massive account endpoint or dashboard.  JSON pointers are supplied with the
capture so provider schema changes fail closed rather than being guessed.  It
retains the exact credential-free bytes owner-only and derives the account,
plan, endpoint, lookback and zero-incremental-cost facts from those bytes.

Even complete evidence is only input to a later, separately reviewed capture-
activation record.  This module has no network, credential, market-data,
quarantine, replay, broker or order capability.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.campaign_v2_revision_2_public_documentation import (
    CampaignV2Revision2PublicDocumentationLedger,
)
from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    ACQUISITION_START,
    MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS,
    assert_capture_window_current,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-campaign-v2-r2-account-entitlement-v1"
EVENT_TYPE = "CAMPAIGN_V2_REVISION_2_ACCOUNT_ENTITLEMENT_BOUND"
PROVIDER_ID = "MASSIVE"
PREREGISTRATION_ID = "HQP2R2-4E0CA212575663AA7010D87EA36B58E5"
PREREGISTRATION_RECORD_SHA256 = (
    "48554e1ebdce8d9641362d1d9843c27bb8d07de2413db72c4b074166fcc70743"
)
PROPOSAL_SHA256 = (
    "cafbaef235d8379e29b17d057ac87a77c452260680afd20bf8c7e4fd24671654"
)
PUBLIC_DOCUMENTATION_EVIDENCE_ID = (
    "CV2R2DOC-C4321E5E3D2555A95EDB89499A1EA6A5"
)
PUBLIC_DOCUMENTATION_RECORD_SHA256 = (
    "0bbcde43deb5d5afc5977dad17949f6484905711cbe07d6d7af5ebe19cd39a99"
)
PUBLIC_DOCUMENTATION_BUNDLE_SHA256 = (
    "feae2c94bf9ee136e64409076f3ec5dc33f42902b566043cd71c66e61927761b"
)
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/account_entitlement/evidence.jsonl"
)
BLOB_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/account_entitlement/blobs"
)
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_SOURCE_KINDS = frozenset(
    {"OFFICIAL_AUTHENTICATED_ACCOUNT_ENDPOINT", "AUTHENTICATED_DASHBOARD_EXPORT"}
)
REQUIRED_FACTS = frozenset(
    {
        "account_identity",
        "plan_name",
        "daily_bars_access",
        "dividends_access",
        "stock_splits_access",
        "historical_lookback_start",
        "incremental_cost_usd",
    }
)
FIXED_FALSE = (
    "api_key_request_allowed",
    "credential_material_recorded",
    "capture_activation_issued",
    "data_calls_allowed",
    "provider_market_data_bytes_accessed",
    "train_validation_opened",
    "untouched_test_opened",
    "dataset_admitted",
    "evaluation_allowed",
    "guardrailed_replay_executed",
    "performance_claim_allowed",
    "aws_resource_creation_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
    "provider_origin_cryptographically_verified",
)
_CAPTURE_FIELDS = frozenset(
    {
        "captured_at",
        "source_kind",
        "source_uri",
        "payload_bytes",
        "fact_json_pointers",
        "authenticated_session_attested",
        "market_data_response_used_for_account_binding",
        "request_id_used_for_account_binding",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "provider_id",
        "assessed_at",
        "captured_at",
        "source_kind",
        "source_uri",
        "payload_sha256",
        "payload_byte_length",
        "fact_json_pointers",
        "account_identity_sha256",
        "plan_name",
        "daily_bars_access_confirmed",
        "dividends_access_confirmed",
        "stock_splits_access_confirmed",
        "historical_lookback_start",
        "rolling_lookback_buffer_days",
        "acquisition_start_covered",
        "capture_window_current_at_assessment",
        "capture_window_must_be_revalidated_at_activation",
        "incremental_cost_usd",
        "zero_incremental_cost_proven",
        "authenticated_session_attested",
        "authentication_basis",
        "provider_origin_cryptographically_verified",
        "credential_material_recorded",
        "market_data_response_used_for_account_binding",
        "request_id_used_for_account_binding",
        "evidence_complete_for_separate_capture_activation_record",
        "evidence_bundle_sha256",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "event_type",
        "recorded_at",
        "evidence_id",
        "preregistration_id",
        "preregistration_record_sha256",
        "proposal_sha256",
        "public_documentation_evidence_id",
        "public_documentation_record_sha256",
        "public_documentation_bundle_sha256",
        "account_entitlement_evidence",
        "blob_reference",
        *FIXED_FALSE,
        "previous_hash",
        "record_hash",
    }
)
_BLOB_REFERENCE_FIELDS = frozenset(
    {"payload_sha256", "byte_length", "blob_relative_path"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _canonical_text(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be canonical nonempty text")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} is invalid")
    return value


def _source_uri(value: Any, kind: str) -> str:
    uri = _canonical_text(value, "source_uri")
    parts = urlsplit(uri)
    allowed_hosts = (
        {"api.massive.com"}
        if kind == "OFFICIAL_AUTHENTICATED_ACCOUNT_ENDPOINT"
        else {"massive.com", "www.massive.com"}
    )
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError(
            "source_uri must be a credential-free official Massive origin"
        ) from error
    if (
        parts.scheme != "https"
        or parts.hostname not in allowed_hosts
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "source_uri must be a credential-free official Massive origin"
        )
    return uri


def _contains_credential_material(value: Any) -> bool:
    sensitive_key = re.compile(
        r"(?i).*(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret).*"
    )
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for key, child in current.items():
                if sensitive_key.fullmatch(str(key)) and child not in (None, "", False):
                    return True
                pending.append(child)
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str) and (
            re.search(r"(?i)authorization\s*:\s*bearer\s+\S+", current)
            or re.search(
                r"(?i)(?:api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9_-]{20,}",
                current,
            )
        ):
            return True
    return False


def _decode_pointer_segment(value: str) -> str:
    if re.search(r"~(?![01])", value):
        raise ValueError("JSON pointer contains an invalid escape")
    return value.replace("~1", "/").replace("~0", "~")


def _resolve_json_pointer(document: Any, pointer: Any, fact: str) -> Any:
    pointer_text = _canonical_text(pointer, f"fact_json_pointers.{fact}")
    if not pointer_text.startswith("/") or pointer_text.endswith("/"):
        raise ValueError(f"fact_json_pointers.{fact} must be an absolute JSON pointer")
    current = document
    for encoded in pointer_text[1:].split("/"):
        segment = _decode_pointer_segment(encoded)
        if isinstance(current, Mapping):
            if segment not in current:
                raise ValueError(f"JSON pointer for {fact} does not resolve")
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit() or (len(segment) > 1 and segment.startswith("0")):
                raise ValueError(f"JSON pointer for {fact} has an invalid array index")
            index = int(segment)
            if index >= len(current):
                raise ValueError(f"JSON pointer for {fact} does not resolve")
            current = current[index]
        else:
            raise ValueError(f"JSON pointer for {fact} traverses a scalar")
    return current


def _normalized_payload(payload: Any) -> tuple[bytes, Any]:
    if type(payload) is not bytes or not payload or len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload_bytes must be nonempty exact bytes within 2 MB")
    try:
        decoded = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("payload_bytes must be strict UTF-8 JSON") from error
    if not isinstance(decoded, Mapping):
        raise ValueError("authenticated account payload must be a JSON object")
    if _contains_credential_material(decoded):
        raise ValueError("credential material must not be retained")
    return payload, decoded


def build_revision_2_account_entitlement_evidence(
    capture: Mapping[str, Any], *, assessed_at: Any
) -> dict[str, Any]:
    """Derive every entitlement fact from exact authenticated export bytes."""

    if not isinstance(capture, Mapping) or set(capture) != _CAPTURE_FIELDS:
        raise ValueError("account entitlement capture fields differ from the contract")
    assessed = _timestamp(assessed_at, "assessed_at")
    assert_capture_window_current(as_of=assessed.date().isoformat())
    captured = _timestamp(capture["captured_at"], "captured_at")
    if captured > assessed:
        raise ValueError("authenticated capture cannot postdate assessment")
    kind = _canonical_text(capture["source_kind"], "source_kind")
    if kind not in ALLOWED_SOURCE_KINDS:
        raise ValueError("source_kind cannot prove authenticated account binding")
    uri = _source_uri(capture["source_uri"], kind)
    if capture["authenticated_session_attested"] is not True:
        raise ValueError("capture must be attested as an authenticated session export")
    if capture["market_data_response_used_for_account_binding"] is not False:
        raise ValueError("market-data responses cannot bind an account")
    if capture["request_id_used_for_account_binding"] is not False:
        raise ValueError("request IDs cannot bind an account")
    payload, decoded = _normalized_payload(capture["payload_bytes"])
    pointers = capture["fact_json_pointers"]
    if not isinstance(pointers, Mapping) or set(pointers) != REQUIRED_FACTS:
        raise ValueError("fact_json_pointers must cover the exact Revision-2 facts")
    canonical_pointers = {
        fact: _canonical_text(pointers[fact], f"fact_json_pointers.{fact}")
        for fact in sorted(REQUIRED_FACTS)
    }
    if len(set(canonical_pointers.values())) != len(REQUIRED_FACTS):
        raise ValueError("each Revision-2 fact requires a distinct JSON pointer")
    observed = {
        fact: _resolve_json_pointer(decoded, canonical_pointers[fact], fact)
        for fact in sorted(REQUIRED_FACTS)
    }
    account_identity = _canonical_text(
        observed["account_identity"], "account_identity", 1000
    )
    plan_name = _canonical_text(observed["plan_name"], "plan_name")
    for fact in ("daily_bars_access", "dividends_access", "stock_splits_access"):
        if observed[fact] is not True:
            raise ValueError(f"authenticated bytes do not confirm {fact}")
    lookback_text = _canonical_text(
        observed["historical_lookback_start"], "historical_lookback_start", 10
    )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lookback_text) is None:
        raise ValueError("historical_lookback_start must be an ISO date")
    try:
        lookback = date.fromisoformat(lookback_text)
    except ValueError as error:
        raise ValueError("historical_lookback_start must be an ISO date") from error
    acquisition_start = date.fromisoformat(ACQUISITION_START)
    latest_allowed_lookback = acquisition_start - timedelta(
        days=MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS
    )
    if lookback > latest_allowed_lookback:
        raise ValueError(
            "authenticated lookback does not preserve the Revision-2 rolling buffer"
        )
    cost_text = observed["incremental_cost_usd"]
    if not isinstance(cost_text, str):
        raise ValueError("incremental_cost_usd must be exact decimal text")
    try:
        cost = Decimal(cost_text)
    except InvalidOperation as error:
        raise ValueError("incremental_cost_usd must be exact decimal text") from error
    if cost_text != "0.00" or cost != Decimal("0.00"):
        raise ValueError("authenticated bytes must prove zero incremental cost")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "provider_id": PROVIDER_ID,
        "assessed_at": assessed.isoformat(timespec="microseconds"),
        "captured_at": captured.isoformat(timespec="microseconds"),
        "source_kind": kind,
        "source_uri": uri,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_byte_length": len(payload),
        "fact_json_pointers": canonical_pointers,
        "account_identity_sha256": hashlib.sha256(
            account_identity.encode("utf-8")
        ).hexdigest(),
        "plan_name": plan_name,
        "daily_bars_access_confirmed": True,
        "dividends_access_confirmed": True,
        "stock_splits_access_confirmed": True,
        "historical_lookback_start": lookback.isoformat(),
        "rolling_lookback_buffer_days": (acquisition_start - lookback).days,
        "acquisition_start_covered": True,
        "capture_window_current_at_assessment": True,
        "capture_window_must_be_revalidated_at_activation": True,
        "incremental_cost_usd": "0.00",
        "zero_incremental_cost_proven": True,
        "authenticated_session_attested": True,
        "authentication_basis": (
            "LOCAL_OPERATOR_CAPTURE_ATTESTATION_ONLY_PROVIDER_ORIGIN_UNVERIFIED"
        ),
        "provider_origin_cryptographically_verified": False,
        "credential_material_recorded": False,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
        "evidence_complete_for_separate_capture_activation_record": True,
    }
    result["evidence_bundle_sha256"] = _hash(result)
    return result


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Account-entitlement append made no progress")
        offset += count


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise LedgerIntegrityError("Account-entitlement directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("Account-entitlement directory must be owner-only")


def _assert_private_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise LedgerIntegrityError("Account-entitlement directory is unsafe") from error
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise LedgerIntegrityError("Account-entitlement directory is unsafe")


def _evidence_id(bundle_sha256: str) -> str:
    return "CV2R2ACCOUNT-" + _hash(
        [PREREGISTRATION_ID, PUBLIC_DOCUMENTATION_RECORD_SHA256, bundle_sha256, POLICY_VERSION]
    )[:32].upper()


class CampaignV2Revision2AccountEntitlementLedger:
    """Persist and reverify exactly one inert authenticated entitlement export."""

    def __init__(
        self,
        path: str | Path,
        blob_root: str | Path,
        *,
        repository_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        public_evidence_resolver: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.blob_root = Path(blob_root)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._public_evidence_resolver = (
            public_evidence_resolver or self._default_public_evidence_resolver
        )

    @classmethod
    def at_repository_root(
        cls, repository_root: str | Path
    ) -> "CampaignV2Revision2AccountEntitlementLedger":
        root = Path(repository_root).resolve()
        return cls(
            root / CONTROL_LEDGER_RELATIVE_PATH,
            root / BLOB_ROOT_RELATIVE_PATH,
            repository_root=root,
        )

    def _default_public_evidence_resolver(self) -> Mapping[str, Any]:
        return CampaignV2Revision2PublicDocumentationLedger.at_repository_root(
            self.repository_root
        ).require(PUBLIC_DOCUMENTATION_EVIDENCE_ID)

    def _verified_public_evidence(self) -> Mapping[str, Any]:
        parent = self._public_evidence_resolver()
        public = parent.get("public_documentation_evidence", {})
        if (
            parent.get("evidence_id") != PUBLIC_DOCUMENTATION_EVIDENCE_ID
            or parent.get("record_hash") != PUBLIC_DOCUMENTATION_RECORD_SHA256
            or parent.get("preregistration_id") != PREREGISTRATION_ID
            or parent.get("preregistration_record_sha256")
            != PREREGISTRATION_RECORD_SHA256
            or parent.get("proposal_sha256") != PROPOSAL_SHA256
            or public.get("evidence_bundle_sha256")
            != PUBLIC_DOCUMENTATION_BUNDLE_SHA256
            or parent.get("authenticated_account_evidence_collected") is not False
            or parent.get("data_calls_allowed") is not False
            or parent.get("evaluation_allowed") is not False
            or parent.get("orders_submitted") is not False
        ):
            raise LedgerIntegrityError("Revision-2 public evidence parent is invalid")
        return parent

    def _verified_blob(self, reference: Mapping[str, Any]) -> bytes:
        if set(reference) != _BLOB_REFERENCE_FIELDS:
            raise LedgerIntegrityError("Account-entitlement blob reference changed")
        digest = str(reference.get("payload_sha256", ""))
        if SHA256_PATTERN.fullmatch(digest) is None:
            raise LedgerIntegrityError("Account-entitlement blob hash is invalid")
        relative = Path("sha256") / digest[:2] / f"{digest}.json"
        if reference.get("blob_relative_path") != relative.as_posix():
            raise LedgerIntegrityError("Account-entitlement blob path changed")
        path = self.blob_root / relative
        try:
            _assert_private_directory(self.blob_root)
            _assert_private_directory(path.parent.parent)
            _assert_private_directory(path.parent)
            details = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) != 0o400
                or path.resolve() != self.blob_root.resolve() / relative
            ):
                raise ValueError("unsafe blob")
            payload = path.read_bytes()
        except (OSError, ValueError) as error:
            raise LedgerIntegrityError("Account-entitlement blob is missing or unsafe") from error
        if (
            len(payload) != reference.get("byte_length")
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            raise LedgerIntegrityError("Account-entitlement blob bytes changed")
        return payload

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        _assert_private_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_RDONLY | _no_follow())
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_uid != os.geteuid()
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError("Account-entitlement ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Account-entitlement ledger has an incomplete line")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"Account-entitlement line {line_number} is invalid"
                ) from error
            if not isinstance(row, dict):
                raise LedgerIntegrityError("Account-entitlement row is not an object")
            rows.append(row)
        self._verify(rows)
        return rows

    def _verify(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if len(rows) > 1:
            raise LedgerIntegrityError("Revision-2 permits one account evidence record")
        if not rows:
            return
        self._verified_public_evidence()
        row = rows[0]
        try:
            evidence = row.get("account_entitlement_evidence")
            reference = row.get("blob_reference")
            if not isinstance(evidence, Mapping) or not isinstance(reference, Mapping):
                raise ValueError("evidence shape")
            payload = self._verified_blob(reference)
            rebuilt = build_revision_2_account_entitlement_evidence(
                {
                    "captured_at": evidence["captured_at"],
                    "source_kind": evidence["source_kind"],
                    "source_uri": evidence["source_uri"],
                    "payload_bytes": payload,
                    "fact_json_pointers": evidence["fact_json_pointers"],
                    "authenticated_session_attested": evidence[
                        "authenticated_session_attested"
                    ],
                    "market_data_response_used_for_account_binding": evidence[
                        "market_data_response_used_for_account_binding"
                    ],
                    "request_id_used_for_account_binding": evidence[
                        "request_id_used_for_account_binding"
                    ],
                },
                assessed_at=evidence["assessed_at"],
            )
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            material = {key: value for key, value in row.items() if key != "record_hash"}
            valid = (
                set(row) == _RECORD_FIELDS
                and set(evidence) == _EVIDENCE_FIELDS
                and row.get("schema_version") == SCHEMA_VERSION
                and row.get("policy_version") == POLICY_VERSION
                and row.get("event_type") == EVENT_TYPE
                and row.get("previous_hash") == GENESIS_HASH
                and row.get("record_hash") == _hash(material)
                and row.get("preregistration_id") == PREREGISTRATION_ID
                and row.get("preregistration_record_sha256")
                == PREREGISTRATION_RECORD_SHA256
                and row.get("proposal_sha256") == PROPOSAL_SHA256
                and row.get("public_documentation_evidence_id")
                == PUBLIC_DOCUMENTATION_EVIDENCE_ID
                and row.get("public_documentation_record_sha256")
                == PUBLIC_DOCUMENTATION_RECORD_SHA256
                and row.get("public_documentation_bundle_sha256")
                == PUBLIC_DOCUMENTATION_BUNDLE_SHA256
                and evidence == rebuilt
                and reference.get("payload_sha256") == evidence["payload_sha256"]
                and reference.get("byte_length") == evidence["payload_byte_length"]
                and _timestamp(evidence["assessed_at"], "assessed_at")
                <= recorded
                <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and row.get("evidence_id")
                == _evidence_id(evidence["evidence_bundle_sha256"])
                and all(row.get(field) is False for field in FIXED_FALSE)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("Account-entitlement evidence is invalid") from error
        if not valid:
            raise LedgerIntegrityError("Account-entitlement boundary was violated")

    def register(
        self, capture: Mapping[str, Any], *, assessed_at: Any
    ) -> dict[str, Any]:
        self._verified_public_evidence()
        evidence = build_revision_2_account_entitlement_evidence(
            capture, assessed_at=assessed_at
        )
        recorded = _timestamp(self._clock(), "recording clock")
        actual_now = datetime.now(timezone.utc)
        if not actual_now - MAX_CLOCK_SKEW <= recorded <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("recording clock must match actual append time")
        if _timestamp(evidence["assessed_at"], "assessed_at") > recorded:
            raise ValueError("assessment must not occur after recording")
        digest = evidence["payload_sha256"]
        reference = {
            "payload_sha256": digest,
            "byte_length": evidence["payload_byte_length"],
            "blob_relative_path": (
                Path("sha256") / digest[:2] / f"{digest}.json"
            ).as_posix(),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "event_type": EVENT_TYPE,
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "evidence_id": _evidence_id(evidence["evidence_bundle_sha256"]),
            "preregistration_id": PREREGISTRATION_ID,
            "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
            "proposal_sha256": PROPOSAL_SHA256,
            "public_documentation_evidence_id": PUBLIC_DOCUMENTATION_EVIDENCE_ID,
            "public_documentation_record_sha256": PUBLIC_DOCUMENTATION_RECORD_SHA256,
            "public_documentation_bundle_sha256": PUBLIC_DOCUMENTATION_BUNDLE_SHA256,
            "account_entitlement_evidence": evidence,
            "blob_reference": reference,
            **{field: False for field in FIXED_FALSE},
            "previous_hash": GENESIS_HASH,
        }
        result["record_hash"] = _hash(result)
        return self._append(result, capture["payload_bytes"])

    def _append(self, result: dict[str, Any], payload: bytes) -> dict[str, Any]:
        _private_directory(self.path.parent)
        _private_directory(self.blob_root)
        _private_directory(self.blob_root / "sha256")
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            os.fchmod(lock, 0o600)
            existing = self.records()
            if existing:
                if (
                    existing[0]["evidence_id"] == result["evidence_id"]
                    and existing[0]["account_entitlement_evidence"]
                    == result["account_entitlement_evidence"]
                ):
                    return existing[0]
                raise LedgerIntegrityError("Different account evidence is already registered")
            path = self.blob_root / result["blob_reference"]["blob_relative_path"]
            _private_directory(path.parent)
            if path.exists():
                if path.is_symlink() or path.read_bytes() != payload:
                    raise LedgerIntegrityError("Account-entitlement blob conflicts")
                os.chmod(path, 0o400)
            else:
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(), 0o400
                )
                try:
                    _write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if self.path.exists():
                raise LedgerIntegrityError(
                    "An empty or unsafe account-entitlement ledger already exists"
                )
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(),
                    0o600,
                )
            except FileExistsError as error:
                raise LedgerIntegrityError(
                    "Account-entitlement ledger appeared during append"
                ) from error
            try:
                _write_all(descriptor, (_canonical_json(result) + "\n").encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return self.records()[0]
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def require(self, evidence_id: str) -> dict[str, Any]:
        records = self.records()
        if len(records) != 1 or records[0].get("evidence_id") != evidence_id:
            raise LedgerIntegrityError("Required Revision-2 account evidence is unavailable")
        return records[0]
