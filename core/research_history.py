import json
import os
from datetime import datetime, timezone


class ResearchHistory:

    def __init__(
        self,
        history_directory="data/research/history",
    ):

        self.history_directory = (
            history_directory
        )

        os.makedirs(
            self.history_directory,
            exist_ok=True,
        )

    # ============================================================
    # TIME
    # ============================================================

    def utc_now(
        self,
    ):

        return datetime.now(
            timezone.utc
        )

    # ============================================================
    # TIMESTAMP
    # ============================================================

    def timestamp(
        self,
    ):

        return self.utc_now().isoformat()

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=None,
    ):

        try:

            if value is None:

                return default

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # LOAD
    # ============================================================

    def load_history(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        path = os.path.join(
            self.history_directory,
            f"{symbol}.json",
        )

        if not os.path.exists(
            path
        ):

            return []

        try:

            with open(
                path,
                "r",
            ) as file:

                data = json.load(
                    file
                )

            if not isinstance(
                data,
                list,
            ):

                return []

            return data

        except Exception as error:

            print(
                f"Could not load history "
                f"for {symbol}: {error}"
            )

            return []

    # ============================================================
    # SAVE
    # ============================================================

    def save_history(
        self,
        symbol,
        history,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        path = os.path.join(
            self.history_directory,
            f"{symbol}.json",
        )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                history,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # BUILD SNAPSHOT
    # ============================================================

    def build_snapshot(
        self,
        research,
    ):

        ticker = (
            research.get(
                "Ticker"
            )
        )

        if not ticker:

            raise ValueError(
                "Research result does not contain Ticker."
            )

        decision = (
            research.get(
                "Decision",
                {},
            )
        )

        fundamental = (
            research.get(
                "Fundamental Research",
                {},
            )
        )

        valuation = (
            research.get(
                "Valuation Research",
                {},
            )
        )

        catalysts = (
            research.get(
                "Catalyst Research",
                {},
            )
        )

        expected_returns = (
            research.get(
                "Expected Returns",
                {},
            )
        )

        factor_model = (
            expected_returns.get(
                "Factor Model",
                {},
            )
        )

        intrinsic_model = (
            expected_returns.get(
                "Intrinsic Value Model",
                {},
            )
        )

        # ========================================================
        # CURRENT PRICE
        # ========================================================

        current_price = (
            self.safe_float(
                valuation.get(
                    "Current Price"
                )
            )
        )

        # ========================================================
        # SNAPSHOT
        # ========================================================

        snapshot = {

            "Snapshot ID":
                (
                    f"{ticker}_"
                    f"{self.utc_now().strftime('%Y%m%d%H%M%S')}"
                ),

            "Ticker":
                ticker,

            "Company":
                research.get(
                    "Company"
                ),

            "Sector":
                research.get(
                    "Sector"
                ),

            "Industry":
                research.get(
                    "Industry"
                ),

            "Timestamp":
                self.timestamp(),

            # ----------------------------------------------------
            # Market state
            # ----------------------------------------------------

            "Market": {

                "Current Price":
                    current_price,

            },

            # ----------------------------------------------------
            # Investment decision
            # ----------------------------------------------------

            "Decision": {

                "Committee Score":
                    decision.get(
                        "Committee Score"
                    ),

                "Recommendation":
                    decision.get(
                        "Recommendation"
                    ),

                "Confidence":
                    decision.get(
                        "Confidence"
                    ),

            },

            # ----------------------------------------------------
            # Fundamental model
            # ----------------------------------------------------

            "Fundamentals": {

                "Overall Score":
                    fundamental.get(
                        "Overall Score"
                    ),

                "Factor Scores":
                    fundamental.get(
                        "Factor Scores",
                        {},
                    ),

                "Weights":
                    fundamental.get(
                        "Weights",
                        {},
                    ),

            },

            # ----------------------------------------------------
            # Valuation
            # ----------------------------------------------------

            "Valuation": {

                "Current Price":
                    current_price,

                "Bear Value":
                    valuation.get(
                        "Bear Value"
                    ),

                "Base Value":
                    valuation.get(
                        "Base Value"
                    ),

                "Bull Value":
                    valuation.get(
                        "Bull Value"
                    ),

                "Base Return":
                    valuation.get(
                        "Base Return"
                    ),

                "Base Annualised Return":
                    valuation.get(
                        "Base Annualised Return"
                    ),

                "Horizon Years":
                    valuation.get(
                        "Horizon Years"
                    ),

                "Growth Assumption":
                    valuation.get(
                        "Growth Assumption"
                    ),

                "FCF Margin":
                    valuation.get(
                        "FCF Margin"
                    ),

            },

            # ----------------------------------------------------
            # Catalysts
            # ----------------------------------------------------

            "Catalysts": {

                "Catalyst Score":
                    catalysts.get(
                        "Catalyst Score"
                    ),

                "Catalyst Count":
                    catalysts.get(
                        "Catalyst Count"
                    ),

                "Upcoming":
                    catalysts.get(
                        "Upcoming Catalysts",
                        [],
                    ),

            },

            # ----------------------------------------------------
            # Expected return forecasts
            # ----------------------------------------------------

            "Expected Returns": {

                "Factor Model": {

                    "Expected Return":
                        factor_model.get(
                            "Expected Return"
                        ),

                    "Horizon Days":
                        factor_model.get(
                            "Horizon Days"
                        ),

                    "Confidence":
                        factor_model.get(
                            "Confidence"
                        ),

                },

                "Intrinsic Value Model": {

                    "Expected Return":
                        intrinsic_model.get(
                            "Expected Return"
                        ),

                    "Annualised Return":
                        intrinsic_model.get(
                            "Annualised Return"
                        ),

                    "Horizon Years":
                        intrinsic_model.get(
                            "Horizon Years"
                        ),

                },

            },

        }

        return snapshot

    # ============================================================
    # RECORD
    # ============================================================

    def record(
        self,
        research,
    ):

        snapshot = (
            self.build_snapshot(
                research
            )
        )

        ticker = (
            snapshot[
                "Ticker"
            ]
        )

        history = (
            self.load_history(
                ticker
            )
        )

        history.append(
            snapshot
        )

        path = (
            self.save_history(
                ticker,
                history,
            )
        )

        return {

            "Snapshot":
                snapshot,

            "History Count":
                len(history),

            "Path":
                path,

        }

    # ============================================================
    # LATEST SNAPSHOT
    # ============================================================

    def latest(
        self,
        symbol,
    ):

        history = (
            self.load_history(
                symbol
            )
        )

        if not history:

            return None

        return history[-1]

    # ============================================================
    # PREVIOUS SNAPSHOT
    # ============================================================

    def previous(
        self,
        symbol,
    ):

        history = (
            self.load_history(
                symbol
            )
        )

        if len(history) < 2:

            return None

        return history[-2]

    # ============================================================
    # CHANGE BETWEEN SNAPSHOTS
    # ============================================================

    def compare(
        self,
        current,
        previous,
    ):

        if not current:

            return {

                "Status":
                    "NO CURRENT SNAPSHOT",

            }

        if not previous:

            return {

                "Status":
                    "NO PREVIOUS SNAPSHOT",

            }

        current_decision = (
            current.get(
                "Decision",
                {},
            )
        )

        previous_decision = (
            previous.get(
                "Decision",
                {},
            )
        )

        current_fundamentals = (
            current.get(
                "Fundamentals",
                {},
            )
        )

        previous_fundamentals = (
            previous.get(
                "Fundamentals",
                {},
            )
        )

        current_valuation = (
            current.get(
                "Valuation",
                {},
            )
        )

        previous_valuation = (
            previous.get(
                "Valuation",
                {},
            )
        )

        # ========================================================
        # DECISION CHANGES
        # ========================================================

        current_score = (
            self.safe_float(
                current_decision.get(
                    "Committee Score"
                )
            )
        )

        previous_score = (
            self.safe_float(
                previous_decision.get(
                    "Committee Score"
                )
            )
        )

        score_change = None

        if (
            current_score is not None
            and previous_score is not None
        ):

            score_change = (
                current_score
                - previous_score
            )

        # ========================================================
        # PRICE CHANGE
        # ========================================================

        current_price = (
            self.safe_float(
                current.get(
                    "Market",
                    {},
                ).get(
                    "Current Price"
                )
            )
        )

        previous_price = (
            self.safe_float(
                previous.get(
                    "Market",
                    {},
                ).get(
                    "Current Price"
                )
            )
        )

        price_change = None
        price_change_percent = None

        if (
            current_price is not None
            and previous_price is not None
            and previous_price > 0
        ):

            price_change = (
                current_price
                - previous_price
            )

            price_change_percent = (
                (
                    current_price
                    / previous_price
                )
                - 1
            ) * 100

        # ========================================================
        # INTRINSIC VALUE CHANGE
        # ========================================================

        current_intrinsic = (
            self.safe_float(
                current_valuation.get(
                    "Base Value"
                )
            )
        )

        previous_intrinsic = (
            self.safe_float(
                previous_valuation.get(
                    "Base Value"
                )
            )
        )

        intrinsic_change = None

        if (
            current_intrinsic is not None
            and previous_intrinsic is not None
        ):

            intrinsic_change = (
                current_intrinsic
                - previous_intrinsic
            )

        # ========================================================
        # FACTOR CHANGES
        # ========================================================

        current_factors = (
            current_fundamentals.get(
                "Factor Scores",
                {},
            )
        )

        previous_factors = (
            previous_fundamentals.get(
                "Factor Scores",
                {},
            )
        )

        factor_changes = {}

        factors = set(
            current_factors
        ) | set(
            previous_factors
        )

        for factor in factors:

            current_value = (
                self.safe_float(
                    current_factors.get(
                        factor
                    )
                )
            )

            previous_value = (
                self.safe_float(
                    previous_factors.get(
                        factor
                    )
                )
            )

            if (
                current_value is not None
                and previous_value is not None
            ):

                factor_changes[
                    factor
                ] = round(
                    current_value
                    - previous_value,
                    2,
                )

        # ========================================================
        # RETURN
        # ========================================================

        return {

            "Status":
                "COMPARED",

            "Current Timestamp":
                current.get(
                    "Timestamp"
                ),

            "Previous Timestamp":
                previous.get(
                    "Timestamp"
                ),

            "Decision": {

                "Current":
                    current_decision.get(
                        "Recommendation"
                    ),

                "Previous":
                    previous_decision.get(
                        "Recommendation"
                    ),

                "Score Change":
                    score_change,

            },

            "Price": {

                "Current":
                    current_price,

                "Previous":
                    previous_price,

                "Change":
                    price_change,

                "Change Percent":
                    price_change_percent,

            },

            "Intrinsic Value": {

                "Current":
                    current_intrinsic,

                "Previous":
                    previous_intrinsic,

                "Change":
                    intrinsic_change,

            },

            "Factor Changes":
                factor_changes,

        }

    # ============================================================
    # HISTORY REPORT
    # ============================================================

    def report(
        self,
        symbol,
    ):

        history = (
            self.load_history(
                symbol
            )
        )

        if not history:

            return {

                "Ticker":
                    symbol.upper(),

                "Status":
                    "NO HISTORY",

                "Snapshots":
                    0,

            }

        current = (
            history[-1]
        )

        previous = None

        if len(history) >= 2:

            previous = (
                history[-2]
            )

        comparison = (
            self.compare(
                current,
                previous,
            )
        )

        return {

            "Ticker":
                symbol.upper(),

            "Status":
                "AVAILABLE",

            "Snapshots":
                len(history),

            "Latest":
                current,

            "Previous":
                previous,

            "Comparison":
                comparison,

        }

    # ============================================================
    # PRINT REPORT
    # ============================================================

    def print_report(
        self,
        report,
    ):

        print()
        print("=" * 80)
        print(
            "RESEARCH HISTORY"
        )
        print("=" * 80)

        print()

        print(
            f"Ticker: "
            f"{report.get('Ticker')}"
        )

        print(
            f"Snapshots: "
            f"{report.get('Snapshots', 0)}"
        )

        comparison = (
            report.get(
                "Comparison",
                {},
            )
        )

        if (
            comparison.get(
                "Status"
            )
            == "COMPARED"
        ):

            decision = (
                comparison.get(
                    "Decision",
                    {},
                )
            )

            price = (
                comparison.get(
                    "Price",
                    {},
                )
            )

            valuation = (
                comparison.get(
                    "Intrinsic Value",
                    {},
                )
            )

            print()

            print(
                "DECISION"
            )

            print(
                f"Current: "
                f"{decision.get('Current')}"
            )

            print(
                f"Previous: "
                f"{decision.get('Previous')}"
            )

            print(
                f"Score change: "
                f"{decision.get('Score Change')}"
            )

            print()

            print(
                "PRICE"
            )

            print(
                f"Current: "
                f"{price.get('Current')}"
            )

            print(
                f"Previous: "
                f"{price.get('Previous')}"
            )

            print(
                f"Change: "
                f"{price.get('Change Percent')}%"
            )

            print()

            print(
                "INTRINSIC VALUE"
            )

            print(
                f"Current: "
                f"{valuation.get('Current')}"
            )

            print(
                f"Previous: "
                f"{valuation.get('Previous')}"
            )

            print(
                f"Change: "
                f"{valuation.get('Change')}"
            )

            print()

            print(
                "FACTOR CHANGES"
            )

            for factor, change in (
                comparison.get(
                    "Factor Changes",
                    {},
                ).items()
            ):

                print(
                    f"{factor:<25}"
                    f"{change:>8.2f}"
                )

        else:

            print()

            print(
                "A previous snapshot is "
                "required before changes "
                "can be calculated."
            )


if __name__ == "__main__":

    engine = (
        ResearchHistory()
    )

    report = (
        engine.report(
            "NVDA"
        )
    )

    engine.print_report(
        report
    )