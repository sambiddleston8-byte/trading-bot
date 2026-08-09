import pandas as pd

from bots.risk.analyser import RiskAnalyser
from bots.technical.analyser import TechnicalAnalyser
from core.company_context import CompanyContext


def context(prices, *, beta=1.0, debt=100.0, cash=100.0):
    return CompanyContext(
        symbol="TEST",
        info={"beta": beta},
        financials=None,
        balance_sheet=pd.DataFrame(
            {0: [debt, cash]},
            index=["Total Debt", "Cash And Cash Equivalents"],
        ),
        cashflow=None,
        history=pd.DataFrame({"Close": prices}),
    )


def test_technical_analysis_uses_multiple_horizons_for_a_rising_price_series():
    prices = [100.0 * (1.0025 ** day) for day in range(260)]
    result = TechnicalAnalyser().analyse(context(prices))

    assert result["Status"] == "COMPLETE"
    assert result["Technical Score"] > 60.0
    assert result["Return 20d"] is not None
    assert result["Return 252d"] is not None
    assert result["Support Level"] is not None
    assert result["Resistance Level"] is not None
    assert result["Fibonacci Levels"]
    assert result["Nearest Fibonacci Level"] in result["Fibonacci Levels"]


def test_technical_analysis_does_not_invent_a_score_for_short_history():
    result = TechnicalAnalyser().analyse(context(list(range(1, 40))))

    assert result["Status"] == "LIMITED"
    assert result["Technical Score"] is None


def test_risk_analysis_penalises_high_volatility_drawdown_and_leverage():
    stable = RiskAnalyser().analyse(context([100.0 + day * 0.1 for day in range(260)]))
    volatile_prices = [100.0 if day % 2 else 55.0 for day in range(260)]
    stressed = RiskAnalyser().analyse(
        context(volatile_prices, beta=2.2, debt=500.0, cash=100.0)
    )

    assert stressed["Risk Score"] < stable["Risk Score"]
    assert stressed["Annualized Volatility"] is not None
    assert stressed["Maximum Drawdown"] < -0.40
    assert stressed["Risk Components"]


if __name__ == "__main__":
    test_technical_analysis_uses_multiple_horizons_for_a_rising_price_series()
    test_technical_analysis_does_not_invent_a_score_for_short_history()
    test_risk_analysis_penalises_high_volatility_drawdown_and_leverage()
    print("TECHNICAL AND RISK SIGNAL TESTS PASSED")
