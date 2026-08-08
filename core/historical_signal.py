import pandas as pd


class HistoricalSignalEngine:

    def __init__(
        self,
        fast_period=20,
        slow_period=50,
        momentum_period=20,
    ):

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.momentum_period = momentum_period

    def calculate_indicators(
        self,
        prices,
    ):

        data = prices.copy()

        if "Close" not in data.columns:

            raise ValueError(
                "Price data must contain a Close column."
            )

        data["Fast MA"] = (
            data["Close"]
            .rolling(
                self.fast_period
            )
            .mean()
        )

        data["Slow MA"] = (
            data["Close"]
            .rolling(
                self.slow_period
            )
            .mean()
        )

        data["Momentum"] = (
            data["Close"]
            / data["Close"].shift(
                self.momentum_period
            )
            - 1
        ) * 100

        return data

    def generate_signal(
        self,
        prices,
    ):

        data = self.calculate_indicators(
            prices
        )

        if len(data) < self.slow_period:

            return {

                "Signal":
                    "INSUFFICIENT DATA",

                "Score":
                    50,

            }

        latest = data.iloc[-1]

        fast_ma = latest[
            "Fast MA"
        ]

        slow_ma = latest[
            "Slow MA"
        ]

        momentum = latest[
            "Momentum"
        ]

        if pd.isna(
            fast_ma
        ) or pd.isna(
            slow_ma
        ):

            return {

                "Signal":
                    "INSUFFICIENT DATA",

                "Score":
                    50,

            }

        score = 50

        # --------------------------------
        # Trend
        # --------------------------------

        if fast_ma > slow_ma:

            score += 20

        else:

            score -= 20

        # --------------------------------
        # Momentum
        # --------------------------------

        if momentum > 15:

            score += 20

        elif momentum > 5:

            score += 10

        elif momentum < -15:

            score -= 20

        elif momentum < -5:

            score -= 10

        # --------------------------------
        # Clamp Score
        # --------------------------------

        score = max(
            0,
            min(
                score,
                100,
            ),
        )

        # --------------------------------
        # Signal
        # --------------------------------

        if score >= 85:

            signal = "STRONG BUY"

        elif score >= 75:

            signal = "BUY"

        elif score >= 65:

            signal = "HOLD"

        else:

            signal = "AVOID"

        return {

            "Signal":
                signal,

            "Score":
                score,

            "Fast MA":
                round(
                    float(fast_ma),
                    2,
                ),

            "Slow MA":
                round(
                    float(slow_ma),
                    2,
                ),

            "Momentum":
                round(
                    float(momentum),
                    2,
                ),

        }