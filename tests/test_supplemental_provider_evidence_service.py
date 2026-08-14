from datetime import datetime, timezone

from core.research.supplemental_provider_evidence_service import (
    SupplementalProviderEvidenceService,
)
from core.data_sources.optional_provider_sources import OptionalProviderError
from core.research_run_telemetry_ledger import _component_observations


class FakeSource:
    def __init__(self, result):
        self.result = result

    def income_statement(self, ticker):
        return self.result

    def analyst_estimates(self, ticker):
        return self.result

    def as_reported_financials(self, ticker):
        return self.result

    def ratings_snapshot(self, ticker):
        return self.result

    def price_target_consensus(self, ticker):
        return self.result

    def snapshot(self, ticker):
        return self.result

    def daily_bars(self, ticker, start, end):
        return self.result

    def company_news(self, ticker, limit=10):
        return self.result

    def observations(self, series, **kwargs):
        return self.result


def test_supplementary_data_is_preserved_as_evidence_not_a_decision_override():
    timestamp = datetime.now(timezone.utc).isoformat()
    evidence = SupplementalProviderEvidenceService.collect(
        "NVDA",
        alpha_vantage=FakeSource({"status": "COMPLETE", "source": "alpha", "retrieved_at": timestamp}),
        fmp=FakeSource({"status": "COMPLETE", "source": "fmp", "retrieved_at": timestamp}),
        polygon=FakeSource({"status": "COMPLETE", "source": "polygon", "retrieved_at": timestamp}),
        fred=FakeSource({"status": "COMPLETE", "source": "fred", "retrieved_at": timestamp}),
    )

    assert evidence["status"] == "COMPLETE"
    assert evidence["evidence_policy"] == "SUPPLEMENTARY_ONLY_PENDING_RECONCILIATION"
    assert evidence["fmp_analyst_estimates"]["source"] == "fmp"
    assert evidence["summary"]["independent_company_source_count"] == 3
    assert evidence["summary"]["completed_source_count"] == 4
    assert evidence["summary"]["completed_evidence_count"] == 8
    assert "financial_statement_cross_check" in evidence["summary"]["completed_roles"]
    assert "analyst_expectations" in evidence["summary"]["completed_roles"]
    assert "independent_company_news" in evidence["summary"]["completed_roles"]
    assert evidence["summary"]["fresh_provider_count"] == 4


def _complete_access(elapsed_seconds, retry_count, **extra):
    return {
        "status": "COMPLETE",
        "provider_access": {
            "elapsed_seconds": elapsed_seconds,
            "retry_count": retry_count,
            **extra,
        },
    }


def test_access_observations_are_secret_free_and_ledger_compatible():
    observations = SupplementalProviderEvidenceService.access_observations(
        {
            "alpha_vantage_income_statement": _complete_access(
                0.0125,
                1,
                request_url="https://example.invalid?apikey=secret",
                response_body="secret",
            )
        }
    )

    assert observations == [
        {
            "component": "supplemental_provider_access.alpha_vantage_income_statement",
            "provider": "alpha_vantage",
            "duration_ms": 12.5,
            "retry_count": 1,
        }
    ]
    assert _component_observations(observations) == observations
    assert "secret" not in str(observations)


def test_access_observations_skip_unrequested_failed_and_malformed_calls():
    evidence = {
        "alpha_vantage_income_statement": {"status": "NOT_CONFIGURED"},
        "fmp_as_reported_financials": {"status": "ERROR"},
        "fmp_analyst_estimates": _complete_access(float("nan"), 0),
        "fmp_ratings_snapshot": _complete_access(0.01, True),
        "fmp_price_target_consensus": _complete_access(-1, 0),
        "massive_market_history": _complete_access(0.01, -1),
    }

    assert SupplementalProviderEvidenceService.access_observations(evidence) == []


def test_access_observation_order_follows_declared_evidence_fields():
    evidence = {
        "fred_ten_year_treasury": _complete_access(0.008, 0),
        "fmp_analyst_estimates": _complete_access(0.003, 0),
        "alpha_vantage_income_statement": _complete_access(0.001, 0),
    }

    observations = SupplementalProviderEvidenceService.access_observations(evidence)

    assert [item["provider"] for item in observations] == [
        "alpha_vantage",
        "financial_modeling_prep",
        "fred",
    ]


