import pandas as pd

from core.company_context import CompanyContext
from core.research.market_signal_engine import MarketSignalEngine


def test_market_signal_engine_normalizes_technical_and_risk_specialists():
    context = CompanyContext(
        symbol="TEST",
        info={"beta": 1.1},
        financials=None,
        balance_sheet=pd.DataFrame(
            {0: [50.0, 100.0]},
            index=["Total Debt", "Cash And Cash Equivalents"],
        ),
        cashflow=None,
        history=pd.DataFrame({"Close": list(range(100, 200))}),
    )

    result = MarketSignalEngine().analyse("TEST", context=context)

    assert result["status"] == "COMPLETE"
    assert result["version"] == MarketSignalEngine.VERSION
    assert result["technical"]["score"] is not None
    assert result["risk"]["score"] is not None
    assert result["risk"]["beta"] == 1.1


if __name__ == "__main__":
    test_market_signal_engine_normalizes_technical_and_risk_specialists()
    print("MARKET SIGNAL ENGINE TESTS PASSED")
