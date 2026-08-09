from core.data_sources.sec_source import SECSource


def metric(value, tag, end=None):
    return {
        "value": value,
        "tag": tag,
        "end": end,
    }


def parse_with_debt_values(
    current_debt,
    noncurrent_debt,
    generic_long_term_debt,
    short_term_borrowings=None,
):
    source = SECSource()
    balance_values = {
        "LongTermDebtCurrent": current_debt,
        "LongTermDebtNoncurrent": noncurrent_debt,
        "LongTermDebt": generic_long_term_debt,
        "ShortTermBorrowings": short_term_borrowings,
    }

    source.latest_annual_flow = lambda facts, tags: None

    def latest_balance_value(facts, tags):
        for tag in tags:
            value = balance_values.get(tag)
            if value is not None:
                return metric(value, tag)
        return None

    source.latest_balance_value = latest_balance_value

    return source.parse(
        {
            "entityName": "Test Company",
            "cik": 1,
        }
    )


def test_sec_total_debt_uses_current_and_noncurrent_components():
    profile = parse_with_debt_values(
        current_debt=10.0,
        noncurrent_debt=90.0,
        generic_long_term_debt=90.0,
    )

    debt = profile["balance_sheet"]["total_debt"]

    assert debt["value"] == 100.0
    assert debt["source_tag"] == (
        "LongTermDebtCurrent+LongTermDebtNoncurrent"
    )


def test_sec_total_debt_falls_back_to_generic_fact_when_components_missing():
    profile = parse_with_debt_values(
        current_debt=None,
        noncurrent_debt=None,
        generic_long_term_debt=90.0,
    )

    debt = profile["balance_sheet"]["total_debt"]

    assert debt["value"] == 90.0
    assert debt["source_tag"] == "LongTermDebt"


def test_sec_total_debt_uses_short_term_borrowings_when_current_debt_tag_is_absent():
    profile = parse_with_debt_values(
        current_debt=None,
        noncurrent_debt=None,
        generic_long_term_debt=90.0,
        short_term_borrowings=10.0,
    )

    debt = profile["balance_sheet"]["total_debt"]

    assert debt["value"] == 100.0
    assert debt["source_tag"] == (
        "ShortTermBorrowings+LongTermDebt"
    )


def test_stale_current_debt_component_is_not_combined_with_current_noncurrent_debt():
    source = SECSource()
    source.latest_annual_flow = lambda facts, tags: None

    values = {
        "LongTermDebtCurrent": metric(
            10.0,
            "LongTermDebtCurrent",
            end="2024-12-31",
        ),
        "LongTermDebtNoncurrent": metric(
            90.0,
            "LongTermDebtNoncurrent",
            end="2025-12-31",
        ),
    }

    def latest_balance_value(facts, tags):
        for tag in tags:
            if tag in values:
                return values[tag]
        return None

    source.latest_balance_value = latest_balance_value
    profile = source.parse({"entityName": "Test", "cik": 1})

    debt = profile["balance_sheet"]["total_debt"]

    assert debt["value"] == 90.0
    assert debt["source_tag"] == "LongTermDebtNoncurrent"


def test_period_matched_flow_does_not_use_a_stale_taxonomy_fact():
    source = SECSource()

    values = {
        "GrossProfit": [
            {
                "value": 5.0,
                "start": "2024-01-01",
                "end": "2024-12-31",
                "filed": "2025-02-01",
                "fiscal_period": "FY",
            },
        ],
    }

    source.annual_values = lambda facts, tag: values.get(tag, [])

    assert (
        source.annual_flow_for_period(
            {},
            ["GrossProfit"],
            "2025-12-31",
        )
        is None
    )


if __name__ == "__main__":
    test_sec_total_debt_uses_current_and_noncurrent_components()
    test_sec_total_debt_falls_back_to_generic_fact_when_components_missing()
    test_sec_total_debt_uses_short_term_borrowings_when_current_debt_tag_is_absent()
    test_stale_current_debt_component_is_not_combined_with_current_noncurrent_debt()
    test_period_matched_flow_does_not_use_a_stale_taxonomy_fact()

    print("SEC DEBT SOURCE TESTS PASSED")
