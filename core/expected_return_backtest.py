import json
import os

from core.expected_return_engine import ExpectedReturnEngine


class ExpectedReturnBacktest:

    def __init__(
        self,
        engine=None,
        output_path="data/expected_return_backtest.json",
    ):

        self.engine = (
            engine
            or ExpectedReturnEngine()
        )

        self.output_path = (
            output_path
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
    # CALCULATE REALISED RETURN
    # ============================================================

    def realised_return(
        self,
        entry_price,
        exit_price,
    ):

        if (
            entry_price is None
            or exit_price is None
        ):

            return None

        try:

            entry_price = float(
                entry_price
            )

            exit_price = float(
                exit_price
            )

            if entry_price <= 0:

                return None

            return (
                (
                    exit_price
                    / entry_price
                )
                - 1
            ) * 100

        except (
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):

            return None

    # ============================================================
    # EVALUATE PREDICTION
    # ============================================================

    def evaluate(
        self,
        expected_return,
        realised_return,
    ):

        if (
            expected_return is None
            or realised_return is None
        ):

            return None

        expected_return = float(
            expected_return
        )

        realised_return = float(
            realised_return
        )

        error = (
            realised_return
            - expected_return
        )

        directional_prediction = (
            expected_return > 0
        )

        directional_result = (
            realised_return > 0
        )

        direction_correct = (
            directional_prediction
            == directional_result
        )

        return {

            "Expected Return":
                round(
                    expected_return,
                    4,
                ),

            "Realised Return":
                round(
                    realised_return,
                    4,
                ),

            "Forecast Error":
                round(
                    error,
                    4,
                ),

            "Absolute Error":
                round(
                    abs(error),
                    4,
                ),

            "Direction Correct":
                direction_correct,

        }

    # ============================================================
    # SUMMARY
    # ============================================================

    def summarise(
        self,
        observations,
    ):

        if not observations:

            return {

                "Observations":
                    0,

                "Average Expected Return":
                    0,

                "Average Realised Return":
                    0,

                "Average Forecast Error":
                    0,

                "Directional Accuracy":
                    0,

            }

        expected = [

            item["Expected Return"]

            for item in observations

        ]

        realised = [

            item["Realised Return"]

            for item in observations

        ]

        errors = [

            item["Forecast Error"]

            for item in observations

        ]

        correct = [

            item["Direction Correct"]

            for item in observations

        ]

        return {

            "Observations":
                len(observations),

            "Average Expected Return":
                round(
                    sum(expected)
                    / len(expected),
                    4,
                ),

            "Average Realised Return":
                round(
                    sum(realised)
                    / len(realised),
                    4,
                ),

            "Average Forecast Error":
                round(
                    sum(errors)
                    / len(errors),
                    4,
                ),

            "Average Absolute Error":
                round(
                    sum(
                        abs(error)
                        for error in errors
                    )
                    / len(errors),
                    4,
                ),

            "Directional Accuracy":
                round(
                    (
                        sum(
                            1
                            for value
                            in correct
                            if value
                        )
                        / len(correct)
                    )
                    * 100,
                    2,
                ),

        }

    # ============================================================
    # RUN FROM OUTCOME HISTORY
    # ============================================================

    def run(
        self,
        history_path="data/outcome_history.json",
    ):

        print()
        print("=" * 80)
        print("EXPECTED RETURN BACKTEST")
        print("=" * 80)

        if not os.path.exists(
            history_path
        ):

            print()
            print(
                "No outcome history available."
            )

            return {
                "Observations": 0
            }

        try:

            with open(
                history_path,
                "r",
            ) as file:

                history = json.load(
                    file
                )

        except Exception as error:

            print(
                f"Could not load outcome history: "
                f"{error}"
            )

            return {
                "Observations": 0
            }

        # --------------------------------------------------------
        # Accept either a list directly or a dictionary containing
        # an outcomes/predictions collection.
        # --------------------------------------------------------

        if isinstance(
            history,
            list,
        ):

            records = history

        elif isinstance(
            history,
            dict,
        ):

            records = (
                history.get(
                    "Outcomes",
                    history.get(
                        "Predictions",
                        [],
                    ),
                )
            )

        else:

            records = []

        observations = []

        for record in records:

            if not isinstance(
                record,
                dict,
            ):

                continue

            expected = record.get(
                "Expected Return"
            )

            realised = record.get(
                "Realised Return"
            )

            if (
                expected is None
                or realised is None
            ):

                continue

            result = self.evaluate(
                expected,
                realised,
            )

            if result:

                result["Ticker"] = (
                    record.get(
                        "Ticker"
                    )
                )

                observations.append(
                    result
                )

        summary = (
            self.summarise(
                observations
            )
        )

        output = {

            "Summary":
                summary,

            "Observations":
                observations,

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

        print()
        print(
            f"Observations: "
            f"{summary['Observations']}"
        )

        if summary[
            "Observations"
        ]:

            print(
                f"Average expected return: "
                f"{summary['Average Expected Return']:.2f}%"
            )

            print(
                f"Average realised return: "
                f"{summary['Average Realised Return']:.2f}%"
            )

            print(
                f"Directional accuracy: "
                f"{summary['Directional Accuracy']:.2f}%"
            )

        else:

            print(
                "No completed expected-return "
                "predictions are available yet."
            )

        print()
        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return output


if __name__ == "__main__":

    ExpectedReturnBacktest().run()