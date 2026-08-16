"""Inert TRAIN/VALIDATION capture-activation proposal for Campaign v2 revision-1.

The proposal fixes a bounded request surface and evidence requirements.  It
does not authenticate an account, read a credential, call Massive, append an
activation record, open quarantine bytes, admit data or run an evaluation.
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.orchestration.campaign_v2_revision_preregistration import (
    PROPOSAL_SHA256 as CAMPAIGN_PROPOSAL_SHA256,
    QUARANTINE_ROOT_RELATIVE_PATH,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    SPLITS as CAMPAIGN_SPLITS,
    TARGET_BASKET,
)


PROPOSAL_SCHEMA_VERSION = "1.0"
ACTIVATION_POLICY_VERSION = (
    "massive-completed-history-capture-activation-v2-revision-1"
)
APPROVAL_STATUS = "PENDING_EXPLICIT_HUMAN_APPROVAL"
PARENT_PREREGISTRATION_ID = "HQP2R1-BBF7B46BB66E1CC5BB5344DE35A9D9E2"
PARENT_PREREGISTRATION_RECORD_SHA256 = (
    "a44f35f269fd49bbd61b8f49899cd22de6d494edba30078f522438ed9ba0499b"
)
AUTHORIZED_SPLIT_ROLES = ("TRAIN", "VALIDATION")
SEALED_SPLIT_ROLE = "UNTOUCHED_TEST"
MAXIMUM_CHUNK_DAYS = 31


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _authorized_splits() -> list[dict[str, str]]:
    return [
        dict(split)
        for split in CAMPAIGN_SPLITS
        if split["role"] in AUTHORIZED_SPLIT_ROLES
    ]


def _sealed_split() -> dict[str, str]:
    return dict(
        next(split for split in CAMPAIGN_SPLITS if split["role"] == SEALED_SPLIT_ROLE)
    )


def planned_request_slices(
    *,
    target_basket: Sequence[str] = TARGET_BASKET,
    splits: Sequence[Mapping[str, str]] | None = None,
) -> tuple[tuple[str, str, str, str], ...]:
    """Return exact 31-day split-contained slices without provider access."""

    resolved_splits = list(splits) if splits is not None else _authorized_splits()
    if list(target_basket) != list(TARGET_BASKET):
        raise ValueError("capture activation target basket changed")
    exact_splits = _authorized_splits()
    if [dict(split) for split in resolved_splits] != exact_splits:
        raise ValueError("capture activation requires the exact campaign splits")
    ranges: list[tuple[str, str, str]] = []
    for split in resolved_splits:
        if (
            not isinstance(split, Mapping)
            or set(split) != {"role", "start", "end"}
            or split["role"] not in AUTHORIZED_SPLIT_ROLES
        ):
            raise ValueError("capture activation accepts only exact TRAIN/VALIDATION splits")
        cursor = date.fromisoformat(split["start"])
        split_end = date.fromisoformat(split["end"])
        if split_end < cursor:
            raise ValueError("capture activation split is empty")
        while cursor <= split_end:
            through = min(
                split_end, cursor + timedelta(days=MAXIMUM_CHUNK_DAYS - 1)
            )
            ranges.append((split["role"], cursor.isoformat(), through.isoformat()))
            cursor = through + timedelta(days=1)
    return tuple(
        (str(symbol), role, start, end)
        for symbol in target_basket
        for role, start, end in ranges
    )


def _verify_completed_dates(*, current_real_world_date: date) -> None:
    for split in (*_authorized_splits(), _sealed_split()):
        if date.fromisoformat(split["end"]) > current_real_world_date:
            raise ValueError(
                f"{split['role']} ends after the current real-world date"
            )


def activation_proposal(
    *, current_real_world_date: date | None = None
) -> dict[str, Any]:
    """Build and hash an inert activation proposal after date verification."""

    real_date = current_real_world_date or date.today()
    _verify_completed_dates(current_real_world_date=real_date)
    authorized_splits = _authorized_splits()
    sealed_split = _sealed_split()
    slices = planned_request_slices(splits=authorized_splits)
    package: dict[str, Any] = {
        "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
        "activation_policy_version": ACTIVATION_POLICY_VERSION,
        "approval_status": APPROVAL_STATUS,
        "parent_campaign_proposal_sha256": CAMPAIGN_PROPOSAL_SHA256,
        "parent_preregistration_id": PARENT_PREREGISTRATION_ID,
        "parent_preregistration_record_sha256": (
            PARENT_PREREGISTRATION_RECORD_SHA256
        ),
        "provider_id": "MASSIVE",
        "provider_dataset_id": "MASSIVE_STOCKS_CUSTOM_BARS_V2",
        "endpoint_template": (
            "https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/"
            "{from}/{to}"
        ),
        "target_basket": list(TARGET_BASKET),
        "authorized_splits": authorized_splits,
        "sealed_split": sealed_split,
        "sealed_split_semantic_role": "SEALED_RETROSPECTIVE_TEST",
        "authorized_request_slices": [list(item) for item in slices],
        "authorized_request_count": len(slices),
        "maximum_chunk_days": MAXIMUM_CHUNK_DAYS,
        "request_record_limit": 120,
        "sort_order": "ASC",
        "unadjusted_daily_bars_only": True,
        "request_policy": {
            "train_validation_only": True,
            "untouched_test_request_allowed": False,
            "one_http_request_per_exact_slice": True,
            "retry_count_per_slice": 0,
            "each_slice_end_must_be_before_request_date": True,
            "every_partition_end_must_be_on_or_before_activation_date": True,
            "quarantine_only": True,
            "dataset_admission_allowed": False,
            "evaluation_allowed": False,
        },
        "entitlement_evidence_required_before_activation": {
            "authenticated_account_session": True,
            "account_identity_sha256": True,
            "account_entitlement_authenticated": True,
            "completed_historical_daily_aggregate_access_confirmed": True,
            "historical_lookback_covers_acquisition_start": "2024-08-01",
            "plan_name": True,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": True,
            "terms_payload_sha256": True,
            "credential_material_recorded": False,
            "redistribution_permission_inferred": False,
            "provider_payload_semantics_qualified": False,
        },
        "activation_record_requirements": {
            "exact_human_approval_text_required": True,
            "recompute_parent_preregistration_hash": True,
            "recompute_proposal_module_sha256": True,
            "recompute_strategy_source_sha256": True,
            "bind_clean_git_revision": True,
            "bind_entitlement_evidence_sha256": True,
            "bind_exact_authorized_request_slices": True,
            "credential_value_forbidden": True,
        },
        "quarantine_root_relative_path": QUARANTINE_ROOT_RELATIVE_PATH.as_posix(),
        "capture_activation_issued": False,
        "entitlement_revalidated": False,
        "data_calls_allowed": False,
        "provider_bytes_accessed": False,
        "train_validation_opened": False,
        "untouched_test_opened": False,
        "dataset_admitted": False,
        "parameter_search_executed": False,
        "evaluation_allowed": False,
        "guardrailed_replay_executed": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    package["proposal_sha256"] = hashlib.sha256(
        _canonical_json(package).encode("utf-8")
    ).hexdigest()
    return package


def required_approval_text() -> str:
    return (
        "I authorize Campaign v2 revision-1 TRAIN and VALIDATION quarantined "
        f"capture proposal {activation_proposal()['proposal_sha256']}"
    )
