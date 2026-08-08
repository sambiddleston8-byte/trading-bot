import json
import os


class FactorLearning:

    def __init__(
        self,
        attribution_path="data/factor_attribution.json",
        output_path="data/factor_learning.json",
        minimum_observations=10,
    ):

        self.attribution_path = (
            attribution_path
        )

        self.output_path = (
            output_path
        )

        self.minimum_observations = (
            minimum_observations
        )

        directory = os.path.dirname(
            output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # ============================================================
    # LOAD ATTRIBUTION
    # ============================================================

    def load_attribution(self):

        if not os.path.exists(
            self.attribution_path
        ):

            return {}

        try:

            with open(
                self.attribution_path,
                "r",
            ) as file:

                return json.load(
                    file
                )

        except Exception:

            return {}

    # ============================================================
    # BUILD LEARNING DATA
    # ============================================================

    def analyse(self):

        attribution = (
            self.load_attribution()
        )

        factors = attribution.get(
            "Factors",
            {},
        )

        learning = {}

        for factor, data in (
            factors.items()
        ):

            observations = int(
                data.get(
                    "Observations",
                    0,
                )
            )

            if observations < (
                self.minimum_observations
            ):

                continue

            accuracy = float(
                data.get(
                    "Directional Accuracy",
                    50,
                )
            )

            average_return = float(
                data.get(
                    "Average Return",
                    0,
                )
            )

            contribution = float(
                data.get(
                    "Average Contribution",
                    0,
                )
            )

            # ----------------------------------------------------
            # Predictive score
            #
            # Directional accuracy receives the largest weight.
            # Average return provides secondary evidence.
            # Contribution provides additional evidence.
            # ----------------------------------------------------

            accuracy_score = (
                accuracy / 100
            )

            return_score = (
                0.5
                + max(
                    -50,
                    min(
                        average_return,
                        50,
                    ),
                )
                / 100
            )

            contribution_score = (
                0.5
                + max(
                    -50,
                    min(
                        contribution,
                        50,
                    ),
                )
                / 100
            )

            predictive_score = (

                accuracy_score
                * 0.60

                + return_score
                * 0.25

                + contribution_score
                * 0.15

            )

            # ----------------------------------------------------
            # Confidence increases with evidence.
            # ----------------------------------------------------

            evidence_factor = min(
                observations
                / 100,
                1,
            )

            confidence = (
                50
                + (
                    abs(
                        predictive_score
                        - 0.50
                    )
                    * 100
                    * evidence_factor
                )
            )

            confidence = max(
                50,
                min(
                    confidence,
                    95,
                ),
            )

            learning[factor] = {

                "Predictions":
                    observations,

                "Accuracy":
                    round(
                        accuracy,
                        2,
                    ),

                "Average Return":
                    round(
                        average_return,
                        4,
                    ),

                "Average Contribution":
                    round(
                        contribution,
                        4,
                    ),

                "Predictive Score":
                    round(
                        predictive_score,
                        4,
                    ),

                "Confidence":
                    round(
                        confidence,
                        2,
                    ),

            }

        output = {

            "Predictions Analysed":
                attribution.get(
                    "Predictions Analysed",
                    0,
                ),

            "Minimum Observations":
                self.minimum_observations,

            "Factors":
                learning,

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

    # ============================================================
    # ADAPTIVE WEIGHT INPUT
    # ============================================================

    def get_adaptive_performance(
        self,
    ):

        learning = (
            self.analyse()
        )

        result = {}

        for factor, data in (
            learning.get(
                "Factors",
                {},
            ).items()
        ):

            result[factor] = {

                "Predictions":
                    data.get(
                        "Predictions",
                        0,
                    ),

                "Accuracy":
                    data.get(
                        "Accuracy",
                        50,
                    ),

                "Average Return":
                    data.get(
                        "Average Return",
                        0,
                    ),

            }

        return result

    # ============================================================
    # PRINT REPORT
    # ============================================================

    def print_report(
        self,
        report,
    ):

        print()
        print("=" * 90)
        print("FACTOR LEARNING REPORT")
        print("=" * 90)

        print()

        print(
            f"Predictions analysed: "
            f"{report['Predictions Analysed']}"
        )

        print()

        if not report["Factors"]:

            print(
                "Not enough completed predictions "
                "to learn from yet."
            )

            return

        ordered = sorted(
            report["Factors"].items(),
            key=lambda item:
                item[1][
                    "Predictive Score"
                ],
            reverse=True,
        )

        for factor, data in ordered:

            print(
                f"{factor:<25} "
                f"Accuracy: "
                f"{data['Accuracy']:>6.2f}% "
                f"Return: "
                f"{data['Average Return']:>8.4f}% "
                f"Score: "
                f"{data['Predictive Score']:.4f}"
            )

    # ============================================================
    # RUN
    # ============================================================

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

    FactorLearning().run()