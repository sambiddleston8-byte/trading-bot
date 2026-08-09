from core.validation.validated_financial_data import (
    ValidatedFinancialData,
)


def test_debt_lease_definition_difference_is_resolved():

    yahoo = {
        "financials": {"latest_annual": {}},
        "balance_sheet": {
            "total_debt": {"value": 110.0},
            "long_term_debt": 90.0,
            "current_debt": 10.0,
            "capital_lease_obligations": 10.0,
        },
        "market": {},
    }

    sec = {
        "financials": {},
        "balance_sheet": {
            "total_debt": {"value": 100.0},
        },
    }

    quality = (
        ValidatedFinancialData(yahoo, sec)
        ._data_quality()
    )

    debt = quality["source_conflicts"]["total_debt"]

    assert debt["status"] == "RESOLVED_DEFINITION_DIFFERENCE"
    assert debt["reconciliation_status"] == "RESOLVED_DEFINITION_DIFFERENCE"
    assert debt["difference_percent"] == 0.0
    assert quality["unresolved_discrepancies"] == 0


def test_matching_total_debt_is_not_reported_as_a_discrepancy():
    yahoo = {
        "financials": {"latest_annual": {}},
        "balance_sheet": {
            "total_debt": {"value": 100.0},
            "long_term_debt": 90.0,
            "current_debt": 10.0,
        },
        "market": {},
    }

    sec = {
        "financials": {},
        "balance_sheet": {
            "total_debt": {"value": 100.0},
        },
    }

    quality = (
        ValidatedFinancialData(yahoo, sec)
        ._data_quality()
    )

    debt = quality["source_conflicts"]["total_debt"]

    assert debt["status"] == "RESOLVED_DEFINITION_DIFFERENCE"
    assert debt["reconciliation_status"] == "RESOLVED_DEFINITION_DIFFERENCE"
    assert debt["difference_percent"] == 0.0
    assert quality["unresolved_discrepancies"] == 0


def test_leases_are_reconciled_when_yahoo_omits_current_debt_component():
    yahoo = {
        "financials": {"latest_annual": {}},
        "balance_sheet": {
            "total_debt": {"value": 153.0},
            "long_term_debt": 66.0,
            "current_debt": None,
            "capital_lease_obligations": 87.0,
        },
        "market": {},
    }

    sec = {
        "financials": {},
        "balance_sheet": {
            "total_debt": {"value": 68.7},
            "noncurrent_debt": {"value": 66.0},
            "current_debt": {"value": 2.7},
        },
    }

    quality = ValidatedFinancialData(yahoo, sec)._data_quality()
    debt = quality["source_conflicts"]["total_debt"]

    assert debt["status"] == "RESOLVED_DEFINITION_DIFFERENCE"
    assert debt["selected"] == 68.7
    assert quality["unresolved_discrepancies"] == 0


if __name__ == "__main__":

    test_debt_lease_definition_difference_is_resolved()
    test_matching_total_debt_is_not_reported_as_a_discrepancy()
    test_leases_are_reconciled_when_yahoo_omits_current_debt_component()

    print("DEBT RECONCILIATION TESTS PASSED")
