from datetime import datetime, timezone

from core.research.supplemental_provider_evidence_service import (
    SupplementalProviderEvidenceService,
)
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


if __name__ == "__main__":
    test_supplementary_data_is_preserved_as_evidence_not_a_decision_override()
    print("SUPPLEMENTAL PROVIDER EVIDENCE SERVICE TESTS PASSED")
