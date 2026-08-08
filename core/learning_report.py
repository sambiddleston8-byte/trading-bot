from core.learning_engine import LearningEngine


class LearningReport:

    def __init__(self):

        self.learning = LearningEngine()

    def generate(self):

        report = {}

        # --------------------------------
        # Performance by horizon
        # --------------------------------

        for horizon in [
            "1M",
            "3M",
            "6M",
            "12M",
        ]:

            report[horizon] = (
                self.learning.performance(
                    horizon
                )
            )

        # --------------------------------
        # Specialist performance
        # --------------------------------

        report["Specialists"] = (
            self.learning.specialist_performance(
                "3M"
            )
        )

        return report

    def print_report(self):

        report = self.generate()

        print()
        print("=" * 50)
        print("TRADING BOT LEARNING REPORT")
        print("=" * 50)
        print()

        # --------------------------------
        # Horizon Performance
        # --------------------------------

        for horizon in [
            "1M",
            "3M",
            "6M",
            "12M",
        ]:

            data = report[horizon]

            predictions = data.get(
                "Evaluated Predictions",
                0,
            )

            accuracy = data.get(
                "Accuracy",
                0,
            )

            average_return = data.get(
                "Average Return",
                0,
            )

            relative_return = data.get(
                "Average Relative Return",
                data.get(
                    "Relative Return",
                    0,
                ),
            )

            print(
                f"{horizon}:"
            )

            print(
                f"  Predictions: "
                f"{predictions}"
            )

            print(
                f"  Accuracy: "
                f"{accuracy}%"
            )

            print(
                f"  Average Return: "
                f"{average_return}%"
            )

            print(
                f"  Relative Return: "
                f"{relative_return}%"
            )

            print()

        # --------------------------------
        # Specialist Performance
        # --------------------------------

        print(
            "SPECIALIST PERFORMANCE"
        )

        print("-" * 50)

        specialists = report.get(
            "Specialists",
            {},
        )

        if not specialists:

            print(
                "No evaluated specialist predictions yet."
            )

        else:

            for analyst, data in specialists.items():

                accuracy = data.get(
                    "Accuracy",
                    0,
                )

                relative_return = data.get(
                    "Average Relative Return",
                    0,
                )

                print(
                    f"{analyst}: "
                    f"{accuracy}% accuracy | "
                    f"{relative_return}% "
                    f"relative return"
                )

        print()
        print("=" * 50)


if __name__ == "__main__":

    LearningReport().print_report()