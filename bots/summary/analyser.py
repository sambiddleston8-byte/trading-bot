class SummaryAnalyser:

    def analyse(self, analysis):

        strengths = []
        weaknesses = []

        if analysis.business_quality >= 80:
            strengths.append("Excellent business quality")

        if analysis.technical >= 80:
            strengths.append("Strong technical momentum")

        if analysis.news >= 70:
            strengths.append("Positive recent news")

        if analysis.catalyst >= 80:
            strengths.append("Strong upcoming catalysts")

        if analysis.valuation < 50:
            weaknesses.append("Premium valuation")

        if analysis.risk < 60:
            weaknesses.append("Elevated investment risk")

        thesis = []

        if analysis.business_quality >= 80:
            thesis.append(
                "The company demonstrates excellent business fundamentals."
            )

        if analysis.technical >= 80:
            thesis.append(
                "Technical momentum remains strong."
            )

        if analysis.news >= 70:
            thesis.append(
                "Recent news flow has been supportive."
            )

        if analysis.catalyst >= 80:
            thesis.append(
                "Upcoming catalysts could influence future performance."
            )

        if analysis.valuation < 50:
            thesis.append(
                "Valuation appears demanding."
            )

        if analysis.risk < 60:
            thesis.append(
                "Investment risk is above average."
            )

        if not thesis:
            thesis.append(
                "No major investment signals were identified."
            )

        return {
            "Strengths": strengths,
            "Weaknesses": weaknesses,
            "Summary": " ".join(thesis),
        }