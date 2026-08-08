from core.adaptive_weights import AdaptiveWeights
from core.learning_engine import LearningEngine


class AdaptiveReport:

    def __init__(self):

        self.learning = LearningEngine()
        self.weights = AdaptiveWeights()

    def generate(self):

        performance = (
            self.learning.specialist_performance(
                "3M"
            )
        )

        weights = self.weights.calculate(
            performance
        )

        return {
            "Performance": performance,
            "Weights": weights,
        }

    def print_report(self):

        report = self.generate()

        print()
        print("=" * 60)
        print("ADAPTIVE WEIGHT REPORT")
        print("=" * 60)
        print()

        print("CURRENT WEIGHTS")
        print("-" * 60)

        for analyst, weight in report[
            "Weights"
        ].items():

            print(
                f"{analyst:<30} "
                f"{weight * 100:>6.2f}%"
            )

        print()

        print("LEARNING DATA")
        print("-" * 60)

        performance = report[
            "Performance"
        ]

        if not performance:

            print(
                "Not enough historical data yet."
            )

        else:

            for analyst, data in performance.items():

                print(
                    f"{analyst}: "
                    f"{data.get('Predictions', 0)} "
                    f"predictions | "
                    f"{data.get('Accuracy', 0)}% accuracy | "
                    f"{data.get('Average Relative Return', 0)}% relative return"
                )

        print()
        print("=" * 60)


if __name__ == "__main__":

    AdaptiveReport().print_report()