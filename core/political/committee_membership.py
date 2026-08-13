from __future__ import annotations

"""Point-in-time committee membership evidence; no relevance inference."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping

from core.decision_ledger import canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json


POLICY_VERSION = "point-in-time-congressional-committee-membership-v1"
ALLOWED_CHAMBERS = {"HOUSE", "SENATE"}
ALLOWED_ROLES = {"MEMBER", "CHAIR", "VICE_CHAIR", "RANKING_MEMBER"}
MAX_CLOCK_SKEW = timedelta(minutes=5)
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


def _membership_id(identity: Mapping[str, Any]) -> str:
    return "CCM-" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32].upper()


def create_committee_membership(
    *, chamber: str, politician_name: str, committee_name: str,
    subcommittee_name: str | None, membership_role: str,
    effective_from: str | date, effective_to: str | date | None,
    evidence_known_at: str | datetime, source_url: str,
    evidence_sha256: str, review_reference: str, reviewed_by: str,
) -> dict[str, Any]:
    resolved_chamber = _required(chamber, "chamber", 20).upper()
    if resolved_chamber not in ALLOWED_CHAMBERS:
        raise ValueError("chamber is unsupported")
    role = _required(membership_role, "membership_role", 40).upper()
    if role not in ALLOWED_ROLES:
        raise ValueError("membership_role is unsupported")
    start = _date(effective_from, "effective_from")
    end = _date(effective_to, "effective_to") if effective_to is not None else None
    if end is not None and end < start:
        raise ValueError("effective_to cannot predate effective_from")
    known = datetime.fromisoformat(canonical_timestamp(evidence_known_at))
    if known > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
        raise ValueError("evidence_known_at cannot be in the future")
    uri = _required(source_url, "source_url", 1000)
    if not uri.startswith("https://"):
        raise ValueError("source_url must be HTTPS")
    evidence_hash = _required(evidence_sha256, "evidence_sha256", 64).lower()
    if not _SHA256.fullmatch(evidence_hash):
        raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
    identity = {
        "policy_version": POLICY_VERSION,
        "chamber": resolved_chamber,
        "politician_name": _required(politician_name, "politician_name", 200),
        "committee_name": _required(committee_name, "committee_name", 300),
        "subcommittee_name": (
            _required(subcommittee_name, "subcommittee_name", 300)
            if subcommittee_name is not None else None
        ),
        "membership_role": role,
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat() if end else None,
        "evidence_known_at": known.isoformat(),
        "source_url": uri,
        "evidence_sha256": evidence_hash,
        "review_reference": _required(review_reference, "review_reference"),
        "reviewed_by": _required(reviewed_by, "reviewed_by", 100),
    }
    return {
        **identity,
        "committee_membership_id": _membership_id(identity),
        "status": "MEMBERSHIP_EVIDENCE_ONLY",
        "committee_relevance_inferred": False,
        "investment_advantage_inferred": False,
        "automatic_recommendation": False,
        "copy_trade_allowed": False,
        "order_submitted": False,
        "live_trading_enabled": False,
    }


def membership_available_for_disclosure(
    disclosure: Mapping[str, Any], membership: Mapping[str, Any]
) -> bool:
    """Return true only for matching membership knowable at disclosure time."""
    transaction = _date(disclosure.get("transaction_date"), "transaction_date")
    available = datetime.fromisoformat(
        canonical_timestamp(disclosure.get("historical_point_in_time_signal_at"))
    )
    start = _date(membership.get("effective_from"), "effective_from")
    end = _date(membership.get("effective_to"), "effective_to") if membership.get("effective_to") else None
    known = datetime.fromisoformat(canonical_timestamp(membership.get("evidence_known_at")))
    identity = {
        key: membership.get(key)
        for key in (
            "policy_version", "chamber", "politician_name", "committee_name",
            "subcommittee_name", "membership_role", "effective_from",
            "effective_to", "evidence_known_at", "source_url",
            "evidence_sha256", "review_reference", "reviewed_by",
        )
    }
    return bool(
        membership.get("policy_version") == POLICY_VERSION
        and membership.get("committee_membership_id") == _membership_id(identity)
        and membership.get("status") == "MEMBERSHIP_EVIDENCE_ONLY"
        and membership.get("chamber") == disclosure.get("chamber")
        and membership.get("politician_name") == disclosure.get("politician_name")
        and start <= transaction
        and (end is None or transaction <= end)
        and known <= available
        and membership.get("committee_relevance_inferred") is False
        and membership.get("investment_advantage_inferred") is False
        and membership.get("automatic_recommendation") is False
        and membership.get("copy_trade_allowed") is False
        and membership.get("order_submitted") is False
        and membership.get("live_trading_enabled") is False
    )
