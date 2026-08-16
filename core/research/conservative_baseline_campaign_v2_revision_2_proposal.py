"""Inert Stage-0 proposal for Campaign v2 revision-2.

The proposal is deliberately incapable of network access, provider capture,
data admission, evaluation, ledger mutation, broker access, or order activity.
It becomes no authority until its exact digest receives separate human
approval and a later preregistration chain is implemented and verified.
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from typing import Any

from core.research.conservative_baseline_campaign_v2_proposal import (
    BENCHMARK_SYMBOL,
    CANDIDATE_SYMBOLS,
    PARENT_ASSUMPTION_SHA256,
    PARENT_RESEARCH_EXEMPTION_ID,
    PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
    PROPOSAL_SCHEMA_VERSION,
    TARGET_BASKET,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    proposal_package as revision_1_proposal_package,
    proposed_execution_policy as revision_1_execution_policy,
)
from core.research.conservative_baseline_strategy import (
    POLICY_VERSION as STRATEGY_VERSION,
    conservative_baseline_parameters,
)


CAMPAIGN_POLICY_VERSION = (
    "conservative-baseline-aapl-msft-spy-campaign-v2-revision-2"
)
APPROVAL_STATUS = "PENDING_EXPLICIT_HUMAN_APPROVAL"
SUPERSEDED_PROPOSAL_SHA256 = (
    "7c43094e64f324d6987b67a25d03626eb4defe4096ae1b135e1c0319b60fc0d5"
)
PROPOSAL_AS_OF = "2026-08-16"
PROPOSAL_VALID_UNTIL = "2026-09-01"
FREE_PLAN_ROLLING_HISTORY_YEARS = 2
MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS = 30
EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE = "2024-08-16"
ACQUISITION_START = "2024-10-01"
ACQUISITION_END = "2025-07-31"
SPLITS = (
    {"role": "TRAIN", "start": "2024-10-01", "end": "2025-02-28"},
    {"role": "VALIDATION", "start": "2025-03-01", "end": "2025-04-30"},
    {"role": "UNTOUCHED_TEST", "start": "2025-05-01", "end": "2025-07-31"},
)
AUTHORITATIVE_EVALUATOR = (
    "core.guardrailed_backtest:GuardrailedBacktestEngine"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _bar_slice_count(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days // 31 + 1


def _shift_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def assert_capture_window_current(*, as_of: str) -> None:
    """Fail when capture activation would erode the registered lookback buffer."""

    assessed = date.fromisoformat(as_of)
    earliest = _shift_calendar_years(assessed, -FREE_PLAN_ROLLING_HISTORY_YEARS)
    start = date.fromisoformat(ACQUISITION_START)
    if assessed < date.fromisoformat(PROPOSAL_AS_OF):
        raise ValueError("capture activation cannot predate the revision-2 proposal")
    if assessed > date.fromisoformat(PROPOSAL_VALID_UNTIL):
        raise ValueError("revision-2 proposal expired before capture activation")
    if (start - earliest).days < MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS:
        raise ValueError("revision-2 rolling-history buffer has eroded")


def _capture_counts() -> dict[str, Any]:
    by_role: dict[str, dict[str, int]] = {}
    for split in SPLITS:
        bar_requests = _bar_slice_count(split["start"], split["end"]) * len(
            TARGET_BASKET
        )
        corporate_action_initial_requests = 2 * len(TARGET_BASKET)
        by_role[split["role"]] = {
            "bar_requests": bar_requests,
            "corporate_action_initial_requests": corporate_action_initial_requests,
            "initial_requests": bar_requests + corporate_action_initial_requests,
        }
    return {
        "by_role": by_role,
        "train_validation_initial_requests": sum(
            values["initial_requests"]
            for role, values in by_role.items()
            if role != "UNTOUCHED_TEST"
        ),
        "all_roles_initial_requests": sum(
            values["initial_requests"] for values in by_role.values()
        ),
        "pagination_continuations": "BOUNDED_BY_CAPTURE_ACTIVATION_NOT_YET_AUTHORIZED",
    }


def proposed_execution_policy() -> dict[str, Any]:
    policy = dict(revision_1_execution_policy())
    policy["campaign_policy_version"] = CAMPAIGN_POLICY_VERSION
    policy["parameter_search_allowed"] = False
    return policy


def proposed_execution_policy_sha256() -> str:
    return hashlib.sha256(
        _canonical_json(proposed_execution_policy()).encode("utf-8")
    ).hexdigest()


def proposed_evaluation_protocol() -> dict[str, Any]:
    return {
        "authoritative_evaluator": AUTHORITATIVE_EVALUATOR,
        "vectorbt_on_critical_path": False,
        "vectorbt_required": False,
        "parameter_screening_allowed": False,
        "indicator_warmup_source": (
            "STRICTLY_EARLIER_ADMITTED_BARS_FROM_PRECEDING_PARTITIONS"
        ),
        "warmup_observations_are_not_scored": True,
        "primary_metric": "TOTAL_RETURN_RELATIVE_TO_MATCHED_TIMING_SPY_TOTAL_RETURN",
        "optimization_direction": "MAXIMIZE",
        "success_thresholds": {
            "all_candidate_symbols_must_pass": list(CANDIDATE_SYMBOLS),
            "benchmark_only_symbol": BENCHMARK_SYMBOL,
            "cross_symbol_selection_allowed": False,
            "maximum_drawdown_each_candidate": "0.20",
            "minimum_completed_trades_each_candidate": 2,
            "insufficient_completed_trades_outcome": (
                "INCONCLUSIVE_NOT_PASS_NOT_FAIL_NO_RERUN"
            ),
            "minimum_matched_spy_total_relative_return_each_candidate": "0",
            "minimum_mechanical_total_return_each_candidate": "0",
            "pessimistic_scenario_must_pass": True,
            "protocol_conformance_required": True,
            "provider_qualification_required_for_promotion": True,
            "complete_corporate_action_evidence_required": True,
            "performance_claim_allowed": False,
        },
        "warmup_observations": 50,
        "purge_observations": 1,
        "embargo_observations": 1,
        "maximum_untouched_test_evaluations": 1,
        "execution_policy_version": CAMPAIGN_POLICY_VERSION,
        "execution_policy_sha256": proposed_execution_policy_sha256(),
        "selection_rule_version": "fixed-candidates-no-parameter-screening-v2r2",
        "corporate_action_total_return_policy": {
            "price_basis": "UNADJUSTED_DAILY_BARS",
            "dividend_amount_field": "cash_amount",
            "dividend_accrual_event": "EX_DIVIDEND_DATE",
            "dividend_reinvestment": False,
            "declaration_date_must_not_follow_ex_dividend_date": True,
            "point_in_time_availability_must_be_proven": True,
            "split_event_field": "execution_date",
            "split_ratio_fields": ["split_from", "split_to"],
            "events_outside_acquisition_window_allowed": False,
            "forbidden_provider_adjusted_fields": [
                "historical_adjustment_factor",
                "split_adjusted_cash_amount",
            ],
            "same_policy_required_for_candidates_and_spy": True,
            "unresolved_semantics_fail_evaluation": True,
        },
    }


def proposed_capture_scope() -> dict[str, Any]:
    """Describe only future quarantine requests; issue none."""

    return {
        "delivery_model": "CLOUD_NATIVE_REST_JSON",
        "provider_host": "api.massive.com",
        "symbols": list(TARGET_BASKET),
        "partition_roles": [split["role"] for split in SPLITS],
        "endpoints": {
            "DAILY_BARS": {
                "method": "GET",
                "path_template": (
                    "/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}"
                ),
                "date_partitioning": "MAXIMUM_31_CALENDAR_DAY_SLICES",
                "non_secret_query": {
                    "adjusted": "false",
                    "sort": "asc",
                    "limit": "120",
                },
                "purpose": "UNADJUSTED_EXECUTION_PRICES",
                "pagination": "FAIL_CLOSED_IF_NEXT_URL_PRESENT_FOR_31_DAY_SLICE",
            },
            "DIVIDENDS": {
                "method": "GET",
                "path": "/stocks/v1/dividends",
                "per_symbol_date_filters": [
                    "ticker",
                    "ex_dividend_date.gte",
                    "ex_dividend_date.lte",
                ],
                "non_secret_query": {
                    "limit": "5000",
                    "sort": "ex_dividend_date.asc",
                },
                "pagination": "EXHAUST_NEXT_URL_WITHIN_SAME_HOST_AND_PATH",
                "purpose": "CASH_DISTRIBUTIONS_FOR_TOTAL_RETURN",
                "evaluation_fields": [
                    "cash_amount",
                    "currency",
                    "declaration_date",
                    "ex_dividend_date",
                    "id",
                    "pay_date",
                    "record_date",
                    "ticker",
                ],
                "forbidden_evaluation_fields": [
                    "historical_adjustment_factor",
                    "split_adjusted_cash_amount",
                ],
                "accounting_boundary": (
                    "ACCRUE_RAW_CASH_AMOUNT_ON_IN_WINDOW_EX_DIVIDEND_DATE_"
                    "ONLY_AFTER_POINT_IN_TIME_ADMISSION"
                ),
            },
            "STOCK_SPLITS": {
                "method": "GET",
                "path": "/stocks/v1/splits",
                "per_symbol_date_filters": [
                    "ticker",
                    "execution_date.gte",
                    "execution_date.lte",
                ],
                "non_secret_query": {
                    "limit": "5000",
                    "sort": "execution_date.asc",
                },
                "pagination": "EXHAUST_NEXT_URL_WITHIN_SAME_HOST_AND_PATH",
                "purpose": "SHARE_AND_PRICE_BASIS_ADJUSTMENT",
                "evaluation_fields": [
                    "adjustment_type",
                    "execution_date",
                    "id",
                    "split_from",
                    "split_to",
                    "ticker",
                ],
                "forbidden_evaluation_fields": ["historical_adjustment_factor"],
            },
        },
        "raw_response_requirements": {
            "hash_full_payload_bytes_before_parsing": True,
            "retain_owner_only_quarantine_bytes": True,
            "bind_request_and_response_provenance": True,
            "bind_byte_derived_documentation_evidence": True,
            "credential_material_recorded": False,
        },
        "untouched_test_request_allowed": False,
        "train_validation_request_allowed": False,
        "provider_request_allowed": False,
        "counts": _capture_counts(),
    }


def proposed_research_exemption_extension() -> dict[str, Any]:
    return {
        "status": APPROVAL_STATUS,
        "parent_exemption_id": PARENT_RESEARCH_EXEMPTION_ID,
        "parent_exemption_record_hash": PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
        "parent_assumption_sha256": dict(PARENT_ASSUMPTION_SHA256),
        "target_basket": list(TARGET_BASKET),
        "scope_start": ACQUISITION_START,
        "scope_end": ACQUISITION_END,
        "limitations": [
            "HUMAN_RESEARCH_ASSUMPTION_NOT_PROVIDER_EVIDENCE",
            "NO_HISTORICAL_AVAILABILITY_PROOF",
            "NO_INDEX_MEMBERSHIP_PROOF",
            "NO_ENTITLEMENT_OR_REPLAY_PERMISSION_PROOF",
            "SEALED_RETROSPECTIVE_TEST_NOT_FUTURE_TEST",
            "NO_PROMOTION_OR_TRACK_RECORD_AUTHORITY",
        ],
        "extension_registered": False,
    }


def proposal_package() -> dict[str, Any]:
    proposal_date = date.fromisoformat(PROPOSAL_AS_OF)
    earliest = date.fromisoformat(EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE)
    acquisition_start = date.fromisoformat(ACQUISITION_START)
    acquisition_end = date.fromisoformat(ACQUISITION_END)
    if earliest != _shift_calendar_years(
        proposal_date, -FREE_PLAN_ROLLING_HISTORY_YEARS
    ):
        raise ValueError("recorded rolling-history boundary differs from proposal date")
    expected_valid_until = _shift_calendar_years(
        acquisition_start, FREE_PLAN_ROLLING_HISTORY_YEARS
    ) - timedelta(days=MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS)
    if date.fromisoformat(PROPOSAL_VALID_UNTIL) != expected_valid_until:
        raise ValueError("proposal expiry differs from the rolling-history buffer")
    expected = acquisition_start
    for split in SPLITS:
        if date.fromisoformat(split["start"]) != expected:
            raise ValueError("Campaign v2 revision-2 splits must be contiguous")
        expected = date.fromisoformat(split["end"]) + timedelta(days=1)
    if expected != acquisition_end + timedelta(days=1):
        raise ValueError("revision-2 splits do not cover the acquisition window")
    if acquisition_end > proposal_date:
        raise ValueError("revision-2 window is not completed history")
    if acquisition_start < earliest + timedelta(
        days=MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS
    ):
        raise ValueError("revision-2 window violates rolling-history controls")
    assert_capture_window_current(as_of=PROPOSAL_AS_OF)
    revision_1 = revision_1_proposal_package()
    if revision_1["proposal_sha256"] != SUPERSEDED_PROPOSAL_SHA256:
        raise ValueError("revision-1 proposal identity differs from supersession parent")
    package = {
        "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
        "approval_status": APPROVAL_STATUS,
        "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
        "supersedes_proposal_sha256": SUPERSEDED_PROPOSAL_SHA256,
        "supersession_effect": "ONLY_AFTER_EXACT_REVISION_2_APPROVAL_AND_REGISTRATION",
        "supersession_reason_codes": [
            "REVISION_1_START_OUTSIDE_CURRENTLY_PUBLISHED_TWO_YEAR_FREE_LOOKBACK",
            "TOTAL_RETURN_GATE_LACKED_DIVIDEND_AND_SPLIT_CAPTURE_SCOPE",
            "VECTORBT_ADDED_NO_VALUE_FOR_FIXED_PARAMETER_SPACE",
        ],
        "proposal_as_of": PROPOSAL_AS_OF,
        "proposal_valid_until": PROPOSAL_VALID_UNTIL,
        "rolling_history_control": {
            "published_free_history_years": FREE_PLAN_ROLLING_HISTORY_YEARS,
            "earliest_published_history_on_proposal_date": (
                EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE
            ),
            "minimum_start_buffer_days": MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS,
            "actual_start_buffer_days": (acquisition_start - earliest).days,
            "must_be_revalidated_before_capture_activation": True,
            "activation_must_call": (
                "assert_capture_window_current(as_of=activation_date)"
            ),
            "public_documentation_is_not_account_binding": True,
        },
        "target_basket": list(TARGET_BASKET),
        "candidate_symbols": list(CANDIDATE_SYMBOLS),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "splits": [dict(split) for split in SPLITS],
        "strategy_version": STRATEGY_VERSION,
        "strategy_parameters": conservative_baseline_parameters(),
        "execution_policy": proposed_execution_policy(),
        "evaluation_protocol": proposed_evaluation_protocol(),
        "capture_scope": proposed_capture_scope(),
        "research_exemption_extension": proposed_research_exemption_extension(),
        "test_evidence_classification": {
            "schema_role": "UNTOUCHED_TEST",
            "semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "single_open_only": True,
            "promotion_or_track_record_authority": False,
        },
        "gates_before_any_provider_request": [
            "EXACT_REVISION_2_PROPOSAL_APPROVAL",
            "REVISION_2_PREREGISTRATION_CHAIN",
            "BYTE_BOUND_PUBLIC_DOCUMENTATION_EVIDENCE",
            "AUTHENTICATED_ACCOUNT_BOUND_ZERO_COST_ENTITLEMENT_EVIDENCE",
            "SEPARATE_BOUNDED_TRAIN_VALIDATION_CAPTURE_ACTIVATION",
        ],
        "data_calls_allowed": False,
        "api_key_request_allowed": False,
        "evaluation_allowed": False,
        "preregistration_append_allowed": False,
        "provider_bytes_accessed": False,
        "untouched_test_opened": False,
        "dataset_admitted": False,
        "performance_claim_allowed": False,
        "aws_resource_creation_allowed": False,
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
        "I approve Campaign v2 revision-2 proposal "
        f"{proposal_package()['proposal_sha256']}"
    )
