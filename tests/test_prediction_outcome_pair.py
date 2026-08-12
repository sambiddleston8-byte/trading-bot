from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import InvestmentDecisionLedger, LedgerIntegrityError
from core.performance import (
    CorporateActionLedger,
    OutcomeObservationLedger,
    PredictionOutcomePairLedger,
    TotalReturnLedger,
)


MODELS = [{"component": "portfolio", "version": "1.0"}]


def chain(
    tmp_path,
    *,
    confidence=72,
    expected_return=0.20,
    horizon_days=30,
    decided_at="2025-01-02T14:55:00+00:00",
):
    decisions = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    decision = decisions.append(
        ticker="NVDA",
        decision="BUY",
        decision_payload={
            "confidence": confidence,
            "expected_return": expected_return,
            "expected_return_horizon_days": horizon_days,
        },
        model_versions=MODELS,
        data_as_of="2025-01-02T14:50:00+00:00",
        portfolio_version="PORT-001",
        git_revision="abc123",
        decided_at=decided_at,
        decision_id="DEC-001",
    )
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id=decision["decision_id"],
        portfolio_version="PORT-001",
        ticker="NVDA",
        side="BUY",
        quantity=2,
        reference_price=100,
        target_weight=0.1,
        strategy_version="strategy-v1",
        model_versions=MODELS,
        created_at="2025-01-02T15:00:00+00:00",
        git_revision="abc123",
        order_id="PORD-001",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill = executions.simulate_full_fill(
        order_id="PORD-001",
        fill_price=100,
        fees=2,
        filled_at="2025-01-02T15:01:00+00:00",
    )
    observations = OutcomeObservationLedger(tmp_path / "observations.jsonl", executions)
    observations.observe(
        fill_id=fill["fill_id"],
        horizon="ENTRY",
        benchmark_price=5_900,
        benchmark_price_effective_at="2025-01-02T15:00:00+00:00",
        retrieved_at="2025-01-02T15:02:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )
    observations.observe(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        asset_price=110,
        benchmark_price=6_018,
        asset_price_effective_at="2025-02-03T16:00:00+00:00",
        benchmark_price_effective_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )
    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    actions.record(
        fill_id=fill["fill_id"],
        covers_from_at="2025-01-02T15:01:00+00:00",
        through_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_CORPORATE_ACTION_FIXTURE",
        source_version="fixture-v1",
        source_input_sha256="a" * 64,
    )
    totals = TotalReturnLedger(tmp_path / "total_returns.jsonl", observations, actions)
    total = totals.calculate(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        calculated_at="2025-02-03T17:01:00+00:00",
    )
    pairs = PredictionOutcomePairLedger(
        tmp_path / "pairs.jsonl", decisions, totals
    )
    return pairs, decisions, totals, decision, total, fill


def pair(item, decision, total, **overrides):
    values = {
        "decision_id": decision["decision_id"],
        "total_return_result_id": total["result_id"],
        "paired_at": "2025-02-03T17:02:00+00:00",
    }
    values.update(overrides)
    return item.pair(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import prediction_outcome_pair as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_pairs_preregistered_prediction_to_exact_fixed_horizon_outcome(tmp_path):
    item, _, _, decision, total, fill = chain(tmp_path)
    result = pair(item, decision, total)
    actual = total["exact_fractions"][
        "gross_total_return_after_entry_fee_excl_exit"
    ]
    assert result["record_type"] == "RAW_FIXED_HORIZON_PREDICTION_OUTCOME_EVIDENCE"
    assert result["decision_record_hash"] == decision["record_hash"]
    assert result["total_return_record_hash"] == total["record_hash"]
    assert result["fill_record_hash"] == fill["record_hash"]
    assert result["predicted_expected_return"] == "0.2"
    assert result["predicted_confidence_score"] == "72"
    assert result["predicted_horizon_days"] == 30
    assert result["exact_fractions"]["actual_total_return"] == actual
    assert result["complete_round_trip"] is False
    assert result["exit_execution_cost_included"] is False
    assert result["success_rule_applied"] is False
    assert result["confidence_bucket_applied"] is False
    assert result["expected_return_bucket_applied"] is False
    assert result["hit_rate_calculated"] is False
    assert result["calibration_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert item.verify() == [result]


@pytest.mark.parametrize(
    "chain_overrides,fragment",
    [
        ({"confidence": 101}, "0-to-100"),
        ({"confidence": -1}, "0-to-100"),
        ({"confidence": "NaN"}, "finite"),
        ({"expected_return": -1.1}, "total loss"),
        ({"expected_return": "NaN"}, "finite"),
        ({"horizon_days": 31}, "does not match"),
        ({"horizon_days": None}, "positive integer"),
        ({"decided_at": "2025-01-02T15:02:00+00:00"}, "predate"),
    ],
)
def test_invalid_or_post_fill_prediction_fails_closed(tmp_path, chain_overrides, fragment):
    item, _, _, decision, total, _ = chain(tmp_path, **chain_overrides)
    result = pair(item, decision, total)
    assert result["status"] == "NOT_PAIRABLE"
    assert fragment in " ".join(result["reasons"])
    assert item.records() == []


def test_missing_or_wrong_support_fails_closed(tmp_path):
    item, _, _, decision, total, _ = chain(tmp_path)
    missing_decision = item.pair(
        decision_id="UNKNOWN",
        total_return_result_id=total["result_id"],
    )
    missing_return = item.pair(
        decision_id=decision["decision_id"],
        total_return_result_id="UNKNOWN",
    )
    assert missing_decision["status"] == "NOT_PAIRABLE"
    assert missing_return["status"] == "NOT_PAIRABLE"


def test_pair_time_guards_fail_closed(tmp_path):
    item, _, _, decision, total, _ = chain(tmp_path)
    before = pair(item, decision, total, paired_at="2025-01-01T00:00:00+00:00")
    future = pair(item, decision, total, paired_at="2099-01-01T00:00:00+00:00")
    assert before["status"] == "NOT_PAIRABLE"
    assert future["status"] == "NOT_PAIRABLE"


def test_identical_concurrent_retries_append_once(tmp_path):
    item, _, _, decision, total, _ = chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: pair(item, decision, total), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"predicted_expected_return": "9"},
        {"predicted_confidence_score": "9"},
        {"actual_total_return": "9"},
        {"prediction_error": "9"},
        {"complete_round_trip": True},
        {"exit_execution_cost_included": True},
        {"success_rule_applied": True},
        {"confidence_bucket_applied": True},
        {"expected_return_bucket_applied": True},
        {"hit_rate_calculated": True},
        {"calibration_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item, _, _, decision, total, _ = chain(tmp_path)
    pair(item, decision, total)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_changed_decision_or_total_return_support_is_detected(tmp_path):
    item, decisions, totals, decision, total, _ = chain(tmp_path)
    pair(item, decision, total)
    rewrite_with_valid_hash(decisions.path, decision="SELL")
    with pytest.raises(LedgerIntegrityError):
        item.verify()

    item, decisions, totals, decision, total, _ = chain(tmp_path / "return")
    pair(item, decision, total)
    from core.performance import total_return as module

    changed = json.loads(totals.path.read_text())
    changed["record_hash"] = "changed"
    totals.path.write_text(json.dumps(changed) + "\n")
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item, _, _, decision, total, _ = chain(tmp_path)
    result = pair(item, decision, total)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
