import json

import pytest

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.research.valuation_quality_engine import ValuationQualityEngine


def assumption_evidence():
    values = {
        "risk_free_rate": 0.04,
        "equity_risk_premium": 0.05,
        "beta": 1.0,
        "pre_tax_cost_of_debt": 0.05,
        "marginal_tax_rate": 0.20,
        "market_value_equity_weight": 0.75,
        "market_value_debt_weight": 0.25,
        "terminal_growth_rate": 0.025,
    }
    return {
        name: {
            "value": value,
            "effective_at": "2025-01-01T00:00:00+00:00",
            "publicly_available_at": "2025-01-02T00:00:00+00:00",
            "retrieved_at": "2025-01-03T00:00:00+00:00",
            "source_uri": f"https://evidence.example/{name}",
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.{name}",
        }
        for index, (name, value) in enumerate(values.items(), start=1)
    }


def authenticate_assumptions(tmp_path, valuation):
    ledger = AuthenticatedSourceContentLedger(
        tmp_path / "sources.jsonl", tmp_path / "blobs"
    )
    for name, item in valuation["DCF Assumption Evidence"].items():
        payload = json.dumps({"field": name, "value": item["value"]}).encode("utf-8")
        record = ledger.ingest(
            source_uri=item["source_uri"], payload=payload, media_type="application/json",
            publicly_available_at=item["publicly_available_at"],
            retrieved_at=item["retrieved_at"], recorded_at="2025-01-04T00:00:00+00:00",
            source_locator=item["source_locator"],
        )
        item["source_input_sha256"] = record["source_input_sha256"]
        item["content_evidence_id"] = record["content_evidence_id"]
    return ledger


def valid_inputs():
    return (
        {
            "Current Price": 100,
            "Intrinsic Value": {"Base": 130},
            "Expected Return": {"Base": 0.30},
            "Forecast Validation": {"Overall Confidence": "HIGH"},
            "Terminal Value Contribution": 0.70,
            "Valuation Data Cutoff": "2025-01-04T00:00:00+00:00",
            "WACC": 0.0775,
            "Terminal Growth": 0.025,
            "DCF Assumption Evidence": assumption_evidence(),
            "DCF Sensitivity Matrix": {
                "wacc_values": [0.0675, 0.0775, 0.0875],
                "terminal_growth_values": [0.015, 0.025, 0.035],
            },
        },
        {"confidence": {"estimate_consistency": "HIGH"}},
    )


def test_schema_complete_dcf_still_fails_until_source_content_is_authenticated():
    valuation, decision = valid_inputs()
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False
    assert any("authenticated" in item for item in result["failures"])


def test_authenticated_dcf_source_bytes_clear_assumption_gate(tmp_path):
    valuation, decision = valid_inputs()
    sources = authenticate_assumptions(tmp_path, valuation)
    result = ValuationQualityEngine.assess(valuation, decision, sources)
    assert result["assessment"] == "PASS"
    assert result["portfolio_expected_return_eligible"] is True
    assert result["dcf_assumption_evidence"]["source_content_authentication_status"] == (
        "ALL_SOURCE_BYTES_HASH_VERIFIED"
    )
    assert result["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )


def test_wrong_content_id_or_tampered_blob_keeps_dcf_blocked(tmp_path):
    valuation, decision = valid_inputs()
    sources = authenticate_assumptions(tmp_path, valuation)
    valuation["DCF Assumption Evidence"]["beta"]["content_evidence_id"] = "SRC-WRONG"
    result = ValuationQualityEngine.assess(valuation, decision, sources)
    assert result["assessment"] == "FAIL"
    valuation, decision = valid_inputs()
    sources = authenticate_assumptions(tmp_path / "tampered", valuation)
    first = sources.verify()[0]
    blob = sources.blob_directory / first["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"changed")
    result = ValuationQualityEngine.assess(valuation, decision, sources)
    assert result["assessment"] == "FAIL"


def test_unvalidated_forecast_fails_quality_check():
    valuation, decision = valid_inputs()
    decision["confidence"]["estimate_consistency"] = "INSUFFICIENT_DATA"
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["failures"]
    assert result["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )


def test_terminal_dominance_is_visible_for_review():
    valuation, decision = valid_inputs()
    valuation["Terminal Value Contribution"] = 0.86
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["warnings"]


def test_coarse_defaults_are_review_only_and_not_portfolio_eligible():
    valuation, decision = valid_inputs()
    valuation.pop("DCF Assumption Evidence")
    valuation.pop("DCF Sensitivity Matrix")
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False
    assert result["expected_return_status"] == (
        "SOURCE_CONTENT_AUTHENTICATION_REQUIRED_NOT_PORTFOLIO_ELIGIBLE"
    )


def test_reported_wacc_must_equal_evidence_derived_wacc():
    valuation, decision = valid_inputs()
    valuation["WACC"] = 0.09
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert any("WACC" in item for item in result["failures"])


def test_assumption_retrieved_after_valuation_cutoff_is_not_eligible():
    valuation, decision = valid_inputs()
    valuation["DCF Assumption Evidence"]["beta"]["retrieved_at"] = (
        "2025-01-05T00:00:00+00:00"
    )
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False


def test_rate_sanity_and_substantive_sensitivity_grid_are_required():
    valuation, decision = valid_inputs()
    valuation["DCF Assumption Evidence"]["beta"]["value"] = 50
    result = ValuationQualityEngine.assess(valuation, decision)
    assert any("beta" in item for item in result["failures"])
    valuation, decision = valid_inputs()
    valuation["DCF Sensitivity Matrix"] = {
        "wacc_values": [0.0775], "terminal_growth_values": [0.025]
    }
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["sensitivity_matrix_complete"] is False
    assert any("three" in item for item in result["failures"])


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_authenticated_assumption_cannot_clear_dcf_gate(tmp_path, invalid):
    valuation, decision = valid_inputs()
    valuation["DCF Assumption Evidence"]["beta"]["value"] = invalid
    sources = authenticate_assumptions(tmp_path, valuation)
    result = ValuationQualityEngine.assess(valuation, decision, sources)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False
    assert result["dcf_assumption_evidence_complete"] is False
    assert any("beta" in item for item in result["failures"])


@pytest.mark.parametrize(
    "path,invalid",
    [
        (("Current Price",), float("nan")),
        (("Intrinsic Value", "Base"), float("inf")),
        (("Expected Return", "Base"), float("-inf")),
        (("WACC",), float("nan")),
        (("Terminal Growth",), float("inf")),
    ],
)
def test_nonfinite_valuation_outputs_fail_closed(path, invalid):
    valuation, decision = valid_inputs()
    target = valuation
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_sensitivity_value_cannot_clear_grid(invalid):
    valuation, decision = valid_inputs()
    valuation["DCF Sensitivity Matrix"]["wacc_values"][1] = invalid
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["sensitivity_matrix_complete"] is False
    assert any("decimal rates" in item for item in result["failures"])


if __name__ == "__main__":
    test_schema_complete_dcf_still_fails_until_source_content_is_authenticated()
    test_unvalidated_forecast_fails_quality_check()
    test_terminal_dominance_is_visible_for_review()
    print("VALUATION QUALITY TESTS PASSED")
