import json
import os
from datetime import datetime, timedelta

from core.data_sources.yahoo_history_access import YahooHistoryClient


class ExpectedReturnTracker:

    def __init__(
        self,
        path="data/expected_return_predictions.json",
        history_client=None,
    ):

        self.path = path

        self.history_client = (
            history_client
            or YahooHistoryClient()
        )

        directory = os.path.dirname(
            self.path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

    # ============================================================
    # LOAD
    # ============================================================

    def load(self):

        if not os.path.exists(
            self.path
        ):

            return []

        try:

            with open(
                self.path,
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
                    [],
                )

        except Exception as error:

            print(
                f"Could not load predictions: "
                f"{error}"
            )

        return []

    # ============================================================
    # SAVE
    # ============================================================

    def save(
        self,
        predictions,
    ):

        with open(
            self.path,
            "w",
        ) as file:

            json.dump(
                {
                    "Predictions":
                        predictions,
                },
                file,
                indent=2,
                default=str,
            )

    # ============================================================
    # CURRENT PRICE
    # ============================================================

    def get_current_price(
        self,
        symbol,
    ):

        try:

            history = self.history_client.history(
                symbol,
                period="5d",
            ).frame

            if history.empty:

                return None

            price = float(
                history["Close"].iloc[-1]
            )

            if price <= 0:

                return None

            return price

        except Exception as error:

            print(
                f"{symbol} price failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # RECORD PREDICTION
    # ============================================================

    def record(
        self,
        analysis,
    ):

        symbol = (
            analysis.get(
                "Ticker"
            )
        )

        if not symbol:

            return None

        symbol = (
            symbol
            .upper()
            .strip()
        )

        expected_return = (
            analysis.get(
                "Expected Return"
            )
        )

        confidence = (
            analysis.get(
                "Expected Return Confidence"
            )
        )

        factor_scores = (
            analysis.get(
                "Factor Scores",
                {},
            )
        )

        expected_return_analysis = (
            analysis.get(
                "Expected Return Analysis",
                {},
            )
        )

        price = self.get_current_price(
            symbol
        )

        if price is None:

            print(
                f"{symbol}: prediction not recorded "
                f"because no current price was available."
            )

            return None

        try:

            expected_return = float(
                expected_return
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

        try:

            confidence = float(
                confidence
            )

        except (
            TypeError,
            ValueError,
        ):

            confidence = 0

        predictions = self.load()

        # --------------------------------------------------------
        # Prevent duplicate prediction records for the same
        # ticker on the same day.
        # --------------------------------------------------------

        today = (
            datetime.now()
            .date()
            .isoformat()
        )

        for prediction in predictions:

            if (
                prediction.get(
                    "Ticker"
                ) == symbol

                and prediction.get(
                    "Prediction Date"
                ) == today

                and prediction.get(
                    "Status"
                ) == "OPEN"
            ):

                return prediction

        prediction = {

            "Prediction ID":
                f"{symbol}_{today}",

            "Ticker":
                symbol,

            "Prediction Date":
                today,

            "Entry Price":
                round(
                    price,
                    6,
                ),

            "Expected Return":
                round(
                    expected_return,
                    4,
                ),

            "Confidence":
                round(
                    confidence,
                    2,
                ),

            "Horizon Days":
                expected_return_analysis.get(
                    "Horizon Days",
                    252,
                ),

            "Factor Scores":
                factor_scores,

            "Factor Signal":
                expected_return_analysis.get(
                    "Factor Signal"
                ),

            "Growth Signal":
                expected_return_analysis.get(
                    "Growth Signal"
                ),

            "Valuation Signal":
                expected_return_analysis.get(
                    "Valuation Signal"
                ),

            "Momentum Signal":
                expected_return_analysis.get(
                    "Momentum Signal"
                ),

            "Risk Signal":
                expected_return_analysis.get(
                    "Risk Signal"
                ),

            "Status":
                "OPEN",

            "Actual Return":
                None,

            "Forecast Error":
                None,

            "Direction Correct":
                None,

            "Completed Date":
                None,

        }

        predictions.append(
            prediction
        )

        self.save(
            predictions
        )

        return prediction

    # ============================================================
    # FUTURE PRICE
    # ============================================================

    def get_future_price(
        self,
        symbol,
        prediction_date,
        horizon_days,
    ):

        try:

            prediction_datetime = datetime.fromisoformat(str(prediction_date))
            target_date = prediction_datetime + timedelta(days=int(horizon_days))
            end_date = target_date + timedelta(days=10)

            history = self.history_client.history(
                symbol,
                start=target_date.date().isoformat(),
                end=end_date.date().isoformat(),
            ).frame

            if history.empty:

                return None

            # ----------------------------------------------------
            # Use the first trading close on or after the predetermined
            # calendar horizon. A late evaluation job must not change the
            # economic outcome being measured.
            # ----------------------------------------------------

            price = float(
                history["Close"].iloc[0]
            )

            if price <= 0:

                return None

            return price

        except Exception as error:

            print(
                f"{symbol} future price failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # COMPLETE PREDICTIONS
    # ============================================================

    def update_completed(
        self,
    ):

        predictions = self.load()

        completed = 0

        for prediction in predictions:

            if prediction.get(
                "Status"
            ) != "OPEN":

                continue

            symbol = prediction.get(
                "Ticker"
            )

            prediction_date = (
                prediction.get(
                    "Prediction Date"
                )
            )

            horizon_days = (
                prediction.get(
                    "Horizon Days",
                    252,
                )
            )

            entry_price = (
                prediction.get(
                    "Entry Price"
                )
            )

            expected_return = (
                prediction.get(
                    "Expected Return"
                )
            )

            if not all(
                [
                    symbol,
                    prediction_date,
                    entry_price,
                    expected_return is not None,
                ]
            ):

                continue

            future_price = (
                self.get_future_price(
                    symbol,
                    prediction_date,
                    horizon_days,
                )
            )

            if future_price is None:

                continue

            try:

                entry_price = float(
                    entry_price
                )

                expected_return = float(
                    expected_return
                )

                actual_return = (

                    (
                        future_price
                        / entry_price
                    )
                    - 1

                ) * 100

            except (
                TypeError,
                ValueError,
                ZeroDivisionError,
            ):

                continue

            forecast_error = (
                actual_return
                - expected_return
            )

            direction_correct = (

                (
                    expected_return > 0
                    and actual_return > 0
                )

                or

                (
                    expected_return <= 0
                    and actual_return <= 0
                )

            )

            prediction[
                "Exit Price"
            ] = round(
                future_price,
                6,
            )

            prediction[
                "Actual Return"
            ] = round(
                actual_return,
                4,
            )

            prediction[
                "Forecast Error"
            ] = round(
                forecast_error,
                4,
            )

            prediction[
                "Absolute Error"
            ] = round(
                abs(
                    forecast_error
                ),
                4,
            )

            prediction[
                "Direction Correct"
            ] = direction_correct

            prediction[
                "Status"
            ] = "COMPLETED"

            prediction[
                "Completed Date"
            ] = (
                datetime.now()
                .date()
                .isoformat()
            )

            completed += 1

        self.save(
            predictions
        )

        return completed

    # ============================================================
    # REPORT
    # ============================================================

    def report(
        self,
    ):

        predictions = self.load()

        completed = [

            prediction

            for prediction
            in predictions

            if prediction.get(
                "Status"
            ) == "COMPLETED"

        ]

        if not completed:

            return {

                "Predictions":
                    len(predictions),

                "Completed":
                    0,

                "Open":
                    len(predictions),

                "Directional Accuracy":
                    0,

                "Average Expected Return":
                    0,

                "Average Actual Return":
                    0,

                "Average Forecast Error":
                    0,

            }

        expected_returns = [

            float(
                prediction.get(
                    "Expected Return",
                    0,
                )
            )

            for prediction
            in completed

        ]

        actual_returns = [

            float(
                prediction.get(
                    "Actual Return",
                    0,
                )
            )

            for prediction
            in completed

        ]

        errors = [

            float(
                prediction.get(
                    "Forecast Error",
                    0,
                )
            )

            for prediction
            in completed

        ]

        correct = [

            prediction.get(
                "Direction Correct",
                False,
            )

            for prediction
            in completed

        ]

        return {

            "Predictions":
                len(predictions),

            "Completed":
                len(completed),

            "Open":
                len(predictions)
                - len(completed),

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

            "Average Expected Return":
                round(
                    sum(
                        expected_returns
                    )
                    / len(
                        expected_returns
                    ),
                    2,
                ),

            "Average Actual Return":
                round(
                    sum(
                        actual_returns
                    )
                    / len(
                        actual_returns
                    ),
                    2,
                ),

            "Average Forecast Error":
                round(
                    sum(
                        errors
                    )
                    / len(
                        errors
                    ),
                    2,
                ),

        }

    # ============================================================
    # PRINT REPORT
    # ============================================================

    def print_report(
        self,
    ):

        report = self.report()

        print()
        print("=" * 80)
        print(
            "EXPECTED RETURN TRACKER"
        )
        print("=" * 80)

        print()

        print(
            f"Predictions: "
            f"{report['Predictions']}"
        )

        print(
            f"Completed: "
            f"{report['Completed']}"
        )

        print(
            f"Open: "
            f"{report['Open']}"
        )

        if report[
            "Completed"
        ] > 0:

            print()

            print(
                f"Directional accuracy: "
                f"{report['Directional Accuracy']:.2f}%"
            )

            print(
                f"Average expected return: "
                f"{report['Average Expected Return']:.2f}%"
            )

            print(
                f"Average actual return: "
                f"{report['Average Actual Return']:.2f}%"
            )

            print(
                f"Average forecast error: "
                f"{report['Average Forecast Error']:.2f}%"
            )

        else:

            print()
            print(
                "No completed predictions available yet."
            )

        print()

        print(
            f"Saved to: "
            f"{self.path}"
        )


if __name__ == "__main__":

    tracker = (
        ExpectedReturnTracker()
    )

    completed = (
        tracker.update_completed()
    )

    print(
        f"Predictions completed this run: "
        f"{completed}"
    )

    tracker.print_report()
