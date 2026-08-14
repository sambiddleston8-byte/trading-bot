import json
from datetime import datetime, timezone

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import ActivePipelineReplayPlanLedger


COMPONENTS = {
    name: {"version": f"{name}-v1", "source_sha256": character * 64}
    for name, character in zip(
        (
            "investment_research_pipeline", "research_contract",
            "master_portfolio_decision_engine", "factor_lineage_policy",
            "portfolio_engine", "portfolio_construction_service",
        ),
        "abcdef",
    )
}
METRICS = {
    "COMPLETE_BENCHMARK_RELATIVE_RETURN": {"comparison": "AT_LEAST", "threshold": "0"},
    "MAXIMUM_DRAWDOWN": {"comparison": "AT_MOST", "threshold": "0.20"},
    "TURNOVER": {"comparison": "AT_MOST", "threshold": "1.5"},
    "HIT_RATE": {"comparison": "AT_LEAST", "threshold": "0.50"},
}
BASE = {
    "git_revision": "1" * 40,
    "components": COMPONENTS,
    "dependency_lock_sha256": "7" * 64,
    "runner_sha256": "8" * 64,
    "sealed_evaluation_dataset_commitment_sha256": "9" * 64,
    "evaluation_not_before": "2022-04-01T00:00:00+00:00",
    "evaluation_not_after": "2022-12-31T00:00:00+00:00",
    "universe": "SP500_AND_NASDAQ100",
    "historical_universe_source_approval_required": True,
    "commission_bps_per_side": "1",
    "bid_ask_half_spread_bps_per_side": "5",
    "slippage_bps_per_side": "10",
    "latency_bps_per_side": "2",
    "market_impact_bps_per_side": "3",
    "pessimistic_cost_multiplier": "2",
    "acceptance_metrics": METRICS,
    "rejection_rule": "Reject on any failed metric, missing evidence, leakage, or cost-model breach.",
    "registered_by": "Codex",
}


def ledger(tmp_path):
    return ActivePipelineReplayPlanLedger(tmp_path / "replay_plans.jsonl")


def plan(target, **overrides):
    values = dict(BASE)
    now = datetime.now(timezone.utc).isoformat()
    values["registered_at"] = now
    values["evaluation_data_access_not_before"] = now
    values.update(overrides)
    return target.preregister(**values)


def test_preregisters_inert_faithful_active_route(tmp_path):
    target = ledger(tmp_path)
    record = plan(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["active_route_only"] is True
    assert set(record["components"]) == set(COMPONENTS)
    assert record["point_in_time_inputs_required"] is True
    assert record["historical_universe_source_approval_required"] is True
    assert record["cost_model"]["pessimistic_cost_multiplier"] == "2"
    for field in (
        "random_split_allowed", "evaluation_period_reuse_allowed",
        "overlapping_evaluation_window_allowed",
        "metric_substitution_allowed", "optional_stopping_allowed",
        "historical_universe_coverage_bound", "evaluation_dataset_opened",
        "replay_executed", "performance_claim_allowed",
        "paper_broker_submission_enabled", "broker_connection_allowed",
        "live_trading_enabled",
    ):
        assert record[field] is False
    assert target.verify() == [record]


@pytest.mark.parametrize("field,value,fragment", [
    ("git_revision", "abc", "full hexadecimal"),
    ("evaluation_not_after", "2022-05-01T00:00:00+00:00", "90 days"),
    ("historical_universe_source_approval_required", False, "remain required"),
    ("bid_ask_half_spread_bps_per_side", 0, "positive"),
    ("slippage_bps_per_side", 0, "positive"),
    ("slippage_bps_per_side", "9.9", "0.10%"),
    ("latency_bps_per_side", 0, "positive"),
    ("market_impact_bps_per_side", 0, "positive"),
    ("pessimistic_cost_multiplier", "1.9", "at least 2"),
    ("latency_bps_per_side", "0.9", "at least 1 basis point"),
])
def test_incomplete_or_unrealistic_plan_fails_closed(tmp_path, field, value, fragment):
    with pytest.raises(ValueError, match=fragment):
        plan(ledger(tmp_path), **{field: value})


def test_requires_exact_active_components_and_metrics(tmp_path):
    with pytest.raises(ValueError, match="active pipeline"):
        plan(ledger(tmp_path), components={})
    incomplete_metrics = dict(METRICS)
    incomplete_metrics.pop("TURNOVER")
    with pytest.raises(ValueError, match="required metrics"):
        plan(ledger(tmp_path), acceptance_metrics=incomplete_metrics)
    invalid_metrics = {key: dict(value) for key, value in METRICS.items()}
    invalid_metrics["MAXIMUM_DRAWDOWN"]["comparison"] = "AT_LEAST"
    with pytest.raises(ValueError, match="AT_MOST"):
        plan(ledger(tmp_path), acceptance_metrics=invalid_metrics)


def test_identical_retry_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    first = plan(target)
    assert target.preregister(**{
        **BASE,
        "registered_at": first["registered_at"],
        "evaluation_data_access_not_before": first["evaluation_data_access_not_before"],
    }) == first
    assert len(target.verify()) == 1


def test_registration_cannot_be_backdated_or_plan_shopped_for_same_revision(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match="actual append time"):
        plan(target, registered_at="2022-03-01T00:00:00+00:00")
    first = plan(target)
    with pytest.raises(LedgerIntegrityError, match="single binding"):
        plan(
            target,
            git_revision=first["git_revision"],
            acceptance_metrics={
                **METRICS,
                "HIT_RATE": {"comparison": "AT_LEAST", "threshold": "0.6"},
            },
        )


def test_overlapping_evaluation_window_is_rejected_across_revisions(tmp_path):
    target = ledger(tmp_path)
    plan(target)
    with pytest.raises(LedgerIntegrityError, match="overlaps an existing unresolved"):
        plan(
            target,
            git_revision="2" * 40,
            evaluation_not_before="2022-06-01T00:00:00+00:00",
            evaluation_not_after="2023-01-31T00:00:00+00:00",
        )
    assert len(target.verify()) == 1


def test_non_overlapping_evaluation_window_is_allowed_for_new_revision(tmp_path):
    target = ledger(tmp_path)
    plan(target)
    second = plan(
        target,
        git_revision="2" * 40,
        evaluation_not_before="2023-01-01T00:00:00+00:00",
        evaluation_not_after="2023-06-30T00:00:00+00:00",
    )
    assert second["git_revision"] == "2" * 40
    assert len(target.verify()) == 2


@pytest.mark.parametrize("change", [
    {"active_route_only": False}, {"random_split_allowed": True},
    {"evaluation_period_reuse_allowed": True}, {"metric_substitution_allowed": True},
    {"optional_stopping_allowed": True}, {"historical_universe_coverage_bound": True},
    {"evaluation_dataset_opened": True}, {"replay_executed": True},
    {"performance_claim_allowed": True}, {"paper_broker_submission_enabled": True},
    {"broker_connection_allowed": True}, {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_execute_or_grant_authority(tmp_path, change):
    target = ledger(tmp_path)
    record = plan(target)
    record.update(change)
    from core.orchestration import active_pipeline_replay_plan as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()
