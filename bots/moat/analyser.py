from core.company_context import CompanyContext


class MoatAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        strengths = []
        weaknesses = []

        score = 50

        gross_margin = info.get(
            "grossMargins"
        )

        operating_margin = info.get(
            "operatingMargins"
        )

        roe = info.get(
            "returnOnEquity"
        )

        roic = info.get(
            "returnOnInvestedCapital"
        )

        # --------------------------------
        # Gross Margin
        # --------------------------------

        if gross_margin is not None:

            if gross_margin >= 0.60:

                score += 15

                strengths.append(
                    "Very strong gross margins suggest significant pricing power or intellectual-property advantages."
                )

            elif gross_margin >= 0.40:

                score += 8

                strengths.append(
                    "Healthy gross margins."
                )

            elif gross_margin < 0.20:

                score -= 8

                weaknesses.append(
                    "Low gross margins may indicate limited pricing power."
                )

        # --------------------------------
        # Operating Margin
        # --------------------------------

        if operating_margin is not None:

            if operating_margin >= 0.25:

                score += 10

                strengths.append(
                    "Strong operating margins indicate attractive economics."
                )

            elif operating_margin < 0.10:

                score -= 8

                weaknesses.append(
                    "Low operating margins may indicate competitive pressure."
                )

        # --------------------------------
        # ROIC
        # --------------------------------

        if roic is not None:

            if roic >= 0.20:

                score += 10

                strengths.append(
                    "Exceptional returns on invested capital support the existence of a durable competitive advantage."
                )

            elif roic >= 0.12:

                score += 5

            elif roic < 0.08:

                score -= 8

                weaknesses.append(
                    "Low returns on invested capital weaken the evidence for a durable moat."
                )

        # --------------------------------
        # ROE
        # --------------------------------

        if roe is not None:

            if roe >= 0.25:

                score += 5

                strengths.append(
                    "High return on equity."
                )

            elif roe < 0.08:

                score -= 5

                weaknesses.append(
                    "Low return on equity."
                )

        # --------------------------------
        # Company Scale
        # --------------------------------

        market_cap = info.get(
            "marketCap"
        )

        if market_cap is not None:

            if market_cap >= 500_000_000_000:

                score += 5

                strengths.append(
                    "Very large scale can provide meaningful competitive advantages."
                )

        # --------------------------------
        # Workforce
        # --------------------------------

        employees = info.get(
            "fullTimeEmployees"
        )

        if employees is not None:

            if employees >= 50_000:

                strengths.append(
                    "Large organisational scale may support significant operational advantages."
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
        # Classification
        # --------------------------------

        if score >= 80:

            classification = "WIDE MOAT"

        elif score >= 65:

            classification = "NARROW MOAT"

        elif score >= 50:

            classification = "UNCERTAIN MOAT"

        else:

            classification = "NO CLEAR MOAT"

        # --------------------------------
        # Summary
        # --------------------------------

        if score >= 80:

            summary = (
                "The available financial evidence "
                "suggests a strong and potentially "
                "durable competitive advantage."
            )

        elif score >= 65:

            summary = (
                "The company appears to possess "
                "some meaningful competitive advantages, "
                "although they may not be exceptionally durable."
            )

        elif score >= 50:

            summary = (
                "There is some evidence of competitive "
                "advantages, but the moat is not yet clear."
            )

        else:

            summary = (
                "The available financial evidence does "
                "not provide strong evidence of a durable moat."
            )

        return {

            "Moat Score": score,

            "Classification": classification,

            "Gross Margin": gross_margin,

            "Operating Margin": operating_margin,

            "ROE": roe,

            "ROIC": roic,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

        }