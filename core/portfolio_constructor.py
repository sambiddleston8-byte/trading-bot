import json
import os


class PortfolioConstructor:

    def __init__(
        self,
        capital=100000,
        max_positions=15,
        max_position_weight=0.25,
        min_position_weight=0.03,
        max_sector_weight=1.00,
        minimum_committee_score=65,
        cash_floor=0.00,
    ):

        self.capital = capital

        self.max_positions = (
            max_positions
        )

        self.max_position_weight = (
            max_position_weight
        )

        self.min_position_weight = (
            min_position_weight
        )

        # --------------------------------------------------------
        # Deliberately set to 100%.
        #
        # We are NOT imposing sector diversification.
        # If the model identifies exceptional opportunities
        # concentrated in one sector, the portfolio is allowed
        # to reflect that.
        # --------------------------------------------------------

        self.max_sector_weight = (
            max_sector_weight
        )

        self.minimum_committee_score = (
            minimum_committee_score
        )

        self.cash_floor = cash_floor

    # ============================================================
    # NORMALISE
    # ============================================================

    def normalise(
        self,
        values,
    ):

        total = sum(
            values.values()
        )

        if total <= 0:

            return {
                key: 0
                for key in values
            }

        return {

            key:
                value / total

            for key, value
            in values.items()

        }

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=0,
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
    # EXPECTED RETURN SCORE
    # ============================================================

    def expected_return_score(
        self,
        result,
    ):

        expected_return = (
            self.safe_float(
                result.get(
                    "Expected Return",
                    0,
                )
            )
        )

        confidence = (
            self.safe_float(
                result.get(
                    "Expected Return Confidence",
                    50,
                ),
                default=50,
            )
        )

        # --------------------------------------------------------
        # Convert expected return into a positive opportunity
        # score.
        #
        # 0% expected return  -> 50
        # 10% expected return -> 60
        # 20% expected return -> 70
        # 30% expected return -> 80
        # 40% expected return -> 90
        #
        # Negative expected returns reduce the score.
        # --------------------------------------------------------

        return_score = (
            50
            + (
                expected_return
                * 1.0
            )
        )

        return_score = max(
            0,
            min(
                100,
                return_score,
            )
        )

        # Confidence moderates the forecast.
        #
        # We do not want a 40% forecast with 50% confidence
        # to receive the same conviction as a 40% forecast
        # with 90% confidence.

        confidence_multiplier = (
            0.50
            + (
                max(
                    0,
                    min(
                        confidence,
                        100,
                    ),
                )
                / 200
            )
        )

        return (
            return_score
            * confidence_multiplier
        )

    # ============================================================
    # CONVICTION SCORE
    # ============================================================

    def conviction(
        self,
        result,
    ):

        committee_score = (
            self.safe_float(
                result.get(
                    "Committee Score",
                    result.get(
                        "Overall Score",
                        0,
                    ),
                )
            )
        )

        committee_confidence = (
            self.safe_float(
                result.get(
                    "Confidence",
                    75,
                ),
                default=75,
            )
        )

        expected_return_score = (
            self.expected_return_score(
                result
            )
        )

        # --------------------------------------------------------
        # Committee quality
        # --------------------------------------------------------

        committee_component = (
            max(
                0,
                committee_score - 50,
            )
            / 50
        )

        committee_component = min(
            committee_component,
            1.50,
        )

        # --------------------------------------------------------
        # Committee confidence
        # --------------------------------------------------------

        committee_confidence_component = (
            max(
                0,
                min(
                    committee_confidence,
                    100,
                ),
            )
            / 100
        )

        # --------------------------------------------------------
        # Expected return opportunity
        # --------------------------------------------------------

        expected_return_component = (
            expected_return_score
            / 100
        )

        # --------------------------------------------------------
        # Combine.
        #
        # Fundamental/committee quality remains the largest
        # component, while expected return has enough influence
        # to favour genuine high-upside opportunities.
        # --------------------------------------------------------

        conviction = (

            committee_component
            * 0.55

            +

            expected_return_component
            * 0.45

        )

        # Confidence moderates the final conviction rather than
        # completely determining it.

        confidence_multiplier = (

            0.60

            +

            (
                committee_confidence_component
                * 0.40
            )

        )

        conviction *= (
            confidence_multiplier
        )

        return max(
            0,
            conviction,
        )

    # ============================================================
    # RISK ADJUSTMENT
    # ============================================================

    def risk_adjustment(
        self,
        result,
    ):

        factors = result.get(
            "Factor Scores",
            {},
        )

        risk = factors.get(
            "Risk",
            None,
        )

        if risk is None:

            specialists = result.get(
                "Specialist Scores",
                {},
            )

            risk = specialists.get(
                "Risk",
                50,
            )

        try:

            risk = float(
                risk
            )

        except (
            TypeError,
            ValueError,
        ):

            risk = 50

        # --------------------------------------------------------
        # Risk remains a position-size modifier.
        #
        # We deliberately do NOT make risk an absolute veto.
        # A high-risk company can still qualify when the expected
        # return and other signals are exceptional.
        # --------------------------------------------------------

        if risk >= 85:

            return 1.00

        if risk >= 75:

            return 0.95

        if risk >= 65:

            return 0.90

        if risk >= 55:

            return 0.82

        if risk >= 45:

            return 0.70

        if risk >= 35:

            return 0.55

        if risk >= 25:

            return 0.35

        return 0.20

    # ============================================================
    # ELIGIBILITY
    # ============================================================

    def eligible(
        self,
        result,
    ):

        score = (
            self.safe_float(
                result.get(
                    "Committee Score",
                    result.get(
                        "Overall Score",
                        0,
                    ),
                )
            )
        )

        recommendation = result.get(
            "Recommendation",
            "",
        )

        if (
            score
            < self.minimum_committee_score
        ):

            return False

        if recommendation in (
            "SELL / AVOID",
            "REDUCE / AVOID",
        ):

            return False

        # --------------------------------------------------------
        # Negative expected-return opportunities should not be
        # allocated simply because their factor score is high.
        # --------------------------------------------------------

        expected_return = (
            self.safe_float(
                result.get(
                    "Expected Return",
                    0,
                )
            )
        )

        if expected_return <= 0:

            return False

        return True

    # ============================================================
    # BUILD RAW WEIGHTS
    # ============================================================

    def raw_weights(
        self,
        committee_results,
    ):

        weights = {}

        for result in (
            committee_results
        ):

            if not self.eligible(
                result
            ):

                continue

            ticker = result.get(
                "Ticker"
            )

            if not ticker:

                continue

            conviction = (
                self.conviction(
                    result
                )
            )

            risk = (
                self.risk_adjustment(
                    result
                )
            )

            weight = (
                conviction
                * risk
            )

            if weight <= 0:

                continue

            weights[ticker] = (
                weight
            )

        return self.normalise(
            weights
        )

    # ============================================================
    # APPLY POSITION LIMITS
    # ============================================================

    def apply_position_limits(
        self,
        weights,
    ):

        if not weights:

            return {}

        # --------------------------------------------------------
        # First cap every position.
        # --------------------------------------------------------

        capped = {}

        for ticker, weight in (
            weights.items()
        ):

            capped[ticker] = min(
                max(
                    0,
                    weight,
                ),
                self.max_position_weight,
            )

        maximum_invested = (
            1
            - self.cash_floor
        )

        # --------------------------------------------------------
        # Redistribute available weight toward the strongest
        # remaining opportunities.
        #
        # No position can exceed max_position_weight.
        # --------------------------------------------------------

        for _ in range(50):

            total_weight = sum(
                capped.values()
            )

            if total_weight >= (
                maximum_invested
                - 1e-12
            ):

                break

            uncapped = {

                ticker:
                    weight

                for ticker, weight
                in capped.items()

                if weight
                < (
                    self.max_position_weight
                    - 1e-12
                )

            }

            if not uncapped:

                break

            remaining = (
                maximum_invested
                - total_weight
            )

            uncapped_total = sum(
                uncapped.values()
            )

            if uncapped_total <= 0:

                break

            changed = False

            for ticker in uncapped:

                capacity = (
                    self.max_position_weight
                    - capped[ticker]
                )

                addition = (
                    remaining
                    * (
                        uncapped[ticker]
                        / uncapped_total
                    )
                )

                addition = min(
                    addition,
                    capacity,
                )

                if addition > 1e-12:

                    capped[ticker] += (
                        addition
                    )

                    changed = True

            if not changed:

                break

        return capped

    # ============================================================
    # MINIMUM POSITION FILTER
    # ============================================================

    def apply_minimum_weight(
        self,
        weights,
    ):

        result = {}

        for ticker, weight in (
            weights.items()
        ):

            if (
                weight
                >= self.min_position_weight
            ):

                result[ticker] = weight

        return result

    # ============================================================
    # SECTOR LIMITS
    # ============================================================

    def apply_sector_limits(
        self,
        weights,
        results,
    ):

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # There is deliberately NO sector diversification rule.
        #
        # The strategy is allowed to become heavily concentrated
        # in a sector if the model identifies the strongest
        # opportunities there.
        # --------------------------------------------------------

        return weights.copy()

    # ============================================================
    # FINAL NORMALISATION
    # ============================================================

    def finalise(
        self,
        weights,
    ):

        if not weights:

            return {
                "Cash": 1.0
            }

        final = {}

        # --------------------------------------------------------
        # Apply hard position limits one final time.
        # --------------------------------------------------------

        for ticker, weight in (
            weights.items()
        ):

            weight = max(
                0,
                min(
                    weight,
                    self.max_position_weight,
                ),
            )

            if weight <= 0:

                continue

            final[ticker] = weight

        maximum_invested = (
            1
            - self.cash_floor
        )

        total_invested = sum(
            final.values()
        )

        # --------------------------------------------------------
        # Never exceed the permitted invested capital.
        # --------------------------------------------------------

        if total_invested > (
            maximum_invested
        ):

            scale = (
                maximum_invested
                / total_invested
            )

            for ticker in final:

                final[ticker] *= scale

            total_invested = sum(
                final.values()
            )

        # --------------------------------------------------------
        # Remaining capital is cash.
        # --------------------------------------------------------

        cash = (
            1
            - total_invested
        )

        final["Cash"] = max(
            0,
            cash,
        )

        return final

    # ============================================================
    # BUILD PORTFOLIO
    # ============================================================

    def build(
        self,
        committee_results,
    ):

        raw = self.raw_weights(
            committee_results
        )

        limited = (
            self.apply_position_limits(
                raw
            )
        )

        minimum = (
            self.apply_minimum_weight(
                limited
            )
        )

        # --------------------------------------------------------
        # No sector restriction.
        # --------------------------------------------------------

        sector_adjusted = (
            self.apply_sector_limits(
                minimum,
                committee_results,
            )
        )

        # --------------------------------------------------------
        # Select the strongest opportunities.
        # --------------------------------------------------------

        ranked = sorted(
            sector_adjusted.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        ranked = ranked[
            :self.max_positions
        ]

        selected = dict(
            ranked
        )

        final_weights = (
            self.finalise(
                selected
            )
        )

        return self.attach_details(
            final_weights,
            committee_results,
        )

    # ============================================================
    # ATTACH POSITION DETAILS
    # ============================================================

    def attach_details(
        self,
        weights,
        results,
    ):

        lookup = {}

        for result in results:

            ticker = result.get(
                "Ticker"
            )

            if ticker:

                lookup[ticker] = result

        portfolio = []

        for ticker, weight in (
            weights.items()
        ):

            if ticker == "Cash":

                continue

            result = lookup.get(
                ticker,
                {},
            )

            capital = (
                self.capital
                * weight
            )

            portfolio.append({

                "Ticker":
                    ticker,

                "Weight %":
                    round(
                        weight * 100,
                        2,
                    ),

                "Capital":
                    round(
                        capital,
                        2,
                    ),

                "Committee Score":
                    result.get(
                        "Committee Score",
                        result.get(
                            "Overall Score"
                        ),
                    ),

                "Recommendation":
                    result.get(
                        "Recommendation",
                        "",
                    ),

                "Confidence":
                    result.get(
                        "Confidence",
                        0,
                    ),

                "Expected Return":
                    result.get(
                        "Expected Return",
                        0,
                    ),

                "Expected Return Confidence":
                    result.get(
                        "Expected Return Confidence",
                        0,
                    ),

                "Sector":
                    result.get(
                        "Sector",
                        "Unknown",
                    ),

            })

        portfolio.sort(
            key=lambda item:
                item["Weight %"],
            reverse=True,
        )

        cash_weight = weights.get(
            "Cash",
            0,
        )

        return {

            "Capital":
                self.capital,

            "Positions":
                len(portfolio),

            "Cash Weight %":
                round(
                    cash_weight * 100,
                    2,
                ),

            "Invested Weight %":
                round(
                    (
                        1
                        - cash_weight
                    )
                    * 100,
                    2,
                ),

            "Holdings":
                portfolio,

        }

    # ============================================================
    # SAVE
    # ============================================================

    def save(
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
            )

        return path

    # ============================================================
    # PRINT
    # ============================================================

    def print_portfolio(
        self,
        portfolio,
    ):

        print()
        print("=" * 80)
        print(
            "PORTFOLIO CONSTRUCTION"
        )
        print("=" * 80)

        print()

        print(
            f"Capital: "
            f"${portfolio['Capital']:,.2f}"
        )

        print(
            f"Positions: "
            f"{portfolio['Positions']}"
        )

        print(
            f"Invested: "
            f"{portfolio['Invested Weight %']:.2f}%"
        )

        print(
            f"Cash: "
            f"{portfolio['Cash Weight %']:.2f}%"
        )

        print()

        print(
            "HOLDINGS"
        )

        print()

        for position in (
            portfolio["Holdings"]
        ):

            print(
                f"{position['Ticker']:<6} "
                f"{position['Weight %']:>6.2f}% "
                f"${position['Capital']:>10,.2f} "
                f"Score "
                f"{position['Committee Score']:>6.2f} "
                f"ER "
                f"{position['Expected Return']:>6.2f}% "
                f"{position['Sector']}"
            )

    # ============================================================
    # RUN
    # ============================================================

    def run(
        self,
        committee_results,
    ):

        portfolio = self.build(
            committee_results
        )

        self.print_portfolio(
            portfolio
        )

        path = self.save(
            portfolio
        )

        print()

        print(
            f"Saved to: {path}"
        )

        return portfolio