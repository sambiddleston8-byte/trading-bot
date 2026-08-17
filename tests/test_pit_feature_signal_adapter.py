from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from core.features.pit_feature_contract import DEFINITION_SHA256, FAMILY
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ResearchExemptionDataAttestation,
    UniverseEvent,
)
from core.research.pit_feature_signal_adapter import (
    DeterministicSignalAdapter,
    MarketBreadthSignalAdapter,
    MomentumConfirmedSignalAdapter,
    PITFeatureConsumer,
    deterministic_signal_parameters,
    market_breadth_signal_parameters,
    momentum_confirmed_signal_parameters,
)
from core.research.stage3_feature_strategy_evaluation import _spy_buy_hold_total_return


UTC = timezone.utc
START = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def feature_row(symbol, effective, *, bullish=True, atr="2", role="VALIDATION"):
    available = effective + timedelta(minutes=1)
    material = {
        "feature_id": "PITF-" + hashlib.sha256(f"{FAMILY}:{symbol}:{effective.isoformat()}".encode()).hexdigest()[:32].upper(),
        "feature_family": FAMILY,
        "feature_definition_sha256": DEFINITION_SHA256,
        "entity_id": symbol,
        "partition_role": role,
        "effective_at": effective.isoformat(),
        "reported_at": effective.isoformat(),
        "available_at": available.isoformat(),
        "retrieved_at": "2026-08-16T20:00:00+00:00",
        "observation_cutoff_at": (available + timedelta(minutes=4)).isoformat(),
        "revision": 1,
        "prior_revision_sha256": None,
        "values": {
            "sma_20": "110" if bullish else "90",
            "sma_50": "100",
            "momentum_20": "0.1" if bullish else "-0.1",
            "atr_14": atr,
        },
        "provenance": {
            "source_artifact_sha256": {role: "a" * 64},
            "input_rows": [{
                "row_id": f"{symbol}:{effective.date().isoformat()}",
                "session_date": effective.date().isoformat(),
                "available_at": available.isoformat(),
                "row_sha256": "b" * 64,
                "source_payload_sha256": "c" * 64,
            }],
            "derivation": "SYNTHETIC_TEST",
        },
    }
    return {**material, "record_sha256": hashlib.sha256(canonical(material)).hexdigest()}


