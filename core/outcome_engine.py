import json
import os
from datetime import datetime, timedelta


class OutcomeEngine:

    def __init__(
        self,
        history_path="data/outcome_history.json",
    ):

        self.history_path = history_path

        directory = os.path.dirname(
            history_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # ============================================================
    # LOAD HISTORY
    # ============================================================

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

            return data.get(
                "Predictions",
                [],
            )

        except Exception:

            return []

    # ============================================================
    # SAVE HISTORY
    # ============================================================

    def save_history(
        self,
        history,
    ):

        with open(
            self.history_path,
            "w",
        ) as file:

            json.dump(
                {
                    "Predictions":
                        history
                },
                file,
                indent=2,
                default=str,
            )

    # ============================================================
    # RECORD PREDICTION
    # ============================================================

    def record_prediction(
        self,
        ticker,
        entry_price,
        committee_score,
        recommendation,
        confidence,
        factor_scores=None,
        benchmark_price=None,
        horizon_days=252,
    ):

        if factor_scores is None:

            factor_scores = {}

        entry_date = datetime.now()

        prediction = {

            "ID":
                len(
                    self.load_history()
                ) + 1,

            "Ticker":
                ticker.upper(),

            "Entry Date":
                entry_date.isoformat(),

            "Entry Price":
                float(
                    entry_price
                ),

            "Committee Score":
                float(
                    committee_score
                ),

            "Recommendation":
                recommendation,

            "Confidence":
                float(
                    confidence
                ),

            "Factor Scores":
                factor_scores,

            "Benchmark Entry Price":
                (
                    float(
                        benchmark_price
                    )
                    if benchmark_price
                    is not None
                    else None
                ),

            "Horizon Days":
                int(
                    horizon_days
                ),

            "Target Date":
                (
                    entry_date
                    + timedelta(
                        days=horizon_days
                    )
                ).isoformat(),

            "Status":
                "OPEN",

            "Exit Date":
                None,

            "Exit Price":
                None,

            "Actual Return":
                None,

            "Benchmark Return":
                None,

            "Alpha":
                None,

            "Correct":
                None,

        }

        history = (
            self.load_history()
        )

        history.append(
            prediction
        )

        self.save_history(
            history
        )

        return prediction

    # ============================================================
    # CALCULATE RETURN
    # ============================================================

    def calculate_return(
        self,
        entry_price,
        exit_price,
    ):

        if entry_price is None:
            return None

        if exit_price is None:
            return None

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

    # ============================================================
    # CLOSE PREDICTION
    # ============================================================

    def close_prediction(
        self,
        prediction_id,
        exit_price,
        benchmark_exit_price=None,
    ):

        history = (
            self.load_history()
        )

        target = None

        for prediction in history:

            if (
                prediction.get(
                    "ID"
                )
                == prediction_id
            ):

                target = prediction
                break

        if target is None:

            return None

        target["Exit Date"] = (
            datetime.now().isoformat()
        )

        target["Exit Price"] = float(
            exit_price
        )

        actual_return = (
            self.calculate_return(
                target.get(
                    "Entry Price"
                ),
                exit_price,
            )
        )

        target["Actual Return"] = (
            actual_return
        )

        benchmark_return = None

        if (
            target.get(
                "Benchmark Entry Price"
            )
            is not None
            and benchmark_exit_price
            is not None
        ):

            benchmark_return = (
                self.calculate_return(
                    target.get(
                        "Benchmark Entry Price"
                    ),
                    benchmark_exit_price,
                )
            )

        target["Benchmark Return"] = (
            benchmark_return
        )

        if (
            actual_return is not None
            and benchmark_return is not None
        ):

            target["Alpha"] = (
                actual_return
                - benchmark_return
            )

        recommendation = (
            target.get(
                "Recommendation",
                "",
            )
        )

        # --------------------------------------------------------
        # Determine whether prediction was directionally correct.
        #
        # BUY / STRONG BUY:
        # positive return = correct
        #
        # SELL:
        # negative return = correct
        #
        # HOLD / WATCH:
        # treated as correct when return is
        # within +/- 5%.
        # --------------------------------------------------------

        if actual_return is not None:

            if recommendation in (
                "BUY",
                "STRONG BUY",
            ):

                target["Correct"] = (
                    actual_return > 0
                )

            elif recommendation in (
                "SELL",
                "SELL / AVOID",
            ):

                target["Correct"] = (
                    actual_return < 0
                )

            elif recommendation in (
                "REDUCE / AVOID",
            ):

                target["Correct"] = (
                    actual_return <= 0
                )

            else:

                target["Correct"] = (
                    abs(
                        actual_return
                    )
                    <= 5
                )

        target["Status"] = "CLOSED"

        self.save_history(
            history
        )

        return target

    # ============================================================
    # OPEN PREDICTIONS
    # ============================================================

    def open_predictions(self):

        history = (
            self.load_history()
        )

        return [

            prediction

            for prediction in history

            if prediction.get(
                "Status"
            ) == "OPEN"

        ]

    # ============================================================
    # CLOSED PREDICTIONS
    # ============================================================

    def closed_predictions(self):

        history = (
            self.load_history()
        )

        return [

            prediction

            for prediction in history

            if prediction.get(
                "Status"
            ) == "CLOSED"

        ]

    # ============================================================
    # PERFORMANCE SUMMARY
    # ============================================================

    def performance_summary(self):

        closed = (
            self.closed_predictions()
        )

        if not closed:

            return {

                "Predictions":
                    0,

                "Accuracy":
                    0,

                "Average Return":
                    0,

                "Average Benchmark Return":
                    0,

                "Average Alpha":
                    0,

            }

        correct = sum(

            1

            for prediction
            in closed

            if prediction.get(
                "Correct"
            ) is True

        )

        returns = [

            prediction.get(
                "Actual Return"
            )

            for prediction
            in closed

            if prediction.get(
                "Actual Return"
            ) is not None

        ]

        benchmark_returns = [

            prediction.get(
                "Benchmark Return"
            )

            for prediction
            in closed

            if prediction.get(
                "Benchmark Return"
            ) is not None

        ]

        alpha_values = [

            prediction.get(
                "Alpha"
            )

            for prediction
            in closed

            if prediction.get(
                "Alpha"
            ) is not None

        ]

        return {

            "Predictions":
                len(closed),

            "Accuracy":
                round(
                    (
                        correct
                        / len(closed)
                    )
                    * 100,
                    2,
                ),

            "Average Return":
                round(
                    (
                        sum(returns)
                        / len(returns)
                    )
                    if returns
                    else 0,
                    2,
                ),

            "Average Benchmark Return":
                round(
                    (
                        sum(
                            benchmark_returns
                        )
                        / len(
                            benchmark_returns
                        )
                    )
                    if benchmark_returns
                    else 0,
                    2,
                ),

            "Average Alpha":
                round(
                    (
                        sum(alpha_values)
                        / len(alpha_values)
                    )
                    if alpha_values
                    else 0,
                    2,
                ),

        }

    # ============================================================
    # PRINT REPORT
    # ============================================================

    def print_report(self):

        history = (
            self.load_history()
        )

        summary = (
            self.performance_summary()
        )

        print()
        print("=" * 80)
        print("OUTCOME ENGINE")
        print("=" * 80)

        print()

        print(
            f"Total predictions: "
            f"{len(history)}"
        )

        print(
            f"Open predictions: "
            f"{len(self.open_predictions())}"
        )

        print(
            f"Closed predictions: "
            f"{len(self.closed_predictions())}"
        )

        print()

        print(
            f"Accuracy: "
            f"{summary['Accuracy']}%"
        )

        print(
            f"Average Return: "
            f"{summary['Average Return']}%"
        )

        print(
            f"Average Benchmark Return: "
            f"{summary['Average Benchmark Return']}%"
        )

        print(
            f"Average Alpha: "
            f"{summary['Average Alpha']}%"
        )

        print()

        print(
            f"Saved to: "
            f"{self.history_path}"
        )


if __name__ == "__main__":

    OutcomeEngine().print_report()