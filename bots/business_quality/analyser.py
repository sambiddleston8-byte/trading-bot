from core.company_context import CompanyContext


class BusinessQualityAnalyser:

    def analyse(self, context: CompanyContext):

        financials = context.financials
        balance_sheet = context.balance_sheet
        cashflow = context.cashflow

        strengths = []
        weaknesses = []

        growth_score = 50.0
        profitability_score = 50.0
        balance_sheet_score = 50.0

        # --------------------------------
        # Revenue Growth
        # --------------------------------

        if financials is not None and "Total Revenue" in financials.index:

            revenue = financials.loc["Total Revenue"].dropna()

            if len(revenue) >= 2:

                oldest = revenue.iloc[-1]
                newest = revenue.iloc[0]

                if oldest > 0:

                    growth = (
                        (newest / oldest) ** (1 / (len(revenue) - 1)) - 1
                    ) * 100

                    if growth >= 20:
                        growth_score = 100
                        strengths.append(
                            "Excellent long-term revenue growth"
                        )

                    elif growth >= 10:
                        growth_score = 80
                        strengths.append(
                            "Strong long-term revenue growth"
                        )

                    elif growth >= 5:
                        growth_score = 65

                    else:
                        growth_score = 40
                        weaknesses.append(
                            "Weak long-term revenue growth"
                        )

        # --------------------------------
        # Profitability
        # --------------------------------

        info = context.info

        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        roic = info.get("returnOnInvestedCapital")

        profitability_values = [
            value
            for value in [roe, roa, roic]
            if value is not None
        ]

        if profitability_values:

            profitability_score = min(
                100,
                max(
                    0,
                    sum(profitability_values)
                    / len(profitability_values)
                    * 200,
                ),
            )

            if profitability_score >= 80:

                strengths.append(
                    "High profitability"
                )

            elif profitability_score < 50:

                weaknesses.append(
                    "Weak profitability"
                )

        # --------------------------------
        # Balance Sheet
        # --------------------------------

        debt = None
        cash = None

        if balance_sheet is not None:

            if "Total Debt" in balance_sheet.index:

                debt = balance_sheet.loc["Total Debt"].iloc[0]

            if "Cash And Cash Equivalents" in balance_sheet.index:

                cash = balance_sheet.loc[
                    "Cash And Cash Equivalents"
                ].iloc[0]

        if debt is not None and cash is not None:

            if cash > debt * 2:

                balance_sheet_score = 100

                strengths.append(
                    "Very strong net cash position"
                )

            elif cash > debt:

                balance_sheet_score = 80

                strengths.append(
                    "Strong balance sheet"
                )

            elif debt > cash * 2:

                balance_sheet_score = 30

                weaknesses.append(
                    "High debt relative to cash"
                )

            else:

                balance_sheet_score = 60

        # --------------------------------
        # Overall Business Quality
        # --------------------------------

        quality = round(
            (
                growth_score
                + profitability_score
                + balance_sheet_score
            ) / 3,
            1,
        )

        # --------------------------------
        # Summary
        # --------------------------------

        if quality >= 85:

            summary = (
                "The business demonstrates exceptional financial "
                "quality with strong growth, profitability and "
                "balance sheet metrics."
            )

        elif quality >= 70:

            summary = (
                "The business demonstrates strong financial quality "
                "with generally healthy fundamentals."
            )

        elif quality >= 55:

            summary = (
                "The business demonstrates mixed financial quality "
                "with both strengths and weaknesses."
            )

        else:

            summary = (
                "The business demonstrates relatively weak "
                "financial quality."
            )

        return {

            "Growth": round(growth_score, 1),

            "Profitability": round(
                profitability_score,
                1,
            ),

            "Balance Sheet": round(
                balance_sheet_score,
                1,
            ),

            "Business Quality": quality,

            "Confidence": 85,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

        }