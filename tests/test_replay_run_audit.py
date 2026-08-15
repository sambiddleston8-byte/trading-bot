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
    ENGINE_POLICY_VERSION,
    BacktestResult,
    CompletedTrade,
    ExecutionRecord,
    PortfolioStateTrace,
    SizingDecisionTrace,
)
from core.orchestration import (
    ReplayRunAuditLedger,
    resolve_authenticated_execution_profile,
)
import core.orchestration.replay_run_audit as module


UTC = timezone.utc
START = datetime(2020, 1, 1, tzinfo=UTC)
END = START + timedelta(days=2)
PLAN_ID = "REPLAY-1"
PLAN_HASH = "8" * 64
DATASET_HASH = "9" * 64


def execution_policy():
    return {
        "replay_plan_id": PLAN_ID,
        "replay_plan_record_hash": PLAN_HASH,
        "replay_execution_policy_record_id": "REXP-1",
        "record_hash": "5" * 64,
        "execution_policy_id": "EXEC-1",
        "execution_policy_version": "execution-v1",
        "scenarios": {
            "BASE": {
                "commission_bps_per_side": "0",
                "spread_bps_per_side": "5",
                "slippage_bps_per_side": "10",
                "latency_bps_per_side": "1",
                "market_impact_bps_per_side": "10",
                "maximum_volume_participation_rate": "0.02",
                "maximum_order_age_minutes": "2000",
            },
            "PESSIMISTIC": {
                "commission_bps_per_side": "0",
                "spread_bps_per_side": "10",
                "slippage_bps_per_side": "20",
                "latency_bps_per_side": "2",
                "market_impact_bps_per_side": "20",
                "maximum_volume_participation_rate": "0.01",
                "maximum_order_age_minutes": "4000",
            },
        },
    }


class VerifiedLedger:
    def __init__(self, records):
        self.records = records

    def verify(self):
        return self.records


def strategy_specification(**changes):
    value = {
        "replay_plan_id": PLAN_ID,
        "replay_plan_record_hash": PLAN_HASH,
        "replay_plan_git_revision": "e" * 40,
        "replay_plan_evaluation_start": START.isoformat(timespec="microseconds"),
        "replay_plan_evaluation_end": END.isoformat(timespec="microseconds"),
        "strategy_specification_id": "RSTRAT-1",
        "record_hash": "7" * 64,
        "strategy_entrypoint": "strategies.causal:Strategy",
        "strategy_version": "strategy-v1",
        "strategy_source_sha256": "6" * 64,
        "parameter_hash": "a" * 64,
    }
    value.update(changes)
    return value


