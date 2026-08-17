from decimal import Decimal, localcontext

import pytest

from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    ExecutivePortfolioIntent,
    RiskEnvelope,
    SpecialistSignal,
    StandingStopInstruction,
    SymbolIntent,
)


DECISION_AT = "2025-01-15T21:30:00+00:00"
EVIDENCE = "a" * 64
RISK_EVIDENCE = "b" * 64


def _risk(**changes):
    values = {
        "version": "risk-envelope-v1",
        "decision_at": DECISION_AT,
        "status": "VALID",
        "regime": "NORMAL",
        "new_entries_allowed": True,
        "forced_exit": False,
        "gross_exposure_cap": Decimal("0.8"),
        "symbol_exposure_cap": Decimal("0.1"),
        "position_size_multiplier": Decimal("0.75"),
        "maximum_input_available_at": "2025-01-15T21:00:00+00:00",
        "evidence_sha256": RISK_EVIDENCE,
        "reason_codes": ("NORMAL_VOLATILITY",),
    }
    values.update(changes)
    return RiskEnvelope(**values)


def _stop():
    return StandingStopInstruction(
        reference_price=Decimal("100"),
        trigger_rule="LAST_PRICE_LTE_95",
        order_type="STOP_MARKET",
        evidence_sha256="c" * 64,
    )


def _intent(symbol="AAPL", **changes):
    values = {
        "symbol": symbol,
        "action": "ENTER_LONG",
        "current_weight": Decimal("0"),
        "target_weight": Decimal("0.1"),
        "conviction": Decimal("0.6"),
        "participation": Decimal("1"),
        "consensus": Decimal("0.75"),
        "disagreement": Decimal("0.2"),
        "risk_multiplier": Decimal("0.75"),
        "specialist_evidence_sha256": (EVIDENCE,),
        "reason_codes": ("ENTRY_THRESHOLD_MET",),
        "standing_stop": _stop(),
    }
    values.update(changes)
    return SymbolIntent(**values)


def _signal(name, score, **changes):
    values = {
        "specialist_id": name,
        "specialist_version": ExecutiveAggregatorBot.SPECIALIST_VERSIONS[name],
        "symbol": "AAPL",
        "decision_at": DECISION_AT,
        "score": Decimal(score),
        "evidence_count": 1,
        "evidence_sha256": ("d" if name == "TECHNICAL" else "e") * 64,
        "reason": "TEST_SIGNAL",
        "maximum_input_available_at": "2025-01-15T21:00:00+00:00",
    }
    values.update(changes)
    return SpecialistSignal(**values)


def test_specialist_signal_is_bounded_pit_bound_and_reason_coded():
    signal = SpecialistSignal(
        specialist_id="TECHNICAL",
        specialist_version="technical-v1",
        symbol="aapl",
        decision_at=DECISION_AT,
        score=Decimal("0.25"),
        confidence=Decimal("0.8"),
        coverage=Decimal("0.75"),
        status="ACTIVE",
        maximum_input_available_at="2025-01-15T21:00:00+00:00",
        evidence_count=2,
        evidence_sha256=EVIDENCE,
        reason="TREND_POSITIVE",
    )
    assert signal.symbol == "AAPL"
    assert signal.directional_score == Decimal("0.25")
    assert signal.reason_codes == ("TREND_POSITIVE",)
    assert signal.as_dict()["maximum_input_available_at"] < signal.decision_at

    with pytest.raises(ValueError, match="unavailable at decision_at"):
        SpecialistSignal(
            specialist_id="TECHNICAL",
            specialist_version="technical-v1",
            symbol="AAPL",
            decision_at=DECISION_AT,
            score=Decimal("0"),
            evidence_count=0,
            evidence_sha256=EVIDENCE,
            reason="FUTURE_INPUT",
            maximum_input_available_at="2025-01-15T22:00:00+00:00",
        )

    with pytest.raises(ValueError, match="zero score"):
        SpecialistSignal(
            specialist_id="TECHNICAL",
            specialist_version="technical-v1",
            symbol="AAPL",
            decision_at=DECISION_AT,
            score=Decimal("0.1"),
            status="ABSTAIN",
            evidence_count=0,
            evidence_sha256=EVIDENCE,
            reason="NO_COVERAGE",
        )


def test_risk_envelope_is_separate_hashed_and_fail_closed():
    first = _risk()
    second = _risk()
    assert first.envelope_sha256 == second.envelope_sha256
    assert first.as_dict()["envelope_sha256"] == first.envelope_sha256

    with pytest.raises(ValueError, match="cannot originate"):
        _risk(status="STALE", new_entries_allowed=True)
    with pytest.raises(ValueError, match="cannot originate"):
        _risk(status="INVALID", new_entries_allowed=False, forced_exit=True)
    with pytest.raises(ValueError, match="unavailable at decision_at"):
        _risk(maximum_input_available_at="2025-01-15T22:00:00+00:00")


