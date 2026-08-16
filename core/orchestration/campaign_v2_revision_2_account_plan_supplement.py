"""Owner-only account/plan same-session supplement for Campaign v2 R2.

The boundary seals one exact Connected Account table row and binds it to a
re-observation of the already-registered Stocks plan-row payload within thirty
seconds in the same locally attested browser session.  Only a SHA-256 identity
is exposed in the ledger record; the exact raw row containing the address is
retained in an owner-only local blob.  Provider origin, cryptographic
account-to-plan binding, endpoint entitlements, lookback, cost, activation, and
every downstream authority remain unresolved or false.
"""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    FIXED_FALSE,
    PREREGISTRATION_ID,
    PREREGISTRATION_RECORD_SHA256,
    PROPOSAL_SHA256,
)
from core.orchestration.campaign_v2_revision_2_dashboard_capture import (
    CampaignV2Revision2DashboardCaptureLedger,
    MAX_CLOCK_SKEW,
    _assert_private_directory,
    _canonical_json,
    _hash,
    _private_directory,
    _timestamp,
    _visible_cells,
    _write_all,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-campaign-v2-r2-account-plan-session-supplement-v1"
EVENT_TYPE = "CAMPAIGN_V2_REVISION_2_ACCOUNT_PLAN_SESSION_SUPPLEMENT_SEALED"
CAPTURE_KIND = "AUTHENTICATED_DASHBOARD_CONNECTED_ACCOUNT_ROW_HTML"
SOURCE_URI = "https://massive.com/dashboard/account"
MAX_BRACKET_SECONDS = 30
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
DASHBOARD_EVIDENCE_ID = "CV2R2DASH-6DF966621092349395BC7B782BDC03E7"
DASHBOARD_RECORD_SHA256 = "f724c56171ecce98a75cf761e505ba05c2a0e1a32b5a4a689ca4d206999857d5"
DASHBOARD_PAYLOAD_SHA256 = "5a2ef5f4aad07421662567d54fb6731f6db25099385ed56c697eab6c5da2a832"
DASHBOARD_BUNDLE_SHA256 = "6df966621092349395bc7b782bdc03e78b2a7b36bb38ad3064154e674272be6c"
CONTROL_LEDGER_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/account_entitlement/account_plan_supplement.jsonl")
BLOB_ROOT_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/account_entitlement/account_plan_supplement_blobs")
_CAPTURE_FIELDS = frozenset({
    "account_captured_at", "plan_row_reobserved_at", "source_uri", "capture_kind",
    "payload_bytes", "plan_row_payload_sha256", "authenticated_session_attested",
    "same_browser_session_attested", "sole_connected_account_row_attested",
})
_BLOB_FIELDS = frozenset({
    "payload_sha256", "byte_length", "blob_relative_path",
    "contains_cleartext_identity",
})
_RECORD_FIELDS = frozenset({
    "schema_version", "policy_version", "event_type", "recorded_at", "evidence_id",
    "preregistration_id", "preregistration_record_sha256", "proposal_sha256",
    "dashboard_evidence_id", "dashboard_record_sha256", "account_plan_supplement",
    "blob_reference", *FIXED_FALSE, "previous_hash", "record_hash",
})
EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)


