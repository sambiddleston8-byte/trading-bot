from core.portfolio.universe_scanner import (
    UniverseScanner,
)
from core.research.research_contract import (
    ResearchContract,
)


def test_research_contract():

    result = {
        "ticker": "TEST",
        "status": "COMPLETE",
        "source_git_revision": "research-commit-123",
        "core": {
            "valuation": {
                "Current Price": 100.0,
                "Intrinsic Value": {
                    "Base": 130.0,
                },
                "Expected Return": {
                    "Base": 0.30,
                    "Annualised": 0.0539,
                    "Horizon Years": 5,
                },
            },
            "decision": {
                "decision": "BUY",
                "valuation": {
                    "current_price": 101.0,
                    "base_intrinsic_value": 131.0,
                    "expected_return": 0.297,
                },
            },
        },
        "research": {
            "thesis_challenge": {
                "result": "THESIS_SURVIVES",
                "tested": 12,
                "material_negative": 1,
                "thesis_survives": True,
            },
            "specialist_research": {
                "status": "COMPLETE",
                "completed_count": 5,
                "requested_count": 5,
                "signals": {},
            },
        },
        "synthesis": {
            "investment_case_score": 82.5,
            "what_would_change_our_mind": [
                "A material deterioration in expected returns would trigger review.",
            ],
        },
        "audit": {
            "status": "PASS",
            "finding_count": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
        },
    }

    canonical = (
        ResearchContract
        .from_pipeline_result(
            result
        )
    )

    assert canonical["current_price"] == 101.0
    assert canonical["base_intrinsic_value"] == 131.0
    assert canonical["expected_return"] == 0.297
    assert canonical["audit"]["status"] == "PASS"
    assert canonical["contract_version"] == "1.4"
    assert canonical["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )
    assert canonical["annualised_expected_return"] == 0.0539
    assert canonical["valuation_horizon_years"] == 5.0
    assert canonical["specialist_research"]["completed_count"] == 5
    assert canonical["monitoring_conditions"] == [
        "A material deterioration in expected returns would trigger review.",
    ]
    assert canonical["research_git_revision"] == "research-commit-123"

    assert (
        UniverseScanner
        ._extract_current_price(
            result
        )
        == 101.0
    )

    assert (
        UniverseScanner
        ._extract_intrinsic_value(
            result
        )
        == 131.0
    )

    assert (
        UniverseScanner
        ._extract_expected_return(
            result
        )
        == 0.297
    )


if __name__ == "__main__":

    test_research_contract()

    print(
        "RESEARCH CONTRACT TESTS PASSED"
    )
