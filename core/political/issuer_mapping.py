from __future__ import annotations

"""Point-in-time issuer mappings for congressional disclosure research."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json


POLICY_VERSION = "point-in-time-congressional-issuer-mapping-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_IDENTIFIER_TYPES = {"CIK", "LEI", "FIGI", "INTERNAL_REVIEWED"}
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required(value: Any, name: str, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return result


def _date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{name} must not contain a time")
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must use YYYY-MM-DD") from error


def _mapping_id(identity: Mapping[str, Any]) -> str:
    return "CIM-" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32].upper()


def create_issuer_mapping(
    *, ticker: str, asset_name: str, issuer_identifier_type: str,
    issuer_identifier: str, effective_from: str | date,
    effective_to: str | date | None, mapping_known_at: str | datetime,
    source_url: str, evidence_sha256: str, review_reference: str,
    reviewed_by: str,
) -> dict[str, Any]:
    symbol = _required(ticker, "ticker", 15).upper()
    if not _TICKER.fullmatch(symbol):
        raise ValueError("ticker format is invalid")
    identifier_type = _required(
        issuer_identifier_type, "issuer_identifier_type", 30
    ).upper()
    if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
        raise ValueError("issuer_identifier_type is unsupported")
    start = _date(effective_from, "effective_from")
    end = _date(effective_to, "effective_to") if effective_to is not None else None
    if end is not None and end < start:
        raise ValueError("effective_to cannot predate effective_from")
    known = datetime.fromisoformat(canonical_timestamp(mapping_known_at))
    if known > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
        raise ValueError("mapping_known_at cannot be in the future")
    uri = _required(source_url, "source_url", 1000)
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username or parsed.password
        or any(character.isspace() for character in uri)
    ):
        raise ValueError("source_url must be a safe HTTPS URL")
    evidence_hash = _required(evidence_sha256, "evidence_sha256", 64).lower()
    if not _SHA256.fullmatch(evidence_hash):
        raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
    identity = {
        "policy_version": POLICY_VERSION,
        "ticker": symbol,
        "asset_name": _required(asset_name, "asset_name"),
        "issuer_identifier_type": identifier_type,
        "issuer_identifier": _required(issuer_identifier, "issuer_identifier", 200),
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat() if end else None,
        "mapping_known_at": known.isoformat(),
        "source_url": uri,
        "evidence_sha256": evidence_hash,
        "review_reference": _required(review_reference, "review_reference"),
        "reviewed_by": _required(reviewed_by, "reviewed_by", 100),
    }
    return {
        **identity,
        "issuer_mapping_id": _mapping_id(identity),
        "status": "REVIEWED_MAPPING_EVIDENCE_ONLY",
        "automatic_recommendation": False,
        "copy_trade_allowed": False,
        "order_submitted": False,
        "live_trading_enabled": False,
    }


def resolve_disclosure_issuer(
    disclosure: Mapping[str, Any], mappings: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Resolve only mappings knowable when the disclosure became available."""
    ticker = str(disclosure.get("ticker") or "").upper()
    asset_name = str(disclosure.get("asset_name") or "").strip()
    transaction_date = _date(disclosure.get("transaction_date"), "transaction_date")
    available_at = datetime.fromisoformat(
        canonical_timestamp(disclosure.get("historical_point_in_time_signal_at"))
    )
    eligible = []
    for mapping in mappings:
        identity = {
            key: mapping.get(key)
            for key in (
                "policy_version", "ticker", "asset_name", "issuer_identifier_type",
                "issuer_identifier", "effective_from", "effective_to",
                "mapping_known_at", "source_url", "evidence_sha256",
                "review_reference", "reviewed_by",
            )
        }
        valid_fingerprint = mapping.get("issuer_mapping_id") == _mapping_id(identity)
        valid_boundary = (
            mapping.get("status") == "REVIEWED_MAPPING_EVIDENCE_ONLY"
            and all(mapping.get(field) is False for field in (
                "automatic_recommendation", "copy_trade_allowed",
                "order_submitted", "live_trading_enabled",
            ))
        )
        if (
            mapping.get("policy_version") != POLICY_VERSION
            or mapping.get("ticker") != ticker
            or mapping.get("asset_name") != asset_name
            or not valid_fingerprint
            or not valid_boundary
        ):
            continue
        start = _date(mapping.get("effective_from"), "effective_from")
        end = _date(mapping.get("effective_to"), "effective_to") if mapping.get("effective_to") else None
        known = datetime.fromisoformat(canonical_timestamp(mapping.get("mapping_known_at")))
        if start <= transaction_date and (end is None or transaction_date <= end) and known <= available_at:
            eligible.append(mapping)
    identifiers = {
        (item.get("issuer_identifier_type"), item.get("issuer_identifier"))
        for item in eligible
    }
    unique = len(eligible) == 1 or (eligible and len(identifiers) == 1)
    selected = sorted(eligible, key=lambda item: item["issuer_mapping_id"])[0] if unique else None
    reasons = []
    if not ticker:
        reasons.append("The disclosure has no ticker.")
    elif not asset_name:
        reasons.append("The disclosure has no asset name.")
    elif not eligible:
        reasons.append("No mapping was both effective and knowable at disclosure availability.")
    elif not unique:
        reasons.append("Conflicting point-in-time issuer mappings exist.")
    return {
        "policy_version": POLICY_VERSION,
        "disclosure_id": disclosure.get("disclosure_id"),
        "ticker": ticker or None,
        "status": "RESOLVED_RESEARCH_IDENTITY" if selected else "UNRESOLVED",
        "issuer_mapping_id": selected.get("issuer_mapping_id") if selected else None,
        "issuer_identifier_type": selected.get("issuer_identifier_type") if selected else None,
        "issuer_identifier": selected.get("issuer_identifier") if selected else None,
        "reasons": reasons,
        "mapping_known_by_disclosure_availability": selected is not None,
        "automatic_recommendation": False,
        "copy_trade_allowed": False,
        "order_submitted": False,
        "live_trading_enabled": False,
    }
