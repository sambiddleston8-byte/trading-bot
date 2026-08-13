from __future__ import annotations

import tempfile
from pathlib import Path

from core.application.research_service import ResearchService


class FakePipeline:
    @staticmethod
    def analyse(ticker, save=False):
        assert ticker == "NVDA"
        assert save is False
        return {
            "ticker": ticker,
            "status": "COMPLETE",
            "core": {
                "fundamental": {},
                "valuation": {},
                "decision": {
                    "valuation": {
                        "current_price": 100.0,
                        "base_intrinsic_value": 125.0,
                        "expected_return": 0.25,
                    },
                    "decision": "BUY",
                },
            },
            "synthesis": {
                "investment_case_score": 70.0,
                "decision": "BUY",
            },
            "audit": {"status": "PASS"},
        }


class FakeTelemetryPipeline(FakePipeline):
    @staticmethod
    def analyse_with_telemetry(ticker, save=False):
        return FakePipeline.analyse(ticker, save=save), [
            {"component": "core_analysis", "duration_ms": 12.5}
        ]


def test_run_saves_canonical_record():
    previous_directory = ResearchService.OUTPUT_DIRECTORY
    with tempfile.TemporaryDirectory() as directory:
        ResearchService.OUTPUT_DIRECTORY = Path(directory)
        completed = ResearchService.run(" nvda ", pipeline=FakePipeline)
        assert completed["ticker"] == "NVDA"
        assert completed["path"].exists()
        assert completed["canonical"]["decision"] == "BUY"
        assert completed["canonical"]["expected_return"] == 0.25
    ResearchService.OUTPUT_DIRECTORY = previous_directory


def test_rejects_invalid_ticker():
    try:
        ResearchService.normalise_ticker("NVDA; rm -rf /")
    except ValueError:
        return
    raise AssertionError("Invalid ticker was accepted.")


def test_run_returns_telemetry_without_persisting_it_in_research_result():
    previous_directory = ResearchService.OUTPUT_DIRECTORY
    with tempfile.TemporaryDirectory() as directory:
        ResearchService.OUTPUT_DIRECTORY = Path(directory)
        completed = ResearchService.run("NVDA", pipeline=FakeTelemetryPipeline)
        assert completed["component_telemetry"] == [
            {"component": "core_analysis", "duration_ms": 12.5}
        ]
        assert "component_telemetry" not in completed["result"]
    ResearchService.OUTPUT_DIRECTORY = previous_directory


if __name__ == "__main__":
    test_run_saves_canonical_record()
    test_rejects_invalid_ticker()
    print("RESEARCH SERVICE TESTS PASSED")
