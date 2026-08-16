"""Offline-only verification of the fixed Campaign v2 R2 account export inbox.

The verifier reads one exact authenticated JSON export and a separate capture
manifest from an ignored, owner-only directory.  It delegates all evidence
semantics to the immutable Revision-2 entitlement boundary and returns only a
redacted verification summary.  It cannot register evidence or authorize any
provider, replay, broker, deployment or trading action.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    FIXED_FALSE,
    MAX_CLOCK_SKEW,
    MAX_PAYLOAD_BYTES,
    PREREGISTRATION_ID,
    PROPOSAL_SHA256,
    build_revision_2_account_entitlement_evidence,
)


INBOX_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/account_entitlement/inbox"
)
ACCOUNT_EXPORT_FILE_NAME = "massive_account_subscription_export.json"
CAPTURE_MANIFEST_FILE_NAME = "massive_account_subscription_export.manifest.json"
MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_FIELDS = frozenset(
    {
        "captured_at",
        "source_kind",
        "source_uri",
        "export_sha256",
        "fact_json_pointers",
        "authenticated_session_attested",
        "market_data_response_used_for_account_binding",
        "request_id_used_for_account_binding",
    }
)


def _read_owner_only_file(path: Path, maximum_bytes: int) -> bytes:
    root = path.parents[len(INBOX_RELATIVE_PATH.parts)]
    current = root
    for component in INBOX_RELATIVE_PATH.parts:
        current /= component
        try:
            details = current.lstat()
        except OSError as error:
            raise ValueError("account-export inbox is missing or unsafe") from error
        if (
            current.is_symlink()
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise ValueError("account-export inbox path is unsafe")
    try:
        parent = path.parent.lstat()
    except OSError as error:
        raise ValueError("account-export inbox is missing or unsafe") from error
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ValueError("account-export inbox must be an owner-only directory")

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) != 0o600
            or not 0 < details.st_size <= maximum_bytes
        ):
            raise ValueError("account-export input must be an owner-only regular file")
        payload = b""
        while len(payload) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(64 * 1024, maximum_bytes + 1 - len(payload)),
            )
            if not chunk:
                break
            payload += chunk
    finally:
        os.close(descriptor)
    if not payload or len(payload) > maximum_bytes:
        raise ValueError("account-export input is outside the size boundary")
    return payload


def _manifest(payload: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(
            "capture manifest must be one strict UTF-8 JSON object"
        ) from error
    if not isinstance(value, Mapping) or set(value) != MANIFEST_FIELDS:
        raise ValueError("capture manifest fields differ from the fixed contract")
    return value


def verify_account_export_offline(
    repository_root: str | Path, *, assessed_at: Any | None = None
) -> dict[str, Any]:
    """Verify the fixed inbox files without writing or using the network."""

    root = Path(repository_root).resolve()
    inbox = root / INBOX_RELATIVE_PATH
    export_path = inbox / ACCOUNT_EXPORT_FILE_NAME
    manifest_path = inbox / CAPTURE_MANIFEST_FILE_NAME
    export_bytes = _read_owner_only_file(export_path, MAX_PAYLOAD_BYTES)
    manifest = _manifest(_read_owner_only_file(manifest_path, MAX_MANIFEST_BYTES))
    export_sha256 = hashlib.sha256(export_bytes).hexdigest()
    if manifest["export_sha256"] != export_sha256:
        raise ValueError("capture manifest is not bound to the exact export bytes")
    actual_now = datetime.now(timezone.utc)
    assessment = actual_now if assessed_at is None else assessed_at
    try:
        candidate = (
            assessment
            if isinstance(assessment, datetime)
            else datetime.fromisoformat(str(assessment))
        )
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            raise ValueError("naive timestamp")
        candidate = candidate.astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError("assessment clock must be a timezone-aware timestamp") from error
    if not actual_now - MAX_CLOCK_SKEW <= candidate <= actual_now + MAX_CLOCK_SKEW:
        raise ValueError("assessment clock must match the actual verification time")
    evidence = build_revision_2_account_entitlement_evidence(
        {
            "captured_at": manifest["captured_at"],
            "source_kind": manifest["source_kind"],
            "source_uri": manifest["source_uri"],
            "payload_bytes": export_bytes,
            "fact_json_pointers": manifest["fact_json_pointers"],
            "authenticated_session_attested": manifest[
                "authenticated_session_attested"
            ],
            "market_data_response_used_for_account_binding": manifest[
                "market_data_response_used_for_account_binding"
            ],
            "request_id_used_for_account_binding": manifest[
                "request_id_used_for_account_binding"
            ],
        },
        assessed_at=candidate,
    )
    return {
        "verification_status": "PASS",
        "verification_mode": "OFFLINE_UNREGISTERED",
        "preregistration_id": PREREGISTRATION_ID,
        "proposal_sha256": PROPOSAL_SHA256,
        "proposal_binding_status": "MODULE_CONSTANT_ONLY_NOT_LEDGER_VERIFIED",
        "payload_sha256": export_sha256,
        "payload_byte_length": evidence["payload_byte_length"],
        "evidence_bundle_sha256": evidence["evidence_bundle_sha256"],
        "account_identity_sha256": evidence["account_identity_sha256"],
        "plan_name_sha256": hashlib.sha256(
            evidence["plan_name"].encode("utf-8")
        ).hexdigest(),
        "daily_bars_access_confirmed": evidence["daily_bars_access_confirmed"],
        "dividends_access_confirmed": evidence["dividends_access_confirmed"],
        "stock_splits_access_confirmed": evidence[
            "stock_splits_access_confirmed"
        ],
        "historical_lookback_start": evidence["historical_lookback_start"],
        "rolling_lookback_buffer_days": evidence["rolling_lookback_buffer_days"],
        "zero_incremental_cost_proven": evidence[
            "zero_incremental_cost_proven"
        ],
        "account_entitlement_evidence_registered": False,
        **{field: False for field in FIXED_FALSE},
    }
