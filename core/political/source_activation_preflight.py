from __future__ import annotations

"""Fail-closed source activation boundary before congressional data ingestion."""

from datetime import date
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlsplit


POLICY_VERSION = "congressional-source-activation-preflight-v1"
OFFICIAL_SOURCES = {"OFFICIAL_HOUSE", "OFFICIAL_SENATE"}
LICENSED_SOURCES = {"CAPITOL_TRADES", "LICENSED_PROVIDER"}
ALLOWED_USES = {"PRIVATE_NONCOMMERCIAL_RESEARCH", "COMMERCIAL_INVESTMENT_RESEARCH"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return text


def _sha256(value: Any, name: str) -> str:
    text = _required(value, name, 64).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


def assess_source_activation(
    *,
    source: str,
    intended_use: str,
    terms_uri: str,
    terms_content_sha256: str,
    terms_review_reference: str,
    reviewed_on: str | date,
    reviewer_role: str,
    legal_use_permitted: bool,
    automated_access_permitted: bool,
    licensed_feed_contract_in_force: bool,
    redistribution_permitted: bool,
) -> dict[str, Any]:
    resolved_source = _required(source, "source", 80).upper()
    if resolved_source not in OFFICIAL_SOURCES | LICENSED_SOURCES:
        raise ValueError("source is unsupported")
    use = _required(intended_use, "intended_use", 80).upper()
    if use not in ALLOWED_USES:
        raise ValueError("intended_use is unsupported")
    uri = _required(terms_uri, "terms_uri")
    parsed = urlsplit(uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("terms_uri must be a safe HTTPS URL")
    content_hash = _sha256(terms_content_sha256, "terms_content_sha256")
    try:
        review_date = reviewed_on if isinstance(reviewed_on, date) else date.fromisoformat(str(reviewed_on))
    except (TypeError, ValueError) as error:
        raise ValueError("reviewed_on must use YYYY-MM-DD") from error
    if review_date > date.today():
        raise ValueError("reviewed_on cannot be in the future")

    reasons = []
    if legal_use_permitted is not True:
        reasons.append("Documented legal review has not approved the intended use.")
    if automated_access_permitted is not True:
        reasons.append("Documented terms review has not approved automated access.")
    if resolved_source in LICENSED_SOURCES and licensed_feed_contract_in_force is not True:
        reasons.append("A commercial provider requires an express licensed feed contract.")
    if resolved_source in OFFICIAL_SOURCES and use == "COMMERCIAL_INVESTMENT_RESEARCH":
        if legal_use_permitted is not True:
            reasons.append("Official disclosure commercial-use restrictions remain unresolved.")
    identity = {
        "policy_version": POLICY_VERSION,
        "source": resolved_source,
        "intended_use": use,
        "terms_uri": uri,
        "terms_content_sha256": content_hash,
        "terms_review_reference": _required(terms_review_reference, "terms_review_reference"),
        "reviewed_on": review_date.isoformat(),
        "reviewer_role": _required(reviewer_role, "reviewer_role", 200),
        "legal_use_permitted": legal_use_permitted is True,
        "automated_access_permitted": automated_access_permitted is True,
        "licensed_feed_contract_in_force": licensed_feed_contract_in_force is True,
        "redistribution_permitted": redistribution_permitted is True,
    }
    ready = not reasons
    return {
        **identity,
        "assessment_id": "CSAP-" + hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()[:32].upper(),
        "status": "READY_FOR_SEPARATE_CONNECTOR_REVIEW" if ready else "BLOCKED",
        "reasons": sorted(set(reasons)),
        "source_approved": ready,
        "connector_implemented": False,
        "data_downloaded": False,
        "scraper_enabled": False,
        "automatic_trade_signal": False,
        "copy_trade_allowed": False,
        "order_submitted": False,
        "live_trading_enabled": False,
    }
