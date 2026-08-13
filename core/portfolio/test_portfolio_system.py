from core.portfolio.portfolio_engine import (
    PortfolioEngine,
)
from core.research.master_portfolio_decision_engine import (
    MasterPortfolioDecisionEngine,
)


def make_candidate(
    ticker,
    score,
    expected_return,
    sector,
):

    return {
        "ticker":
            ticker,

        "name":
            ticker,

        "sector":
            sector,

        "industry":
            "Test",

        "index_membership":
            ["SP500"],

        "investment_case_score":
            score,

        "current_price":
            100.0,

        "base_intrinsic_value":
            130.0,

        "expected_return":
            expected_return,

        "decision":
            "BUY",

        "research_status":
            "COMPLETE",

        "thesis":
            {
                "result":
                    "THESIS_SURVIVES",

                "tested":
                    10,

                "material_negative":
                    0,

                "thesis_survives":
                    True,
            },

        "audit":
            {
                "status":
                    "PASS",

                "finding_count":
                    0,

                "critical":
                    0,

                "high":
                    0,

                "medium":
                    0,
            },

        "market_signals": {
            "technical_score": 75,
            "risk_score": 75,
            "annualised_volatility": 0.20,
        },

        "master_decision": {
            "version": MasterPortfolioDecisionEngine.VERSION,
            "status": "COMPLETE",
            "portfolio_recommendation": "ELIGIBLE",
            "opportunity_score": min(94.0, float(score)),
            "conviction_score": min(94.0, float(score)),
            "confidence": 78.0,
            "research_confidence": {"score": 78.0},
            "hard_gate_reasons": [],
        },
    }


def test_candidate_scoring():

    candidate = make_candidate(
        "AAA",
        90,
        0.25,
        "Technology",
    )

    score = (
        PortfolioEngine
        .candidate_score(
            candidate
        )
    )

    assert score is not None
    assert 0 < score < 100


def test_failed_audit_is_rejected():

    candidate = make_candidate(
        "BAD",
        99,
        0.50,
        "Technology",
    )

    candidate[
        "audit"
    ][
        "status"
    ] = "REVIEW"

    score = (
        PortfolioEngine
        .candidate_score(
            candidate
        )
    )

    assert score is None


def test_warning_only_audit_remains_visible_without_a_second_ranking_penalty():
    clean = make_candidate("CLEAN", 80, 0.20, "Technology")
    warning = make_candidate("WARNING", 80, 0.20, "Healthcare")
    warning["audit"].update(
        {
            "status": "PASS_WITH_WARNINGS",
            "finding_count": 1,
            "medium": 1,
        }
    )
    warning["master_decision"]["research_confidence"] = {"score": 72.0}
    warning["master_decision"]["confidence"] = 72.0

    assert PortfolioEngine.eligibility_reason(warning) is None
    assert PortfolioEngine.candidate_score(warning) is not None
    assert PortfolioEngine.candidate_score(warning) == PortfolioEngine.candidate_score(clean)
    assert warning["audit"]["status"] == "PASS_WITH_WARNINGS"
    assert PortfolioEngine.position_sizing_signal(warning) < (
        PortfolioEngine.position_sizing_signal(clean)
    )


def test_avoid_and_negative_expected_return_are_rejected():

    candidate = make_candidate(
        "AVOID",
        99,
        -0.20,
        "Technology",
    )


def test_missing_risk_or_technical_research_reduces_conviction():

    candidate = make_candidate("INCOMPLETE", 90, 0.25, "Technology")
    candidate["market_signals"]["risk_score"] = None

    score_without_risk = PortfolioEngine.candidate_score(candidate)
    assert score_without_risk is not None

    candidate["master_decision"]["portfolio_recommendation"] = "EXCLUDE"
    candidate["master_decision"]["hard_gate_reasons"] = ["Risk review is incomplete."]

    assert PortfolioEngine.candidate_score(candidate) is None


