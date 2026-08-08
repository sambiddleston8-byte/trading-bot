import json


class ResearchCompare:

    def compare(self, previous, current):

        if not previous:
            return {
                "Has Previous": False,
                "Changes": [],
            }

        changes = []

        score_fields = [
            "Overall Score",
            "Business Quality",
            "Valuation",
            "Technical",
            "Risk",
            "News",
            "Catalyst",
        ]

        for field in score_fields:

            old = previous.get(field)
            new = current.get(field)

            if old is None or new is None:
                continue

            try:
                difference = round(
                    float(new) - float(old),
                    1,
                )
            except (TypeError, ValueError):
                continue

            if difference == 0:
                continue

            direction = (
                "improved"
                if difference > 0
                else "deteriorated"
            )

            changes.append(
                {
                    "Metric": field,
                    "Previous": old,
                    "Current": new,
                    "Change": difference,
                    "Direction": direction,
                }
            )

        previous_rating = previous.get("Rating")
        current_rating = current.get("Rating")

        if previous_rating != current_rating:

            changes.append(
                {
                    "Metric": "Rating",
                    "Previous": previous_rating,
                    "Current": current_rating,
                    "Change": None,
                    "Direction": "changed",
                }
            )

        return {
            "Has Previous": True,
            "Changes": changes,
        }