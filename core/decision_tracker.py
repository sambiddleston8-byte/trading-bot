import json
import os

from core.outcome_engine import OutcomeEngine


class DecisionTracker:

    def __init__(
        self,
        outcome_engine=None,
        benchmark_symbol="^GSPC",
    ):

        self.outcome_engine = (
            outcome_engine
            or OutcomeEngine()
        )

        self.benchmark_symbol = (
            benchmark_symbol
        )

    # ============================================================
    # RECORD COMMITTEE DECISION
    # ============================================================

    def record_decision(
        self,
        decision,
        entry_price,
        benchmark_price=None,
        horizon_days=252,
    ):

        ticker = decision.get(
            "Ticker"
        )

        if not ticker:
            return None

        committee_score = float(
            decision.get(
                "Committee Score",
                decision.get(
                    "Overall Score",
                    0,
                ),
            )
        )

        recommendation = decision.get(
            "Recommendation",
            "",
        )

        confidence = float(
            decision.get(
                "Confidence",
                0,
            )
        )

        factor_scores = decision.get(
            "Factor Scores",
            decision.get(
                "Specialist Scores",
                {},
            ),
        )

        prediction = (
            self.outcome_engine.record_prediction(
                ticker=ticker,
                entry_price=entry_price,
                committee_score=committee_score,
                recommendation=recommendation,
                confidence=confidence,
                factor_scores=factor_scores,
                benchmark_price=benchmark_price,
                horizon_days=horizon_days,
            )
        )

        return prediction

    # ============================================================
    # RECORD PORTFOLIO DECISIONS
    # ============================================================

    def record_portfolio(
        self,
        portfolio,
        price_data,
        benchmark_price=None,
        horizon_days=252,
    ):

        recorded = []

        holdings = portfolio.get(
            "Holdings",
            [],
        )

        for position in holdings:

            ticker = position.get(
                "Ticker"
            )

            if not ticker:
                continue

            price = self.get_price(
                price_data,
                ticker,
            )

            if price is None:
                continue

            decision = {

                "Ticker":
                    ticker,

                "Committee Score":
                    position.get(
                        "Committee Score",
                        0,
                    ),

                "Recommendation":
                    position.get(
                        "Recommendation",
                        "",
                    ),

                "Confidence":
                    position.get(
                        "Confidence",
                        0,
                    ),

                "Factor Scores":
                    position.get(
                        "Factor Scores",
                        {},
                    ),

            }

            prediction = (
                self.record_decision(
                    decision=decision,
                    entry_price=price,
                    benchmark_price=benchmark_price,
                    horizon_days=horizon_days,
                )
            )

            if prediction:

                recorded.append(
                    prediction
                )

        return recorded

    # ============================================================
    # PRICE EXTRACTION
    # ============================================================

    def get_price(
        self,
        price_data,
        ticker,
    ):

        if price_data is None:
            return None

        if ticker not in price_data:
            return None

        data = price_data[
            ticker
        ]

        if data is None:
            return None

        try:

            if hasattr(
                data,
                "iloc",
            ):

                return float(
                    data["Close"].iloc[-1]
                )

            return float(
                data
            )

        except Exception:

            return None

    # ============================================================
    # CLOSE PREDICTION
    # ============================================================

    def close_prediction(
        self,
        prediction_id,
        exit_price,
        benchmark_exit_price=None,
    ):

        return (
            self.outcome_engine.close_prediction(
                prediction_id=prediction_id,
                exit_price=exit_price,
                benchmark_exit_price=benchmark_exit_price,
            )
        )

    # ============================================================
    # OPEN PREDICTIONS
    # ============================================================

    def open_predictions(self):

        return (
            self.outcome_engine.open_predictions()
        )

    # ============================================================
    # CLOSED PREDICTIONS
    # ============================================================

    def closed_predictions(self):

        return (
            self.outcome_engine.closed_predictions()
        )

    # ============================================================
    # REPORT
    # ============================================================

    def report(self):

        return (
            self.outcome_engine.performance_summary()
        )

    # ============================================================
    # PRINT
    # ============================================================

    def print_report(self):

        self.outcome_engine.print_report()


if __name__ == "__main__":

    tracker = DecisionTracker()

    tracker.print_report()