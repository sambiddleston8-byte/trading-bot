import math


class ExpectedReturnEngine:

    def __init__(
        self,
        horizon_days=252,
    ):

        self.horizon_days = (
            horizon_days
        )

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=0.0,
    ):

        try:

            if value is None:
                return default

            value = float(value)

            if math.isnan(value):
                return default

            if math.isinf(value):
                return default

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # FACTOR RETURN SIGNAL
    # ============================================================

    def factor_signal(
        self,
        factor_scores,
    ):

        if not factor_scores:

            return 0.0

        # --------------------------------------------------------
        # Convert factor scores into a centred signal.
        #
        # 50 = neutral
        # 100 = strongly positive
        # 0 = strongly negative
        # --------------------------------------------------------

        signals = {}

        for factor, score in (
            factor_scores.items()
        ):

            score = self.safe_float(
                score,
                50,
            )

            signals[factor] = (
                score - 50
            ) / 50

        # --------------------------------------------------------
        # Initial factor importance.
        #
        # These are deliberately conservative starting weights.
        # The Multi-Factor Engine remains responsible for its
        # primary scoring weights.
        # --------------------------------------------------------

        weights = {

            "Business Quality":
                0.15,

            "Financial Strength":
                0.10,

            "Valuation":
                0.15,

            "Growth":
                0.15,

            "Profitability":
                0.10,

            "Momentum":
                0.15,

            "Risk":
                0.10,

            "Size":
                0.02,

            "Balance Sheet":
                0.08,

            "Dividend":
                0.00,

        }

        numerator = 0.0
        denominator = 0.0

        for factor, weight in (
            weights.items()
        ):

            if factor not in signals:
                continue

            numerator += (
                signals[factor]
                * weight
            )

            denominator += weight

        if denominator <= 0:

            return 0.0

        return (
            numerator
            / denominator
        )

    # ============================================================
    # GROWTH SIGNAL
    # ============================================================

    def growth_signal(
        self,
        info,
    ):

        revenue_growth = (
            self.safe_float(
                info.get(
                    "revenueGrowth"
                ),
                0,
            )
        )

        earnings_growth = (
            self.safe_float(
                info.get(
                    "earningsGrowth"
                ),
                0,
            )
        )

        earnings_growth = max(
            -1,
            min(
                earnings_growth,
                2,
            ),
        )

        revenue_growth = max(
            -1,
            min(
                revenue_growth,
                2,
            ),
        )

        return (
            revenue_growth
            * 0.40
            +
            earnings_growth
            * 0.60
        )

    # ============================================================
    # VALUATION SIGNAL
    # ============================================================

    def valuation_signal(
        self,
        info,
    ):

        forward_pe = (
            self.safe_float(
                info.get(
                    "forwardPE"
                ),
                0,
            )
        )

        if forward_pe <= 0:

            return 0.0

        # Lower valuation receives a more positive signal.
        #
        # This is deliberately bounded so valuation cannot
        # dominate the entire expected-return model.

        if forward_pe <= 10:

            return 0.50

        if forward_pe <= 15:

            return 0.30

        if forward_pe <= 20:

            return 0.15

        if forward_pe <= 25:

            return 0.05

        if forward_pe <= 35:

            return -0.05

        if forward_pe <= 50:

            return -0.15

        return -0.25

    # ============================================================
    # MOMENTUM SIGNAL
    # ============================================================

    def momentum_signal(
        self,
        prices,
    ):

        if prices is None:

            return 0.0

        try:

            if len(prices) < 200:

                return 0.0

            close = prices["Close"]

            current = float(
                close.iloc[-1]
            )

            price_6m = float(
                close.iloc[-126]
            )

            price_12m = float(
                close.iloc[-252]
            )

            if (
                price_6m <= 0
                or price_12m <= 0
            ):

                return 0.0

            six_month_return = (
                current
                / price_6m
            ) - 1

            twelve_month_return = (
                current
                / price_12m
            ) - 1

            signal = (
                six_month_return
                * 0.60
                +
                twelve_month_return
                * 0.40
            )

            return max(
                -1,
                min(
                    signal,
                    1,
                ),
            )

        except Exception:

            return 0.0

    # ============================================================
    # RISK ADJUSTMENT
    # ============================================================

    def risk_adjustment(
        self,
        factor_scores,
    ):

        risk = self.safe_float(
            factor_scores.get(
                "Risk"
            ),
            50,
        )

        # Higher Risk score = better risk profile
        # within the Multi-Factor model.

        return (
            risk - 50
        ) / 50

    # ============================================================
    # EXPECTED RETURN
    # ============================================================

    def estimate(
        self,
        factor_scores,
        info=None,
        prices=None,
    ):

        if info is None:

            info = {}

        factor_signal = (
            self.factor_signal(
                factor_scores
            )
        )

        growth_signal = (
            self.growth_signal(
                info
            )
        )

        valuation_signal = (
            self.valuation_signal(
                info
            )
        )

        momentum_signal = (
            self.momentum_signal(
                prices
            )
        )

        risk_signal = (
            self.risk_adjustment(
                factor_scores
            )
        )

        # --------------------------------------------------------
        # Combine signals.
        #
        # This is a starting forecasting model, not a claim that
        # these inputs can deterministically predict returns.
        # --------------------------------------------------------

        combined_signal = (

            factor_signal
            * 0.45

            +
            growth_signal
            * 0.20

            +
            valuation_signal
            * 0.15

            +
            momentum_signal
            * 0.15

            +
            risk_signal
            * 0.05

        )

        # --------------------------------------------------------
        # Translate signal into expected return.
        #
        # The output is deliberately bounded while we gather
        # real historical evidence.
        # --------------------------------------------------------

        expected_return = (
            combined_signal
            * 0.40
        )

        expected_return = max(
            -0.50,
            min(
                expected_return,
                1.00,
            ),
        )

        confidence = self.confidence(
            factor_scores,
            info,
            prices,
        )

        return {

            "Expected Return":
                round(
                    expected_return
                    * 100,
                    2,
                ),

            "Confidence":
                round(
                    confidence,
                    2,
                ),

            "Horizon Days":
                self.horizon_days,

            "Factor Signal":
                round(
                    factor_signal,
                    4,
                ),

            "Growth Signal":
                round(
                    growth_signal,
                    4,
                ),

            "Valuation Signal":
                round(
                    valuation_signal,
                    4,
                ),

            "Momentum Signal":
                round(
                    momentum_signal,
                    4,
                ),

            "Risk Signal":
                round(
                    risk_signal,
                    4,
                ),

        }

    # ============================================================
    # FORECAST CONFIDENCE
    # ============================================================

    def confidence(
        self,
        factor_scores,
        info,
        prices,
    ):

        available = 0
        total = 5

        if factor_scores:

            available += 1

        if info.get(
            "forwardPE"
        ) is not None:

            available += 1

        if info.get(
            "revenueGrowth"
        ) is not None:

            available += 1

        if info.get(
            "earningsGrowth"
        ) is not None:

            available += 1

        try:

            if prices is not None:

                if len(prices) >= 200:

                    available += 1

        except Exception:

            pass

        confidence = (
            40
            +
            (
                available
                / total
            )
            * 40
        )

        return max(
            40,
            min(
                confidence,
                80,
            ),
        )


if __name__ == "__main__":

    engine = (
        ExpectedReturnEngine()
    )

    example = engine.estimate(

        factor_scores={

            "Business Quality": 90,
            "Financial Strength": 85,
            "Valuation": 70,
            "Growth": 88,
            "Profitability": 90,
            "Momentum": 80,
            "Risk": 75,
            "Size": 60,
            "Balance Sheet": 85,
            "Dividend": 50,

        },

        info={

            "forwardPE": 30,
            "revenueGrowth": 0.30,
            "earningsGrowth": 0.40,

        },

    )

    print(example)