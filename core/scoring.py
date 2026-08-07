class ScoringEngine:

    def overall_score(
        self,
        business_quality,
        valuation,
        technical,
    ):

        return round(
            (
                business_quality +
                valuation +
                technical
            ) / 3,
            1,
        )


def calculate_overall_score(
    business_quality,
    valuation,
    technical,
):

    return ScoringEngine().overall_score(
        business_quality,
        valuation,
        technical,
    )