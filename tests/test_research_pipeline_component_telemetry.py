from __future__ import annotations

import pytest

from core.research.investment_research_pipeline import InvestmentResearchPipeline


def test_measure_component_records_only_name_and_non_negative_duration():
    ticks = iter([10.0, 10.0125])
    observations = []

    value = InvestmentResearchPipeline._measure_component(
        "news_research",
        observations,
        lambda: next(ticks),
        lambda: {"status": "COMPLETE"},
    )

    assert value == {"status": "COMPLETE"}
    assert observations == [
        {"component": "news_research", "duration_ms": 12.5}
    ]


def test_measure_component_preserves_exception_and_records_elapsed_time():
    ticks = iter([5.0, 5.001])
    observations = []

    def fail():
        raise RuntimeError("unchanged failure")

    with pytest.raises(RuntimeError, match="unchanged failure"):
        InvestmentResearchPipeline._measure_component(
            "evidence_audit",
            observations,
            lambda: next(ticks),
            fail,
        )

    assert observations == [
        {"component": "evidence_audit", "duration_ms": 1.0}
    ]


def test_measure_component_is_a_no_op_when_observations_are_disabled():
    def clock_must_not_be_called():
        raise AssertionError("clock called when telemetry was disabled")

    assert InvestmentResearchPipeline._measure_component(
        "core_analysis",
        None,
        clock_must_not_be_called,
        lambda: "normal result",
    ) == "normal result"
