from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

import core.orchestration.replay_strategy_specification as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.guardrailed_backtest import strategy_parameter_hash
from core.orchestration import ReplayStrategySpecificationLedger


UTC = timezone.utc


class VerifiedLedger:
    def __init__(self, records):
        self.records = records

    def verify(self):
        return self.records


def plan(access_at=None):
    return {
        "replay_plan_id": "REPLAY-1",
        "record_hash": "a" * 64,
        "git_revision": "e" * 40,
        "evaluation_not_before": "2020-01-01T00:00:00+00:00",
        "evaluation_not_after": "2020-04-01T00:00:00+00:00",
        "evaluation_data_access_not_before": (
            access_at or datetime.now(UTC) + timedelta(days=1)
        ).isoformat(),
    }


def ledger(tmp_path, source_plan=None):
    return ReplayStrategySpecificationLedger(
        tmp_path / "replay-strategy.jsonl",
        plan_ledger=VerifiedLedger([source_plan or plan()]),
    )


def preregister(target, **changes):
    values = {
        "replay_plan_id": "REPLAY-1",
        "strategy_entrypoint": "strategies.causal:RoadmapStrategy",
        "strategy_version": "roadmap-strategy-v1",
        "strategy_source_sha256": "b" * 64,
        "parameters": {
            "enter_on_history_count": 20,
            "exit_threshold": Decimal("1.5"),
        },
        "registered_by": "pytest",
    }
    values.update(changes)
    return target.preregister(**values)


def test_preregisters_one_exact_strategy_before_data_access(tmp_path):
    target = ledger(tmp_path)
    record = preregister(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["replay_plan_record_hash"] == "a" * 64
    assert record["replay_plan_git_revision"] == "e" * 40
    assert record["replay_plan_evaluation_start"].startswith("2020-01-01")
    assert record["replay_plan_evaluation_end"].startswith("2020-04-01")
    assert record["parameter_hash"] == strategy_parameter_hash({
        "enter_on_history_count": 20,
        "exit_threshold": Decimal("1.5"),
    })
    assert json.loads(record["parameters_canonical_json"]) == {
        "enter_on_history_count": 20,
        "exit_threshold": "1.5",
    }
    assert record["single_strategy_for_plan"] is True
    assert record["simulation_only"] is True
    for field in (
        "evaluation_dataset_opened", "strategy_search_allowed",
        "parameter_search_allowed", "post_access_mutation_allowed",
        "replay_executed", "performance_calculated", "performance_claim_allowed",
        "model_trained", "learning_applied", "network_allowed",
        "paper_broker_submission_enabled", "broker_connection_allowed",
        "orders_submitted", "live_trading_enabled",
    ):
        assert record[field] is False
    assert target.verify() == [record]


def test_identical_retry_is_idempotent_and_concurrent(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: preregister(target), range(2)))
    assert first == second
    assert preregister(target) == first
    assert len(target.verify()) == 1


def test_plan_cannot_receive_a_second_strategy_or_parameter_set(tmp_path):
    target = ledger(tmp_path)
    preregister(target)
    with pytest.raises(LedgerIntegrityError, match="single strategy"):
        preregister(target, strategy_version="roadmap-strategy-v2")
    with pytest.raises(LedgerIntegrityError, match="single strategy"):
        preregister(target, parameters={"enter_on_history_count": 21})


def test_strategy_cannot_be_registered_at_or_after_data_access(tmp_path):
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="before sealed"):
        preregister(ledger(tmp_path, plan(now)), registered_at=now)


def test_unknown_plan_backdating_and_invalid_parameters_fail_closed(tmp_path):
    unknown = ReplayStrategySpecificationLedger(
        tmp_path / "unknown.jsonl", plan_ledger=VerifiedLedger([])
    )
    with pytest.raises(ValueError, match="verified replay plan"):
        preregister(unknown)
    with pytest.raises(ValueError, match="actual append time"):
        preregister(
            ledger(tmp_path),
            registered_at=datetime.now(UTC) - timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="finite"):
        preregister(ledger(tmp_path / "nan"), parameters={"threshold": float("nan")})
    with pytest.raises(ValueError, match="keys"):
        preregister(ledger(tmp_path / "keys"), parameters={1: "not-string"})


def test_verify_rechecks_plan_and_detects_semantic_tampering(tmp_path):
    source_plan = plan()
    plans = VerifiedLedger([source_plan])
    target = ReplayStrategySpecificationLedger(
        tmp_path / "replay-strategy.jsonl", plan_ledger=plans
    )
    preregister(target)
    source_plan["record_hash"] = "c" * 64
    with pytest.raises(LedgerIntegrityError):
        target.verify()

    source_plan["record_hash"] = "a" * 64
    value = json.loads(target.path.read_text())
    value["parameter_hash"] = "d" * 64
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_incomplete_tail_and_unknown_fields_fail_closed(tmp_path):
    target = ledger(tmp_path)
    preregister(target)
    target.path.write_text(target.path.read_text().rstrip("\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.verify()

    target = ledger(tmp_path / "unknown")
    preregister(target)
    value = json.loads(target.path.read_text())
    value["unexpected"] = True
    target.path.write_text(json.dumps(value) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()
