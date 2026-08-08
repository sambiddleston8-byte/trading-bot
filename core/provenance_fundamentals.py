from __future__ import annotations

from core.provenance import ProvenanceBuilder


class FundamentalProvenance:

    def build(
        self,
        financials,
        profitability,
        growth,
        balance_sheet,
        analyst_consensus,
        validation,
    ):

        builder = ProvenanceBuilder()

        validation_confidence = (
            validation
            .get(
                "overall_confidence"
            )
            if isinstance(
                validation,
                dict,
            )
            else None
        )

        # ====================================================
        # REPORTED FINANCIALS
        # ====================================================

        builder.add_reported(
            name="Revenue",
            value=financials.get(
                "revenue"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Revenue",
            period="Latest validated period",
            confidence=(
                validation_confidence
                or "HIGH"
            ),
        )

        builder.add_reported(
            name="Net Income",
            value=financials.get(
                "net_income"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Net Income",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Operating Income",
            value=financials.get(
                "operating_income"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Operating Income",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Operating Cash Flow",
            value=financials.get(
                "operating_cash_flow"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Operating Cash Flow",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Free Cash Flow",
            value=financials.get(
                "free_cash_flow"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Operating Cash Flow - Capital Expenditure",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Cash",
            value=financials.get(
                "cash"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Cash and Cash Equivalents",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Debt",
            value=financials.get(
                "debt"
            ),
            source_name="SEC EDGAR",
            source_type="SEC",
            field="Debt",
            period="Latest reported period",
        )

        builder.add_reported(
            name="Net Debt",
            value=financials.get(
                "net_debt"
            ),
            source_name="Trading Bot",
            source_type="CALCULATION",
            field="Debt - Cash",
            period="Latest validated period",
        )

        # ====================================================
        # PROFITABILITY
        # ====================================================

        builder.add_calculated(
            name="Gross Margin",
            value=profitability.get(
                "gross_margin"
            ),
            method="Gross Profit / Revenue",
            inputs={
                "gross_profit": financials.get(
                    "gross_profit"
                ),
                "revenue": financials.get(
                    "revenue"
                ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="Operating Margin",
            value=profitability.get(
                "operating_margin"
            ),
            method="Operating Income / Revenue",
            inputs={
                "operating_income":
                    financials.get(
                        "operating_income"
                    ),
                "revenue":
                    financials.get(
                        "revenue"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="Net Margin",
            value=profitability.get(
                "net_margin"
            ),
            method="Net Income / Revenue",
            inputs={
                "net_income":
                    financials.get(
                        "net_income"
                    ),
                "revenue":
                    financials.get(
                        "revenue"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="FCF Margin",
            value=profitability.get(
                "fcf_margin"
            ),
            method="Free Cash Flow / Revenue",
            inputs={
                "free_cash_flow":
                    financials.get(
                        "free_cash_flow"
                    ),
                "revenue":
                    financials.get(
                        "revenue"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="ROE",
            value=profitability.get(
                "roe"
            ),
            method="Net Income / Equity",
            inputs={
                "net_income":
                    financials.get(
                        "net_income"
                    ),
                "equity":
                    financials.get(
                        "equity"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="ROIC",
            value=profitability.get(
                "roic"
            ),
            method=(
                "Operating Income / "
                "(Equity + Debt - Cash)"
            ),
            inputs={
                "operating_income":
                    financials.get(
                        "operating_income"
                    ),
                "equity":
                    financials.get(
                        "equity"
                    ),
                "debt":
                    financials.get(
                        "debt"
                    ),
                "cash":
                    financials.get(
                        "cash"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        # ====================================================
        # GROWTH
        # ====================================================

        builder.add_calculated(
            name="Historical Revenue CAGR",
            value=growth.get(
                "historical_revenue_cagr"
            ),
            method=(
                "Compound annual growth rate "
                "of historical reported revenue"
            ),
            inputs={
                "historical_revenue":
                    growth.get(
                        "historical_revenue"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="Historical FCF CAGR",
            value=growth.get(
                "historical_fcf_cagr"
            ),
            method=(
                "Compound annual growth rate "
                "of historical free cash flow"
            ),
            inputs={
                "historical_free_cash_flow":
                    growth.get(
                        "historical_fcf"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        # ====================================================
        # BALANCE SHEET
        # ====================================================

        builder.add_calculated(
            name="Debt / FCF",
            value=balance_sheet.get(
                "debt_to_fcf"
            ),
            method="Debt / Free Cash Flow",
            inputs={
                "debt":
                    financials.get(
                        "debt"
                    ),
                "free_cash_flow":
                    financials.get(
                        "free_cash_flow"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        builder.add_calculated(
            name="Net Debt / FCF",
            value=balance_sheet.get(
                "net_debt_to_fcf"
            ),
            method="Net Debt / Free Cash Flow",
            inputs={
                "net_debt":
                    financials.get(
                        "net_debt"
                    ),
                "free_cash_flow":
                    financials.get(
                        "free_cash_flow"
                    ),
            },
            sources=[
                "SEC EDGAR"
            ],
        )

        # ====================================================
        # ANALYST CONSENSUS
        # ====================================================

        builder.add_reported(
            name="Forward Revenue Growth",
            value=analyst_consensus.get(
                "forward_revenue_growth"
            ),
            source_name="Yahoo Finance",
            source_type="ANALYST_CONSENSUS",
            field="Revenue Growth",
            period="Next fiscal year",
        )

        builder.add_reported(
            name="Forward EPS Growth",
            value=analyst_consensus.get(
                "forward_eps_growth"
            ),
            source_name="Yahoo Finance",
            source_type="ANALYST_CONSENSUS",
            field="EPS Growth",
            period="Next fiscal year",
        )

        builder.add_reported(
            name="Current Year EPS",
            value=analyst_consensus.get(
                "current_year_eps"
            ),
            source_name="Yahoo Finance",
            source_type="ANALYST_CONSENSUS",
            field="EPS Estimate",
            period="Current fiscal year",
        )

        builder.add_reported(
            name="Next Year EPS",
            value=analyst_consensus.get(
                "next_year_eps"
            ),
            source_name="Yahoo Finance",
            source_type="ANALYST_CONSENSUS",
            field="EPS Estimate",
            period="Next fiscal year",
        )

        return builder.build()
