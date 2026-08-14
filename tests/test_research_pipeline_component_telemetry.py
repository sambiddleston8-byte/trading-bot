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


def test_record_provider_access_appends_after_stage_observation():
    class Service:
        @staticmethod
        def access_observations(evidence):
            assert evidence == {"status": "COMPLETE"}
            return [
                {
                    "component": "supplemental_provider_access.fmp_analyst_estimates",
                    "provider": "financial_modeling_prep",
                    "duration_ms": 2.5,
                    "retry_count": 0,
                }
            ]

    observations = [{"component": "supplemental_evidence", "duration_ms": 5.0}]
    InvestmentResearchPipeline._record_provider_access(
        {"status": "COMPLETE"}, observations, Service()
    )

    assert [item["component"] for item in observations] == [
        "supplemental_evidence",
        "supplemental_provider_access.fmp_analyst_estimates",
    ]


def test_record_provider_access_does_not_extract_when_telemetry_is_disabled():
    class Service:
        @staticmethod
        def access_observations(evidence):
            raise AssertionError("provider telemetry extracted while disabled")

    evidence = {"status": "COMPLETE"}
    InvestmentResearchPipeline._record_provider_access(evidence, None, Service())
    assert evidence == {"status": "COMPLETE"}


def test_record_provider_access_failure_cannot_change_research():
    class BrokenService:
        @staticmethod
        def access_observations(evidence):
            raise RuntimeError("diagnostics unavailable")

    for service in (object(), BrokenService()):
        observations = [
            {"component": "supplemental_evidence", "duration_ms": 5.0}
        ]
        InvestmentResearchPipeline._record_provider_access(
            {"status": "COMPLETE"}, observations, service
        )

        assert observations == [
            {"component": "supplemental_evidence", "duration_ms": 5.0}
        ]