def result(**changes):
    scenario = changes.get("execution_scenario", "BASE")
    profile = resolve_authenticated_execution_profile(execution_policy(), scenario)
    quantity = Decimal("5")
    liquidity = Decimal("100000")
    buy_reference = Decimal("100")
    sell_reference = Decimal("111")

    def economics(reference):
        participation = quantity * reference / liquidity
        normalized = min(
            Decimal("1"),
            participation / profile.config.maximum_lagged_volume_participation,
        )
        impact = (
            profile.config.liquidity_impact_bps_at_max_participation
            * normalized * normalized
        )
        total = (
            profile.config.bid_ask_half_spread_bps
            + profile.config.baseline_slippage_bps
            + profile.config.latency_adverse_bps
            + impact
        )
        return impact, total

    buy_impact, buy_total = economics(buy_reference)
    sell_impact, sell_total = economics(sell_reference)
    buy_price = buy_reference * (Decimal("1") + buy_total / Decimal("10000"))
    sell_price = sell_reference * (Decimal("1") - sell_total / Decimal("10000"))
    entry_cost = quantity * buy_price
    exit_proceeds = quantity * sell_price
    buy_execution = ExecutionRecord(
        "AAA", "BUY", "STRATEGY_SIGNAL", START, START + timedelta(days=1),
        buy_reference, buy_price, quantity, quantity, Decimal("0"), "FILLED",
        liquidity, profile.config.bid_ask_half_spread_bps,
        profile.config.baseline_slippage_bps, profile.config.latency_adverse_bps,
        buy_impact, buy_total,
    )
    sell_execution = ExecutionRecord(
        "AAA", "SELL", "EVALUATION_END", START + timedelta(days=1), END,
        sell_reference, sell_price, quantity, quantity, Decimal("0"), "FILLED",
        liquidity, profile.config.bid_ask_half_spread_bps,
        profile.config.baseline_slippage_bps, profile.config.latency_adverse_bps,
        sell_impact, sell_total,
    )
    buy_sizing = SizingDecisionTrace(
        "AAA", "BUY", "STRATEGY_SIGNAL", START, START + timedelta(days=1),
        Decimal("100000"), Decimal("100000"), Decimal("0"), Decimal("0"),
        Decimal("0"), Decimal("10"), Decimal("50"), Decimal("5"),
        Decimal("100000"), Decimal("5"), Decimal("9"), Decimal("5"),
        Decimal("5"), ("RISK_BUDGET",), buy_price - Decimal("10"),
    )
    sell_sizing = SizingDecisionTrace(
        "AAA", "SELL", "EVALUATION_END", START + timedelta(days=1), END,
        Decimal("105000"), Decimal("99499"), exit_proceeds, Decimal("5"),
        Decimal("50"), None, None, None, Decimal("100000"), Decimal("10"),
        None, Decimal("5"), Decimal("5"), ("POSITION_QUANTITY",), None,
    )
    trade = CompletedTrade(
        "AAA", START + timedelta(days=1), END,
        entry_cost, exit_proceeds, exit_proceeds / entry_cost - Decimal("1"),
        "EVALUATION_END",
    )
    states = (
        PortfolioStateTrace(
            1, START + timedelta(days=1), "POST_SIMULATED_BUY", "AAA",
            Decimal("100000") - entry_cost, Decimal("0"), Decimal("100000"),
            Decimal("5"), buy_price, entry_cost, buy_price - Decimal("10"),
            buy_price,
        ),
        PortfolioStateTrace(
            2, END, "SESSION_CLOSE", "AAA", Decimal("105000"), Decimal("0"),
            Decimal("105000"), Decimal("0"), None, Decimal("0"), None,
            Decimal("110"),
        ),
    )
    values = {
        "strategy_version": "strategy-v1", "parameter_hash": "a" * 64,
        "source_id": "RDA-1", "validation_receipt_sha256": "b" * 64,
        "fee_schedule_id": profile.fee_schedule.schedule_id,
        "execution_scenario": scenario,
        "starting_equity": Decimal("100000"), "ending_equity": Decimal("105000"),
        "total_return": Decimal("0.05"), "maximum_drawdown": Decimal("0"),
        "executions": (buy_execution, sell_execution), "completed_trades": (trade,),
        "equity_curve": ((START + timedelta(days=1), Decimal("100000")),),
        "sizing_decisions": (buy_sizing, sell_sizing), "portfolio_states": states,
        "evaluation_start": START, "evaluation_end": END,
        "source_content_sha256": "c" * 64,
        "evidence_role_hashes": tuple(sorted(
            (role, (str(index + 1) * 64)[:64])
            for index, role in enumerate(sorted(AUTHENTICATED_REPLAY_ROLES))
        )),
        "engine_policy_version": ENGINE_POLICY_VERSION,
        "engine_config_sha256": profile.engine_config_sha256,
        "engine_config_canonical_json": profile.engine_config_canonical_json,
        "strategy_entrypoint": "strategies.causal:Strategy",
        "strategy_source_sha256": "6" * 64,
    }
    values.update(changes)
    return BacktestResult(**values)