def matrix(effective_times, *, suppress=(), atr="2"):
    rows = []
    for effective, bullish in effective_times:
        rows.extend(feature_row(symbol, effective, bullish=bullish, atr=atr) for symbol in ("AAPL", "MSFT", "SPY"))
    value = {
        "schema_version": "1.0",
        "feature_family": FAMILY,
        "feature_definition_sha256": DEFINITION_SHA256,
        "partition_role": "VALIDATION",
        "qualification_report_artifact_sha256": "d" * 64,
        "source_artifact_sha256": "e" * 64,
        "rows": rows,
        "admitted": True,
        "untouched_test_included": False,
    }
    value["matrix_sha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return value, PITFeatureConsumer(value, expected_matrix_sha256=value["matrix_sha256"], suppressed_decision_ats=suppress)


def momentum_matrix(effective_momentums, *, suppress=()):
    rows = []
    for item in effective_momentums:
        effective, momentum = item[:2]
        bullish = item[2] if len(item) == 3 else True
        for symbol in ("AAPL", "MSFT", "SPY"):
            row = feature_row(symbol, effective, bullish=bullish)
            material = {key: value for key, value in row.items() if key != "record_sha256"}
            material["values"] = {**material["values"], "momentum_20": momentum}
            rows.append(
                {
                    **material,
                    "record_sha256": hashlib.sha256(canonical(material)).hexdigest(),
                }
            )
    value = {
        "schema_version": "1.0",
        "feature_family": FAMILY,
        "feature_definition_sha256": DEFINITION_SHA256,
        "partition_role": "VALIDATION",
        "qualification_report_artifact_sha256": "d" * 64,
        "source_artifact_sha256": "e" * 64,
        "rows": rows,
        "admitted": True,
        "untouched_test_included": False,
    }
    value["matrix_sha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return value, PITFeatureConsumer(
        value,
        expected_matrix_sha256=value["matrix_sha256"],
        suppressed_decision_ats=suppress,
    )


def breadth_matrix(effective, states, *, delayed_symbol=None):
    rows = [
        feature_row(symbol, effective, bullish=states[symbol], role="TRAIN")
        for symbol in ("AAPL", "MSFT", "SPY")
    ]
    if delayed_symbol is not None:
        delayed = next(row for row in rows if row["entity_id"] == delayed_symbol)
        delayed_at = (effective + timedelta(minutes=2)).isoformat()
        delayed["available_at"] = delayed_at
        delayed["provenance"]["input_rows"][0]["available_at"] = delayed_at
        material = {
            key: value for key, value in delayed.items() if key != "record_sha256"
        }
        delayed["record_sha256"] = hashlib.sha256(canonical(material)).hexdigest()
    value = {
        "schema_version": "1.0",
        "feature_family": FAMILY,
        "feature_definition_sha256": DEFINITION_SHA256,
        "partition_role": "TRAIN",
        "qualification_report_artifact_sha256": "d" * 64,
        "source_artifact_sha256": "e" * 64,
        "rows": rows,
        "admitted": True,
        "untouched_test_included": False,
    }
    value["matrix_sha256"] = hashlib.sha256(canonical(value)).hexdigest()
    return PITFeatureConsumer(
        value, expected_matrix_sha256=value["matrix_sha256"]
    )


def bars(count=23):
    rows = []
    for index in range(count):
        opened = START + timedelta(days=index)
        close = Decimal(100 + index)
        rows.append(MarketBar("AAPL", opened, opened + timedelta(hours=6, minutes=30), opened + timedelta(hours=6, minutes=31), close, close + 1, close - 1, close, Decimal("1000000")))
    return rows


def attestation():
    return ResearchExemptionDataAttestation._from_explicit_research_exemption(
        source_id="RESEARCH_EXEMPTION:SYNTHETIC:PIT_ADAPTER",
        source_content_sha256="1" * 64,
        validation_receipt_sha256="2" * 64,
        derivation_policy_version="synthetic-pit-adapter-test-v1",
        evidence_role_hashes=(("ASSUMED_FEATURE_MATRIX", "3" * 64),),
        exemption_id="TEST-EXEMPTION",
        exemption_record_sha256="4" * 64,
    )


def test_consumer_enforces_matrix_hash_alignment_as_of_and_embargo():
    effective = START + timedelta(hours=6, minutes=30)
    value, consumer = matrix(((effective, True),))
    with pytest.raises(ValueError, match="available_at exceeds"):
        consumer.consume("AAPL", effective_at=effective, decision_at=effective)
    assert consumer.consume("AAPL", effective_at=effective, decision_at=effective + timedelta(minutes=1)).values["atr_14"] == "2"
    with pytest.raises(ValueError, match="missing at decision"):
        consumer.consume("AAPL", effective_at=effective + timedelta(days=1), decision_at=effective + timedelta(days=1, minutes=1))
    _, embargoed = matrix(((effective, True),), suppress=(effective,))
    assert embargoed.consume("AAPL", effective_at=effective, decision_at=effective + timedelta(minutes=1)) is None
    tampered = dict(value);tampered["admitted"] = False
    with pytest.raises(ValueError, match="SHA-256"):PITFeatureConsumer(tampered, expected_matrix_sha256=value["matrix_sha256"])


def test_adapter_drives_engine_signal_and_uses_admitted_atr_for_sizing():
    market = bars()
    first = market[19].close_at
    second = market[20].close_at
    _, consumer = matrix(((first, True), (second, False)))
    strategy = DeterministicSignalAdapter(consumer, liquidation_signal_at=market[21].close_at)
    assert strategy.decide("AAPL", market[:20], deterministic_signal_parameters()) == ACTION_ENTER_LONG
    assert strategy.decide("AAPL", market[:21], deterministic_signal_parameters()) == ACTION_EXIT_LONG
    result = GuardrailedBacktestEngine(
        config=BacktestConfig(initial_cash=Decimal("100000")),
        fee_schedule=ExchangeFeeSchedule("TEST-FEES", (ExchangeFeeTier(None, Decimal("1")),)),
        data_attestation=attestation(),
    ).run(
        bars=market,
        universe_events=(UniverseEvent("AAPL", "ADD", market[0].open_at, market[0].open_at, "synthetic"),),
        terminal_outcomes=(),corporate_actions=(),prices_are_unadjusted=True,
        strategy=strategy,parameters=deterministic_signal_parameters(),
        evaluation_start=market[0].open_at,evaluation_end=market[21].close_at,
    )
    buy = next(trace for trace in result.sizing_decisions if trace.action == "BUY")
    assert buy.risk_per_share == Decimal("4")
    assert len(result.completed_trades) == 1


@pytest.mark.parametrize(
    "states,expected",
    (
        (
            {"AAPL": True, "MSFT": False, "SPY": True},
            ACTION_ENTER_LONG,
        ),
        (
            {"AAPL": True, "MSFT": True, "SPY": True},
            ACTION_ENTER_LONG,
        ),
        (
            {"AAPL": True, "MSFT": False, "SPY": False},
            ACTION_EXIT_LONG,
        ),
        (
            {"AAPL": False, "MSFT": True, "SPY": True},
            ACTION_EXIT_LONG,
        ),
    ),
)
def test_market_breadth_requires_own_signal_and_exact_pit_majority(states, expected):
    market = bars(22)
    effective = market[19].close_at
    strategy = MarketBreadthSignalAdapter(
        breadth_matrix(effective, states),
        liquidation_signal_at=market[20].close_at,
    )
    parameters = market_breadth_signal_parameters()
    assert strategy.decide("AAPL", market[:20], parameters) == expected
    with pytest.raises(ValueError, match="fixed market breadth policy"):
        strategy.decide("AAPL", market[:20], deterministic_signal_parameters())


def test_market_breadth_fails_closed_when_peer_vintage_is_delayed():
    market = bars(22)
    effective = market[19].close_at
    strategy = MarketBreadthSignalAdapter(
        breadth_matrix(
            effective,
            {"AAPL": True, "MSFT": True, "SPY": True},
            delayed_symbol="MSFT",
        ),
        liquidation_signal_at=market[20].close_at,
    )
    assert (
        strategy.decide(
            "AAPL", market[:20], market_breadth_signal_parameters()
        )
        == ACTION_EXIT_LONG
    )


def test_market_breadth_rejects_symbols_outside_fixed_basket():
    market = bars(22)
    market[-1] = MarketBar(
        "QQQ",
        market[-1].open_at,
        market[-1].close_at,
        market[-1].available_at,
        market[-1].open,
        market[-1].high,
        market[-1].low,
        market[-1].close,
        market[-1].volume,
    )
    effective = market[19].close_at
    strategy = MarketBreadthSignalAdapter(
        breadth_matrix(
            effective, {"AAPL": True, "MSFT": True, "SPY": True}
        ),
        liquidation_signal_at=market[20].close_at,
    )
    with pytest.raises(ValueError, match="outside the fixed breadth basket"):
        strategy.decide(
            "QQQ", market, market_breadth_signal_parameters()
        )


def test_market_breadth_liquidation_precedes_feature_consumption():
    market = bars(22)
    effective = market[20].close_at
    strategy = MarketBreadthSignalAdapter(
        breadth_matrix(
            effective, {"AAPL": True, "MSFT": True, "SPY": True}
        ),
        liquidation_signal_at=market[20].close_at,
    )
    assert (
        strategy.decide(
            "AAPL", market[:21], market_breadth_signal_parameters()
        )
        == ACTION_EXIT_LONG
    )


def test_momentum_confirmation_requires_rising_prior_pit_session_and_trades():
    market = bars(25)
    first, second, third, fourth = (
        market[index].close_at for index in (19, 20, 21, 22)
    )
    _, consumer = momentum_matrix(
        (
            (first, "0.05"),
            (second, "0.04"),
            (third, "0.06"),
            (fourth, "0.07"),
        )
    )
    strategy = MomentumConfirmedSignalAdapter(
        consumer, liquidation_signal_at=market[22].close_at
    )
    parameters = momentum_confirmed_signal_parameters()
    assert strategy.decide("AAPL", market[:20], parameters) != ACTION_ENTER_LONG
    assert strategy.decide("AAPL", market[:21], parameters) != ACTION_ENTER_LONG
    assert strategy.decide("AAPL", market[:22], parameters) == ACTION_ENTER_LONG
    with pytest.raises(ValueError, match="fixed confirmed signal policy"):
        strategy.decide("AAPL", market[:22], deterministic_signal_parameters())

    result = GuardrailedBacktestEngine(
        config=BacktestConfig(initial_cash=Decimal("100000")),
        fee_schedule=ExchangeFeeSchedule(
            "TEST-FEES", (ExchangeFeeTier(None, Decimal("1")),)
        ),
        data_attestation=attestation(),
    ).run(
        bars=market,
        universe_events=(
            UniverseEvent(
                "AAPL", "ADD", market[0].open_at, market[0].open_at, "synthetic"
            ),
        ),
        terminal_outcomes=(),
        corporate_actions=(),
        prices_are_unadjusted=True,
        strategy=strategy,
        parameters=parameters,
        evaluation_start=market[0].open_at,
        evaluation_end=market[23].close_at,
    )
    assert len(result.completed_trades) == 1
    buy_execution = next(
        execution for execution in result.executions if execution.action == "BUY"
    )
    assert buy_execution.signal_at == third + timedelta(minutes=1)
    assert buy_execution.executed_at == market[22].open_at
    assert buy_execution.reference_price == market[22].open
    buy_sizing = next(
        trace for trace in result.sizing_decisions if trace.action == "BUY"
    )
    assert buy_sizing.risk_per_share == Decimal("4")
    assert buy_sizing.stop_price_after == buy_execution.execution_price - Decimal("4")

    _, embargoed = momentum_matrix(
        (
            (first, "0.05"),
            (second, "0.04"),
            (third, "0.06"),
            (fourth, "0.07"),
        ),
        suppress=(second,),
    )
    blocked = MomentumConfirmedSignalAdapter(
        embargoed, liquidation_signal_at=market[22].close_at
    )
    assert blocked.decide("AAPL", market[:22], parameters) != ACTION_ENTER_LONG

    delayed_value, _ = momentum_matrix(
        (
            (first, "0.05"),
            (second, "0.04"),
            (third, "0.06"),
            (fourth, "0.07"),
        )
    )
    delayed = json.loads(json.dumps(delayed_value))
    delayed_row = next(
        row
        for row in delayed["rows"]
        if row["entity_id"] == "AAPL"
        and datetime.fromisoformat(row["effective_at"]) == second
    )
    delayed_at = (second + timedelta(minutes=2)).isoformat()
    delayed_row["available_at"] = delayed_at
    delayed_row["provenance"]["input_rows"][0]["available_at"] = delayed_at
    delayed_material = {
        key: value for key, value in delayed_row.items() if key != "record_sha256"
    }
    delayed_row["record_sha256"] = hashlib.sha256(
        canonical(delayed_material)
    ).hexdigest()
    delayed_material = {
        key: value for key, value in delayed.items() if key != "matrix_sha256"
    }
    delayed["matrix_sha256"] = hashlib.sha256(
        canonical(delayed_material)
    ).hexdigest()
    delayed_consumer = PITFeatureConsumer(
        delayed, expected_matrix_sha256=delayed["matrix_sha256"]
    )
    fail_closed = MomentumConfirmedSignalAdapter(
        delayed_consumer, liquidation_signal_at=market[22].close_at
    )
    assert fail_closed.decide("AAPL", market[:22], parameters) != ACTION_ENTER_LONG


@pytest.mark.parametrize(
    "states,expected",
    (
        ((("0.04", False), ("0.06", True)), ACTION_HOLD),
        ((("-0.01", True), ("0.06", True)), ACTION_HOLD),
        ((("0.04", True), ("-0.01", False)), ACTION_EXIT_LONG),
    ),
)
def test_momentum_confirmation_rejects_unconfirmed_trend_states(states, expected):
    market = bars(22)
    first, second = (market[index].close_at for index in (19, 20))
    _, consumer = momentum_matrix(
        (
            (first, states[0][0], states[0][1]),
            (second, states[1][0], states[1][1]),
        )
    )
    strategy = MomentumConfirmedSignalAdapter(
        consumer, liquidation_signal_at=market[21].close_at
    )
    assert (
        strategy.decide(
            "AAPL", market[:21], momentum_confirmed_signal_parameters()
        )
        == expected
    )


def test_liquidation_precedes_missing_feature_consumption():
    market = bars(22)
    first = market[19].close_at
    _, consumer = momentum_matrix(((first, "0.05"),))
    strategy = MomentumConfirmedSignalAdapter(
        consumer, liquidation_signal_at=market[20].close_at
    )
    assert (
        strategy.decide(
            "AAPL", market[:21], momentum_confirmed_signal_parameters()
        )
        == ACTION_EXIT_LONG
    )


def test_spy_benchmark_applies_splits_before_later_per_share_dividends():
    rows=[{"open":"100","close":"100"},{"open":"50","close":"55"}]
    actions=[
        {"symbol":"SPY","action_type":"SPLIT","effective_date":"2025-01-03","split_ratio":"2"},
        {"symbol":"SPY","action_type":"CASH_DIVIDEND","effective_date":"2025-01-04","cash_per_share":"1"},
    ]
    assert _spy_buy_hold_total_return(rows,actions,start_day="2025-01-02",end_day="2025-01-04")==Decimal("0.12")
    assert _spy_buy_hold_total_return(rows,[{**actions[0],"effective_date":"2025-01-02"}],start_day="2025-01-02",end_day="2025-01-04")==Decimal("-0.45")
    with pytest.raises(ValueError,match="ordering is ambiguous"):
        _spy_buy_hold_total_return(rows,[actions[0],{**actions[1],"effective_date":"2025-01-03"}],start_day="2025-01-02",end_day="2025-01-04")


def test_engine_rejects_feature_atr_outside_reconciliation_tolerance():
    market=bars();first=market[19].close_at;second=market[20].close_at
    _,consumer=matrix(((first,True),(second,False)),atr="2.01")
    strategy=DeterministicSignalAdapter(consumer,liquidation_signal_at=market[21].close_at)
    engine=GuardrailedBacktestEngine(config=BacktestConfig(initial_cash=Decimal("100000")),fee_schedule=ExchangeFeeSchedule("TEST-FEES",(ExchangeFeeTier(None,Decimal("1")),)),data_attestation=attestation())
    with pytest.raises(ValueError,match="differs from causal"):
        engine.run(bars=market,universe_events=(UniverseEvent("AAPL","ADD",market[0].open_at,market[0].open_at,"synthetic"),),terminal_outcomes=(),corporate_actions=(),prices_are_unadjusted=True,strategy=strategy,parameters=deterministic_signal_parameters(),evaluation_start=market[0].open_at,evaluation_end=market[21].close_at)
