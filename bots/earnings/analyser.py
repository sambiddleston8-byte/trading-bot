import yfinance as yf

from core.company_context import CompanyContext


class EarningsAnalyser:

    def analyse(self, context: CompanyContext):

        symbol = context.symbol

        strengths = []
        weaknesses = []
        catalysts = []
        risks = []

        score = 50

        ticker = yf.Ticker(symbol)

        # --------------------------------
        # Earnings Calendar
        # --------------------------------

        earnings_date = None

        try:

            calendar = ticker.calendar

            if calendar is not None:

                if hasattr(
                    calendar,
                    "to_dict",
                ):

                    calendar_data = (
                        calendar.to_dict()
                    )

                    earnings_date = (
                        calendar_data.get(
                            "Earnings Date"
                        )
                    )

        except Exception:

            earnings_date = None

        # --------------------------------
        # Earnings History
        # --------------------------------

        earnings_history = None

        try:

            earnings_history = (
                ticker.get_earnings_dates(
                    limit=8
                )
            )

        except Exception:

            earnings_history = None

        positive_surprises = 0
        negative_surprises = 0

        if earnings_history is not None:

            for _, row in earnings_history.iterrows():

                actual = row.get(
                    "Reported EPS"
                )

                estimate = row.get(
                    "EPS Estimate"
                )

                if (
                    actual is None
                    or estimate is None
                ):

                    continue

                try:

                    actual = float(actual)
                    estimate = float(estimate)

                except (
                    TypeError,
                    ValueError,
                ):

                    continue

                if actual > estimate:

                    positive_surprises += 1

                elif actual < estimate:

                    negative_surprises += 1

        # --------------------------------
        # Earnings Surprise Assessment
        # --------------------------------

        if positive_surprises >= 5:

            score += 20

            strengths.append(
                "The company has frequently exceeded recent EPS estimates."
            )

            catalysts.append(
                "Continued earnings beats could support positive sentiment."
            )

        elif positive_surprises >= 3:

            score += 10

            strengths.append(
                "Recent earnings history shows a positive surprise trend."
            )

        if negative_surprises >= 4:

            score -= 15

            weaknesses.append(
                "The company has frequently missed recent EPS estimates."
            )

            risks.append(
                "Further earnings disappointments could pressure the shares."
            )

        elif negative_surprises >= 2:

            score -= 5

            weaknesses.append(
                "Recent earnings performance has included some misses."
            )

        # --------------------------------
        # Revenue Growth
        # --------------------------------

        revenue_growth = context.info.get(
            "revenueGrowth"
        )

        if revenue_growth is not None:

            if revenue_growth >= 0.20:

                score += 10

                catalysts.append(
                    "Strong revenue growth provides support for future earnings expansion."
                )

            elif revenue_growth < 0:

                score -= 10

                risks.append(
                    "Declining revenue creates earnings risk."
                )

        # --------------------------------
        # Earnings Growth
        # --------------------------------

        earnings_growth = context.info.get(
            "earningsGrowth"
        )

        if earnings_growth is not None:

            if earnings_growth >= 0.20:

                score += 10

                catalysts.append(
                    "Strong earnings growth is a positive fundamental catalyst."
                )

            elif earnings_growth < 0:

                score -= 10

                risks.append(
                    "Declining earnings create downside risk."
                )

        # --------------------------------
        # Forward PE
        # --------------------------------

        forward_pe = context.info.get(
            "forwardPE"
        )

        trailing_pe = context.info.get(
            "trailingPE"
        )

        if (
            forward_pe is not None
            and trailing_pe is not None
        ):

            if forward_pe < trailing_pe:

                score += 5

                strengths.append(
                    "Forward valuation is below trailing valuation, suggesting expected earnings growth."
                )

            elif forward_pe > trailing_pe * 1.15:

                score -= 5

                risks.append(
                    "Forward valuation is above trailing valuation."
                )

        # --------------------------------
        # Score
        # --------------------------------

        score = max(
            0,
            min(
                score,
                100,
            ),
        )

        # --------------------------------
        # Assessment
        # --------------------------------

        if score >= 80:

            assessment = "STRONG"

        elif score >= 65:

            assessment = "POSITIVE"

        elif score >= 50:

            assessment = "NEUTRAL"

        elif score >= 35:

            assessment = "WEAK"

        else:

            assessment = "NEGATIVE"

        # --------------------------------
        # Summary
        # --------------------------------

        summary = (
            f"Earnings intelligence is currently "
            f"{assessment.lower()}. "
            f"The system identified "
            f"{positive_surprises} recent positive "
            f"earnings surprises and "
            f"{negative_surprises} negative surprises."
        )

        return {

            "Earnings Score":
                score,

            "Assessment":
                assessment,

            "Earnings Date":
                str(earnings_date),

            "Positive Surprises":
                positive_surprises,

            "Negative Surprises":
                negative_surprises,

            "Trailing PE":
                trailing_pe,

            "Forward PE":
                forward_pe,

            "Revenue Growth":
                revenue_growth,

            "Earnings Growth":
                earnings_growth,

            "Strengths":
                strengths,

            "Weaknesses":
                weaknesses,

            "Catalysts":
                catalysts,

            "Risks":
                risks,

            "Summary":
                summary,
        }