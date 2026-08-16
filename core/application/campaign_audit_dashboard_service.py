"""Deterministic, read-only Stage-0/Stage-1 campaign audit projection."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    PREREGISTRATION_ID,
    PREREGISTRATION_RECORD_SHA256,
    PROPOSAL_SHA256,
    PUBLIC_DOCUMENTATION_BUNDLE_SHA256,
    PUBLIC_DOCUMENTATION_EVIDENCE_ID,
    PUBLIC_DOCUMENTATION_RECORD_SHA256,
)
from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    ACQUISITION_END,
    ACQUISITION_START,
    AUTHORITATIVE_EVALUATOR,
    EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE,
    MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS,
    PROPOSAL_AS_OF,
    PROPOSAL_VALID_UNTIL,
)


REQUESTED_START = "2024-09-01"
REQUESTED_END = "2025-07-31"
DASHBOARD_EVIDENCE_ID = "CV2R2DASH-6DF966621092349395BC7B782BDC03E7"
DASHBOARD_RECORD_SHA256 = "f724c56171ecce98a75cf761e505ba05c2a0e1a32b5a4a689ca4d206999857d5"
DASHBOARD_PAYLOAD_SHA256 = "5a2ef5f4aad07421662567d54fb6731f6db25099385ed56c697eab6c5da2a832"
DASHBOARD_BUNDLE_SHA256 = "6df966621092349395bc7b782bdc03e78b2a7b36bb38ad3064154e674272be6c"


def campaign_audit_snapshot() -> dict[str, Any]:
    """Return a fixed, secret-free projection with no filesystem or network I/O."""

    requested_buffer = (
        date.fromisoformat(REQUESTED_START)
        - date.fromisoformat(EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE)
    ).days
    return {
        "dashboard_mode": "READ_ONLY_STAGE_1_PREVIEW",
        "stage_0_status": "INCOMPLETE_BLOCKED",
        "stage_1_status": "PREVIEW_ONLY_LOCKED_BY_STAGE_0",
        "approved_revision_2": {
            "proposal_sha256": PROPOSAL_SHA256,
            "preregistration_id": PREREGISTRATION_ID,
            "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
            "window": f"{ACQUISITION_START} to {ACQUISITION_END}",
            "proposal_as_of": PROPOSAL_AS_OF,
            "proposal_valid_until": PROPOSAL_VALID_UNTIL,
            "authoritative_evaluator": AUTHORITATIVE_EVALUATOR,
        },
        "campaign_chain": [
            {
                "gate": "Revision-2 proposal and preregistration",
                "status": "REGISTERED_INERT",
                "identity": PREREGISTRATION_ID,
            },
            {
                "gate": "Public documentation",
                "status": "REGISTERED_PRODUCT_FACTS_ONLY",
                "identity": PUBLIC_DOCUMENTATION_EVIDENCE_ID,
            },
            {
                "gate": "Authenticated dashboard labels",
                "status": "REGISTERED_PARTIAL_LABEL_EVIDENCE",
                "identity": DASHBOARD_EVIDENCE_ID,
            },
            {
                "gate": "Account-bound entitlement",
                "status": "UNRESOLVED",
                "identity": "NONE",
            },
            {
                "gate": "Capture activation",
                "status": "NOT_AUTHORIZED",
                "identity": "NONE",
            },
        ],
        "entitlement_status": {
            "visible_dashboard_labels": "REGISTERED_PARTIAL_LABELS_ONLY_NOT_ACCOUNT_BINDING",
            "dashboard_authentication_basis": "LOCAL_OPERATOR_ATTESTATION_ONLY",
            "row_selection_semantics": "PAGE_UNIQUENESS_NOT_INDEPENDENTLY_VERIFIED",
            "account_identity": "UNRESOLVED",
            "account_to_plan_binding": "UNRESOLVED",
            "daily_bars": "PUBLIC_PRODUCT_FACT_ONLY_ACCOUNT_ACCESS_UNRESOLVED",
            "dividends": "PUBLIC_PRODUCT_FACT_ONLY_ACCOUNT_ACCESS_UNRESOLVED",
            "stock_splits": "PUBLIC_PRODUCT_FACT_ONLY_ACCOUNT_ACCESS_UNRESOLVED",
            "lookback": "PUBLIC_PRODUCT_FACT_ONLY_ACCOUNT_COVERAGE_UNRESOLVED",
            "zero_incremental_cost": "UNRESOLVED",
            "provider_origin": "UNRESOLVED",
        },
        "evidence_hashes": {
            "public_documentation_record_sha256": PUBLIC_DOCUMENTATION_RECORD_SHA256,
            "public_documentation_bundle_sha256": PUBLIC_DOCUMENTATION_BUNDLE_SHA256,
            "dashboard_record_sha256": DASHBOARD_RECORD_SHA256,
            "dashboard_payload_sha256": DASHBOARD_PAYLOAD_SHA256,
            "dashboard_bundle_sha256": DASHBOARD_BUNDLE_SHA256,
        },
        "requested_variant": {
            "window": f"{REQUESTED_START} to {REQUESTED_END}",
            "history_buffer_days": requested_buffer,
            "minimum_history_buffer_days": MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS,
            "calculated_as_of": PROPOSAL_AS_OF,
            "proposal_valid_until": PROPOSAL_VALID_UNTIL,
            "qualification": "REJECTED_NOT_AN_OFFICIAL_REVISION_2_PROPOSAL",
        },
        "compliance_block_reasons": [
            "REVISION_2_ALREADY_APPROVED_AND_REGISTERED_NO_DUPLICATE_APPROVAL",
            f"REQUESTED_START_BUFFER_{requested_buffer}_DAYS_BELOW_{MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS}_DAY_MINIMUM",
            "SPY_ONLY_CORPORATE_ACTION_SCOPE_DIFFERS_FROM_APPROVED_AAPL_MSFT_SPY_POLICY",
            "AUTHENTICATED_ACCOUNT_IDENTITY_UNRESOLVED",
            "ACCOUNT_BOUND_DAILY_BARS_DIVIDENDS_SPLITS_LOOKBACK_AND_ZERO_COST_UNRESOLVED",
            "NO_CAPTURE_ACTIVATION_OR_PROVIDER_REQUEST_AUTHORITY",
        ],
        "authorities": {
            "temporary_api_key_request_allowed": False,
            "provider_request_allowed": False,
            "capture_activation_allowed": False,
            "dataset_admission_allowed": False,
            "evaluation_allowed": False,
            "broker_connection_allowed": False,
            "orders_allowed": False,
            "live_trading_allowed": False,
        },
    }