def test_symbol_intent_requires_consistent_action_and_pre_authorized_stop():
    with pytest.raises(ValueError, match="requires a standing stop"):
        _intent(standing_stop=None)
    with pytest.raises(ValueError, match="inconsistent"):
        _intent(action="HOLD")

    hold = _intent(
        action="HOLD",
        current_weight=Decimal("0.1"),
        target_weight=Decimal("0.1"),
        standing_stop=None,
        reason_codes=("RISK_STALE",),
    )
    assert hold.action == "HOLD"
    assert hold.current_weight == hold.target_weight


def test_executive_portfolio_intent_is_complete_ordered_and_immutable():
    risk = _risk()
    aapl = _intent("AAPL")
    msft = _intent("MSFT")
    first = ExecutivePortfolioIntent(
        version="executive-v1",
        decision_at=DECISION_AT,
        risk_envelope_sha256=risk.envelope_sha256,
        gross_exposure_cap=Decimal("0.8"),
        symbol_intents=(msft, aapl),
        reason_codes=("QUORUM_MET",),
    )
    second = ExecutivePortfolioIntent(
        version="executive-v1",
        decision_at=DECISION_AT,
        risk_envelope_sha256=risk.envelope_sha256,
        gross_exposure_cap=Decimal("0.8"),
        symbol_intents=(aapl, msft),
        reason_codes=("QUORUM_MET",),
    )
    assert [value.symbol for value in first.symbol_intents] == ["AAPL", "MSFT"]
    assert first.intent_sha256 == second.intent_sha256

    with pytest.raises(ValueError, match="duplicate symbols"):
        ExecutivePortfolioIntent(
            version="executive-v1",
            decision_at=DECISION_AT,
            risk_envelope_sha256=risk.envelope_sha256,
            gross_exposure_cap=Decimal("0.8"),
            symbol_intents=(aapl, aapl),
            reason_codes=("QUORUM_MET",),
        )
    with pytest.raises(ValueError, match="exceed"):
        ExecutivePortfolioIntent(
            version="executive-v1",
            decision_at=DECISION_AT,
            risk_envelope_sha256=risk.envelope_sha256,
            gross_exposure_cap=Decimal("0.15"),
            symbol_intents=(aapl, msft),
            reason_codes=("QUORUM_MET",),
        )


def test_executive_is_the_only_portfolio_authority_and_risk_is_not_alpha():
    risk = _risk(position_size_multiplier=Decimal("0.75"))
    intent = ExecutiveAggregatorBot().decide(
        {
            "AAPL": {
                "TECHNICAL": _signal("TECHNICAL", "1"),
                "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
            }
        },
        risk=risk,
        current_weights={},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    )
    symbol = intent.symbol_intents[0]
    assert symbol.action == "ENTER_LONG"
    assert symbol.consensus == Decimal("1")
    assert symbol.conviction == Decimal("1")
    assert symbol.target_weight == Decimal("0.075")
    assert symbol.risk_multiplier == Decimal("0.75")
    assert "RISK_REGIME" not in ExecutiveAggregatorBot.WEIGHTS


