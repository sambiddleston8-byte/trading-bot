def average_scores(scores):

    valid_scores = [
        score
        for score in scores
        if score is not None
    ]

    if len(valid_scores) == 0:
        return 0

    return round(

        sum(valid_scores) / len(valid_scores),

        1

    )