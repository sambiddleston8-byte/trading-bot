from core.fundamental_analysis_engine import (
    FundamentalAnalysisEngine,
)
from core.valuation_engine import (
    ValuationEngine,
)
import pytest

from core.investment_decision_engine import (
    InvestmentDecisionEngine,
)


@pytest.mark.live_data
def test_nvda_investment_pipeline(tmp_path):

    symbol = "NVDA"

    fundamentals = (
        FundamentalAnalysisEngine()
        .analyse(symbol)
    )

    valuation = (
        ValuationEngine(output_directory=tmp_path / "valuation")
        .analyse(symbol)
    )

    decision = (
        InvestmentDecisionEngine()
        .analyse(
            fundamentals,
            valuation,
        )
    )

    # --------------------------------------------------------
    # FUNDAMENTALS
    # --------------------------------------------------------

    assert (
        fundamentals["fundamental_score"]
        is not None
    )

    assert (
        fundamentals["fundamental_quality"]
        in {
            "STRONG",
            "MODERATE",
            "WEAK",
            "REVIEW",
        }
    )

    assert (
        fundamentals["validation"]
        ["overall_confidence"]
        in {
            "HIGH",
            "MEDIUM",
            "LOW",
            "REVIEW",
        }
    )

    # --------------------------------------------------------
    # VALUATION
    # --------------------------------------------------------

    assert valuation["Status"] == "COMPLETE"

    scenarios = valuation["Scenarios"]

    assert "Bear" in scenarios
    assert "Base" in scenarios
    assert "Bull" in scenarios

    base_value = (
        scenarios["Base"]
        ["Intrinsic Value Per Share"]
    )

    assert base_value is not None
    assert base_value > 0

    # --------------------------------------------------------
    # DECISION ENGINE
    # --------------------------------------------------------

    assert decision["status"] == "COMPLETE"

    assert (
        decision["valuation"]
        ["base_intrinsic_value"]
        is not None
    )

    assert (
        decision["valuation"]
        ["expected_return"]
        is not None
    )

    assert (
        decision["scores"]
        ["overall"]
        is not None
    )

    assert decision["decision"] in {
        "STRONG_BUY",
        "BUY",
        "WATCHLIST",
        "HOLD",
        "AVOID",
    }

    # --------------------------------------------------------
    # PROVENANCE
    # --------------------------------------------------------

    provenance = (
        fundamentals
        .get(
            "provenance",
            {}
        )
    )

    assert len(provenance) > 0

    # --------------------------------------------------------
    # VALUATION GUARDRAIL
    #
    # NVDA's current base expected return is materially
    # negative, so the system must not call it BUY.
    # --------------------------------------------------------

    expected_return = (
        decision["valuation"]
        ["expected_return"]
    )

    if expected_return <= -0.10:

        assert decision["decision"] not in {
            "BUY",
            "STRONG_BUY",
        }

if __name__ == "__main__":

    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        test_nvda_investment_pipeline(Path(directory))

    print()
    print("=" * 80)
    print("INVESTMENT PIPELINE TEST PASSED")
    print("=" * 80)
