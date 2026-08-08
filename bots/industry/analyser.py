from core.company_context import CompanyContext


class IndustryAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        sector = info.get("sector")
        industry = info.get("industry")

        strengths = []
        weaknesses = []

        score = 50

        # --------------------------------
        # Sector / Industry Classification
        # --------------------------------

        if sector:
            strengths.append(
                f"Sector: {sector}"
            )

        if industry:
            strengths.append(
                f"Industry: {industry}"
            )

        # --------------------------------
        # Company Scale
        # --------------------------------

        market_cap = info.get(
            "marketCap"
        )

        if market_cap is not None:

            if market_cap >= 500_000_000_000:

                score += 15

                strengths.append(
                    "The company has exceptional scale within its market."
                )

            elif market_cap >= 100_000_000_000:

                score += 10

                strengths.append(
                    "The company has significant scale."
                )

            elif market_cap >= 10_000_000_000:

                score += 5

        # --------------------------------
        # Revenue Growth
        # --------------------------------

        revenue_growth = info.get(
            "revenueGrowth"
        )

        if revenue_growth is not None:

            if revenue_growth >= 0.20:

                score += 15

                strengths.append(
                    "Revenue growth is strong."
                )

            elif revenue_growth >= 0.10:

                score += 8

            elif revenue_growth < 0:

                score -= 15

                weaknesses.append(
                    "Revenue is declining."
                )

        # --------------------------------
        # Profitability
        # --------------------------------

        operating_margin = info.get(
            "operatingMargins"
        )

        if operating_margin is not None:

            if operating_margin >= 0.25:

                score += 10

                strengths.append(
                    "Operating profitability is strong."
                )

            elif operating_margin < 0.10:

                score -= 10

                weaknesses.append(
                    "Operating profitability is relatively weak."
                )

        # --------------------------------
        # Competitive Position
        # --------------------------------

        gross_margin = info.get(
            "grossMargins"
        )

        if gross_margin is not None:

            if gross_margin >= 0.50:

                score += 5

                strengths.append(
                    "High gross margins suggest strong economics."
                )

            elif gross_margin < 0.20:

                score -= 5

                weaknesses.append(
                    "Low gross margins may indicate intense competition."
                )

        # --------------------------------
        # Final Score
        # --------------------------------

        score = max(
            0,
            min(
                score,
                100,
            ),
        )

        # --------------------------------
        # Industry Assessment
        # --------------------------------

        if score >= 80:

            assessment = "EXCELLENT"

        elif score >= 65:

            assessment = "STRONG"

        elif score >= 50:

            assessment = "NEUTRAL"

        elif score >= 35:

            assessment = "WEAK"

        else:

            assessment = "POOR"

        # --------------------------------
        # Summary
        # --------------------------------

        summary = (
            f"The company operates in the "
            f"{industry or 'unknown'} industry "
            f"within the {sector or 'unknown'} sector. "
            f"The current industry/competitive "
            f"assessment is {assessment}."
        )

        return {

            "Industry Score":
                score,

            "Sector":
                sector,

            "Industry":
                industry,

            "Assessment":
                assessment,

            "Strengths":
                strengths,

            "Weaknesses":
                weaknesses,

            "Summary":
                summary,

        }