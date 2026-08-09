from __future__ import annotations

from core.data_sources.yahoo_source import YahooSource
from core.valuation_engine import ValuationEngine


class Statement:
    """Minimal statement double matching the pandas access used by sources."""

    def __init__(self, values):
        self.index = list(values)
        self.columns = ["latest"]
        self.loc = values


def statements():
    income = Statement({"Total Revenue": {"latest": 1_000.0}})
    cash_flow = Statement(
        {
            "Operating Cash Flow": {"latest": 120.0},
            "Capital Expenditure": {"latest": -30.0},
            "Free Cash Flow": {"latest": 95.0},
        }
    )
    return income, cash_flow


def test_reported_free_cash_flow_is_preferred_to_component_reconstruction():
    income, cash_flow = statements()
    yahoo = YahooSource.__new__(YahooSource)

    source_result = yahoo.get_annual_financials(income, cash_flow)
    valuation_result = ValuationEngine().get_annual_financials(income, cash_flow)

    assert source_result[0]["free_cash_flow"] == 95.0
    assert valuation_result[0]["Free Cash Flow"] == 95.0


if __name__ == "__main__":
    test_reported_free_cash_flow_is_preferred_to_component_reconstruction()
    print("FREE CASH FLOW SOURCE TESTS PASSED")