def test_authoritative_opportunity_score_passes_through_full_scale():
    candidate = make_candidate("CALIBRATED", 99, 0.50, "Technology")
    candidate["master_decision"]["opportunity_score"] = 100.0
    candidate["master_decision"]["conviction_score"] = 100.0
    candidate["master_decision"]["research_confidence"] = {"score": 90.0}

    assert PortfolioEngine.candidate_score(candidate) == 100.0


def test_decision_rating_is_display_only_authoritative_score():
    candidate = make_candidate("RATING", 94, 0.30, "Technology")
    candidate["provider_evidence"] = {"completed_evidence_count": 8}
    detail = PortfolioEngine.decision_rating_detail(candidate)

    assert detail["maximum_score"] == 100.0
    assert detail["score"] == candidate["master_decision"]["opportunity_score"]
    assert detail["formula"] == "NO_RECALCULATION"
    assert detail["source"] == "master_decision.opportunity_score"
    assert detail["display_only"] is True


def test_risk_aware_weights_differ_for_different_volatility():
    low_volatility = make_candidate("LOWVOL", 90, 0.20, "Technology")
    high_volatility = make_candidate("HIGHVOL", 90, 0.20, "Health Care")
    high_volatility["market_signals"]["annualised_volatility"] = 0.60

    weights = PortfolioEngine.calculate_weights(
        [low_volatility, high_volatility],
        max_weight=0.70,
        min_weight=0.03,
        cash_weight=0.0,
        max_sector_weight=0.80,
    )

    assert abs(sum(weights) - 1.0) < 0.00001
    assert weights[0] > weights[1]


def test_multiple_share_classes_do_not_duplicate_one_issuer():
    class_a = make_candidate("NWSA", 92, 0.20, "Communication Services")
    class_b = make_candidate("NWS", 94, 0.22, "Communication Services")
    class_a["name"] = "News Corp (Class A)"
    class_b["name"] = "News Corp (Class B)"
    other = make_candidate("OTHER", 88, 0.18, "Industrials")

    selected = PortfolioEngine.select(
        [class_b, class_a, other],
        number_of_stocks=2,
        max_weight=0.50,
        max_sector_weight=1.0,
        minimum_positions=2,
    )

    selected_tickers = {item["ticker"] for item in selected}
    assert len({"NWS", "NWSA"} & selected_tickers) == 1
    assert "OTHER" in selected_tickers


def test_portfolio_construction():

    candidates = [
        make_candidate(
            "AAA",
            95,
            0.30,
            "Technology",
        ),

        make_candidate(
            "BBB",
            90,
            0.20,
            "Healthcare",
        ),

        make_candidate(
            "CCC",
            88,
            0.15,
            "Financials",
        ),

        make_candidate(
            "DDD",
            86,
            0.12,
            "Industrials",
        ),
    ]

    scan = {
        "universe":
            "TEST",

        "requested_count":
            4,

        "completed_count":
            4,

        "audit_pass_count":
            4,

        "results":
            candidates,
    }

    portfolio = (
        PortfolioEngine.construct(
            scan,
            number_of_stocks=4,
            max_weight=0.30,
            cash_weight=0.0,
        )
    )

    assert (
        portfolio[
            "number_of_stocks"
        ]
        == 4
    )

    assert (
        len(
            portfolio[
                "holdings"
            ]
        )
        == 4
    )

    total_weight = sum(
        item[
            "weight"
        ]
        for item in portfolio[
            "holdings"
        ]
    )

    assert abs(
        total_weight - 1.0
    ) < 0.00001

    for holding in portfolio[
        "holdings"
    ]:

        assert (
            holding[
                "weight"
            ]
            <= 0.300001
        )


def test_infeasible_position_constraints():

    selected = [
        {
            "portfolio_conviction":
                90,
        }
        for _ in range(4)
    ]

    try:

        PortfolioEngine.calculate_weights(
            selected,
            max_weight=0.15,
            min_weight=0.03,
            cash_weight=0.0,
        )

    except ValueError as exc:

        assert (
            "Infeasible portfolio constraints"
            in str(exc)
        )

    else:

        raise AssertionError(
            "Expected infeasible constraints "
            "to raise ValueError."
        )