def test_disagreement_or_missing_quorum_never_originates_new_risk():
    executive = ExecutiveAggregatorBot()
    disagreement = executive.decide(
        {
            "AAPL": {
                "TECHNICAL": _signal("TECHNICAL", "1"),
                "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "-1"),
            }
        },
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={},
        eligible_symbols=("AAPL",),
        standing_stops={},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert disagreement.action == "CASH"
    assert disagreement.disagreement == Decimal("1")
    assert disagreement.conviction == Decimal("0")

    no_quorum = executive.decide(
        {"AAPL": {"TECHNICAL": _signal("TECHNICAL", "1")}},
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"AAPL": Decimal("0.08")},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert no_quorum.action == "HOLD"
    assert no_quorum.target_weight == Decimal("0.08")
    assert "NO_QUORUM" in no_quorum.reason_codes


def test_stale_risk_preserves_holdings_and_blocks_new_entries():
    signals = {
        "AAPL": {
            "TECHNICAL": _signal("TECHNICAL", "1"),
            "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
        }
    }
    stale = _risk(
        status="STALE",
        new_entries_allowed=False,
        gross_exposure_cap=Decimal("0"),
        symbol_exposure_cap=Decimal("0"),
        position_size_multiplier=Decimal("0"),
        reason_codes=("RISK_INPUT_STALE",),
    )
    held = ExecutiveAggregatorBot().decide(
        signals,
        risk=stale,
        current_weights={"AAPL": Decimal("0.08")},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert held.action == "HOLD"
    assert held.target_weight == Decimal("0.08")
    assert "RISK_STALE" in held.reason_codes

    unheld = ExecutiveAggregatorBot().decide(
        signals,
        risk=stale,
        current_weights={},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert unheld.action == "CASH"

    ineligible_held = ExecutiveAggregatorBot().decide(
        {},
        risk=stale,
        current_weights={"MSFT": Decimal("0.06")},
        eligible_symbols=("AAPL",),
        standing_stops={"MSFT": _stop()},
        decision_at=DECISION_AT,
    )
    msft = next(item for item in ineligible_held.symbol_intents if item.symbol == "MSFT")
    assert msft.action == "HOLD"
    assert msft.target_weight == Decimal("0.06")
    assert "UNIVERSE_EXIT_DEFERRED" in msft.reason_codes


def test_valid_forced_exit_and_universe_exit_reduce_without_alpha_authority():
    forced = _risk(
        new_entries_allowed=False,
        forced_exit=True,
        position_size_multiplier=Decimal("1"),
        reason_codes=("AUTHENTICATED_KILL_SWITCH",),
    )
    result = ExecutiveAggregatorBot().decide(
        {},
        risk=forced,
        current_weights={"AAPL": Decimal("0.08")},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert result.action == "EXIT"
    assert "RISK_FORCED_EXIT" in result.reason_codes

    universe_exit = ExecutiveAggregatorBot().decide(
        {},
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"MSFT": Decimal("0.06")},
        eligible_symbols=("AAPL",),
        standing_stops={"MSFT": _stop()},
        decision_at=DECISION_AT,
    )
    msft = next(item for item in universe_exit.symbol_intents if item.symbol == "MSFT")
    assert msft.action == "EXIT"
    assert msft.reason_codes == ("UNIVERSE_EXIT",)


def test_binding_gross_cap_rounds_down_under_a_pinned_decimal_context():
    signals = {}
    stops = {}
    for symbol in ("AAPL", "MSFT", "NVDA"):
        signals[symbol] = {
            "TECHNICAL": _signal(
                "TECHNICAL", "1", symbol=symbol, confidence=Decimal("0.3")
            ),
            "SEC_FORM4_INSIDER": _signal(
                "SEC_FORM4_INSIDER", "1", symbol=symbol
            ),
        }
        stops[symbol] = _stop()
    intent = ExecutiveAggregatorBot().decide(
        signals,
        risk=_risk(
            gross_exposure_cap=Decimal("0.05"),
            symbol_exposure_cap=Decimal("0.05"),
            position_size_multiplier=Decimal("1"),
        ),
        current_weights={},
        eligible_symbols=("AAPL", "MSFT", "NVDA"),
        standing_stops=stops,
        decision_at=DECISION_AT,
    )
    assert sum(item.target_weight for item in intent.symbol_intents) <= Decimal("0.05")
    assert all(
        item.target_weight.as_tuple().exponent == Decimal("0.0001").as_tuple().exponent
        for item in intent.symbol_intents
    )


def test_missing_stop_and_stray_signal_fail_closed_per_symbol():
    executive = ExecutiveAggregatorBot()
    signals = {
        "AAPL": {
            "TECHNICAL": _signal("TECHNICAL", "1"),
            "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
        }
    }
    result = executive.decide(
        signals,
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"MSFT": Decimal("0.06")},
        eligible_symbols=("AAPL", "MSFT"),
        standing_stops={"MSFT": _stop()},
        decision_at=DECISION_AT,
    )
    aapl = next(item for item in result.symbol_intents if item.symbol == "AAPL")
    msft = next(item for item in result.symbol_intents if item.symbol == "MSFT")
    assert aapl.action == "CASH"
    assert "STOP_UNAVAILABLE" in aapl.reason_codes
    assert msft.action == "HOLD"

    stray = executive.decide(
        {
            "MSFT": {
                "TECHNICAL": _signal("TECHNICAL", "1", symbol="MSFT"),
                "SEC_FORM4_INSIDER": _signal(
                    "SEC_FORM4_INSIDER", "1", symbol="MSFT"
                ),
            }
        },
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={},
        eligible_symbols=("AAPL",),
        standing_stops={},
        decision_at=DECISION_AT,
    )
    assert stray.reason_codes == (
        "RISK_ENVELOPE_APPLIED",
        "INELIGIBLE_SIGNALS_DROPPED",
    )
    assert [item.symbol for item in stray.symbol_intents] == ["AAPL"]


def test_health_is_emitted_and_hashes_ignore_ambient_decimal_precision():
    signals = {
        "AAPL": {
            "TECHNICAL": _signal("TECHNICAL", "0.7"),
            "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "0.4"),
        }
    }
    arguments = {
        "risk": _risk(position_size_multiplier=Decimal("1")),
        "current_weights": {},
        "eligible_symbols": ("AAPL",),
        "standing_stops": {"AAPL": _stop()},
        "decision_at": DECISION_AT,
    }
    first = ExecutiveAggregatorBot().decide(signals, **arguments)
    with localcontext() as context:
        context.prec = 9
        second = ExecutiveAggregatorBot().decide(signals, **arguments)
    assert first.intent_sha256 == second.intent_sha256
    assert first.symbol_intents[0].specialist_health == (
        ("SEC_FORM4_INSIDER", True),
        ("TECHNICAL", True),
    )

    unhealthy = ExecutiveAggregatorBot().decide(
        {
            "AAPL": {
                "TECHNICAL": _signal(
                    "TECHNICAL", "1", evidence_hash_continuity=False
                ),
                "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
            }
        },
        **arguments,
    ).symbol_intents[0]
    assert unhealthy.action == "CASH"
    assert ("TECHNICAL", False) in unhealthy.specialist_health
    assert "NO_QUORUM" in unhealthy.reason_codes


@pytest.mark.parametrize(
    ("current_weight", "signals"),
    (
        (Decimal("0.06234511"), {"TECHNICAL": _signal("TECHNICAL", "1")}),
        (
            Decimal("0.10000127"),
            {
                "TECHNICAL": _signal("TECHNICAL", "1"),
                "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
            },
        ),
        (
            Decimal("0.09999873"),
            {
                "TECHNICAL": _signal("TECHNICAL", "1"),
                "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "1"),
            },
        ),
    ),
)
def test_live_weight_drift_does_not_create_dust_orders(current_weight, signals):
    item = ExecutiveAggregatorBot().decide(
        {"AAPL": signals},
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"AAPL": current_weight},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert item.action == "HOLD"
    assert item.target_weight == item.current_weight


def test_sub_quantum_holdings_remain_visible_and_risk_reducible():
    forced = _risk(
        new_entries_allowed=False,
        forced_exit=True,
        position_size_multiplier=Decimal("1"),
        reason_codes=("AUTHENTICATED_KILL_SWITCH",),
    )
    forced_item = ExecutiveAggregatorBot().decide(
        {},
        risk=forced,
        current_weights={"AAPL": Decimal("0.00004")},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert forced_item.current_weight == Decimal("0.00004")
    assert forced_item.action == "EXIT"

    universe = ExecutiveAggregatorBot().decide(
        {},
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"MSFT": Decimal("0.00004")},
        eligible_symbols=("AAPL",),
        standing_stops={"MSFT": _stop()},
        decision_at=DECISION_AT,
    )
    msft = next(item for item in universe.symbol_intents if item.symbol == "MSFT")
    assert msft.current_weight == Decimal("0.00004")
    assert msft.action == "EXIT"
    assert msft.reason_codes == ("UNIVERSE_EXIT",)

    no_quorum = ExecutiveAggregatorBot().decide(
        {"AAPL": {"TECHNICAL": _signal("TECHNICAL", "1")}},
        risk=_risk(position_size_multiplier=Decimal("1")),
        current_weights={"AAPL": Decimal("0.00004")},
        eligible_symbols=("AAPL",),
        standing_stops={"AAPL": _stop()},
        decision_at=DECISION_AT,
    ).symbol_intents[0]
    assert no_quorum.action == "HOLD"
    assert no_quorum.target_weight == Decimal("0.00004")


def test_binding_gross_cap_cannot_be_undone_by_live_weight_snapback():
    symbols = ("AAPL", "MSFT", "NVDA", "AMZN", "META")
    signals = {
        symbol: {
            "TECHNICAL": _signal("TECHNICAL", "1", symbol=symbol),
            "SEC_FORM4_INSIDER": _signal(
                "SEC_FORM4_INSIDER", "1", symbol=symbol
            ),
        }
        for symbol in symbols
    }
    current = {
        symbol: Decimal("0.09991") for symbol in ("AAPL", "MSFT", "NVDA")
    }
    intent = ExecutiveAggregatorBot().decide(
        signals,
        risk=_risk(
            gross_exposure_cap=Decimal("0.4995"),
            symbol_exposure_cap=Decimal("0.1"),
            position_size_multiplier=Decimal("1"),
        ),
        current_weights=current,
        eligible_symbols=symbols,
        standing_stops={symbol: _stop() for symbol in symbols},
        decision_at=DECISION_AT,
    )
    assert sum(item.target_weight for item in intent.symbol_intents) <= Decimal(
        "0.4995"
    )
    held = [item for item in intent.symbol_intents if item.symbol in current]
    assert all(item.action == "REDUCE" for item in held)
    assert all(item.target_weight == Decimal("0.0999") for item in held)
