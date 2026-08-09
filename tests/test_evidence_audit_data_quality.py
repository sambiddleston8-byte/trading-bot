from core.research.evidence_audit_engine import (
    EvidenceAuditEngine,
)


def test_small_discrepancy_is_a_warning():

    findings = (
        EvidenceAuditEngine
        .audit_data_quality(
            {
                "data_quality": {
                    "unresolved_discrepancies": 1,
                    "source_conflicts": {
                        "total_debt": {
                            "status":
                                "DISCREPANCY",
                            "difference_percent":
                                0.03,
                            "first": {
                                "source": "Yahoo",
                                "value": 103.0,
                            },
                            "second": {
                                "source": "SEC",
                                "value": 100.0,
                            },
                            "selected": 100.0,
                            "selected_source": "SEC",
                        },
                    },
                },
            }
        )
    )

    assert len(findings) == 1
    assert findings[0]["severity"] == "MEDIUM"
    assert findings[0]["field"] == "total_debt"


def test_material_discrepancy_fails_audit():

    result = (
        EvidenceAuditEngine
        .audit(
            {
                "scores": {
                    "fundamental_quality": 80,
                },
                "validation": {
                    "overall_confidence": "HIGH",
                },
                "provenance": {
                    "revenue": "SEC",
                },
                "valuation": {
                    "current_price": 100.0,
                    "base_intrinsic_value": 120.0,
                    "expected_return": 0.20,
                },
                "catalysts": [],
                "thesis_challenge": {
                    "challenge_count": 5,
                },
                "data_quality": {
                    "unresolved_discrepancies": 1,
                    "source_conflicts": {
                        "total_debt": {
                            "status":
                                "DISCREPANCY",
                            "difference_percent":
                                0.25,
                            "first": {
                                "source": "Yahoo",
                                "value": 125.0,
                            },
                            "second": {
                                "source": "SEC",
                                "value": 100.0,
                            },
                        },
                    },
                },
                "decision": "WATCHLIST",
            }
        )
    )

    assert result["status"] == "FAIL"
    assert result["critical"] == 1


def test_percent_point_format_is_normalized():

    findings = (
        EvidenceAuditEngine
        .audit_data_quality(
            {
                "data_quality": {
                    "unresolved_discrepancies": 1,
                    "source_conflicts": {
                        "total_debt": {
                            "status":
                                "DISCREPANCY",
                            "difference_percent":
                                -9.06,
                            "first": {
                                "source": "Yahoo",
                            },
                            "second": {
                                "source": "SEC",
                            },
                        },
                    },
                },
            }
        )
    )

    assert findings[0]["severity"] == "HIGH"

    assert abs(
        findings[0]["evidence"]
        ["difference_percent"]
        + 0.0906
    ) < 0.000001


if __name__ == "__main__":

    test_small_discrepancy_is_a_warning()
    test_material_discrepancy_fails_audit()
    test_percent_point_format_is_normalized()

    print(
        "DATA-QUALITY AUDIT TESTS PASSED"
    )