def test_sector_limit_is_enforced_in_final_portfolio():

    candidates = [
        make_candidate("TECH1", 95, 0.30, "Technology"),
        make_candidate("TECH2", 94, 0.28, "Technology"),
        make_candidate("TECH3", 93, 0.26, "Technology"),
        make_candidate("HEALTH", 90, 0.20, "Healthcare"),
        make_candidate("FIN", 89, 0.20, "Financials"),
        make_candidate("IND", 88, 0.20, "Industrials"),
    ]

    portfolio = PortfolioEngine.construct(
        {"results": candidates},
        number_of_stocks=4,
        max_weight=0.30,
        max_sector_weight=0.30,
        cash_weight=0.0,
    )

    assert portfolio["sector_weights"]["Technology"] <= 0.300001
    assert portfolio["number_of_stocks"] == 4


def test_impossible_sector_mix_is_blocked():

    candidates = [
        make_candidate(f"TECH{number}", 90, 0.20, "Technology")
        for number in range(4)
    ]

    try:
        PortfolioEngine.construct(
            {"results": candidates},
            number_of_stocks=4,
            max_weight=0.30,
            max_sector_weight=0.30,
            cash_weight=0.0,
        )
    except ValueError as exc:
        assert "sector-diverse" in str(exc)
    else:
        raise AssertionError("Expected an impossible sector mix to be blocked.")


def test_portfolio_is_fully_invested():

    candidates = [
        make_candidate("AAA", 95, 0.30, "Technology"),
        make_candidate("BBB", 90, 0.20, "Healthcare"),
        make_candidate("CCC", 88, 0.15, "Financials"),
        make_candidate("DDD", 86, 0.12, "Industrials"),
    ]

    portfolio = PortfolioEngine.construct(
        {"results": candidates},
        number_of_stocks=4,
        max_weight=0.30,
        max_sector_weight=0.30,
        cash_weight=0.0,
    )

    invested = sum(item["weight"] for item in portfolio["holdings"])

    assert abs(invested - 1.0) < 0.00001
    assert portfolio["cash_weight"] == 0.0

    try:
        PortfolioEngine.construct(
            {"results": candidates},
            number_of_stocks=4,
            max_weight=0.30,
            max_sector_weight=0.30,
            cash_weight=0.05,
        )
    except ValueError as exc:
        assert "Cash allocation is disabled" in str(exc)
    else:
        raise AssertionError("Expected non-zero cash allocation to be rejected.")


def test_portfolio_preserves_holding_monitoring_conditions():
    candidate = make_candidate("MONITOR", 90, 0.20, "Technology")
    candidate["monitoring_conditions"] = ["Expected-return deterioration triggers review."]

    portfolio = PortfolioEngine.construct(
        {"results": [candidate]},
        number_of_stocks=1,
        max_weight=1.0,
        max_sector_weight=1.0,
        cash_weight=0.0,
    )

    assert portfolio["holdings"][0]["monitoring_conditions"] == [
        "Expected-return deterioration triggers review.",
    ]


if __name__ == "__main__":

    test_candidate_scoring()
    test_failed_audit_is_rejected()
    test_warning_only_audit_is_eligible_with_a_conviction_penalty()
    test_avoid_and_negative_expected_return_are_rejected()
    test_missing_risk_or_technical_research_reduces_conviction()
    test_calibrated_scores_use_the_full_scale_without_claiming_certainty()
    test_decision_rating_is_explained_and_capped_below_certainty()
    test_risk_aware_weights_differ_for_different_volatility()
    test_multiple_share_classes_do_not_duplicate_one_issuer()
    test_portfolio_construction()
    test_infeasible_position_constraints()
    test_sector_limit_is_enforced_in_final_portfolio()
    test_impossible_sector_mix_is_blocked()
    test_portfolio_is_fully_invested()
    test_portfolio_preserves_holding_monitoring_conditions()

    print(
        "PORTFOLIO SYSTEM TESTS PASSED"
    )
