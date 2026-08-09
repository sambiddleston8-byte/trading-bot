from core.research.research_failure_diagnostics_engine import ResearchFailureDiagnosticsEngine


def test_diagnostics_explains_blockers_and_data_source_actions():
    result = {
        "ticker": "TEST",
        "status": "COMPLETE",
        "core": {
            "decision": {"valuation": {"current_price": 100, "base_intrinsic_value": 120, "expected_return": 0.2}},
            "valuation_quality": {"assessment": "FAIL"},
        },
        "research": {
            "thesis_challenge": {"thesis_survives": False, "material_negative": 3},
            "market_signals": {"technical": {}, "risk": {}},
            "specialist_research": {"status": "PARTIAL"},
        },
        "audit": {"status": "FAIL"},
    }
    diagnosis = ResearchFailureDiagnosticsEngine.analyse(result)
    assert diagnosis["blocker_count"] >= 2
    assert any(issue["component"] == "valuation" for issue in diagnosis["issues"])
    assert all(issue["recommended_sources"] for issue in diagnosis["issues"])


if __name__ == "__main__":
    test_diagnostics_explains_blockers_and_data_source_actions()
    print("RESEARCH FAILURE DIAGNOSTICS TESTS PASSED")
