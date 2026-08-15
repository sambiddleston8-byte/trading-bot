import json
import os
from datetime import datetime, timedelta

import yfinance as yf

from core.data_sources.yahoo_history_access import YahooHistoryClient


class LearningEngine:

    HORIZONS = {
        "1M": 30,
        "3M": 90,
        "6M": 180,
        "12M": 365,
    }

    def __init__(
        self,
        path="data/learning_history.json",
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

        if not os.path.exists(
            self.path
        ):
            self._save([])

    # --------------------------------
    # Storage
    # --------------------------------

    def _load(self):

        try:

            with open(
                self.path,
                "r",
            ) as file:

                data = json.load(file)

                if isinstance(
                    data,
                    list,
                ):
                    return data

                return []

        except (
            FileNotFoundError,
            json.JSONDecodeError,
        ):

            return []

    def _save(self, history):

        with open(
            self.path,
            "w",
        ) as file:

            json.dump(
                history,
                file,
                indent=2,
                default=str,
            )

    # --------------------------------
    # Record Prediction
    # --------------------------------

    def record_prediction(
        self,
        analysis,
    ):

        history = self._load()

        committee = analysis.get(
            "Investment Committee",
            {},
        )

        record = {

            "Ticker":
                analysis.get(
                    "Ticker"
                ),

            "Date":
                datetime.now().isoformat(),

            "Price At Decision":
                analysis.get(
                    "Price"
                ),

            "Overall Score":
                analysis.get(
                    "Overall Score"
                ),

            "Rating":
                analysis.get(
                    "Rating"
                ),

            "Committee Score":
                committee.get(
                    "Committee Score"
                ),

            "Recommendation":
                committee.get(
                    "Recommendation"
                ),

            "Confidence":
                committee.get(
                    "Confidence"
                ),

            "Specialist Scores":
                committee.get(
                    "Specialist Scores",
                    {},
                ),

            "Status":
                "OPEN",

            "Outcomes": {

                "1M": {},
                "3M": {},
                "6M": {},
                "12M": {},

            },
        }

        history.append(
            record
        )

        self._save(
            history
        )

        return record

    # --------------------------------
    # Current Price
    # --------------------------------

    def get_current_price(
        self,
        ticker,
    ):

        try:

            data = yf.Ticker(
                ticker
            )

            price = data.fast_info.get(
                "last_price"
            )

            if price is not None:

                return float(price)

        except Exception:

            pass

        try:

            data = self.history_client.history(
                ticker,
                period="1d",
            ).frame

            if not data.empty:

                return float(
                    data["Close"].iloc[-1]
                )

        except Exception:

            pass

        return None

    def get_horizon_price(self, ticker, target_date):
        """Return the first trading close on/after an exact calendar horizon."""
        try:
            data = self.history_client.history(
                ticker,
                start=target_date.date().isoformat(),
                end=(target_date + timedelta(days=10)).date().isoformat(),
            ).frame
            if data.empty:
                return None
            price = float(data["Close"].iloc[0])
            return price if price > 0 else None
        except Exception:
            return None

    # --------------------------------
    # Evaluate Predictions
    # --------------------------------

    def evaluate_predictions(self):

        history = self._load()

        evaluated = 0

        now = datetime.now()

        for record in history:

            ticker = record.get(
                "Ticker"
            )

            start_price = record.get(
                "Price At Decision"
            )

            decision_date = record.get(
                "Date"
            )

            if not ticker:
                continue

            if start_price is None:
                continue

            if not decision_date:
                continue

            try:

                start_price = float(
                    start_price
                )

                decision_datetime = (
                    datetime.fromisoformat(
                        decision_date
                    )
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            if start_price <= 0:
                continue

            evaluated_this_record = False

            outcomes = record.setdefault(
                "Outcomes",
                {},
            )

            # --------------------------------
            # Evaluate Each Horizon
            # --------------------------------

            for horizon, days in self.HORIZONS.items():

                existing = outcomes.get(
                    horizon,
                    {},
                )

                if existing.get(
                    "Evaluated"
                ):
                    continue

                elapsed_days = (
                    now
                    - decision_datetime
                ).days

                if elapsed_days < days:
                    continue

                target_date = decision_datetime + timedelta(days=days)
                horizon_price = self.get_horizon_price(ticker, target_date)
                if horizon_price is None:
                    continue

                return_pct = (

                    (
                        horizon_price
                        / start_price
                    ) - 1

                ) * 100

                outcome = (
                    self._classify_outcome(
                        record.get(
                            "Recommendation",
                            "",
                        ),
                        return_pct,
                    )
                )

                outcomes[
                    horizon
                ] = {

                    "Evaluated":
                        True,

                    "Price":
                        round(
                            horizon_price,
                            4,
                        ),

                    "Target Date":
                        target_date.date().isoformat(),

                    "Return %":
                        round(
                            return_pct,
                            2,
                        ),

                    "Outcome":
                        outcome,

                    "Evaluated At":
                        now.isoformat(),

                }

                evaluated_this_record = True

            if evaluated_this_record:

                evaluated += 1

            # --------------------------------
            # Prediction Status
            # --------------------------------

            completed = all(
                outcomes.get(
                    horizon,
                    {},
                ).get(
                    "Evaluated",
                    False,
                )
                for horizon in self.HORIZONS
            )

            if completed:

                record[
                    "Status"
                ] = "CLOSED"

            else:

                record[
                    "Status"
                ] = "OPEN"

        self._save(
            history
        )

        return {

            "Evaluated":
                evaluated,

            "Total Predictions":
                len(history),

            "History":
                history,
        }

    # --------------------------------
    # Outcome Classification
    # --------------------------------

    def _classify_outcome(
        self,
        recommendation,
        return_pct,
    ):

        recommendation = (
            recommendation.upper()
        )

        if recommendation == "STRONG BUY":

            if return_pct >= 5:
                return "CORRECT"

            if return_pct <= -5:
                return "INCORRECT"

            return "UNCLEAR"

        if recommendation == "BUY":

            if return_pct >= 3:
                return "CORRECT"

            if return_pct <= -3:
                return "INCORRECT"

            return "UNCLEAR"

        if recommendation == "HOLD / WATCH":

            if -5 <= return_pct <= 5:
                return "CORRECT"

            return "UNCLEAR"

        if (
            recommendation == "REDUCE / AVOID"
            or recommendation == "SELL / AVOID"
        ):

            if return_pct <= -3:
                return "CORRECT"

            if return_pct >= 3:
                return "INCORRECT"

            return "UNCLEAR"

        return "UNCLEAR"

    # --------------------------------
    # Overall Performance
    # --------------------------------

    def performance(
        self,
        horizon="3M",
    ):

        history = self._load()

        total = 0
        correct = 0
        incorrect = 0
        unclear = 0
        total_return = 0.0

        for record in history:

            outcome_data = record.get(
                "Outcomes",
                {},
            ).get(
                horizon,
                {},
            )

            if not outcome_data.get(
                "Evaluated"
            ):
                continue

            outcome = outcome_data.get(
                "Outcome"
            )

            return_pct = outcome_data.get(
                "Return %"
            )

            total += 1

            if outcome == "CORRECT":

                correct += 1

            elif outcome == "INCORRECT":

                incorrect += 1

            else:

                unclear += 1

            if return_pct is not None:

                total_return += float(
                    return_pct
                )

        accuracy = 0

        average_return = 0

        if total > 0:

            accuracy = round(
                (
                    correct
                    / total
                ) * 100,
                1,
            )

            average_return = round(
                total_return
                / total,
                2,
            )

        return {

            "Horizon":
                horizon,

            "Evaluated Predictions":
                total,

            "Correct":
                correct,

            "Incorrect":
                incorrect,

            "Unclear":
                unclear,

            "Accuracy":
                accuracy,

            "Average Return":
                average_return,
        }

    # --------------------------------
    # Specialist Performance
    # --------------------------------

    def specialist_performance(
        self,
        horizon="3M",
    ):

        history = self._load()

        results = {}

        for record in history:

            outcome_data = record.get(
                "Outcomes",
                {},
            ).get(
                horizon,
                {},
            )

            if not outcome_data.get(
                "Evaluated"
            ):
                continue

            outcome = outcome_data.get(
                "Outcome"
            )

            return_pct = outcome_data.get(
                "Return %"
            )

            scores = record.get(
                "Specialist Scores",
                {},
            )

            for analyst, score in scores.items():

                if analyst not in results:

                    results[
                        analyst
                    ] = {

                        "Predictions":
                            0,

                        "Correct":
                            0,

                        "Incorrect":
                            0,

                        "Average Return":
                            0.0,
                    }

                result = results[
                    analyst
                ]

                result[
                    "Predictions"
                ] += 1

                if outcome == "CORRECT":

                    result[
                        "Correct"
                    ] += 1

                elif outcome == "INCORRECT":

                    result[
                        "Incorrect"
                    ] += 1

                if return_pct is not None:

                    result[
                        "Average Return"
                    ] += float(
                        return_pct
                    )

        for analyst, result in results.items():

            predictions = result[
                "Predictions"
            ]

            if predictions == 0:
                continue

            result[
                "Average Return"
            ] = round(
                result[
                    "Average Return"
                ]
                / predictions,
                2,
            )

            result[
                "Accuracy"
            ] = round(
                (
                    result[
                        "Correct"
                    ]
                    / predictions
                ) * 100,
                1,
            )

        return results
