from core.company_context import CompanyContext


class TechnicalAnalyser:

    def analyse(self, context: CompanyContext):

        history = context.history

        if history is None or history.empty:

            return {
                "Momentum": 50,
                "Moving Average": 50,
                "Technical Score": 50,
            }

        close = history["Close"].dropna()

        if len(close) < 50:

            return {
                "Momentum": 50,
                "Moving Average": 50,
                "Technical Score": 50,
            }

        # --------------------------------
        # Momentum
        # --------------------------------

        momentum_score = 50

        if len(close) >= 60:

            price_now = close.iloc[-1]
            price_previous = close.iloc[-60]

            momentum = (
                (price_now / price_previous) - 1
            ) * 100

            if momentum >= 20:
                momentum_score = 100

            elif momentum >= 10:
                momentum_score = 80

            elif momentum >= 0:
                momentum_score = 60

            elif momentum >= -10:
                momentum_score = 40

            else:
                momentum_score = 20

        # --------------------------------
        # Moving Average
        # --------------------------------

        moving_average_score = 50

        current_price = close.iloc[-1]

        ma_50 = close.tail(50).mean()

        if current_price > ma_50 * 1.05:

            moving_average_score = 100

        elif current_price > ma_50:

            moving_average_score = 80

        elif current_price > ma_50 * 0.95:

            moving_average_score = 50

        else:

            moving_average_score = 25

        # --------------------------------
        # Overall Technical Score
        # --------------------------------

        technical_score = round(
            (
                momentum_score
                + moving_average_score
            ) / 2,
            1,
        )

        return {

            "Momentum": momentum_score,

            "Moving Average": moving_average_score,

            "Technical Score": technical_score,

        }