def build_account_plan_supplement(capture: Mapping[str, Any], *, assessed_at: Any) -> dict[str, Any]:
    if not isinstance(capture, Mapping) or set(capture) != _CAPTURE_FIELDS:
        raise ValueError("account-plan supplement fields differ from the contract")
    assessed = _timestamp(assessed_at, "assessed_at")
    account_at = _timestamp(capture["account_captured_at"], "account_captured_at")
    plan_at = _timestamp(capture["plan_row_reobserved_at"], "plan_row_reobserved_at")
    if not account_at <= plan_at <= assessed:
        raise ValueError("account/plan observations must be ordered before assessment")
    bracket = (plan_at - account_at).total_seconds()
    if bracket < 0 or bracket > MAX_BRACKET_SECONDS:
        raise ValueError("account/plan observations exceed the 30-second bracket")
    if capture["source_uri"] != SOURCE_URI or capture["capture_kind"] != CAPTURE_KIND:
        raise ValueError("account capture source or kind differs from the contract")
    for field in (
        "authenticated_session_attested", "same_browser_session_attested",
        "sole_connected_account_row_attested",
    ):
        if capture[field] is not True:
            raise ValueError(f"{field} must be locally attested")
    if capture["plan_row_payload_sha256"] != DASHBOARD_PAYLOAD_SHA256:
        raise ValueError("re-observed plan row differs from registered exact bytes")
    payload = capture["payload_bytes"]
    if type(payload) is not bytes or len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("account row must be exact bytes within 64 KB")
    visible, cells = _visible_cells(payload)
    if len(cells) != 2 or cells[0] != "Connected Account":
        raise ValueError("account row differs from the exact two-cell contract")
    parts = cells[1].split()
    if len(parts) != 2 or EMAIL_PATTERN.fullmatch(parts[0]) is None or parts[1] != "github":
        raise ValueError("connected-account identity row is ambiguous")
    identity = parts[0]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "provider_id": "MASSIVE",
        "assessed_at": assessed.isoformat(timespec="microseconds"),
        "account_captured_at": account_at.isoformat(timespec="microseconds"),
        "plan_row_reobserved_at": plan_at.isoformat(timespec="microseconds"),
        "bracket_seconds": f"{bracket:.6f}",
        "source_uri": SOURCE_URI,
        "capture_kind": CAPTURE_KIND,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_byte_length": len(payload),
        "visible_text_sha256": hashlib.sha256(visible.encode()).hexdigest(),
        "account_identity_sha256": hashlib.sha256(identity.encode()).hexdigest(),
        "account_identity_kind": "CONNECTED_ACCOUNT_EMAIL",
        "account_identity_cleartext_in_ledger_record": False,
        "account_identity_cleartext_retained_in_local_blob": True,
        "authentication_source_label": "github",
        "authenticated_session_attested": True,
        "same_browser_session_attested": True,
        "sole_connected_account_row_attested": True,
        "dashboard_evidence_id": DASHBOARD_EVIDENCE_ID,
        "dashboard_record_sha256": DASHBOARD_RECORD_SHA256,
        "dashboard_payload_sha256": DASHBOARD_PAYLOAD_SHA256,
        "dashboard_bundle_sha256": DASHBOARD_BUNDLE_SHA256,
        "account_identity_locally_observed": True,
        "account_to_plan_same_session_locally_attested": True,
        "account_to_plan_binding_cryptographically_verified": False,
        "provider_origin_cryptographically_verified": False,
        "daily_bars_access_confirmed": False,
        "dividends_access_confirmed": False,
        "stock_splits_access_confirmed": False,
        "historical_lookback_start_proven": False,
        "zero_incremental_cost_proven": False,
        "evidence_complete_for_capture_activation": False,
        "binding_semantics": "LOCAL_SAME_BROWSER_SESSION_ATTESTATION_ONLY_PROVIDER_ORIGIN_UNVERIFIED",
    }
    result["evidence_bundle_sha256"] = _hash(result)
    return result


