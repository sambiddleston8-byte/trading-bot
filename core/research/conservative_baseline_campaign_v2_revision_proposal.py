"""Inert completed-history revision of the Campaign v2 proposal.

This module defines and hashes a proposal only.  It cannot open data, call a
provider, run an engine, contact a broker, or append an approval.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any, Mapping

from core.research.conservative_baseline_campaign import (
    ACQUISITION_START as CAMPAIGN_V1_ACQUISITION_START,
    approved_execution_policy as campaign_v1_execution_policy,
)
from core.research.conservative_baseline_campaign_v2_proposal import (
    BENCHMARK_SYMBOL,
    CANDIDATE_SYMBOLS,
    PARENT_ASSUMPTION_SHA256,
    PARENT_RESEARCH_EXEMPTION_ID,
    PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
    PROPOSAL_SCHEMA_VERSION,
    TARGET_BASKET,
)
from core.research.conservative_baseline_strategy import (
    POLICY_VERSION as STRATEGY_VERSION,
    ConservativeBaselineStrategy,
    conservative_baseline_parameters,
)


CAMPAIGN_POLICY_VERSION = (
    "conservative-baseline-aapl-msft-spy-campaign-v2-revision-1"
)
APPROVAL_STATUS = "PENDING_EXPLICIT_HUMAN_APPROVAL"
SUPERSEDED_PROPOSAL_SHA256 = (
    "ff4ca9a0919e43044337e609140197bb5869681e47e226720b12df64075cc82b"
)
CALENDAR_CONFLICT_IDENTIFIED_ON = "2026-08-15"
COMPLETED_HISTORY_CUTOFF = "2026-07-31"
ACQUISITION_START = "2024-08-01"
ACQUISITION_END = "2025-07-31"
SPLITS = (
    {"role": "TRAIN", "start": "2024-08-01", "end": "2025-02-28"},
    {"role": "VALIDATION", "start": "2025-03-01", "end": "2025-04-30"},
    {"role": "UNTOUCHED_TEST", "start": "2025-05-01", "end": "2025-07-31"},
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _expected_capture_counts() -> dict[str, int]:
    counts = {
        split["role"]: (
            (
                date.fromisoformat(split["end"])
                - date.fromisoformat(split["start"])
            ).days
            // 31
            + 1
        )
        * len(TARGET_BASKET)
        for split in SPLITS
    }
    return {**counts, "TOTAL": sum(counts.values())}


EXPECTED_CAPTURE_COUNTS = _expected_capture_counts()


def proposed_execution_policy() -> dict[str, Any]:
    """Retain v1 economics; revise only the campaign identity and dates."""

    policy = campaign_v1_execution_policy()
    policy["campaign_policy_version"] = CAMPAIGN_POLICY_VERSION
    policy["parameter_search_allowed"] = False
    return policy


def proposed_execution_policy_sha256() -> str:
    return hashlib.sha256(
        _canonical_json(proposed_execution_policy()).encode("utf-8")
    ).hexdigest()


def proposed_evaluation_protocol() -> dict[str, Any]:
    return {
        "primary_metric": "MATCHED_TIMING_SPY_PRICE_RETURN_DIAGNOSTIC",
        "optimization_direction": "MAXIMIZE",
        "tie_break_metrics": [
            {"metric": "MAXIMUM_DRAWDOWN", "direction": "MINIMIZE"},
            {"metric": "TURNOVER", "direction": "MINIMIZE"},
        ],
        "success_thresholds": {
            "all_candidate_symbols_must_pass": list(CANDIDATE_SYMBOLS),
            "benchmark_only_symbol": BENCHMARK_SYMBOL,
            "cross_symbol_selection_allowed": False,
            "maximum_drawdown_each_candidate": "0.20",
            "minimum_completed_trades_each_candidate": 2,
            "insufficient_completed_trades_outcome": (
                "INCONCLUSIVE_NOT_PASS_NOT_FAIL_NO_RERUN"
            ),
            "minimum_matched_spy_price_relative_return_each_candidate": "0",
            "minimum_mechanical_price_return_each_candidate": "0",
            "pessimistic_scenario_must_pass": True,
            "protocol_conformance_required": True,
            "provider_qualification_required_for_promotion": True,
            "performance_claim_allowed": False,
        },
        "warmup_observations": 50,
        "purge_observations": 1,
        "embargo_observations": 1,
        "maximum_untouched_test_evaluations": 1,
        "execution_policy_version": CAMPAIGN_POLICY_VERSION,
        "execution_policy_sha256": proposed_execution_policy_sha256(),
        "selection_rule_version": (
            "both-candidates-pass-no-cross-symbol-selection-v2-protocol-bound"
        ),
    }


def proposed_research_exemption_extension() -> dict[str, Any]:
    return {
        "status": APPROVAL_STATUS,
        "parent_exemption_id": PARENT_RESEARCH_EXEMPTION_ID,
        "parent_exemption_record_hash": PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
        "target_basket": list(TARGET_BASKET),
        "scope_start": ACQUISITION_START,
        "scope_end": ACQUISITION_END,
        "assumptions_to_be_reasserted_per_completed_capture_slice": [
            "HISTORICAL_INDEX_MEMBERSHIP_REQUIREMENTS_ASSUMED_SATISFIED",
            "DAILY_BAR_POINT_IN_TIME_AVAILABILITY_AT_ASSUMED_1600_NEW_YORK_CLOSE",
            "SESSION_OPEN_CLOSE_ASSUMED_0930_1600_AMERICA_NEW_YORK",
        ],
        "parent_assumption_sha256": dict(PARENT_ASSUMPTION_SHA256),
        "assumption_application_policy": (
            "TO_BE_ASSERTED_AT_CAPTURE_TIME_ONLY_AFTER_APPROVAL_AND_ONLY_FOR_"
            "A_COMPLETED_HISTORICAL_REQUEST_SLICE"
        ),
        "limitations": [
            "NOT_PROVIDER_EVIDENCE",
            "NOT_AUTHENTICATED_REPLAY_EVIDENCE",
            "NO_HISTORICAL_AVAILABILITY_PROOF",
            "NO_INDEX_MEMBERSHIP_PROOF",
            "NO_CORPORATE_ACTION_OR_TOTAL_RETURN_EVIDENCE",
            "NO_ENTITLEMENT_OR_REPLAY_PERMISSION_PROOF",
            "RETROSPECTIVE_AT_PREREGISTRATION_NOT_GENUINELY_FUTURE_UNTOUCHED",
            "NO_ASSERTION_MARKET_OUTCOMES_UNKNOWN_TO_RESEARCHERS",
            "NO_PROMOTION_OR_TRACK_RECORD_AUTHORITY",
        ],
        "extension_registered": False,
        "provider_evidence": False,
        "authenticated_replay_evidence": False,
        "canonical_dataset_admitted": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }


def proposed_quarantine_definition(
    *, registered_by: str, entitlement_metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Return inert schema-shaped input without registering it."""

    return {
        "proposal_control": {
            "approval_status": APPROVAL_STATUS,
            "explicit_approval_record_required": True,
            "superseded_v2_preregistration_ledger_accepted": False,
            "retrospective_test_classification_required": True,
        },
        "registered_by": str(registered_by),
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "splits": [dict(value) for value in SPLITS],
        "strategy_entrypoint": (
            "core.research.conservative_baseline_strategy:"
            f"{ConservativeBaselineStrategy.__name__}"
        ),
        "strategy_source_path": "core/research/conservative_baseline_strategy.py",
        "strategy_version": STRATEGY_VERSION,
        "parameter_space": conservative_baseline_parameters(),
        "evaluation_protocol": proposed_evaluation_protocol(),
        "entitlement_metadata": dict(entitlement_metadata),
    }


