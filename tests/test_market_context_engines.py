import pandas as pd

from core.research.macro_environment_engine import MacroEnvironmentEngine
from core.research.market_regime_engine import MarketRegimeEngine


def test_market_regime_detects_risk_on_trend():
    history = pd.DataFrame({"Close": list(range(100, 320))})
    result = MarketRegimeEngine.classify(history)

    assert result["status"] == "COMPLETE"
    assert result["regime"] == "RISK_ON"
    assert result["score"] >= 70


def test_macro_environment_uses_explicit_series_values():
    values = {
        "policy_rate": [3.0, 3.5],
        "inflation_index": [100 + 0.2 * index for index in range(14)],
        "real_gdp": [100, 101, 102, 103, 104],
    }
    result = MacroEnvironmentEngine().analyse(lambda name: values[name])

    assert result["status"] == "COMPLETE"
    assert result["regime"] == "SUPPORTIVE"
    assert result["inflation_yoy"] > 0


if __name__ == "__main__":
    test_market_regime_detects_risk_on_trend()
    test_macro_environment_uses_explicit_series_values()
    print("MARKET CONTEXT ENGINE TESTS PASSED")
