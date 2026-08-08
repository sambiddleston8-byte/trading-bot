import os

from core.factor_attribution import FactorAttribution
from core.factor_learning import FactorLearning
from core.adaptive_weights_engine import AdaptiveWeightsEngine


class LearningPipeline:

    def __init__(self):

        self.attribution = (
            FactorAttribution()
        )

        self.learning = (
            FactorLearning()
        )

        self.adaptive_weights = (
            AdaptiveWeightsEngine()
        )

    # ============================================================
    # RUN ATTRIBUTION
    # ============================================================

    def run_attribution(self):

        print()
        print("=" * 80)
        print("STEP 1 — FACTOR ATTRIBUTION")
        print("=" * 80)

        try:

            report = (
                self.attribution.analyse()
            )

            if hasattr(
                self.attribution,
                "print_report",
            ):

                self.attribution.print_report(
                    report
                )

            return report

        except Exception as error:

            print(
                f"Factor attribution failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # RUN FACTOR LEARNING
    # ============================================================

    def run_learning(self):

        print()
        print("=" * 80)
        print("STEP 2 — FACTOR LEARNING")
        print("=" * 80)

        try:

            report = (
                self.learning.analyse()
            )

            self.learning.print_report(
                report
            )

            return report

        except Exception as error:

            print(
                f"Factor learning failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # RUN ADAPTIVE WEIGHTS
    # ============================================================

    def run_adaptive_weights(self):

        print()
        print("=" * 80)
        print("STEP 3 — ADAPTIVE WEIGHTS")
        print("=" * 80)

        try:

            output = (
                self.adaptive_weights.run()
            )

            return output

        except Exception as error:

            print(
                f"Adaptive weights failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # RUN
    # ============================================================

    def run(self):

        print()
        print("=" * 80)
        print("SELF-LEARNING PIPELINE")
        print("=" * 80)

        attribution = (
            self.run_attribution()
        )

        learning = (
            self.run_learning()
        )

        adaptive_weights = (
            self.run_adaptive_weights()
        )

        print()
        print("=" * 80)
        print("LEARNING PIPELINE COMPLETE")
        print("=" * 80)

        return {

            "Attribution":
                attribution,

            "Learning":
                learning,

            "Adaptive Weights":
                adaptive_weights,

        }


if __name__ == "__main__":

    LearningPipeline().run()