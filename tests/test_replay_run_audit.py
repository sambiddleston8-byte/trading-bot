from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.guardrailed_backtest import (
    AUTHENTICATED_REPLAY_ROLES,
    BacktestResult,
    CompletedTrade,
    ExecutionRecord,
    PortfolioStateTrace,
    SizingDecisionTrace,
)
from core.orchestration import ReplayRunAuditLedger
import core.orchestration.replay_run_audit as module


UTC = timezone.utc
START = datetime(2020, 1, 1, tzinfo=UTC)
END = START + timedelta(days=2)


def result(**changes):
    buy_execution = ExecutionRecord(
        "AAA", "BUY", "STRATEGY_SIGNAL", START, START + timedelta(days=1),
        Decimal("100"), Decimal("101"), Decimal("5"), Decimal("5"),
        Decimal("0.05"), "FILLED", Decimal("100000"), Decimal("5"),
        Decimal("10"), Decimal("85"), Decimal("100"),
    )
    sell_execution = ExecutionRecord(
        "AAA", "SELL", "EVALUATION_END", START + timedelta(days=1), END,
        Decimal("111"), Decimal("109.89"), Decimal("5"), Decimal("5"),
        Decimal("0.05"), "FILLED", Decimal("100000"), Decimal("5"),
        Decimal("10"), Decimal("85"), Decimal("100"),
    )
    buy_sizing = SizingDecisionTrace(
        "AAA", "BUY", "STRATEGY_SIGNAL", START, START + timedelta(days=1),
        Decimal("1000"), Decimal("1000"), Decimal("0"), Decimal("0"),
        Decimal("0"), Decimal("10"), Decimal("50"), Decimal("5"),
        Decimal("100000"), Decimal("5"), Decimal("9"), Decimal("5"),
        Decimal("5"), ("RISK_BUDGET",), Decimal("91"),
    )
    sell_sizing = SizingDecisionTrace(
        "AAA", "SELL", "EVALUATION_END", START + timedelta(days=1), END,
        Decimal("1050"), Decimal("500.6"), Decimal("549.4"), Decimal("5"),
        Decimal("50"), None, None, None, Decimal("100000"), Decimal("10"),
        None, Decimal("5"), Decimal("5"), ("POSITION_QUANTITY",), None,
    )
    trade = CompletedTrade(
        "AAA", START + timedelta(days=1), END,
        Decimal("505.05"), Decimal("549.4"),
        Decimal("549.4") / Decimal("505.05") - Decimal("1"),
        "EVALUATION_END",
    )
    states = (
        PortfolioStateTrace(
            1, START + timedelta(days=1), "POST_SIMULATED_BUY", "AAA",
            Decimal("494.95"), Decimal("0"), Decimal("999.95"), Decimal("5"),
            Decimal("101"), Decimal("505.05"), Decimal("91"), Decimal("101"),
        ),
        PortfolioStateTrace(
            2, END, "SESSION_CLOSE", "AAA", Decimal("1050"), Decimal("0"),
            Decimal("1050"), Decimal("0"), None, Decimal("0"), None, Decimal("110"),
        ),
    )
    values = {
        "strategy_version": "strategy-v1", "parameter_hash": "a" * 64,
        "source_id": "RDA-1", "validation_receipt_sha256": "b" * 64,
        "fee_schedule_id": "fees-v1", "execution_scenario": "BASE",
        "starting_equity": Decimal("1000"), "ending_equity": Decimal("1050"),
        "total_return": Decimal("0.05"), "maximum_drawdown": Decimal("0.00005"),
        "executions": (buy_execution, sell_execution), "completed_trades": (trade,),
        "equity_curve": ((START + timedelta(days=1), Decimal("999.95")),),
        "sizing_decisions": (buy_sizing, sell_sizing), "portfolio_states": states,
        "evaluation_start": START, "evaluation_end": END,
        "source_content_sha256": "c" * 64,
        "evidence_role_hashes": tuple(sorted(
            (role, (str(index + 1) * 64)[:64])
            for index, role in enumerate(sorted(AUTHENTICATED_REPLAY_ROLES))
        )),
        "engine_policy_version": "engine-v1", "engine_config_sha256": "d" * 64,
    }
    values.update(changes)
    return BacktestResult(**values)


