from datetime import datetime, timedelta, timezone

from core.application.portfolio_monitor_service import PortfolioMonitorService


def portfolio():
    return {
        "created_at": "2026-08-09T00:00:00+00:00",
        "holdings": [
            {"ticker": "GOOD", "name": "Good Co", "weight": 0.5, "current_price": 100.0},
            {"ticker": "LOSS", "name": "Loss Co", "weight": 0.3, "current_price": 100.0},
            {"ticker": "THESIS", "name": "Thesis Co", "weight": 0.2, "current_price": 100.0},
        ],
    }


def test_monitor_creates_review_alerts_without_automatic_trades():
    prices = {"GOOD": 106.0, "LOSS": 78.0, "THESIS": 102.0}
    thesis_alerts = {
        "GOOD": (None, None),
        "LOSS": (None, None),
        "THESIS": ("EXIT_REVIEW", "Thesis was invalidated."),
    }
    result = PortfolioMonitorService.evaluate(
        portfolio(),
        price_lookup=lambda ticker: prices.get(ticker),
        research_alert_lookup=lambda ticker: thesis_alerts[ticker],
        allocation_recommendation_lookup=lambda holding: {
            "action": "NO_CHANGE",
            "current_weight": holding["weight"],
            "suggested_weight": holding["weight"],
            "allocation_change": 0.0,
            "reason": "No allocation change.",
        },
        benchmark_price_lookup=lambda ticker: 6000.0 if ticker == "^GSPC" else None,
        market_exposure_lookup=lambda portfolio: {"status": "LIMITED"},
    )

    actions = {position["ticker"]: position["action"] for position in result["positions"]}
    assert actions == {
        "GOOD": "HOLD",
        "LOSS": "HOLD",
        "THESIS": "EXIT_REVIEW",
    }
    assert result["policy"]["execution"] == "ALERT_ONLY_NO_AUTOMATIC_TRADES"
    assert result["summary"]["alerts_required"] == 1
    loss = next(position for position in result["positions"] if position["ticker"] == "LOSS")
    assert abs(loss["price_change"] + 0.22) < 0.000001
    assert result["benchmark"] == {
        "name": "S&P 500 Index",
        "ticker": "^GSPC",
        "price": 6000.0,
    }


def test_monitor_records_a_research_led_paper_allocation_change_not_a_trade():
    result = PortfolioMonitorService.evaluate(
        portfolio(),
        price_lookup=lambda ticker: 100.0,
        research_alert_lookup=lambda ticker: (None, None),
        allocation_recommendation_lookup=lambda holding: {
            "action": "REDUCE_REVIEW" if holding["ticker"] == "GOOD" else "NO_CHANGE",
            "current_weight": holding["weight"],
            "suggested_weight": holding["weight"] * 0.7,
            "allocation_change": holding["weight"] * -0.3,
            "reason": "Decision rating deteriorated after research review.",
        },
        benchmark_price_lookup=lambda ticker: 6000.0,
        market_exposure_lookup=lambda portfolio: {"status": "LIMITED"},
    )

    good = next(item for item in result["positions"] if item["ticker"] == "GOOD")
    assert good["action"] == "REDUCE_REVIEW"
    assert good["allocation_recommendation"]["suggested_weight"] == 0.35
    assert result["summary"]["allocation_changes_required"] == 1
    assert "proposed paper portfolio" in result["policy"]["portfolio_changes"]


def test_saved_research_age_is_measured_without_creating_a_trade_signal():
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    age = PortfolioMonitorService.research_age_days({"completed_at": old})

    assert age is not None
    assert age > PortfolioMonitorService.MAX_RESEARCH_AGE_DAYS


def test_reallocation_plan_matches_each_increase_to_research_led_reductions():
    plan = PortfolioMonitorService.reallocation_plan(
        [
            {
                "ticker": "APPLE",
                "company": "Apple",
                "allocation_recommendation": {
                    "action": "INCREASE_REVIEW",
                    "current_weight": 0.08,
                    "suggested_weight": 0.10,
                    "decision_rating": 82,
                    "reason": "Research quality and valuation improved.",
                },
            },
            {
                "ticker": "DONOR1",
                "company": "Donor One",
                "allocation_recommendation": {
                    "action": "REDUCE_REVIEW",
                    "current_weight": 0.08,
                    "suggested_weight": 0.07,
                    "decision_rating": 65,
                    "reason": "Technical and valuation evidence weakened.",
                },
            },
            {
                "ticker": "DONOR2",
                "company": "Donor Two",
                "allocation_recommendation": {
                    "action": "REDUCE_REVIEW",
                    "current_weight": 0.07,
                    "suggested_weight": 0.06,
                    "decision_rating": 60,
                    "reason": "Catalyst evidence weakened.",
                },
            },
        ]
    )

    assert plan["status"] == "READY_TO_APPLY_TO_PROPOSED_PORTFOLIO"
    assert plan["automatic_proposed_portfolio_update"] is True
    assert plan["total_transfer_proposed"] == 0.02
    assert {item["from_ticker"] for item in plan["transfers"]} == {"DONOR1", "DONOR2"}
    assert {item["to_ticker"] for item in plan["transfers"]} == {"APPLE"}