def _error_access(elapsed_seconds, retry_count, **extra):
    return {
        "status": "ERROR",
        "error_type": "OptionalProviderError",
        "error": "Provider request failed or was rejected.",
        "provider_access": {
            "provider": "Alpha Vantage",
            "attempts": 1,
            "retry_count": retry_count,
            "retried_status_codes": [],
            "total_wait_seconds": 0.0,
            "elapsed_seconds": elapsed_seconds,
            "circuit_state": "CLOSED",
            "request_url_recorded": False,
            "request_parameters_recorded": False,
            "request_headers_recorded": False,
            "response_body_recorded": False,
            **extra,
        },
    }


def test_safely_copies_only_whitelisted_access_fields_from_provider_errors():
    error = OptionalProviderError(
        "Alpha Vantage rejected the request or quota.",
        access={
            "provider": "Alpha Vantage",
            "attempts": 2,
            "retry_count": 1,
            "retried_status_codes": [503],
            "total_wait_seconds": 0.5,
            "elapsed_seconds": 1.25,
            "circuit_state": "CLOSED",
        },
    )

    def failing():
        raise error

    result = SupplementalProviderEvidenceService.safely(failing)

    assert result["status"] == "ERROR"
    assert result["error"] == "Provider request failed or was rejected."
    assert set(result["provider_access"]) == {
        "provider",
        "attempts",
        "retry_count",
        "retried_status_codes",
        "total_wait_seconds",
        "elapsed_seconds",
        "circuit_state",
        "request_url_recorded",
        "request_parameters_recorded",
        "request_headers_recorded",
        "response_body_recorded",
    }
    assert result["provider_access"]["retry_count"] == 1


def test_safely_ignores_arbitrary_exception_attributes():
    class Hostile(Exception):
        access = {
            "provider": "Impostor",
            "attempts": 1,
            "retry_count": 0,
            "retried_status_codes": [],
            "total_wait_seconds": 0.0,
            "elapsed_seconds": 0.01,
            "circuit_state": "CLOSED",
        }

    def failing():
        raise Hostile("boom")

    result = SupplementalProviderEvidenceService.safely(failing)

    assert "provider_access" not in result
    assert "secret" not in str(result)

    def plain():
        raise RuntimeError("https://x.invalid?apikey=secret")

    assert "provider_access" not in SupplementalProviderEvidenceService.safely(plain)


def test_failed_observations_use_the_error_namespace_and_declared_order():
    evidence = {
        "fred_ten_year_treasury": _error_access(0.004, 1),
        "fmp_analyst_estimates": _complete_access(0.003, 0),
        "alpha_vantage_income_statement": _error_access(0.001, 0),
    }

    observations = SupplementalProviderEvidenceService.access_observations(evidence)

    assert [(item["component"], item["duration_ms"]) for item in observations] == [
        ("supplemental_provider_access_error.alpha_vantage_income_statement", 1.0),
        ("supplemental_provider_access.fmp_analyst_estimates", 3.0),
        ("supplemental_provider_access_error.fred_ten_year_treasury", 4.0),
    ]
    assert [item["provider"] for item in observations] == [
        "alpha_vantage",
        "financial_modeling_prep",
        "fred",
    ]
    assert _component_observations(observations) == observations


def test_failed_observations_skip_unrequested_and_malformed_measurements():
    evidence = {
        "alpha_vantage_income_statement": {"status": "NOT_CONFIGURED"},
        "fmp_as_reported_financials": {"status": "ERROR"},
        "fmp_analyst_estimates": _error_access(float("nan"), 0),
        "fmp_ratings_snapshot": _error_access(0.01, -1),
        "fmp_price_target_consensus": _error_access(-1, 0),
        "massive_market_history": {"status": "ERROR", "provider_access": "unavailable"},
    }

    assert SupplementalProviderEvidenceService.access_observations(evidence) == []


if __name__ == "__main__":
    test_supplementary_data_is_preserved_as_evidence_not_a_decision_override()
    print("SUPPLEMENTAL PROVIDER EVIDENCE SERVICE TESTS PASSED")
