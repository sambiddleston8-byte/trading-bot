from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pandas as pd
import pytest

from core.research.fundamental_valuation_specialist import (
    FundamentalValuationSpecialistBot,
    build_fundamental_artifact,
)
from core.research.specialist_signals import (
    FundamentalResearchExecutiveAggregatorBot,
    RiskEnvelope,
    SpecialistSignal,
    StandingStopInstruction,
)


UTC = timezone.utc
RETRIEVED = "2025-03-01T00:00:00+00:00"


def _row(symbol="AAPL", revision=1, available="2024-11-01T21:00:00+00:00", metrics=None):
    return {
        "symbol": symbol,
        "fiscal_period": "2024Q3",
        "effective_at": "2024-09-28T00:00:00+00:00",
        "reported_at": available,
        "available_at": available,
        "revision": revision,
        "metrics": metrics or {
            "earnings_yield": "0.08", "fcf_yield": "0.08",
            "roic": "0.20", "estimate_revision": "0.10",
            "valuation_dispersion": "1",
        },
        "source_payload_sha256": hashlib.sha256(f"{symbol}:{revision}".encode()).hexdigest(),
        "source_locator": f"synthetic://{symbol}/2024Q3/{revision}",
    }


def _artifact(rows=None):
    return build_fundamental_artifact(rows or [_row()], retrieved_at=RETRIEVED)


def test_five_timestamp_normalization_is_immutable_and_train_only():
    artifact = _artifact()
    record = artifact["records"][0]
    assert record["reported_at"] == record["available_at"] == record["observation_cutoff_at"]
    assert artifact["external_data_calls"] is False
    assert artifact["validation_data_read"] is False
    with pytest.raises(ValueError, match="TRAIN"):
        build_fundamental_artifact([_row()], retrieved_at=RETRIEVED, partition_role="VALIDATION")
    tampered = json.loads(json.dumps(artifact))
    tampered["records"][0]["metrics"]["roic"] = "9"
    with pytest.raises(ValueError, match="SHA-256"):
        FundamentalValuationSpecialistBot(tampered, expected_sha256=artifact["artifact_sha256"])


def test_revision_chain_and_future_evidence_fail_closed():
    first = _row()
    second = _row(revision=2, available="2024-11-15T21:00:00+00:00")
    artifact = _artifact([first, second])
    assert artifact["records"][1]["prior_revision_sha256"] == artifact["records"][0]["record_sha256"]
    bot = FundamentalValuationSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-10-31T21:00:00+00:00")
    assert before.status == "ABSTAIN" and before.score == 0
    with pytest.raises(ValueError, match="revisions"):
        _artifact([second])


def test_revision_cannot_be_available_before_or_with_its_parent():
    first = _row()
    for available in (
        "2024-10-01T21:00:00+00:00",
        first["available_at"],
    ):
        second = _row(revision=2, available=available)
        with pytest.raises(ValueError, match="available after its parent"):
            _artifact([first, second])


def test_bounded_tick_vector_parity_staleness_and_missing_coverage():
    artifact = _artifact()
    bot = FundamentalValuationSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    decision = datetime(2024, 11, 2, 21, tzinfo=UTC)
    signal = bot.score_tick("AAPL", decision_at=decision)
    assert signal.score == Decimal("1")
    assert signal.maximum_input_available_at <= signal.decision_at
    frame = bot.score_frame(pd.DataFrame({"symbol": ["AAPL"], "decision_at": [decision]}))
    assert frame.iloc[0]["score"] == "1"
    assert bot.score_tick("MSFT", decision_at=decision).status == "ABSTAIN"
    stale = bot.score_tick("AAPL", decision_at="2025-03-02T21:00:00+00:00")
    assert stale.status == "STALE" and stale.score == 0


def _signal(name, score, decision):
    versions = FundamentalResearchExecutiveAggregatorBot.SPECIALIST_VERSIONS
    return SpecialistSignal(
        specialist_id=name, specialist_version=versions[name], symbol="AAPL",
        decision_at=decision, score=Decimal(score), evidence_count=1,
        evidence_sha256=hashlib.sha256(name.encode()).hexdigest(), reason="FIXTURE",
    )


def test_three_alpha_research_candidate_feeds_one_executive_with_risk_separate():
    decision = "2024-11-02T21:00:00+00:00"
    risk = RiskEnvelope(
        version="fixture", decision_at=decision, status="VALID", regime="NORMAL",
        new_entries_allowed=True, forced_exit=False, gross_exposure_cap=Decimal("0.3"),
        symbol_exposure_cap=Decimal("0.1"), position_size_multiplier=Decimal("1"),
        maximum_input_available_at=decision,
        evidence_sha256=hashlib.sha256(b"risk").hexdigest(), reason_codes=("FIXTURE",),
    )
    signals = {"AAPL": {name: _signal(name, "1", decision) for name in FundamentalResearchExecutiveAggregatorBot.REQUIRED_SPECIALISTS}}
    intent = FundamentalResearchExecutiveAggregatorBot().decide(
        signals, risk=risk, current_weights={"AAPL": Decimal("0")},
        eligible_symbols=("AAPL",), standing_stops={
            "AAPL": StandingStopInstruction(
                reference_price=Decimal("100"), trigger_rule="LAST_PRICE_LTE_90",
                order_type="STOP_MARKET",
                evidence_sha256=hashlib.sha256(b"stop").hexdigest(),
            )
        }, decision_at=decision,
    )
    item = intent.symbol_intents[0]
    assert item.action == "ENTER_LONG" and item.target_weight == Decimal("0.1000")
    assert len(item.specialist_evidence_sha256) == 3
    assert "RISK_REGIME" not in FundamentalResearchExecutiveAggregatorBot.WEIGHTS


def test_research_candidate_stays_unregistered_without_real_train_ablation():
    # Synthetic fixtures prove mechanics, never stable incremental investment value.
    assert FundamentalResearchExecutiveAggregatorBot.VERSION.endswith("research-v1")
    from core.research.specialist_signals import ExecutiveAggregatorBot
    assert "FUNDAMENTAL_VALUATION" not in ExecutiveAggregatorBot.REQUIRED_SPECIALISTS
