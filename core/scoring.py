FUNDAMENTAL_WEIGHT = 0.70
TECHNICAL_WEIGHT = 0.30


def calculate_overall_score(fundamental, technical):
    """
    Calculate the weighted overall investment score.
    """

    score = (
        (fundamental * FUNDAMENTAL_WEIGHT)
        + (technical * TECHNICAL_WEIGHT)
    )

    return round(score, 1)