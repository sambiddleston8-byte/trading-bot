from core.research.catalyst_engine import CatalystEngine
from core.research.news_research_engine import NewsResearchEngine
from unittest.mock import patch


def test_specific_catalyst_event_types_are_not_double_counted():
    news = {
            "independent_source_count": 2,
            "evidence": [
                {
                    "headline": "Company receives FDA approval after phase 3 trial result",
                    "source": "Company IR",
                    "impact": "POSITIVE",
                    "confidence": "HIGH",
                }
            ],
        }

    class Ticker:
        calendar = {}

    with patch.object(NewsResearchEngine, "analyse", return_value=news), patch(
        "core.research.catalyst_engine.yf.Ticker",
        return_value=Ticker(),
    ):
        result = CatalystEngine.analyse("TEST")

    assert len(result["catalysts"]) == 1
    assert result["catalysts"][0]["category"] == "clinical_trial"


if __name__ == "__main__":
    test_specific_catalyst_event_types_are_not_double_counted()
    print("CATALYST EVENT CLASSIFICATION TESTS PASSED")
