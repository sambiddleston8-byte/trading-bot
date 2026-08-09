from datetime import datetime, timezone

from core.research.supplemental_provider_evidence_service import (
    SupplementalProviderEvidenceService,
)


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


if __name__ == "__main__":
    test_supplementary_data_is_preserved_as_evidence_not_a_decision_override()
    print("SUPPLEMENTAL PROVIDER EVIDENCE SERVICE TESTS PASSED")
