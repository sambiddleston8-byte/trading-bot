from core.config import WEIGHTS


class ScoringEngine:

    def overall_score(
        self,
        business_quality,
        valuation,
        technical,
        risk,
        news,
        catalyst,
    ):

        return round(

            (
                business_quality * WEIGHTS["business_quality"] +
                valuation * WEIGHTS["valuation"] +
                technical * WEIGHTS["technical"] +
                risk * WEIGHTS["risk"] +
                news * WEIGHTS["news"] +
                catalyst * WEIGHTS["catalyst"]
            ),

            1,

        )


def calculate_overall_score(
    business_quality,
    valuation,
    technical,
    risk,
    news,
    catalyst,
):

    return ScoringEngine().overall_score(
        business_quality,
        valuation,
        technical,
        risk,
        news,
        catalyst,
    )