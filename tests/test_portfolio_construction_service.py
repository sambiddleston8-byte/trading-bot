from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.application.portfolio_construction_service import PortfolioConstructionService
from core.research.master_portfolio_decision_engine import MasterPortfolioDecisionEngine


class FakePortfolio:
    @staticmethod
    def prepare_candidates(scan):
        return [
            dict(item, portfolio_conviction=80.0, research_confidence=75.0)
            for item in scan["results"]
            if item["audit"]["status"] == "PASS"
        ]

    @staticmethod
    def construct(scan, number_of_stocks, cash_weight=0.0):
        return {
            "number_of_stocks": number_of_stocks,
            "cash_weight": cash_weight,
            "holdings": [
                {
                    "ticker": item["ticker"],
                    "portfolio_conviction": item["portfolio_conviction"],
                    "research_confidence": item["research_confidence"],
                }
                for item in scan["ranked"][:number_of_stocks]
            ],
        }

    @staticmethod
    def save(portfolio, path):
        Path(path).write_text(json.dumps(portfolio), encoding="utf-8")


class FakeRiskReviewer:
    @staticmethod
    def review(portfolio):
        return {"Pass": True, "Flags": []}


def pipeline_record(ticker):
    return {
        "ticker": ticker,
        "status": "COMPLETE",
        "core": {
            "fundamental": {},
            "valuation": {},
            "decision": {
                "valuation": {
                    "current_price": 100,
                    "base_intrinsic_value": 130,
                    "expected_return": 0.3,
                },
                "decision": "BUY",
            },
        },
        "synthesis": {"investment_case_score": 75, "decision": "BUY"},
        "research": {
            "thesis_challenge": {
                "result": "THESIS_SURVIVES",
                "thesis_survives": True,
            },
            "market_signals": {
                "technical": {"score": 70},
                "risk": {"score": 70},
            },
            "specialist_research": {
                "status": "COMPLETE",
                "completed_count": 5,
                "requested_count": 5,
            },
        },
        "audit": {"status": "PASS"},
        "master_decision": {
            "version": MasterPortfolioDecisionEngine.VERSION,
            "status": "COMPLETE",
            "portfolio_recommendation": "ELIGIBLE",
            "opportunity_score": 80.0,
            "conviction_score": 80.0,
            "confidence": 75.0,
            "research_confidence": {"score": 75.0},
            "hard_gate_reasons": [],
        },
    }


def test_constructs_from_saved_records():
    original_pipeline = PortfolioConstructionService.PIPELINE_DIRECTORY
    original_universe = PortfolioConstructionService.UNIVERSE_PATH
    original_portfolios = PortfolioConstructionService.PORTFOLIO_DIRECTORY
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        for number in range(5):
            (pipeline / f"T{number}.json").write_text(
                json.dumps(pipeline_record(f"T{number}")),
                encoding="utf-8",
            )
        universe = root / "both.json"
        universe.write_text(
            json.dumps({"companies": [{"ticker": f"T{number}", "sector": "Tech"} for number in range(5)]}),
            encoding="utf-8",
        )
        PortfolioConstructionService.PIPELINE_DIRECTORY = pipeline
        PortfolioConstructionService.UNIVERSE_PATH = universe
        PortfolioConstructionService.PORTFOLIO_DIRECTORY = root / "portfolios"
        result = PortfolioConstructionService.construct(
            5,
            portfolio_class=FakePortfolio,
            risk_reviewer=FakeRiskReviewer,
        )
        assert result["status"] == "CONSTRUCTED"
        assert result["path"].exists()
        assert result["portfolio"]["holdings"][0]["confidence_label"] == "STRONG"
        assert result["readiness"]["ready"] is True
    PortfolioConstructionService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioConstructionService.UNIVERSE_PATH = original_universe
    PortfolioConstructionService.PORTFOLIO_DIRECTORY = original_portfolios


def test_readiness_explains_the_construction_shortfall():
    readiness = PortfolioConstructionService.portfolio_readiness(
        {"eligible_count": 2},
        target_holdings=8,
    )

    assert readiness["ready"] is False
    assert readiness["shortfall"] == 6
    assert "Fewer than five" in readiness["message"]


def test_readiness_constructs_with_all_eligible_companies_before_target_is_reached():
    readiness = PortfolioConstructionService.portfolio_readiness(
        {"eligible_count": 13},
        target_holdings=15,
    )

    assert readiness["ready"] is True
    assert readiness["target_reached"] is False
    assert readiness["constructible_holdings"] == 13
    assert readiness["shortfall"] == 2


def test_readiness_counts_one_issuer_once_across_share_classes():
    original_pipeline = PortfolioConstructionService.PIPELINE_DIRECTORY
    original_universe = PortfolioConstructionService.UNIVERSE_PATH
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        for ticker in ("NWS", "NWSA", "OTHER"):
            (pipeline / f"{ticker}.json").write_text(
                json.dumps(pipeline_record(ticker)),
                encoding="utf-8",
            )
        universe = root / "both.json"
        universe.write_text(
            json.dumps(
                {
                    "companies": [
                        {"ticker": "NWS", "name": "News Corp (Class B)", "sector": "Communication Services"},
                        {"ticker": "NWSA", "name": "News Corp (Class A)", "sector": "Communication Services"},
                        {"ticker": "OTHER", "name": "Other Company", "sector": "Industrials"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        PortfolioConstructionService.PIPELINE_DIRECTORY = pipeline
        PortfolioConstructionService.UNIVERSE_PATH = universe
        scan = PortfolioConstructionService.research_scan()
        assert scan["eligible_count"] == 2
        assert scan["issuer_duplicate_count"] == 1
    PortfolioConstructionService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioConstructionService.UNIVERSE_PATH = original_universe


if __name__ == "__main__":
    test_constructs_from_saved_records()
    test_readiness_explains_the_construction_shortfall()
    test_readiness_constructs_with_all_eligible_companies_before_target_is_reached()
    test_readiness_counts_one_issuer_once_across_share_classes()
    print("PORTFOLIO CONSTRUCTION SERVICE TESTS PASSED")
