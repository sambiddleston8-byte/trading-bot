from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import DownsideTargetPolicyLedger


MODELS = [{"component": "portfolio", "version": "1.0"}]


def ledger(tmp_path):
    return DownsideTargetPolicyLedger(tmp_path / "downside_policy.jsonl")


def preregister(item, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "target_basis": "MATCHED_DAILY_SOFR",
        "evaluation_not_before": "2025-02-01T00:10:00+00:00",
        "recorded_at": "2025-02-01T00:00:00+00:00",
        "decided_by": "Sam Biddleston",
        "decision_reference": "user-confirmed-sortino-policy",
        "human_decision_confirmed": True,
        "strategy_version": "strategy-v1",
        "model_versions": MODELS,
        "git_revision": "abc123",
    }
    values.update(overrides)
    return item.preregister(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import downside_target_policy as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_preregisters_matched_sofr_policy_without_calculating_sortino(tmp_path):
    item = ledger(tmp_path)
    result = preregister(item)
    assert result["status"] == "PREREGISTERED"
    assert result["record_type"] == "HUMAN_APPROVED_SORTINO_DOWNSIDE_TARGET_POLICY"
    assert result["target_basis"] == "MATCHED_DAILY_SOFR"
    assert result["minimum_acceptable_return_formula"] == "matched_daily_risk_free_return"
    assert result["risk_free_pairing_required"] is True
    assert result["minimum_total_observations"] == 252
    assert result["minimum_downside_observations"] == 30
    assert result["immutable_before_observation"] is True
    assert result["retrospective_application_allowed"] is False
    assert result["sortino_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


def test_preregisters_zero_return_policy_with_exact_target(tmp_path):
    item = ledger(tmp_path)
    result = preregister(item, target_basis="ZERO_DAILY_RETURN")
    assert result["minimum_acceptable_return_formula"] == "0"
    assert result["fixed_daily_target"] == {
        "decimal": "0",
        "exact_fraction": {"numerator": "0", "denominator": "1"},
    }
    assert result["risk_free_pairing_required"] is False


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"human_decision_confirmed": False}, "explicit human decision"),
        ({"target_basis": "CUSTOM"}, "must be"),
        ({"decided_by": ""}, "decided_by"),
        ({"decision_reference": ""}, "decision_reference"),
        ({"portfolio_version": ""}, "portfolio_version"),
        ({"strategy_version": ""}, "strategy_version"),
        ({"git_revision": ""}, "git_revision"),
        ({"model_versions": []}, "model_versions"),
        (
            {"model_versions": [{"component": "", "version": "1"}]},
            "model_versions",
        ),
        (
            {"evaluation_not_before": "2025-02-01T00:04:59+00:00"},
            "at least five minutes",
        ),
        ({"recorded_at": "2099-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_or_unapproved_policy_is_rejected(tmp_path, overrides, fragment):
    item = ledger(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        preregister(item, **overrides)
    assert item.records() == []


def test_conflicting_target_for_same_window_is_rejected(tmp_path):
    item = ledger(tmp_path)
    preregister(item)
    with pytest.raises(LedgerIntegrityError, match="different downside target"):
        preregister(item, target_basis="ZERO_DAILY_RETURN")


def test_distinct_future_windows_may_have_distinct_preregistered_policies(tmp_path):
    item = ledger(tmp_path)
    first = preregister(item)
    second = preregister(
        item,
        target_basis="ZERO_DAILY_RETURN",
        evaluation_not_before="2025-03-01T00:10:00+00:00",
    )
    assert item.verify() == [first, second]


def test_identical_concurrent_retries_append_once(tmp_path):
    item = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: preregister(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"target_basis": "ZERO_DAILY_RETURN"},
        {"evaluation_not_before": "2024-01-01T00:00:00+00:00"},
        {"human_decision_confirmed": False},
        {"minimum_total_observations": 1},
        {"minimum_downside_observations": 1},
        {"downside_definition": "changed"},
        {"downside_deviation_formula": "changed"},
        {"immutable_before_observation": False},
        {"retrospective_application_allowed": True},
        {"sortino_calculated": True},
        {"performance_metric_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item = ledger(tmp_path)
    preregister(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item = ledger(tmp_path)
    result = preregister(item)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
