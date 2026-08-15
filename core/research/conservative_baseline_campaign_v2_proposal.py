"""Inert Campaign v2 preregistration proposal awaiting human approval.

Nothing in this module appends a ledger, opens data, calls a provider, runs
VectorBT or the guardrailed engine, contacts a broker, or submits an order.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any, Mapping

from core.research.conservative_baseline_campaign import (
    ACQUISITION_START as CAMPAIGN_V1_ACQUISITION_START,
    ACQUISITION_END as CAMPAIGN_V1_ACQUISITION_END,
    SPLITS as CAMPAIGN_V1_SPLITS,
    approved_execution_policy as campaign_v1_execution_policy,
)
from core.research.conservative_baseline_strategy import (
    POLICY_VERSION as STRATEGY_VERSION,
    ConservativeBaselineStrategy,
    conservative_baseline_parameters,
)


PROPOSAL_SCHEMA_VERSION = "1.0"
CAMPAIGN_POLICY_VERSION = "conservative-baseline-aapl-msft-spy-campaign-v2"
APPROVAL_STATUS = "PENDING_EXPLICIT_HUMAN_APPROVAL"
ACQUISITION_START = "2026-03-01"
ACQUISITION_END = "2027-02-28"
SPLITS = (
    {"role": "TRAIN", "start": "2026-03-01", "end": "2026-06-30"},
    {"role": "VALIDATION", "start": "2026-07-01", "end": "2026-08-31"},
    {"role": "UNTOUCHED_TEST", "start": "2026-09-01", "end": "2027-02-28"},
)
TARGET_BASKET = ("AAPL", "MSFT", "SPY")
CANDIDATE_SYMBOLS = ("AAPL", "MSFT")
BENCHMARK_SYMBOL = "SPY"


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
PARENT_RESEARCH_EXEMPTION_ID = "RIXA-E657C58EC99C067EAB6879FB035C0A0D"
PARENT_RESEARCH_EXEMPTION_RECORD_HASH = (
    "8eaf1d499213f0abc324b3265b1c6a184b4a083621a01ed0b2808ef89b624513"
)
PARENT_ASSUMPTION_SHA256 = {
    "HISTORICAL_AVAILABILITY": (
        "773913e8f1422586b3a62c177bb3b82799eef42ecaa642a44ac85b88f7eda05d"
    ),
    "INDEX_MEMBERSHIP": (
        "9b5b1804a8fc8c2b7ec9a0cb0ddf0e025a4816834feda9a78574f49b98681fba"
    ),
    "SESSION_TIMES": (
        "e314c8265516382cbbf8f07dace4f8eb2bb0e1846f5e456a0439cd16eac8b822"
    ),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def proposed_execution_policy() -> dict[str, Any]:
    """Keep v1 economics unchanged so only protocol correctness changes."""

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
    """Reassert the same human assumptions over v2; grant no new authority."""

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
            "NO_PERFORMANCE_OR_TRACK_RECORD_CLAIM",
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
    *,
    registered_by: str,
    entitlement_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build schema-compatible inert input; this function never preregisters it."""

    return {
        "proposal_control": {
            "approval_status": APPROVAL_STATUS,
            "explicit_approval_record_required": True,
            "current_v1_preregistration_ledger_accepted": False,
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
    v1_untouched = next(
        split for split in CAMPAIGN_V1_SPLITS if split["role"] == "UNTOUCHED_TEST"
    )
    if (
        acquisition_days > 366
        or date.fromisoformat(SPLITS[-1]["start"])
        <= date.fromisoformat(CAMPAIGN_V1_ACQUISITION_END)
    ):
        raise ValueError("Campaign v2 window violates its fresh bounded-test rule")
    package = {
        "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
        "approval_status": APPROVAL_STATUS,
        "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
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
        "fresh_untouched_rule": (
            "NO_OBSERVATION_OPENED_IN_CAMPAIGN_V1_MAY_BE_TREATED_AS_V2_UNTOUCHED"
        ),
        "campaign_v1_opened_data_reuse_disclosure": {
            "v1_acquisition_start": CAMPAIGN_V1_ACQUISITION_START,
            "v1_acquisition_end": CAMPAIGN_V1_ACQUISITION_END,
            "v1_untouched_start": v1_untouched["start"],
            "v1_untouched_end": v1_untouched["end"],
            "overlaps_v2_train_or_validation": True,
            "eligible_only_as_known_train_or_validation_input": True,
            "eligible_as_v2_untouched": False,
            "v1_outcome_was_known_when_v2_splits_were_proposed": True,
            "strategy_or_parameter_change_based_on_v1_allowed": False,
        },
        "post_v1_protocol_change_disclosure": {
            "primary_metric_changed": True,
            "rationale": (
                "UNADJUSTED_BARS_WITHOUT_COMPLETE_CORPORATE_ACTIONS_CANNOT_"
                "SUPPORT_COMPLETE_TOTAL_OR_BENCHMARK_RELATIVE_RETURN"
            ),
            "replacement_is_diagnostic_only": True,
            "change_selected_to_improve_observed_v1_result": False,
            "promotion_authority_created": False,
        },
        "current_v1_preregistration_schema_compatible": False,
        "required_preregistration_schema_version": (
            "MASSIVE_FUTURE_WINDOW_PREREGISTRATION_V2"
        ),
        "future_window_controls": {
            "campaign_v1_closure_must_precede_v2_preregistration": True,
            "distinct_v2_preregistration_ledger_required": True,
            "distinct_v2_quarantine_root_required": True,
            "preregistration_must_precede_any_v2_data_call": True,
            "request_slice_must_be_complete_and_historical_at_fetch_time": True,
            "future_dated_provider_request_allowed": False,
            "untouched_payload_open_before_single_test_allowed": False,
            "train_validation_evaluation_before_validation_end_allowed": False,
            "untouched_evaluation_before_untouched_end_allowed": False,
        },
        "required_implementation_before_any_v2_evaluation": [
            "FUTURE_WINDOW_PREREGISTRATION_V2_WITH_APPROVAL_PARENT",
            "CAMPAIGN_V2_SPECIFIC_CAPTURE_ROLE_COUNTS",
            "CAMPAIGN_V2_SPECIFIC_EXEMPTION_EXTENSION",
            "CAMPAIGN_V2_RUNNER_WITH_NO_V1_CONSTANT_FALLBACK",
            "MATCHED_TIMING_SPY_DIAGNOSTIC_IMPLEMENTATION_AND_TESTS",
        ],
        "required_v2_preregistration_ledger_relative_path": (
            "data/research/massive_campaign_v2/preregistration.jsonl"
        ),
        "required_v2_quarantine_root_relative_path": (
            "data/research/massive_campaign_v2/raw"
        ),
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