@pytest.fixture(autouse=True)
def authenticated_parent(monkeypatch):
    def load(**kwargs):
        value = result()
        return SimpleNamespace(
            admission_id=value.source_id,
            data_attestation=SimpleNamespace(
                source_content_sha256=value.source_content_sha256,
                validation_receipt_sha256=value.validation_receipt_sha256,
                evidence_role_hashes=value.evidence_role_hashes,
            ),
            broker_connection_allowed=False,
            orders_submitted=False,
            live_trading_enabled=False,
        )
    monkeypatch.setattr(module, "load_authenticated_backtest_inputs", load)


def ledger(path):
    return ReplayRunAuditLedger(
        path, admission_ledger=object(), content_ledger=object()
    )


def append(target, value=None, **changes):
    return target.append(
        result=value or result(), git_revision=changes.pop("git_revision", "e" * 40),
        recorded_by="pytest",
        recorded_at=datetime.now(UTC), **changes,
    )


def rewrite(path, **changes):
    value = json.loads(path.read_text())
    value.update(changes)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n")


def test_records_complete_simulation_audit_without_track_record_or_trading_authority(tmp_path):
    target = ledger(tmp_path / "replay-runs.jsonl")
    record = append(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["status"] == "MECHANICAL_SIMULATION_RECORDED_NOT_A_TRACK_RECORD"
    assert len(record["sizing_decisions"]) == 2
    assert len(record["portfolio_states"]) == 2
    assert record["portfolio_states"][-1]["equity"] == record["ending_equity"]
    for field in module.FIXED_FALSE:
        assert record[field] is False
    assert target.verify() == [record]


def test_records_valid_no_trade_run_without_fabricating_executions(tmp_path):
    flat_state = PortfolioStateTrace(
        1, END, "SESSION_CLOSE", "AAA", Decimal("1000"), Decimal("0"),
        Decimal("1000"), Decimal("0"), None, Decimal("0"), None, Decimal("100"),
    )
    no_trade = result(
        ending_equity=Decimal("1000"), total_return=Decimal("0"),
        maximum_drawdown=Decimal("0"), executions=(), completed_trades=(),
        equity_curve=((START + timedelta(days=1), Decimal("1000")),),
        sizing_decisions=(), portfolio_states=(flat_state,),
    )
    target = ledger(tmp_path / "replay-runs.jsonl")
    record = append(target, no_trade)
    assert record["executions"] == []
    assert record["completed_trades"] == []
    assert target.verify() == [record]


def test_identical_retry_is_idempotent_despite_a_new_recording_timestamp(tmp_path):
    target = ledger(tmp_path / "replay-runs.jsonl")
    first = append(target)
    second = append(target)
    assert second == first
    assert len(target.verify()) == 1


def test_concurrent_identical_append_creates_one_run(tmp_path):
    target = ledger(tmp_path / "replay-runs.jsonl")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: append(target), range(2)))
    assert first == second
    assert len(target.verify()) == 1


@pytest.mark.parametrize(
    "change,fragment",
    [
        ({"sizing_decisions": ()}, "lacks complete"),
        ({"portfolio_states": ()}, "lacks complete"),
        ({"executions": ()}, "lacks complete"),
        ({"evaluation_start": None}, "evaluation window"),
        ({"live_trading_enabled": True}, "authority boundary"),
        ({"engine_config_sha256": "bad"}, "SHA-256"),
    ],
)
def test_incomplete_or_over_authorized_results_fail_closed(tmp_path, change, fragment):
    with pytest.raises(ValueError, match=fragment):
        append(ledger(tmp_path / "runs.jsonl"), result(**change))


