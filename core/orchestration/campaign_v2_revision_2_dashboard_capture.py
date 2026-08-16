"""Exact-byte authenticated dashboard evidence for Campaign v2 R2.

This offline-only boundary seals one exact credential-free HTML ``tr`` fragment
from the authenticated Plans table and derives only three fixed visible labels.
It cannot infer account identity, API entitlements, lookback, incremental cost,
or any downstream authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    FIXED_FALSE, PREREGISTRATION_ID, PREREGISTRATION_RECORD_SHA256, PROPOSAL_SHA256,
    PUBLIC_DOCUMENTATION_BUNDLE_SHA256, PUBLIC_DOCUMENTATION_EVIDENCE_ID,
    PUBLIC_DOCUMENTATION_RECORD_SHA256,
)
from core.orchestration.campaign_v2_revision_2_public_documentation import (
    CampaignV2Revision2PublicDocumentationLedger,
)

SCHEMA_VERSION = "1.1"
POLICY_VERSION = "massive-campaign-v2-r2-dashboard-plan-row-capture-v2"
EVENT_TYPE = "CAMPAIGN_V2_REVISION_2_DASHBOARD_CAPTURE_SEALED"
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
EXPECTED_VISIBLE_LABELS = {
    "plan_label": "Stocks Basic",
    "customer_type_label": "Individual",
    "displayed_price_label": "$0/m",
}
CONTROL_LEDGER_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/account_entitlement/dashboard_capture_evidence.jsonl")
BLOB_ROOT_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/account_entitlement/dashboard_capture_blobs")
CAPTURE_KIND = "AUTHENTICATED_DASHBOARD_STOCKS_PLAN_ROW_HTML"
_CAPTURE_FIELDS = frozenset({"captured_at", "source_uri", "capture_kind", "payload_bytes", "authenticated_session_attested", "sole_stocks_plan_row_attested"})
_UNRESOLVED = (
    "account_identity_proven", "account_to_plan_binding_proven",
    "daily_bars_access_confirmed", "dividends_access_confirmed",
    "stock_splits_access_confirmed", "historical_lookback_start_proven",
    "zero_incremental_cost_proven", "provider_origin_cryptographically_verified",
)
_BLOB_FIELDS = frozenset({"payload_sha256", "byte_length", "blob_relative_path"})
_RECORD_FIELDS = frozenset({
    "schema_version", "policy_version", "event_type", "recorded_at", "evidence_id",
    "preregistration_id", "preregistration_record_sha256", "proposal_sha256",
    "dashboard_capture_evidence", "blob_reference", *FIXED_FALSE,
    "previous_hash", "record_hash",
})
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0
        self.cell_parts: list[str] | None = None
        self.cells: list[str] = []
        self.unsafe_hidden_markup = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        style = attributes.get("style") or ""
        if ("hidden" in attributes or (attributes.get("aria-hidden") or "").lower() == "true"
                or re.search(r"(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden)", style)):
            self.unsafe_hidden_markup = True
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self.hidden_depth += 1
        elif tag.lower() in {"td", "th"}:
            if self.cell_parts is not None:
                self.unsafe_hidden_markup = True
            self.cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.lower() in {"td", "th"} and self.cell_parts is not None:
            self.cells.append(" ".join(" ".join(self.cell_parts).split()))
            self.cell_parts = None

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)
            if self.cell_parts is not None:
                self.cell_parts.append(data)


def _visible_cells(payload: bytes) -> tuple[str, list[str]]:
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise ValueError("payload_bytes must be nonempty exact bytes within 64 KB")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("dashboard capture must be strict UTF-8 HTML") from error
    stripped = text.strip()
    if (re.match(r"(?is)^<tr(?:\s|>)", stripped) is None
            or re.search(r"(?is)</tr>$", stripped) is None
            or len(re.findall(r"(?is)<tr(?:\s|>)", stripped)) != 1
            or len(re.findall(r"(?is)</tr>", stripped)) != 1
            or re.search(r"(?is)<!doctype\s+html|<html(?:\s|>)", stripped)):
        raise ValueError("dashboard capture must be one exact HTML table-row fragment")
    if re.search(r"(?i)(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret|session(?:_id)?|csrf(?:-token)?|cookie|sid|token)\s*[:=]", text) or re.search(r"(?i)bearer\s+\S+|eyJ[A-Za-z0-9_-]{10,}", text):
        raise ValueError("credential material must not be retained")
    parser = _VisibleText()
    parser.feed(text)
    parser.close()
    if parser.unsafe_hidden_markup or parser.cell_parts is not None:
        raise ValueError("hidden or malformed dashboard-row markup cannot prove visible labels")
    visible = " ".join(" ".join(parser.parts).split())
    return visible, parser.cells


def _source_uri(value: Any) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 500 or any(ord(character) < 32 for character in value)):
        raise ValueError("source_uri must be canonical text")
    if value != "https://massive.com/dashboard/subscriptions":
        raise ValueError("source_uri must be the canonical Massive Plans page")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("source_uri must be an official Massive HTTPS page") from error
    if (parts.scheme != "https" or parts.hostname not in {"massive.com", "www.massive.com"}
            or parts.username is not None or parts.password is not None or port is not None
            or parts.path != "/dashboard/subscriptions" or parts.query or parts.fragment):
        raise ValueError("source_uri must be an official Massive HTTPS page")
    return value


def build_dashboard_capture_evidence(capture: Mapping[str, Any], *, assessed_at: Any) -> dict[str, Any]:
    """Derive only fixed visible labels from exact authenticated-page bytes."""
    if not isinstance(capture, Mapping) or set(capture) != _CAPTURE_FIELDS:
        raise ValueError("dashboard capture fields differ from the contract")
    assessed = _timestamp(assessed_at, "assessed_at")
    captured = _timestamp(capture["captured_at"], "captured_at")
    if captured > assessed:
        raise ValueError("dashboard capture cannot postdate assessment")
    if capture["authenticated_session_attested"] is not True:
        raise ValueError("capture requires local authenticated-session attestation")
    if capture["capture_kind"] != CAPTURE_KIND:
        raise ValueError("capture_kind must identify the exact Stocks plan row")
    if capture["sole_stocks_plan_row_attested"] is not True:
        raise ValueError("capture requires local sole-Stocks-row attestation")
    uri = _source_uri(capture["source_uri"])
    payload = capture["payload_bytes"]
    visible, cells = _visible_cells(payload)
    for name, label in EXPECTED_VISIBLE_LABELS.items():
        if cells.count(label) != 1:
            raise ValueError(f"dashboard capture must contain exactly one whole-cell {name}")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION,
        "provider_id": "MASSIVE", "assessed_at": assessed.isoformat(timespec="microseconds"),
        "captured_at": captured.isoformat(timespec="microseconds"), "source_uri": uri,
        "capture_kind": CAPTURE_KIND,
        "payload_sha256": hashlib.sha256(payload).hexdigest(), "payload_byte_length": len(payload),
        "visible_text_sha256": hashlib.sha256(visible.encode()).hexdigest(),
        "authenticated_session_attested": True, "authentication_basis": "LOCAL_OPERATOR_ATTESTATION_ONLY",
        "sole_stocks_plan_row_attested": True,
        "row_selection_semantics": "OPERATOR_SELECTED_FRAGMENT_PAGE_UNIQUENESS_NOT_INDEPENDENTLY_VERIFIED",
        "observed_labels": dict(EXPECTED_VISIBLE_LABELS),
        "public_documentation_evidence_id": PUBLIC_DOCUMENTATION_EVIDENCE_ID,
        "public_documentation_record_sha256": PUBLIC_DOCUMENTATION_RECORD_SHA256,
        "public_documentation_bundle_sha256": PUBLIC_DOCUMENTATION_BUNDLE_SHA256,
        "public_documentation_combination_status": "EXACT_PARENT_BOUND_PRODUCT_FACTS_ONLY_NO_ACCOUNT_ENTITLEMENT_INFERENCE",
        "displayed_price_semantics": "SUBSCRIPTION_DISPLAY_ONLY_NOT_INCREMENTAL_DATA_COST_PROOF",
        "evidence_complete_for_separate_capture_activation_record": False,
        **{field: False for field in _UNRESOLVED},
    }
    result["evidence_bundle_sha256"] = _hash(result)
    return result


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise LedgerIntegrityError("Dashboard-capture directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("Dashboard-capture directory must be owner-only")


def _assert_private_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise LedgerIntegrityError("Dashboard-capture directory is unsafe") from error
    if (path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o700):
        raise LedgerIntegrityError("Dashboard-capture directory is unsafe")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("Dashboard-capture write made no progress")
        offset += written


class CampaignV2Revision2DashboardCaptureLedger:
    """Seal and reverify one exact, inert authenticated dashboard capture."""
    def __init__(self, path: str | Path, blob_root: str | Path, *, repository_root: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None,
                 public_evidence_resolver: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.path, self.blob_root = Path(path), Path(blob_root)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._public_evidence_resolver = public_evidence_resolver or (
            lambda: CampaignV2Revision2PublicDocumentationLedger.at_repository_root(self.repository_root).require(PUBLIC_DOCUMENTATION_EVIDENCE_ID)
        )

    @classmethod
    def at_repository_root(cls, root: str | Path) -> "CampaignV2Revision2DashboardCaptureLedger":
        resolved = Path(root).resolve()
        return cls(resolved / CONTROL_LEDGER_RELATIVE_PATH, resolved / BLOB_ROOT_RELATIVE_PATH, repository_root=resolved)

    def _verify_parent(self) -> None:
        parent = self._public_evidence_resolver()
        public = parent.get("public_documentation_evidence", {})
        if (parent.get("evidence_id") != PUBLIC_DOCUMENTATION_EVIDENCE_ID
                or parent.get("record_hash") != PUBLIC_DOCUMENTATION_RECORD_SHA256
                or parent.get("preregistration_id") != PREREGISTRATION_ID
                or parent.get("preregistration_record_sha256") != PREREGISTRATION_RECORD_SHA256
                or parent.get("proposal_sha256") != PROPOSAL_SHA256
                or public.get("evidence_bundle_sha256") != PUBLIC_DOCUMENTATION_BUNDLE_SHA256
                or any(parent.get(field) is not False for field in ("authenticated_account_evidence_collected", "data_calls_allowed", "evaluation_allowed", "orders_submitted"))):
            raise LedgerIntegrityError("Revision-2 public evidence parent is invalid")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            _assert_private_directory(self.path.parent)
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                details = os.fstat(descriptor)
                if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                        or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o600
                        or not 0 < details.st_size <= MAX_LEDGER_BYTES):
                    raise ValueError("unsafe ledger")
                raw = b""
                while len(raw) <= MAX_LEDGER_BYTES:
                    chunk = os.read(descriptor, min(1024 * 1024, MAX_LEDGER_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw += chunk
            finally:
                os.close(descriptor)
            if len(raw) > MAX_LEDGER_BYTES or not raw.endswith(b"\n"):
                raise ValueError("incomplete ledger")
            rows = [json.loads(line) for line in raw.splitlines()]
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LedgerIntegrityError("Dashboard-capture ledger is unsafe") from error
        if len(rows) != 1:
            raise LedgerIntegrityError("Revision-2 permits one dashboard capture")
        row = rows[0]
        try:
            reference = row["blob_reference"]
            evidence = row["dashboard_capture_evidence"]
            if not isinstance(row, dict) or not isinstance(reference, dict) or set(reference) != _BLOB_FIELDS:
                raise ValueError("record shape")
            digest = reference["payload_sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("blob digest")
            relative = Path("sha256") / digest[:2] / f"{digest}.html"
            if reference["blob_relative_path"] != relative.as_posix():
                raise ValueError("blob path")
            _assert_private_directory(self.blob_root)
            _assert_private_directory(self.blob_root / "sha256")
            _assert_private_directory((self.blob_root / relative).parent)
            blob = self.blob_root / relative
            descriptor = os.open(blob, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                details = os.fstat(descriptor)
                if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                        or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o400):
                    raise ValueError("unsafe blob")
                payload = b""
                while len(payload) <= MAX_PAYLOAD_BYTES:
                    chunk = os.read(descriptor, min(64 * 1024, MAX_PAYLOAD_BYTES + 1 - len(payload)))
                    if not chunk:
                        break
                    payload += chunk
            finally:
                os.close(descriptor)
            rebuilt = build_dashboard_capture_evidence({"captured_at": evidence["captured_at"], "source_uri": evidence["source_uri"],
                "capture_kind": evidence["capture_kind"], "payload_bytes": payload,
                "sole_stocks_plan_row_attested": evidence["sole_stocks_plan_row_attested"],
                "authenticated_session_attested": evidence["authenticated_session_attested"]}, assessed_at=evidence["assessed_at"])
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("Dashboard-capture record or blob is unsafe") from error
        material = {key: value for key, value in row.items() if key != "record_hash"}
        recorded = _timestamp(row.get("recorded_at"), "recorded_at")
        assessed = _timestamp(evidence["assessed_at"], "assessed_at")
        if (set(row) != _RECORD_FIELDS or rebuilt != evidence or row.get("record_hash") != _hash(material)
                or row.get("schema_version") != SCHEMA_VERSION or row.get("policy_version") != POLICY_VERSION
                or row.get("event_type") != EVENT_TYPE
                or row.get("evidence_id") != "CV2R2DASH-" + evidence["evidence_bundle_sha256"][:32].upper()
                or row.get("previous_hash") != GENESIS_HASH or row.get("preregistration_id") != PREREGISTRATION_ID
                or row.get("preregistration_record_sha256") != PREREGISTRATION_RECORD_SHA256
                or row.get("proposal_sha256") != PROPOSAL_SHA256
                or not assessed <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                or any(row.get(field) is not False for field in FIXED_FALSE)
                or hashlib.sha256(payload).hexdigest() != row["blob_reference"]["payload_sha256"]
                or len(payload) != row["blob_reference"]["byte_length"]):
            raise LedgerIntegrityError("Dashboard-capture record or blob changed")
        self._verify_parent()
        return rows

    def register(self, capture: Mapping[str, Any], *, assessed_at: Any) -> dict[str, Any]:
        self._verify_parent()
        evidence = build_dashboard_capture_evidence(capture, assessed_at=assessed_at)
        existing = self.records()
        if existing:
            if existing[0]["dashboard_capture_evidence"] == evidence:
                return existing[0]
            raise LedgerIntegrityError("Different dashboard capture cannot replace evidence")
        payload, digest = capture["payload_bytes"], evidence["payload_sha256"]
        relative = Path("sha256") / digest[:2] / f"{digest}.html"
        recorded = _timestamp(self._clock(), "recorded_at")
        assessed = _timestamp(evidence["assessed_at"], "assessed_at")
        actual_now = datetime.now(timezone.utc)
        if not assessed <= recorded <= actual_now + MAX_CLOCK_SKEW or recorded < actual_now - MAX_CLOCK_SKEW:
            raise ValueError("recording clock is outside the safe assessment window")
        _private_directory(self.path.parent); _private_directory(self.blob_root)
        _private_directory(self.blob_root / "sha256"); _private_directory((self.blob_root / relative).parent)
        blob = self.blob_root / relative
        try:
            descriptor = os.open(blob, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        except FileExistsError:
            details = blob.lstat()
            if (blob.is_symlink() or not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                    or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) != 0o400
                    or blob.read_bytes() != payload):
                raise LedgerIntegrityError("Existing dashboard-capture blob is unsafe")
        else:
            try:
                _write_all(descriptor, payload); os.fsync(descriptor)
            finally:
                os.close(descriptor)
        record: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION, "event_type": EVENT_TYPE,
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "evidence_id": "CV2R2DASH-" + evidence["evidence_bundle_sha256"][:32].upper(), "preregistration_id": PREREGISTRATION_ID,
            "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256, "proposal_sha256": PROPOSAL_SHA256,
            "dashboard_capture_evidence": evidence, "blob_reference": {"payload_sha256": digest, "byte_length": len(payload), "blob_relative_path": relative.as_posix()},
            **{field: False for field in FIXED_FALSE}, "previous_hash": GENESIS_HASH}
        record["record_hash"] = _hash(record)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            concurrent = self.records()
            if concurrent and concurrent[0]["dashboard_capture_evidence"] == evidence:
                return concurrent[0]
            raise LedgerIntegrityError("Concurrent dashboard capture differs")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _write_all(descriptor, (_canonical_json(record) + "\n").encode()); os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return self.records()[0]