@pytest.fixture(autouse=True)
def authenticated_parent(monkeypatch):
    def load(**kwargs):
        value = result()
        return SimpleNamespace(
            admission_id=value.source_id,
            replay_plan_id=PLAN_ID,
            replay_plan_record_hash=PLAN_HASH,
            dataset_commitment_sha256=DATASET_HASH,
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


def ledger(path, specifications=None, policies=None):
    return ReplayRunAuditLedger(
        path, admission_ledger=object(), content_ledger=object(),
        strategy_ledger=VerifiedLedger(
            [strategy_specification()] if specifications is None else specifications
        ),
        execution_policy_ledger=VerifiedLedger(
            [execution_policy()] if policies is None else policies
        ),
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
    assert record["replay_plan_id"] == PLAN_ID
    assert record["replay_plan_record_hash"] == PLAN_HASH
    assert record["dataset_commitment_sha256"] == DATASET_HASH
    assert record["replay_plan_git_revision"] == "e" * 40
    assert record["strategy_specification_id"] == "RSTRAT-1"
    assert record["replay_execution_policy_record_id"] == "REXP-1"
    assert record["execution_profile_version"]
    assert len(record["sizing_decisions"]) == 2
    assert len(record["portfolio_states"]) == 2
    assert record["portfolio_states"][-1]["equity"] == record["ending_equity"]
    for field in module.FIXED_FALSE:
        assert record[field] is False
    assert target.verify() == [record]


def test_records_valid_no_trade_run_without_fabricating_executions(tmp_path):
    flat_state = PortfolioStateTrace(
        1, END, "SESSION_CLOSE", "AAA", Decimal("100000"), Decimal("0"),
        Decimal("100000"), Decimal("0"), None, Decimal("0"), None, Decimal("100"),
    )
    no_trade = result(
        ending_equity=Decimal("100000"), total_return=Decimal("0"),
        maximum_drawdown=Decimal("0"), executions=(), completed_trades=(),
        equity_curve=((START + timedelta(days=1), Decimal("100000")),),
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


def test_verify_rejects_rehashed_git_revision_rebinding(tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    value = json.loads(target.path.read_text())
    value["git_revision"] = "f" * 40
    identity = target._identity(value)
    value["replay_run_id"] = "REPLAY-RUN-" + module.hashlib.sha256(
        module._canonical_json([identity, module.POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


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
    entry_cost = (
        base.executions[0].filled_quantity * base.executions[0].execution_price
        + base.executions[0].fee
    )
    proceeds = (
        Decimal("2") * Decimal("109") - Decimal("0.02")
        + Decimal("3") * base.executions[1].execution_price - Decimal("0.03")
    )
    completed = CompletedTrade(
        "AAA", START + timedelta(days=1), END,
        entry_cost, proceeds, proceeds / entry_cost - Decimal("1"), "EVALUATION_END",
    )
    module._match_completed_trades_and_exits(
        [json.loads(module._canonical_json(asdict(completed)))],
        [json.loads(module._canonical_json(item)) for item in (buy, sell_one, sell_two)],
    )
    tampered = replace(
        completed, entry_total_cost=entry_cost - Decimal("1"),
        return_rate=proceeds / (entry_cost - Decimal("1")) - Decimal("1"),
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
        ending_equity=Decimal("110000"),
        total_return=Decimal("0.1"),
        portfolio_states=(
            *original.portfolio_states[:-1],
            replace(
                original.portfolio_states[-1],
                settled_cash=Decimal("110000"),
                equity=Decimal("110000"),
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


def test_append_requires_exact_preregistered_strategy_and_parameters(tmp_path):
    with pytest.raises(ValueError, match="authenticated input evidence"):
        append(
            ledger(tmp_path / "strategy.jsonl"),
            result(strategy_version="strategy-v2"),
        )
    with pytest.raises(ValueError, match="authenticated input evidence"):
        append(
            ledger(tmp_path / "parameters.jsonl"),
            result(parameter_hash="f" * 64),
        )
    with pytest.raises(ValueError, match="authenticated input evidence"):
        append(
            ledger(tmp_path / "implementation.jsonl"),
            result(strategy_source_sha256="4" * 64),
        )
    with pytest.raises(ValueError, match="exactly one"):
        append(ledger(tmp_path / "missing.jsonl", specifications=[]))
    with pytest.raises(ValueError, match="execution policy"):
        append(ledger(tmp_path / "missing-policy.jsonl", policies=[]))
    with pytest.raises(ValueError, match="authenticated input evidence"):
        append(
            ledger(tmp_path / "window.jsonl"),
            result(evaluation_start=START + timedelta(hours=1)),
        )
    with pytest.raises(ValueError, match="Git revision"):
        append(ledger(tmp_path / "git.jsonl"), git_revision="f" * 40)


def test_verify_rechecks_strategy_specification_parent(tmp_path):
    specification = strategy_specification()
    target = ledger(tmp_path / "runs.jsonl", [specification])
    append(target)
    specification["record_hash"] = "5" * 64
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        target.verify()


def test_verify_rechecks_execution_policy_parent(tmp_path):
    policy_record = execution_policy()
    target = ledger(tmp_path / "runs.jsonl", policies=[policy_record])
    append(target)
    policy_record["record_hash"] = "4" * 64
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        target.verify()


def test_engine_configuration_must_exactly_match_preregistered_policy(tmp_path):
    base = result()
    engine = json.loads(base.engine_config_canonical_json)
    engine["config"]["maximum_order_age_minutes"] = "1999"
    canonical = module._canonical_json(engine)
    changed = replace(
        base,
        engine_config_canonical_json=canonical,
        engine_config_sha256=module.hashlib.sha256(canonical.encode()).hexdigest(),
    )
    with pytest.raises(ValueError, match="authenticated input evidence"):
        append(ledger(tmp_path / "runs.jsonl"), changed)


def test_base_and_pessimistic_pair_is_required_before_completion(tmp_path):
    target = ledger(tmp_path / "runs.jsonl")
    append(target)
    with pytest.raises(ValueError, match="incomplete until BASE and PESSIMISTIC"):
        target.require_paired_scenarios(
            replay_plan_id=PLAN_ID,
            strategy_specification_id="RSTRAT-1",
        )
    append(target, result(execution_scenario="PESSIMISTIC"))
    pair = target.require_paired_scenarios(
        replay_plan_id=PLAN_ID,
        strategy_specification_id="RSTRAT-1",
    )
    assert set(pair) == {"BASE", "PESSIMISTIC"}
    assert (
        pair["BASE"]["replay_execution_policy_record_hash"]
        == pair["PESSIMISTIC"]["replay_execution_policy_record_hash"]
    )
    assert pair["BASE"]["engine_config_sha256"] != pair["PESSIMISTIC"][
        "engine_config_sha256"
    ]


def test_terminal_settlement_cannot_claim_execution_costs():
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    terminal = dict(payload["executions"][0])
    terminal.update(
        action="TERMINAL_SETTLEMENT",
        execution_price=terminal["reference_price"],
        fee="0",
    )
    payload["executions"] = [terminal]
    with pytest.raises(ValueError, match="terminal settlement cannot carry"):
        module._validate_engine_execution_economics(payload, engine)


def test_position_fraction_cap_is_revalidated_against_execution_reference_price():
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    engine["config"]["maximum_position_fraction"] = "0.0001"
    for item in payload["sizing_decisions"]:
        module._validate_sizing(item, engine)

    with pytest.raises(ValueError, match="position-fraction cap"):
        module._match_executions_and_sizing(
            payload["executions"], payload["sizing_decisions"], engine
        )


def test_legacy_v2_engine_records_remain_interpretable_without_position_cap():
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    engine["engine_policy_version"] = "causal-single-instrument-guardrailed-backtest-v2"
    engine["config"].pop("maximum_position_fraction")
    payload["engine_policy_version"] = engine["engine_policy_version"]

    module._validate_engine_execution_economics(payload, engine)
    for item in payload["sizing_decisions"]:
        module._validate_sizing(item, engine)
    module._match_executions_and_sizing(
        payload["executions"], payload["sizing_decisions"], engine
    )


def test_unknown_engine_policy_version_fails_closed():
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    engine["engine_policy_version"] = "causal-single-instrument-guardrailed-backtest-v4"
    payload["engine_policy_version"] = engine["engine_policy_version"]

    with pytest.raises(ValueError, match="unsupported by replay audit"):
        module._validate_engine_execution_economics(payload, engine)


@pytest.mark.parametrize(
    ("constraint", "risk_per_share", "risk_budget"),
    [
        ("NO_POSITIVE_ATR_RISK_DISTANCE", "0", "0"),
        ("UNIVERSE_INELIGIBLE_AT_EXECUTION", "10", None),
    ],
)
def test_declared_rejected_buy_sizing_paths_remain_auditable(
    constraint, risk_per_share, risk_budget
):
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    trace = dict(payload["sizing_decisions"][0])
    trace.update(
        risk_per_share=risk_per_share,
        risk_budget=risk_budget,
        risk_quantity_limit="0",
        liquidity_quantity_limit="0",
        cash_quantity_limit="0",
        requested_quantity="0",
        filled_quantity="0",
        limiting_constraints=[constraint],
        stop_price_after=None,
    )

    module._validate_sizing(trace, engine)


@pytest.mark.parametrize(
    ("requested", "constraints", "message"),
    [
        ("6", ["RISK_BUDGET"], "exceeds its risk quantity"),
        ("4", ["RISK_BUDGET"], "lacks its position-fraction constraint"),
    ],
)
def test_v3_buy_request_cannot_bypass_risk_or_position_constraints(
    requested, constraints, message
):
    payload = module._result_payload(result())
    engine = json.loads(payload["engine_config_canonical_json"])
    trace = dict(payload["sizing_decisions"][0])
    trace.update(
        requested_quantity=requested,
        filled_quantity=requested,
        limiting_constraints=constraints,
    )

    with pytest.raises(ValueError, match=message):
        module._validate_sizing(trace, engine)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(total_return="9"),
        lambda value: value.update(maximum_drawdown="0.1"),
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