def proposal_package() -> dict[str, Any]:
    acquisition_days = (
        date.fromisoformat(ACQUISITION_END)
        - date.fromisoformat(ACQUISITION_START)
    ).days + 1
    expected = date.fromisoformat(ACQUISITION_START)
    for split in SPLITS:
        if date.fromisoformat(split["start"]) != expected:
            raise ValueError("Campaign v2 revision splits must be contiguous")
        expected = date.fromisoformat(split["end"]) + date.resolution
    if (
        acquisition_days > 366
        or expected != date.fromisoformat(ACQUISITION_END) + date.resolution
        or date.fromisoformat(ACQUISITION_END)
        >= date.fromisoformat(CAMPAIGN_V1_ACQUISITION_START)
        or date.fromisoformat(ACQUISITION_END)
        > date.fromisoformat(COMPLETED_HISTORY_CUTOFF)
    ):
        raise ValueError("Campaign v2 revision violates completed disjoint history")
    package = {
        "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
        "approval_status": APPROVAL_STATUS,
        "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
        "supersedes_proposal_sha256": SUPERSEDED_PROPOSAL_SHA256,
        "supersession_reason_codes": [
            "ORIGINAL_VALIDATION_INCOMPLETE_AT_CAPTURE_ACTIVATION_REQUEST",
            "ORIGINAL_UNTOUCHED_TEST_WINDOW_IN_FUTURE",
        ],
        "calendar_conflict_identified_on": CALENDAR_CONFLICT_IDENTIFIED_ON,
        "completed_history_cutoff": COMPLETED_HISTORY_CUTOFF,
        "target_basket": list(TARGET_BASKET),
        "candidate_symbols": list(CANDIDATE_SYMBOLS),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "splits": [dict(value) for value in SPLITS],
        "expected_capture_counts": dict(EXPECTED_CAPTURE_COUNTS),
        "acquisition_calendar_days": acquisition_days,
        "strategy_version": STRATEGY_VERSION,
        "strategy_parameters": conservative_baseline_parameters(),
        "execution_policy": proposed_execution_policy(),
        "evaluation_protocol": proposed_evaluation_protocol(),
        "research_exemption_extension": proposed_research_exemption_extension(),
        "opened_campaign_v1_overlap": False,
        "project_capture_chain_disclosure": {
            "campaign_v1_opened_data_start": CAMPAIGN_V1_ACQUISITION_START,
            "revised_window_ends_before_campaign_v1_opened_data": True,
            "project_local_provider_bytes_previously_opened_for_revised_window": False,
            "market_outcomes_may_be_publicly_knowable": True,
            "researcher_prior_knowledge_not_proven": True,
        },
        "test_evidence_classification": {
            "schema_role": "UNTOUCHED_TEST",
            "semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "genuinely_future_at_preregistration": False,
            "single_open_only": True,
            "promotion_or_track_record_authority": False,
            "claim_outcomes_were_unknown_to_researchers": False,
        },
        "required_controls_before_any_data_call": [
            "EXACT_REVISED_PROPOSAL_APPROVAL",
            "DISTINCT_REVISION_PREREGISTRATION_CHAIN",
            "ENTITLEMENT_REVALIDATION",
            "COMPLETED_HISTORICAL_SLICE_CHECK",
            "QUARANTINE_ONLY_CAPTURE",
        ],
        "purge_embargo_change_allowed": False,
        "success_threshold_change_allowed": False,
        "execution_economics_change_allowed": False,
        "data_calls_allowed": False,
        "evaluation_allowed": False,
        "preregistration_append_allowed": False,
        "provider_evidence": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    package["proposal_sha256"] = hashlib.sha256(
        _canonical_json(package).encode("utf-8")
    ).hexdigest()
    return package
