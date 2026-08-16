from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from core.features.pit_feature_contract import DEFINITION_SHA256, FAMILY
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
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
    PITFeatureConsumer,
    deterministic_signal_parameters,
)
from core.research.stage3_feature_strategy_evaluation import _spy_buy_hold_total_return


UTC = timezone.utc
START = datetime(2025, 1, 2, 14, 30, tzinfo=UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def feature_row(symbol, effective, *, bullish=True, atr="2"):
    available = effective + timedelta(minutes=1)
    material = {
        "feature_id": "PITF-" + hashlib.sha256(f"{FAMILY}:{symbol}:{effective.isoformat()}".encode()).hexdigest()[:32].upper(),
        "feature_family": FAMILY,
        "feature_definition_sha256": DEFINITION_SHA256,
        "entity_id": symbol,
        "partition_role": "VALIDATION",
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
            "source_artifact_sha256": {"VALIDATION": "a" * 64},
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
