from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.application.portfolio_construction_service import PortfolioConstructionService
from core.research.master_portfolio_decision_engine import MasterPortfolioDecisionEngine


SECTORS = (
    "Information Technology",
    "Health Care",
    "Financials",
    "Industrials",
    "Energy",
    "Utilities",
    "Materials",
    "Consumer Staples",
)


def pipeline_record(ticker: str, risk_score: float = 75.0) -> dict:
    return {
        "ticker": ticker,
        "status": "COMPLETE",
        "completed_at": "2026-08-11T12:00:00+00:00",
        "core": {
            "fundamental": {},
            "valuation": {},
            "decision": {
                "decision": "BUY",
                "valuation": {
                    "current_price": 100.0,
                    "base_intrinsic_value": 130.0,
                    "expected_return": 0.30,
                },
            },
        },
        "synthesis": {
            "investment_case_score": 78.0,
            "decision": "BUY",
            "decision_reason": "Synthetic test candidate clears the research gate.",
            "bull_case": "Synthetic upside case.",
            "bear_case": "Synthetic downside case.",
            "catalysts": ["Synthetic catalyst"],
        },
        "research": {
            "thesis_challenge": {
                "result": "THESIS_SURVIVES",
                "thesis_survives": True,
                "summary": {"tested": 10, "material_negative": 0},
            },
            "market_signals": {
                "technical": {"score": 75.0, "momentum_score": 70.0},
                "risk": {
                    "score": risk_score,
                    "beta": 1.0,
                    "annualised_volatility": 0.20,
                },
            },
            "sentiment": {
                "score": 60.0,
                "label": "POSITIVE",
                "confidence": "HIGH",
                "independent_source_count": 2,
            },
        },
        "audit": {
            "status": "PASS",
            "finding_count": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "findings": [],
        },
        "master_decision": {
            "version": MasterPortfolioDecisionEngine.VERSION,
            "status": "COMPLETE",
            "portfolio_recommendation": "ELIGIBLE",
            "opportunity_score": 82.0,
            "conviction_score": 82.0,
            "confidence": 76.0,
            "research_confidence": {"score": 76.0},
            "hard_gate_reasons": [],
        },
    }


def with_test_paths(callback):
    original = (
        PortfolioConstructionService.PIPELINE_DIRECTORY,
        PortfolioConstructionService.UNIVERSE_PATH,
        PortfolioConstructionService.PORTFOLIO_DIRECTORY,
        PortfolioConstructionService.DECISION_LEDGER_PATH,
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        companies = []
        for number, sector in enumerate(SECTORS):
            ticker = f"T{number}"
            (pipeline / f"{ticker}.json").write_text(
                json.dumps(pipeline_record(ticker)),
                encoding="utf-8",
            )
            companies.append({"ticker": ticker, "sector": sector})

        universe = root / "both.json"
        universe.write_text(json.dumps({"companies": companies}), encoding="utf-8")
        PortfolioConstructionService.PIPELINE_DIRECTORY = pipeline
        PortfolioConstructionService.UNIVERSE_PATH = universe
        PortfolioConstructionService.PORTFOLIO_DIRECTORY = root / "portfolios"
        PortfolioConstructionService.DECISION_LEDGER_PATH = root / "decision_ledger.jsonl"
        callback(root, pipeline)

    (
        PortfolioConstructionService.PIPELINE_DIRECTORY,
        PortfolioConstructionService.UNIVERSE_PATH,
        PortfolioConstructionService.PORTFOLIO_DIRECTORY,
        PortfolioConstructionService.DECISION_LEDGER_PATH,
    ) = original


def test_real_services_construct_and_risk_review_a_diversified_portfolio():
    def verify(root, pipeline):
        result = PortfolioConstructionService.construct(target_holdings=8)

        assert result["status"] == "CONSTRUCTED"
        assert result["path"].exists()
        assert len(result["portfolio"]["holdings"]) == 8
        assert result["portfolio"]["risk_review"]["Pass"] is True
        assert sum(item["weight"] for item in result["portfolio"]["holdings"]) == 1.0
        assert max(result["portfolio"]["sector_weights"].values()) <= 0.15
        for record in result["ledger_records"]:
            payload = record["decision_payload"]
            assert payload["bull_case"] == "Synthetic upside case."
            assert payload["bear_case"] == "Synthetic downside case."
            assert payload["catalysts"] == ["Synthetic catalyst"]

    with_test_paths(verify)


def test_real_services_block_a_portfolio_with_low_risk_holding():
    def verify(root, pipeline):
        path = pipeline / "T0.json"
        path.write_text(json.dumps(pipeline_record("T0", risk_score=35.0)), encoding="utf-8")

        result = PortfolioConstructionService.construct(target_holdings=8)

        assert result["status"] == "BLOCKED"
        assert "risk review" in result["reason"].lower()
        assert result["portfolio"]["risk_review"]["Pass"] is False

    with_test_paths(verify)


if __name__ == "__main__":
    test_real_services_construct_and_risk_review_a_diversified_portfolio()
    test_real_services_block_a_portfolio_with_low_risk_holding()
    print("PORTFOLIO APPLICATION INTEGRATION TESTS PASSED")
