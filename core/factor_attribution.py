import json
import os
from collections import defaultdict
from datetime import datetime


class FactorAttribution:

    def __init__(
        self,
        history_path="data/outcome_history.json",
        output_path="data/factor_attribution.json",
    ):

        self.history_path = history_path
        self.output_path = output_path

        directory = os.path.dirname(
            output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------
    # Load History
    # --------------------------------

    def load_history(self):

        if not os.path.exists(
            self.history_path
        ):

            return []

        try:

            with open(
                self.history_path,
                "r",
            ) as file:

                data = json.load(
                    file
                )

            if isinstance(
                data,
                list,
            ):

                return data

            if isinstance(
                data,
                dict,
            ):

                return data.get(
                    "Predictions",
                    data.get(
                        "History",
                        [],
                    ),
                )

            return []

        except Exception:

            return []

    # --------------------------------
    # Extract Factor Scores
    # --------------------------------

    def extract_factors(
        self,
        prediction,
    ):

        factors = (
            prediction.get(
                "Factor Scores",
                {},
            )
        )

        if not factors:

            factors = (
                prediction.get(
                    "Specialist Scores",
                    {},
                )
            )

        return factors

    # --------------------------------
    # Determine Outcome
    # --------------------------------

    def outcome_return(
        self,
        prediction,
    ):

        possible_fields = [

            "Actual Return",

            "Realised Return",

            "Return",

            "Return %",

            "Outcome Return",

        ]

        for field in possible_fields:

            value = prediction.get(
                field
            )

            if value is None:
                continue

            try:

                return float(
                    value
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

        return None

    # --------------------------------
    # Factor Contribution
    # --------------------------------

    def contribution(
        self,
        factor_score,
        actual_return,
    ):

        if actual_return is None:
            return None

        # Convert factor score from
        # 0-100 into conviction relative
        # to neutral 50.

        conviction = (
            factor_score
            - 50
        )

        # A positive return rewards
        # positive conviction.
        #
        # A negative return rewards
        # negative conviction.

        contribution = (
            conviction
            * actual_return
        )

        return contribution

    # --------------------------------
    # Analyse
    # --------------------------------

    def analyse(self):

        history = (
            self.load_history()
        )

        factor_results = defaultdict(
            lambda: {

                "Observations": 0,

                "Correct Direction": 0,

                "Incorrect Direction": 0,

                "Neutral Direction": 0,

                "Average Score": 0,

                "Average Return": 0,

                "Average Contribution": 0,

                "Positive Contribution": 0,

                "Negative Contribution": 0,

            }
        )

        for prediction in history:

            actual_return = (
                self.outcome_return(
                    prediction
                )
            )

            if actual_return is None:
                continue

            factors = (
                self.extract_factors(
                    prediction
                )
            )

            for factor, score in (
                factors.items()
            ):

                try:

                    score = float(
                        score
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    continue

                result = (
                    factor_results[
                        factor
                    ]
                )

                result[
                    "Observations"
                ] += 1

                result[
                    "Average Score"
                ] += score

                result[
                    "Average Return"
                ] += actual_return

                contribution = (
                    self.contribution(
                        score,
                        actual_return,
                    )
                )

                if contribution is not None:

                    result[
                        "Average Contribution"
                    ] += contribution

                    if contribution > 0:

                        result[
                            "Positive Contribution"
                        ] += 1

                    elif contribution < 0:

                        result[
                            "Negative Contribution"
                        ] += 1

                conviction = (
                    score - 50
                )

                if (
                    conviction > 5
                    and actual_return > 0
                ):

                    result[
                        "Correct Direction"
                    ] += 1

                elif (
                    conviction < -5
                    and actual_return < 0
                ):

                    result[
                        "Correct Direction"
                    ] += 1

                elif (
                    conviction > 5
                    and actual_return < 0
                ):

                    result[
                        "Incorrect Direction"
                    ] += 1

                elif (
                    conviction < -5
                    and actual_return > 0
                ):

                    result[
                        "Incorrect Direction"
                    ] += 1

                else:

                    result[
                        "Neutral Direction"
                    ] += 1

        # --------------------------------
        # Finalise
        # --------------------------------

        final = {}

        for factor, result in (
            factor_results.items()
        ):

            observations = (
                result[
                    "Observations"
                ]
            )

            if observations == 0:
                continue

            average_score = (
                result[
                    "Average Score"
                ]
                / observations
            )

            average_return = (
                result[
                    "Average Return"
                ]
                / observations
            )

            average_contribution = (
                result[
                    "Average Contribution"
                ]
                / observations
            )

            correct = (
                result[
                    "Correct Direction"
                ]
            )

            incorrect = (
                result[
                    "Incorrect Direction"
                ]
            )

            directional_observations = (
                correct
                + incorrect
            )

            if directional_observations:

                directional_accuracy = (
                    correct
                    / directional_observations
                    * 100
                )

            else:

                directional_accuracy = 0

            final[factor] = {

                "Observations":
                    observations,

                "Average Score":
                    round(
                        average_score,
                        2,
                    ),

                "Average Return":
                    round(
                        average_return,
                        4,
                    ),

                "Average Contribution":
                    round(
                        average_contribution,
                        4,
                    ),

                "Correct Direction":
                    correct,

                "Incorrect Direction":
                    incorrect,

                "Neutral Direction":
                    result[
                        "Neutral Direction"
                    ],

                "Directional Accuracy":
                    round(
                        directional_accuracy,
                        2,
                    ),

                "Positive Contribution":
                    result[
                        "Positive Contribution"
                    ],

                "Negative Contribution":
                    result[
                        "Negative Contribution"
                    ],

            }

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Predictions Analysed":
                len(history),

            "Factors":
                final,

        }

        with open(
            self.output_path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
            )

        return output

    # --------------------------------
    # Print Report
    # --------------------------------

    def print_report(
        self,
        report,
    ):

        print()
        print("=" * 90)
        print("FACTOR ATTRIBUTION REPORT")
        print("=" * 90)

        print()

        print(
            f"Predictions analysed: "
            f"{report['Predictions Analysed']}"
        )

        print()

        if not report["Factors"]:

            print(
                "No completed predictions "
                "are available yet."
            )

            return

        ordered = sorted(
            report["Factors"].items(),
            key=lambda item: (
                item[1][
                    "Average Contribution"
                ]
            ),
            reverse=True,
        )

        for factor, data in ordered:

            print(
                f"{factor:<25} "
                f"Accuracy: "
                f"{data['Directional Accuracy']:>6.2f}% "
                f"Contribution: "
                f"{data['Average Contribution']:>8.4f}"
            )

    # --------------------------------
    # Run
    # --------------------------------

    def run(self):

        report = (
            self.analyse()
        )

        self.print_report(
            report
        )

        print()

        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return report


if __name__ == "__main__":

    FactorAttribution().run()