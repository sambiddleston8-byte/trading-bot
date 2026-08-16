"""Fail-closed Massive entitlement evidence boundary.

Public provider documentation can establish published product semantics, but it
cannot authenticate a subscriber, name the subscriber's plan or bind an API
key to an account identity.  This module preserves that distinction and has no
network, credential, quarantine, replay or broker capability.

The resulting assessment is evidence for a *later* activation record.  It never
authorizes a provider request by itself.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import canonical_timestamp
from core.research.campaign_v2_revision_capture_activation_proposal import (
    activation_proposal,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-entitlement-evidence-boundary-v1"
PROVIDER_ID = "MASSIVE"
PROVIDER_DATASET_ID = "MASSIVE_STOCKS_CUSTOM_BARS_V2"
CAPTURE_APPROVAL_RECORD_ID = "CV2R1CAP-3072E69774CF1438898644B7ECFB6495"
CAPTURE_APPROVAL_RECORD_SHA256 = (
    "42431cf0471f9697c6a77f9d7366d671196d89b898d533e01f98d883382267a1"
)
ACQUISITION_START = "2024-08-01"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

PUBLIC_DOCUMENT_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "MASSIVE_REST_QUICKSTART": {
        "uri": "https://massive.com/docs/rest",
        "documented_facts": {
            "api_key_authentication_documented": True,
            "common_response_request_id_documented": True,
            "account_identity_response_field_documented": False,
            "account_plan_response_field_documented": False,
        },
    },
    "MASSIVE_STOCKS_CUSTOM_BARS": {
        "uri": (
            "https://massive.com/docs/rest/stocks/aggregates/custom-bars"
            "?assetClass=stocks&license=personal&name=stocks_basic"
        ),
        "documented_facts": {
            "stocks_basic_free_access_published": True,
            "stocks_basic_free_recency": "END_OF_DAY",
            "stocks_basic_free_history_years": 2,
            "records_date_back_to": "2003-09-10",
            "adjusted_default": True,
            "unadjusted_requires_adjusted_false": True,
        },
    },
    "MASSIVE_STOCKS_DAY_AGGREGATE_FLAT_FILES": {
        "uri": "https://massive.com/docs/flat-files/stocks/day-aggregates",
        "documented_facts": {
            "delivery_model": "S3_DAILY_DOWNLOAD",
            "stocks_basic_free_access_published": False,
            "stocks_starter_history_years": 5,
            "updated_after_session": "11:00_ET_NEXT_DAY",
        },
    },
}

ALLOWED_ACCOUNT_BINDING_EVIDENCE_KINDS = frozenset(
    {
        "OFFICIAL_AUTHENTICATED_ACCOUNT_ENDPOINT",
        "AUTHENTICATED_DASHBOARD_EXPORT",
    }
)
FORBIDDEN_ACCOUNT_BINDING_EVIDENCE_KINDS = frozenset(
    {
        "API_REQUEST_ID",
        "MARKET_DATA_RESPONSE",
        "PUBLIC_DOCUMENTATION",
        "USER_ASSERTION",
    }
)

_PUBLIC_INPUT_FIELDS = frozenset(
    {"document_id", "uri", "retrieved_at", "payload_sha256"}
)
_PUBLIC_DOCUMENT_FIELDS = frozenset(
    {
        *_PUBLIC_INPUT_FIELDS,
        "source_class",
        "authenticated_account_evidence",
        "documented_facts",
    }
)
_PUBLIC_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "provider_id",
        "assessed_at",
        "documents",
        "public_documentation_proves_account_identity",
        "public_documentation_proves_account_plan",
        "public_documentation_proves_current_account_entitlement",
        "custom_bars_published_free_plan_access",
        "flat_files_published_free_plan_access",
        "evidence_bundle_sha256",
    }
)
_ACCOUNT_INPUT_FIELDS = frozenset(
    {
        "captured_at",
        "account_binding_evidence_kind",
        "account_binding_payload_sha256",
        "account_identity_sha256",
        "plan_name",
        "plan_evidence_payload_sha256",
        "custom_bars_access_confirmed",
        "historical_lookback_start",
        "request_limit_per_minute",
        "incremental_cost_usd",
        "terms_uri",
        "terms_retrieved_at",
        "terms_payload_sha256",
        "authenticated_account_session",
        "credential_material_recorded",
        "market_data_response_used_for_account_binding",
        "request_id_used_for_account_binding",
    }
)
_ACCOUNT_BUNDLE_FIELDS = frozenset(
    {
        *_ACCOUNT_INPUT_FIELDS,
        "schema_version",
        "policy_version",
        "provider_id",
        "provider_dataset_id",
        "assessed_at",
        "evidence_bundle_sha256",
    }
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


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _canonical_text(value: Any, name: str, maximum: int = 150) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be canonical nonempty text")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _official_https_uri(value: Any, name: str) -> str:
    uri = _canonical_text(value, name, 500)
    parts = urlsplit(uri)
    if (
        parts.scheme != "https"
        or parts.hostname not in {"massive.com", "www.massive.com"}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError(f"{name} must be a credential-free official Massive HTTPS URI")
    return uri


def _lookback_covers_acquisition_start(value: Any) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return False
    try:
        return date.fromisoformat(value) <= date.fromisoformat(ACQUISITION_START)
    except ValueError:
        return False


def build_public_documentation_evidence(
    documents: Sequence[Mapping[str, Any]], *, assessed_at: Any
) -> dict[str, Any]:
    """Bind exact public bytes without turning them into account evidence."""

    assessed = _timestamp(assessed_at, "assessed_at")
    if not isinstance(documents, Sequence) or isinstance(
        documents, (str, bytes, bytearray)
    ):
        raise ValueError("documents must be a sequence")
    if len(documents) != len(PUBLIC_DOCUMENT_CONTRACTS):
        raise ValueError("all exact public documentation contracts are required")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, Mapping) or set(document) != _PUBLIC_INPUT_FIELDS:
            raise ValueError("public documentation fields differ from the contract")
        document_id = _canonical_text(document["document_id"], "document_id")
        if document_id in seen or document_id not in PUBLIC_DOCUMENT_CONTRACTS:
            raise ValueError("public documentation identity is duplicate or unsupported")
        seen.add(document_id)
        contract = PUBLIC_DOCUMENT_CONTRACTS[document_id]
        uri = _official_https_uri(document["uri"], "uri")
        if uri != contract["uri"]:
            raise ValueError("public documentation URI differs from the contract")
        retrieved = _timestamp(document["retrieved_at"], "retrieved_at")
        if retrieved > assessed:
            raise ValueError("public documentation was retrieved after assessment")
        normalized.append(
            {
                "document_id": document_id,
                "uri": uri,
                "retrieved_at": retrieved.isoformat(timespec="microseconds"),
                "payload_sha256": _sha256(
                    document["payload_sha256"], "payload_sha256"
                ),
                "source_class": "PUBLIC_PROVIDER_DOCUMENTATION",
                "authenticated_account_evidence": False,
                "documented_facts": dict(contract["documented_facts"]),
            }
        )
    normalized.sort(key=lambda item: item["document_id"])
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "provider_id": PROVIDER_ID,
        "assessed_at": assessed.isoformat(timespec="microseconds"),
        "documents": normalized,
        "public_documentation_proves_account_identity": False,
        "public_documentation_proves_account_plan": False,
        "public_documentation_proves_current_account_entitlement": False,
        "custom_bars_published_free_plan_access": True,
        "flat_files_published_free_plan_access": False,
    }
    result["evidence_bundle_sha256"] = _hash(result)
    return result


def build_authenticated_account_evidence(
    evidence: Mapping[str, Any], *, assessed_at: Any
) -> dict[str, Any]:
    """Normalize a future account-bound capture without accepting credentials."""

    assessed = _timestamp(assessed_at, "assessed_at")
    if not isinstance(evidence, Mapping) or set(evidence) != _ACCOUNT_INPUT_FIELDS:
        raise ValueError("authenticated account evidence fields differ from the contract")
    captured = _timestamp(evidence["captured_at"], "captured_at")
    terms_retrieved = _timestamp(evidence["terms_retrieved_at"], "terms_retrieved_at")
    if captured > assessed or terms_retrieved > assessed:
        raise ValueError("authenticated evidence cannot postdate assessment")
    kind = _canonical_text(
        evidence["account_binding_evidence_kind"],
        "account_binding_evidence_kind",
    )
    if kind in FORBIDDEN_ACCOUNT_BINDING_EVIDENCE_KINDS:
        raise ValueError(f"{kind} cannot prove authenticated account binding")
    if kind not in ALLOWED_ACCOUNT_BINDING_EVIDENCE_KINDS:
        raise ValueError("account binding evidence kind is unsupported")
    if evidence["authenticated_account_session"] is not True:
        raise ValueError("an authenticated account session is required")
    if evidence["credential_material_recorded"] is not False:
        raise ValueError("credential material must not be recorded")
    if evidence["market_data_response_used_for_account_binding"] is not False:
        raise ValueError("a market-data response cannot bind an account identity")
    if evidence["request_id_used_for_account_binding"] is not False:
        raise ValueError("a provider request_id cannot bind an account identity")
    if evidence["custom_bars_access_confirmed"] is not True:
        raise ValueError("custom-bars access must be confirmed by account evidence")
    try:
        lookback_text = _canonical_text(
            evidence["historical_lookback_start"],
            "historical_lookback_start",
            10,
        )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lookback_text) is None:
            raise ValueError
        lookback = date.fromisoformat(
            lookback_text
        )
        acquisition_start = date.fromisoformat(ACQUISITION_START)
    except ValueError as error:
        raise ValueError("historical_lookback_start must be an ISO date") from error
    if lookback > acquisition_start:
        raise ValueError("authenticated lookback does not cover acquisition start")
    limit = evidence["request_limit_per_minute"]
    if type(limit) is not int or limit != 5:
        raise ValueError("request_limit_per_minute must match the approved value 5")
    try:
        cost = Decimal(str(evidence["incremental_cost_usd"]))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("incremental_cost_usd must be exact decimal text") from error
    if evidence["incremental_cost_usd"] != "0.00" or cost != Decimal("0.00"):
        raise ValueError("incremental_cost_usd must be exactly 0.00")
    requirements = activation_proposal()[
        "entitlement_evidence_required_before_activation"
    ]
    terms_uri = _official_https_uri(evidence["terms_uri"], "terms_uri")
    if terms_uri != requirements["terms_uri"]:
        raise ValueError("terms_uri differs from the approved proposal")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "provider_id": PROVIDER_ID,
        "provider_dataset_id": PROVIDER_DATASET_ID,
        "assessed_at": assessed.isoformat(timespec="microseconds"),
        "captured_at": captured.isoformat(timespec="microseconds"),
        "account_binding_evidence_kind": kind,
        "account_binding_payload_sha256": _sha256(
            evidence["account_binding_payload_sha256"],
            "account_binding_payload_sha256",
        ),
        "account_identity_sha256": _sha256(
            evidence["account_identity_sha256"], "account_identity_sha256"
        ),
        "plan_name": _canonical_text(evidence["plan_name"], "plan_name"),
        "plan_evidence_payload_sha256": _sha256(
            evidence["plan_evidence_payload_sha256"],
            "plan_evidence_payload_sha256",
        ),
        "custom_bars_access_confirmed": True,
        "historical_lookback_start": lookback.isoformat(),
        "request_limit_per_minute": limit,
        "incremental_cost_usd": "0.00",
        "terms_uri": terms_uri,
        "terms_retrieved_at": terms_retrieved.isoformat(timespec="microseconds"),
        "terms_payload_sha256": _sha256(
            evidence["terms_payload_sha256"], "terms_payload_sha256"
        ),
        "authenticated_account_session": True,
        "credential_material_recorded": False,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
    }
    result["evidence_bundle_sha256"] = _hash(result)
    return result


def assess_capture_activation_evidence(
    *,
    public_documentation: Mapping[str, Any] | None,
    authenticated_account: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Report evidence completeness while keeping every authority false."""

    public_valid = False
    authenticated_valid = False
    missing: list[str] = []
    if public_documentation is None:
        missing.append("PUBLIC_DOCUMENTATION_EVIDENCE")
    else:
        expected_public = {
            key: value
            for key, value in public_documentation.items()
            if key != "evidence_bundle_sha256"
        }
        documents = public_documentation.get("documents")
        public_documents_valid = (
            isinstance(documents, list)
            and len(documents) == len(PUBLIC_DOCUMENT_CONTRACTS)
            and all(isinstance(item, Mapping) for item in documents)
        )
        if public_documents_valid:
            observed_ids = {item.get("document_id") for item in documents}
            public_documents_valid = observed_ids == set(PUBLIC_DOCUMENT_CONTRACTS)
        if public_documents_valid:
            for item in documents:
                contract = PUBLIC_DOCUMENT_CONTRACTS[str(item["document_id"])]
                if not (
                    set(item) == _PUBLIC_DOCUMENT_FIELDS
                    and item.get("uri") == contract["uri"]
                    and item.get("documented_facts")
                    == contract["documented_facts"]
                    and item.get("source_class")
                    == "PUBLIC_PROVIDER_DOCUMENTATION"
                    and item.get("authenticated_account_evidence") is False
                    and SHA256_PATTERN.fullmatch(
                        str(item.get("payload_sha256", ""))
                    )
                    is not None
                ):
                    public_documents_valid = False
                    break
        public_valid = (
            set(public_documentation) == _PUBLIC_BUNDLE_FIELDS
            and public_documents_valid
            and public_documentation.get("schema_version") == SCHEMA_VERSION
            and public_documentation.get("policy_version") == POLICY_VERSION
            and public_documentation.get("provider_id") == PROVIDER_ID
            and public_documentation.get("evidence_bundle_sha256")
            == _hash(expected_public)
            and public_documentation.get(
                "public_documentation_proves_account_identity"
            )
            is False
            and public_documentation.get("public_documentation_proves_account_plan")
            is False
            and public_documentation.get(
                "public_documentation_proves_current_account_entitlement"
            )
            is False
            and public_documentation.get("flat_files_published_free_plan_access")
            is False
        )
        if not public_valid:
            raise ValueError("public documentation evidence is invalid or tampered")
    if authenticated_account is None:
        missing.append("AUTHENTICATED_ACCOUNT_BOUND_ENTITLEMENT_EVIDENCE")
    else:
        expected_account = {
            key: value
            for key, value in authenticated_account.items()
            if key != "evidence_bundle_sha256"
        }
        kind = authenticated_account.get("account_binding_evidence_kind")
        authenticated_valid = (
            set(authenticated_account) == _ACCOUNT_BUNDLE_FIELDS
            and authenticated_account.get("schema_version") == SCHEMA_VERSION
            and authenticated_account.get("policy_version") == POLICY_VERSION
            and authenticated_account.get("provider_id") == PROVIDER_ID
            and authenticated_account.get("provider_dataset_id")
            == PROVIDER_DATASET_ID
            and authenticated_account.get("evidence_bundle_sha256")
            == _hash(expected_account)
            and authenticated_account.get("authenticated_account_session") is True
            and kind in ALLOWED_ACCOUNT_BINDING_EVIDENCE_KINDS
            and SHA256_PATTERN.fullmatch(
                str(authenticated_account.get("account_binding_payload_sha256", ""))
            )
            is not None
            and SHA256_PATTERN.fullmatch(
                str(authenticated_account.get("account_identity_sha256", ""))
            )
            is not None
            and SHA256_PATTERN.fullmatch(
                str(authenticated_account.get("plan_evidence_payload_sha256", ""))
            )
            is not None
            and authenticated_account.get("custom_bars_access_confirmed") is True
            and _lookback_covers_acquisition_start(
                authenticated_account.get("historical_lookback_start")
            )
            and authenticated_account.get("request_limit_per_minute") == 5
            and authenticated_account.get("incremental_cost_usd") == "0.00"
            and authenticated_account.get("terms_uri")
            == activation_proposal()["entitlement_evidence_required_before_activation"][
                "terms_uri"
            ]
            and SHA256_PATTERN.fullmatch(
                str(authenticated_account.get("terms_payload_sha256", ""))
            )
            is not None
            and authenticated_account.get("credential_material_recorded") is False
            and authenticated_account.get(
                "market_data_response_used_for_account_binding"
            )
            is False
            and authenticated_account.get("request_id_used_for_account_binding")
            is False
        )
        if not authenticated_valid:
            raise ValueError("authenticated account evidence is invalid or tampered")
    complete = public_valid and authenticated_valid
    proposal = activation_proposal()
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "provider_id": PROVIDER_ID,
        "proposal_sha256": proposal["proposal_sha256"],
        "capture_approval_record_id": CAPTURE_APPROVAL_RECORD_ID,
        "capture_approval_record_sha256": CAPTURE_APPROVAL_RECORD_SHA256,
        "public_documentation_evidence_sha256": (
            public_documentation["evidence_bundle_sha256"]
            if public_valid and public_documentation is not None
            else None
        ),
        "authenticated_account_evidence_sha256": (
            authenticated_account["evidence_bundle_sha256"]
            if authenticated_valid and authenticated_account is not None
            else None
        ),
        "evidence_complete_for_separate_activation_record": complete,
        "missing_evidence": missing,
        "public_documentation_is_not_account_binding": True,
        "market_data_response_is_not_account_binding": True,
        "request_id_is_not_account_identity": True,
        "free_plan_flat_file_access_inferred": False,
        "capture_activation_issued": False,
        "data_calls_allowed": False,
        "provider_bytes_accessed": False,
        "untouched_test_opened": False,
        "dataset_admitted": False,
        "evaluation_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    result["assessment_sha256"] = _hash(result)
    return result