def test_git_revision_accepts_real_commit_ids_and_rejects_other_lengths(tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    assert append(target)["git_revision"] == "e" * 40
    with pytest.raises(ValueError, match="commit ID"):
        append(ledger(tmp_path / "other.jsonl"), git_revision="e" * 41)


def test_partial_exits_reconcile_as_one_complete_round_trip():
    base = result()
    buy = asdict(base.executions[0])
    sell_one = asdict(replace(
        base.executions[1], reason="PARTIAL_EXIT",
        executed_at=END - timedelta(hours=1), requested_quantity=Decimal("5"),
        filled_quantity=Decimal("2"), execution_price=Decimal("109"),
        fee=Decimal("0.02"), status="PARTIALLY_FILLED",
    ))
    sell_two = asdict(replace(
        base.executions[1], requested_quantity=Decimal("3"),
        filled_quantity=Decimal("3"), fee=Decimal("0.03"),
    ))
    completed = CompletedTrade(
        "AAA", START + timedelta(days=1), END,
        Decimal("505.05"), Decimal("547.62"),
        Decimal("547.62") / Decimal("505.05") - Decimal("1"), "EVALUATION_END",
    )
    module._match_completed_trades_and_exits(
        [json.loads(module._canonical_json(asdict(completed)))],
        [json.loads(module._canonical_json(item)) for item in (buy, sell_one, sell_two)],
    )
    tampered = replace(
        completed, entry_total_cost=Decimal("500"),
        return_rate=Decimal("547.62") / Decimal("500") - Decimal("1"),
    )
    with pytest.raises(ValueError, match="filled entry"):
        module._match_completed_trades_and_exits(
            [json.loads(module._canonical_json(asdict(tampered)))],
            [json.loads(module._canonical_json(item)) for item in (buy, sell_one, sell_two)],
        )


def test_same_run_identity_cannot_receive_different_content(tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    original = result()
    changed = replace(
        original,
        ending_equity=Decimal("1100"),
        total_return=Decimal("0.1"),
        portfolio_states=(
            *original.portfolio_states[:-1],
            replace(
                original.portfolio_states[-1],
                settled_cash=Decimal("1100"),
                equity=Decimal("1100"),
            ),
        ),
    )
    with pytest.raises(LedgerIntegrityError, match="different audit content"):
        append(target, changed)


def test_append_and_verify_revalidate_authenticated_parent(monkeypatch, tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    original = module.load_authenticated_backtest_inputs

    def changed(**kwargs):
        parent = original(**kwargs)
        parent.data_attestation.validation_receipt_sha256 = "f" * 64
        return parent

    monkeypatch.setattr(module, "load_authenticated_backtest_inputs", changed)
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        target.verify()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(total_return="9"),
        lambda value: value.update(maximum_drawdown="0"),
        lambda value: value["executions"][0].update(filled_quantity="4"),
        lambda value: value["sizing_decisions"][0].update(filled_quantity="4"),
        lambda value: value["completed_trades"][0].update(
            entry_total_cost="500",
            return_rate=module._canonical_decimal(
                Decimal(value["completed_trades"][0]["exit_net_proceeds"])
                / Decimal("500")
                - Decimal("1")
            ),
        ),
        lambda value: value["portfolio_states"][-1].update(equity="999"),
        lambda value: value["portfolio_states"][0].update(sequence=2),
        lambda value: value.update(live_trading_enabled=True),
    ],
)
def test_rehashed_semantic_tampering_fails_verification(tmp_path, mutator):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    value = json.loads(target.path.read_text())
    mutator(value)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_unknown_fields_and_incomplete_tail_fail_closed(tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    rewrite(target.path, unexpected=True)
    with pytest.raises(LedgerIntegrityError):
        target.verify()
    target.path.write_text('{"partial":')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.verify()