class CampaignV2Revision2AccountPlanSupplementLedger:
    def __init__(self, path: str | Path, blob_root: str | Path, *, repository_root: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None,
                 dashboard_resolver: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.path, self.blob_root = Path(path), Path(blob_root)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._dashboard_resolver = dashboard_resolver or (
            lambda: CampaignV2Revision2DashboardCaptureLedger.at_repository_root(self.repository_root).records()[0]
        )

    @classmethod
    def at_repository_root(cls, root: str | Path) -> "CampaignV2Revision2AccountPlanSupplementLedger":
        root = Path(root).resolve()
        return cls(root / CONTROL_LEDGER_RELATIVE_PATH, root / BLOB_ROOT_RELATIVE_PATH, repository_root=root)

    def _verify_parent(self) -> None:
        try:
            parent = self._dashboard_resolver()
        except (LookupError, OSError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("dashboard evidence parent is missing") from error
        if not isinstance(parent, Mapping):
            raise LedgerIntegrityError("dashboard evidence parent is invalid")
        evidence = parent.get("dashboard_capture_evidence")
        if not isinstance(evidence, Mapping):
            raise LedgerIntegrityError("dashboard evidence parent is invalid")
        if (parent.get("evidence_id") != DASHBOARD_EVIDENCE_ID
                or parent.get("record_hash") != DASHBOARD_RECORD_SHA256
                or evidence.get("payload_sha256") != DASHBOARD_PAYLOAD_SHA256
                or evidence.get("evidence_bundle_sha256") != DASHBOARD_BUNDLE_SHA256
                or any(parent.get(field) is not False for field in FIXED_FALSE)):
            raise LedgerIntegrityError("dashboard evidence parent is invalid")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            _assert_private_directory(self.path.parent)
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH)
                details = os.fstat(descriptor)
                if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                        or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o600
                        or not 0 < details.st_size <= MAX_LEDGER_BYTES):
                    raise ValueError("unsafe ledger")
                raw = os.read(descriptor, MAX_LEDGER_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > MAX_LEDGER_BYTES or not raw.endswith(b"\n"):
                raise ValueError("incomplete ledger")
            rows = [json.loads(line) for line in raw.splitlines()]
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise LedgerIntegrityError("account-plan supplement ledger is unsafe") from error
        if len(rows) != 1:
            raise LedgerIntegrityError("exactly one account-plan supplement is permitted")
        row = rows[0]
        try:
            reference, evidence = row["blob_reference"], row["account_plan_supplement"]
            if set(reference) != _BLOB_FIELDS:
                raise ValueError("blob reference")
            if reference["contains_cleartext_identity"] is not True:
                raise ValueError("blob identity disclosure")
            if (type(reference["byte_length"]) is not int
                    or not 0 < reference["byte_length"] <= MAX_PAYLOAD_BYTES):
                raise ValueError("blob length")
            digest = reference["payload_sha256"]
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("blob digest")
            relative = Path("sha256") / digest[:2] / f"{digest}.html"
            if reference["blob_relative_path"] != relative.as_posix():
                raise ValueError("blob path")
            _assert_private_directory(self.blob_root)
            _assert_private_directory(self.blob_root / "sha256")
            _assert_private_directory((self.blob_root / relative).parent)
            path = self.blob_root / relative
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                details = os.fstat(descriptor)
                if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                        or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o400):
                    raise ValueError("unsafe blob")
                payload = os.read(descriptor, MAX_PAYLOAD_BYTES + 1)
            finally:
                os.close(descriptor)
            rebuilt = build_account_plan_supplement({
                "account_captured_at": evidence["account_captured_at"],
                "plan_row_reobserved_at": evidence["plan_row_reobserved_at"],
                "source_uri": evidence["source_uri"], "capture_kind": evidence["capture_kind"],
                "payload_bytes": payload, "plan_row_payload_sha256": evidence["dashboard_payload_sha256"],
                "authenticated_session_attested": evidence["authenticated_session_attested"],
                "same_browser_session_attested": evidence["same_browser_session_attested"],
                "sole_connected_account_row_attested": evidence["sole_connected_account_row_attested"],
            }, assessed_at=evidence["assessed_at"])
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("account-plan supplement record or blob is unsafe") from error
        try:
            if set(row) != _RECORD_FIELDS:
                raise ValueError("record fields")
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            assessed = _timestamp(evidence["assessed_at"], "assessed_at")
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("account-plan supplement record changed") from error
        material = {key: value for key, value in row.items() if key != "record_hash"}
        if (set(row) != _RECORD_FIELDS or rebuilt != evidence or row.get("record_hash") != _hash(material)
                or row.get("schema_version") != SCHEMA_VERSION or row.get("policy_version") != POLICY_VERSION
                or row.get("event_type") != EVENT_TYPE or row.get("previous_hash") != GENESIS_HASH
                or row.get("evidence_id") != "CV2R2ACCTPLAN-" + evidence["evidence_bundle_sha256"][:32].upper()
                or row.get("preregistration_id") != PREREGISTRATION_ID
                or row.get("preregistration_record_sha256") != PREREGISTRATION_RECORD_SHA256
                or row.get("proposal_sha256") != PROPOSAL_SHA256
                or row.get("dashboard_evidence_id") != DASHBOARD_EVIDENCE_ID
                or row.get("dashboard_record_sha256") != DASHBOARD_RECORD_SHA256
                or not assessed <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                or any(row.get(field) is not False for field in FIXED_FALSE)
                or hashlib.sha256(payload).hexdigest() != reference["payload_sha256"]
                or len(payload) != reference["byte_length"]):
            raise LedgerIntegrityError("account-plan supplement record changed")
        self._verify_parent()
        return rows

    def register(self, capture: Mapping[str, Any], *, assessed_at: Any) -> dict[str, Any]:
        capture = dict(capture)
        self._verify_parent()
        evidence = build_account_plan_supplement(capture, assessed_at=assessed_at)
        existing = self.records()
        if existing:
            if existing[0]["account_plan_supplement"] == evidence:
                return existing[0]
            raise LedgerIntegrityError("different account-plan evidence cannot replace the record")
        recorded = _timestamp(self._clock(), "recorded_at")
        assessed = _timestamp(evidence["assessed_at"], "assessed_at")
        now = datetime.now(timezone.utc)
        if not assessed <= recorded <= now + MAX_CLOCK_SKEW or recorded < now - MAX_CLOCK_SKEW:
            raise ValueError("recording clock is outside the safe window")
        payload, digest = capture["payload_bytes"], evidence["payload_sha256"]
        if (type(payload) is not bytes
                or hashlib.sha256(payload).hexdigest() != digest):
            raise ValueError("payload changed between validation and persistence")
        relative = Path("sha256") / digest[:2] / f"{digest}.html"
        _private_directory(self.path.parent); _private_directory(self.blob_root)
        _private_directory(self.blob_root / "sha256"); _private_directory((self.blob_root / relative).parent)
        blob_path = self.blob_root / relative
        try:
            descriptor = os.open(blob_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        except FileExistsError:
            descriptor = os.open(blob_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                details = os.fstat(descriptor)
                existing_payload = os.read(descriptor, MAX_PAYLOAD_BYTES + 1)
                if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                        or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o400
                        or existing_payload != payload):
                    raise LedgerIntegrityError("conflicting blob already exists")
            finally:
                os.close(descriptor)
        else:
            try:
                os.fchmod(descriptor, 0o400)
                _write_all(descriptor, payload); os.fsync(descriptor)
            finally:
                os.close(descriptor)
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION, "event_type": EVENT_TYPE,
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "evidence_id": "CV2R2ACCTPLAN-" + evidence["evidence_bundle_sha256"][:32].upper(),
            "preregistration_id": PREREGISTRATION_ID, "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
            "proposal_sha256": PROPOSAL_SHA256, "dashboard_evidence_id": DASHBOARD_EVIDENCE_ID,
            "dashboard_record_sha256": DASHBOARD_RECORD_SHA256, "account_plan_supplement": evidence,
            "blob_reference": {
                "payload_sha256": digest,
                "byte_length": len(payload),
                "blob_relative_path": relative.as_posix(),
                "contains_cleartext_identity": True,
            },
            **{field: False for field in FIXED_FALSE}, "previous_hash": GENESIS_HASH,
        }
        record["record_hash"] = _hash(record)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, (_canonical_json(record) + "\n").encode()); os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary_path, self.path, follow_symlinks=False)
            except OSError as error:
                raise LedgerIntegrityError(
                    "account-plan supplement ledger could not be sealed"
                ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        return self.records()[0]
