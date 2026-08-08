import json
import os

from core.multi_factor_engine import MultiFactorEngine
from core.investment_committee import InvestmentCommittee
from core.portfolio_constructor import PortfolioConstructor
from core.risk_engine import RiskEngine
from core.decision_tracker import DecisionTracker
from core.expected_return_tracker import ExpectedReturnTracker


class PortfolioPipeline:

    def __init__(
        self,
        capital=100000,
    ):

        self.capital = capital

        self.multi_factor = (
            MultiFactorEngine()
        )

        self.committee = (
            InvestmentCommittee()
        )

        self.constructor = (
            PortfolioConstructor(
                capital=capital
            )
        )

        self.risk = (
            RiskEngine()
        )

        # Existing portfolio decision tracker.
        self.tracker = (
            DecisionTracker()
        )

        # New expected-return prediction tracker.
        self.expected_return_tracker = (
            ExpectedReturnTracker()
        )

    # ============================================================
    # BUILD COMMITTEE INPUT
    # ============================================================

    def build_committee_input(
        self,
        result,
    ):

        return {

            "Ticker":
                result.get(
                    "Ticker"
                ),

            "Company":
                result.get(
                    "Company"
                ),

            "Sector":
                result.get(
                    "Sector"
                ),

            "Industry":
                result.get(
                    "Industry"
                ),

            "Overall Score":
                result.get(
                    "Overall Score",
                    0,
                ),

            "Factor Scores":
                result.get(
                    "Factor Scores",
                    {},
                ),

            "Weights":
                result.get(
                    "Weights",
                    {},
                ),

            # ----------------------------------------------------
            # Expected Return
            # ----------------------------------------------------

            "Expected Return":
                result.get(
                    "Expected Return",
                    0,
                ),

            "Expected Return Confidence":
                result.get(
                    "Expected Return Confidence",
                    50,
                ),

            "Expected Return Analysis":
                result.get(
                    "Expected Return Analysis",
                    {},
                ),

        }

    # ============================================================
    # RUN INVESTMENT COMMITTEE
    # ============================================================

    def run_committee(
        self,
        factor_results,
    ):

        committee_results = []

        for result in factor_results:

            analysis = (
                self.build_committee_input(
                    result
                )
            )

            decision = (
                self.committee.review(
                    analysis
                )
            )

            combined = {

                "Ticker":
                    result.get(
                        "Ticker"
                    ),

                "Company":
                    result.get(
                        "Company"
                    ),

                "Sector":
                    result.get(
                        "Sector"
                    ),

                "Industry":
                    result.get(
                        "Industry"
                    ),

                "Overall Score":
                    result.get(
                        "Overall Score",
                        0,
                    ),

                # ------------------------------------------------
                # Committee
                # ------------------------------------------------

                "Committee Score":
                    decision.get(
                        "Committee Score",
                        0,
                    ),

                "Recommendation":
                    decision.get(
                        "Recommendation",
                        "",
                    ),

                "Confidence":
                    decision.get(
                        "Confidence",
                        0,
                    ),

                "Adaptive Weights":
                    decision.get(
                        "Adaptive Weights",
                        result.get(
                            "Weights",
                            {},
                        ),
                    ),

                # ------------------------------------------------
                # Factors
                # ------------------------------------------------

                "Factor Scores":
                    decision.get(
                        "Factor Scores",
                        result.get(
                            "Factor Scores",
                            {},
                        ),
                    ),

                "Specialist Scores":
                    decision.get(
                        "Specialist Scores",
                        result.get(
                            "Factor Scores",
                            {},
                        ),
                    ),

                # ------------------------------------------------
                # Expected Return
                # ------------------------------------------------

                "Expected Return":
                    decision.get(
                        "Expected Return",
                        result.get(
                            "Expected Return",
                            0,
                        ),
                    ),

                "Expected Return Score":
                    decision.get(
                        "Expected Return Score",
                        50,
                    ),

                "Expected Return Confidence":
                    decision.get(
                        "Expected Return Confidence",
                        result.get(
                            "Expected Return Confidence",
                            50,
                        ),
                    ),

                "Expected Return Analysis":
                    decision.get(
                        "Expected Return Analysis",
                        result.get(
                            "Expected Return Analysis",
                            {},
                        ),
                    ),

                # ------------------------------------------------
                # Learning
                # ------------------------------------------------

                "Learning Enabled":
                    decision.get(
                        "Learning Enabled",
                        False,
                    ),

                "Strengths":
                    decision.get(
                        "Strengths",
                        [],
                    ),

                "Concerns":
                    decision.get(
                        "Concerns",
                        [],
                    ),

                "Summary":
                    decision.get(
                        "Summary",
                        "",
                    ),

            }

            committee_results.append(
                combined
            )

        committee_results.sort(
            key=lambda item:
                item.get(
                    "Committee Score",
                    0,
                ),
            reverse=True,
        )

        for rank, result in enumerate(
            committee_results,
            start=1,
        ):

            result["Rank"] = rank

        return committee_results

    # ============================================================
    # BUILD PORTFOLIO
    # ============================================================

    def build_portfolio(
        self,
        committee_results,
    ):

        return self.constructor.build(
            committee_results
        )

    # ============================================================
    # GET CURRENT PRICES
    # ============================================================

    def get_current_prices(
        self,
        symbols,
    ):

        prices = {}

        for symbol in symbols:

            try:

                info = (
                    self.multi_factor.get_info(
                        symbol
                    )
                )

                price = info.get(
                    "currentPrice"
                )

                if price is not None:

                    prices[symbol] = float(
                        price
                    )

            except Exception:

                continue

        return prices

    # ============================================================
    # GET BENCHMARK PRICE
    # ============================================================

    def get_benchmark_price(
        self,
    ):

        try:

            info = (
                self.multi_factor.get_info(
                    "^GSPC"
                )
            )

            price = info.get(
                "currentPrice"
            )

            if price is not None:

                return float(
                    price
                )

        except Exception:

            pass

        return None

    # ============================================================
    # RECORD EXPECTED RETURN PREDICTIONS
    # ============================================================

    def record_expected_return_predictions(
        self,
        committee_results,
    ):

        recorded = []

        for result in (
            committee_results
        ):

            try:

                prediction = (
                    self.expected_return_tracker.record(
                        result
                    )
                )

                if prediction:

                    recorded.append(
                        prediction
                    )

            except Exception as error:

                ticker = result.get(
                    "Ticker",
                    "UNKNOWN",
                )

                print(
                    f"{ticker}: expected-return "
                    f"prediction recording failed: "
                    f"{error}"
                )

        return recorded

    # ============================================================
    # RECORD PORTFOLIO DECISIONS
    # ============================================================

    def record_decisions(
        self,
        portfolio,
        committee_results,
        prices,
        benchmark_price=None,
    ):

        committee_lookup = {}

        for result in (
            committee_results
        ):

            ticker = result.get(
                "Ticker"
            )

            if ticker:

                committee_lookup[
                    ticker
                ] = result

        recorded = []

        for holding in (
            portfolio.get(
                "Holdings",
                [],
            )
        ):

            ticker = holding.get(
                "Ticker"
            )

            if not ticker:

                continue

            price = prices.get(
                ticker
            )

            if price is None:

                continue

            decision = (
                committee_lookup.get(
                    ticker
                )
            )

            if decision is None:

                continue

            prediction = (
                self.tracker.record_decision(
                    decision=decision,
                    entry_price=price,
                    benchmark_price=benchmark_price,
                    horizon_days=252,
                )
            )

            if prediction:

                recorded.append(
                    prediction
                )

        return recorded

    # ============================================================
    # SAVE COMMITTEE RESULTS
    # ============================================================

    def save_committee_results(
        self,
        results,
        path="data/final_investment_rankings.json",
    ):

        directory = os.path.dirname(
            path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

        output = {

            "Stock Count":
                len(results),

            "Rankings":
                results,

        }

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # SAVE PORTFOLIO
    # ============================================================

    def save_portfolio(
        self,
        portfolio,
        path="data/portfolio.json",
    ):

        directory = os.path.dirname(
            path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                portfolio,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # SAVE RISK REPORT
    # ============================================================

    def save_risk_report(
        self,
        report,
        path="data/risk_report.json",
    ):

        directory = os.path.dirname(
            path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                report,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # RUN
    # ============================================================

    def run(
        self,
        symbols,
    ):

        print()
        print("=" * 80)
        print("PORTFOLIO PIPELINE")
        print("=" * 80)

        # --------------------------------------------------------
        # STEP 1 — MULTI-FACTOR ANALYSIS
        # --------------------------------------------------------

        print()
        print(
            "STEP 1 — MULTI-FACTOR ANALYSIS"
        )

        factor_results = (
            self.multi_factor.analyse_universe(
                symbols
            )
        )

        print()
        print(
            f"Analysed "
            f"{len(factor_results)} stocks."
        )

        # --------------------------------------------------------
        # STEP 2 — INVESTMENT COMMITTEE
        # --------------------------------------------------------

        print()
        print(
            "STEP 2 — INVESTMENT COMMITTEE"
        )

        committee_results = (
            self.run_committee(
                factor_results
            )
        )

        self.save_committee_results(
            committee_results
        )

        print()
        print(
            "TOP COMMITTEE DECISIONS"
        )

        print()

        for rank, result in enumerate(
            committee_results[:20],
            start=1,
        ):

            print(
                f"{rank:>2}. "
                f"{result['Ticker']:<6} "
                f"{result['Committee Score']:>6.2f} "
                f"{result['Recommendation']:<15} "
                f"{result['Confidence']:>5.1f}% "
                f"ER: "
                f"{result.get('Expected Return', 0):>6.2f}%"
            )

        # --------------------------------------------------------
        # STEP 3 — PORTFOLIO CONSTRUCTION
        # --------------------------------------------------------

        print()
        print(
            "STEP 3 — PORTFOLIO CONSTRUCTION"
        )

        portfolio = (
            self.build_portfolio(
                committee_results
            )
        )

        self.constructor.print_portfolio(
            portfolio
        )

        self.save_portfolio(
            portfolio
        )

        # --------------------------------------------------------
        # STEP 4 — RISK REVIEW
        # --------------------------------------------------------

        print()
        print(
            "STEP 4 — RISK REVIEW"
        )

        risk_report = (
            self.risk.review(
                portfolio
            )
        )

        self.risk.print_report(
            risk_report
        )

        self.save_risk_report(
            risk_report
        )

        # --------------------------------------------------------
        # STEP 5 — EXPECTED RETURN RECORDING
        # --------------------------------------------------------

        print()
        print(
            "STEP 5 — EXPECTED RETURN RECORDING"
        )

        expected_return_predictions = (
            self.record_expected_return_predictions(
                committee_results
            )
        )

        print(
            f"Expected-return predictions recorded: "
            f"{len(expected_return_predictions)}"
        )

        # --------------------------------------------------------
        # STEP 6 — PORTFOLIO DECISION RECORDING
        # --------------------------------------------------------

        print()
        print(
            "STEP 6 — DECISION RECORDING"
        )

        selected_symbols = [

            holding.get(
                "Ticker"
            )

            for holding
            in portfolio.get(
                "Holdings",
                [],
            )

            if holding.get(
                "Ticker"
            )

        ]

        prices = (
            self.get_current_prices(
                selected_symbols
            )
        )

        benchmark_price = (
            self.get_benchmark_price()
        )

        recorded = (
            self.record_decisions(
                portfolio,
                committee_results,
                prices,
                benchmark_price,
            )
        )

        print(
            f"Portfolio predictions recorded: "
            f"{len(recorded)}"
        )

        if benchmark_price is not None:

            print(
                f"S&P 500 entry price: "
                f"{benchmark_price:.2f}"
            )

        else:

            print(
                "S&P 500 entry price: "
                "unavailable"
            )

        # --------------------------------------------------------
        # FINAL STATUS
        # --------------------------------------------------------

        print()
        print("=" * 80)

        if risk_report.get(
            "Pass",
            False,
        ):

            print(
                "PORTFOLIO STATUS: APPROVED"
            )

        else:

            print(
                "PORTFOLIO STATUS: "
                "RISK REVIEW REQUIRED"
            )

        print("=" * 80)

        return {

            "Factor Results":
                factor_results,

            "Committee Results":
                committee_results,

            "Portfolio":
                portfolio,

            "Risk Report":
                risk_report,

            "Expected Return Predictions":
                expected_return_predictions,

            "Recorded Predictions":
                recorded,

        }


if __name__ == "__main__":

    from core.stock_universe import (
        StockUniverse
    )

    universe = (
        StockUniverse()
    )

    symbols = (
        universe.load()
    )

    if not symbols:

        symbols = (
            universe.build()
        )

    pipeline = (
        PortfolioPipeline(
            capital=100000
        )
    )

    pipeline.run(
        symbols
    )