def test_reallocation_updates_only_the_proposed_paper_portfolio_with_a_trace():
    original = {
        "created_at": "2026-08-09T00:00:00+00:00",
        "constraints": {"max_weight": 0.80, "max_sector_weight": 1.0},
        "holdings": [
            {"ticker": "APPLE", "name": "Apple", "sector": "Technology", "weight": 0.08},
            {"ticker": "DONOR1", "name": "Donor One", "sector": "Financials", "weight": 0.08},
            {"ticker": "DONOR2", "name": "Donor Two", "sector": "Industrials", "weight": 0.07},
            {"ticker": "OTHER", "name": "Other", "sector": "Health Care", "weight": 0.77},
        ],
    }
    plan = PortfolioMonitorService.reallocation_plan(
        [
            {
                "ticker": "APPLE",
                "company": "Apple",
                "allocation_recommendation": {
                    "action": "INCREASE_REVIEW",
                    "current_weight": 0.08,
                    "suggested_weight": 0.10,
                    "decision_rating": 82,
                    "reason": "Research quality and valuation improved.",
                },
            },
            {
                "ticker": "DONOR1",
                "company": "Donor One",
                "allocation_recommendation": {
                    "action": "REDUCE_REVIEW",
                    "current_weight": 0.08,
                    "suggested_weight": 0.07,
                    "decision_rating": 65,
                    "reason": "Technical and valuation evidence weakened.",
                },
            },
            {
                "ticker": "DONOR2",
                "company": "Donor Two",
                "allocation_recommendation": {
                    "action": "REDUCE_REVIEW",
                    "current_weight": 0.07,
                    "suggested_weight": 0.06,
                    "decision_rating": 60,
                    "reason": "Catalyst evidence weakened.",
                },
            },
        ]
    )
    snapshot = {
        "checked_at": "2026-08-09T12:00:00+00:00",
        "reallocation_plan": plan,
        "positions": [
            {
                "ticker": "APPLE",
                "allocation_recommendation": {"decision_rating": 82},
            },
        ],
    }

    result = PortfolioMonitorService.apply_reallocation(original, snapshot)

    assert result["status"] == "APPLIED"
    assert original["holdings"][0]["weight"] == 0.08
    weights = {holding["ticker"]: holding["weight"] for holding in result["portfolio"]["holdings"]}
    assert weights == {"APPLE": 0.10, "DONOR1": 0.07, "DONOR2": 0.06, "OTHER": 0.77}
    assert abs(sum(weights.values()) - 1.0) < 0.00001
    assert result["reallocation_plan"]["status"] == "APPLIED_TO_PROPOSED_PORTFOLIO"
    assert result["portfolio"]["last_rebalance"]["policy"].endswith("no broker order was sent.")


def test_exit_can_fund_an_audit_approved_external_replacement_without_cash():
    candidate = {
        "ticker": "REPLACE",
        "name": "Replacement Co",
        "sector": "Health Care",
        "industry": "Biotechnology",
        "portfolio_conviction": 88.0,
        "opportunity_score": 88.0,
        "research_confidence": 78.0,
        "expected_return": 0.25,
        "decision_rating": {"score": 84.0},
        "investment_case_score": 80.0,
        "current_price": 100.0,
        "base_intrinsic_value": 125.0,
        "valuation_upside": 0.25,
        "decision": "BUY",
        "thesis": {"thesis_survives": True},
        "audit": {"status": "PASS"},
        "market_signals": {"risk_score": 70.0, "annualised_volatility": 0.25},
        "sentiment": {"score": 55.0},
        "monitoring_conditions": [],
    }
    plan = PortfolioMonitorService.reallocation_plan(
        [
            {
                "ticker": "EXIT",
                "company": "Exit Co",
                "allocation_recommendation": {
                    "action": "EXIT_REVIEW",
                    "current_weight": 0.10,
                    "suggested_weight": 0.0,
                    "decision_rating": 30.0,
                    "reason": "Audit and thesis evidence no longer clear the portfolio safeguards.",
                },
            },
        ],
        replacement_candidates=[candidate],
        max_weight=0.90,
        min_weight=0.03,
    )
    original = {
        "constraints": {"max_weight": 0.90, "max_sector_weight": 1.0},
        "holdings": [
            {"ticker": "EXIT", "name": "Exit Co", "sector": "Technology", "weight": 0.10},
            {"ticker": "KEEP", "name": "Keep Co", "sector": "Financials", "weight": 0.90},
        ],
    }
    result = PortfolioMonitorService.apply_reallocation(
        original,
        {"checked_at": "2026-08-09T12:00:00+00:00", "reallocation_plan": plan},
    )

    assert plan["replacement_candidates_considered"] == 1
    assert plan["replacement_transfers"] == 1
    assert result["status"] == "APPLIED"
    weights = {holding["ticker"]: holding["weight"] for holding in result["portfolio"]["holdings"]}
    assert weights == {"KEEP": 0.90, "REPLACE": 0.10}
    assert sum(weights.values()) == 1.0


if __name__ == "__main__":
    test_monitor_creates_review_alerts_without_automatic_trades()
    test_monitor_records_a_research_led_paper_allocation_change_not_a_trade()
    test_saved_research_age_is_measured_without_creating_a_trade_signal()
    test_reallocation_plan_matches_each_increase_to_research_led_reductions()
    test_reallocation_updates_only_the_proposed_paper_portfolio_with_a_trace()
    test_exit_can_fund_an_audit_approved_external_replacement_without_cash()
    print("PORTFOLIO MONITOR SERVICE TESTS PASSED")
