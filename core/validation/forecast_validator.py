class ForecastValidator:

    def __init__(
        self,
        revenue_estimates,
        earnings_estimates,
    ):

        self.revenue = (
            revenue_estimates or {}
        )

        self.earnings = (
            earnings_estimates or {}
        )

    def safe_float(self, value):

        try:

            if value is None:
                return None

            value = float(value)

            if value != value:
                return None

            return value

        except (
            TypeError,
            ValueError,
        ):

            return None

    def compare_period(
        self,
        period,
    ):

        revenue = self.revenue.get(
            period,
            {},
        )

        earnings = self.earnings.get(
            period,
            {},
        )

        revenue_growth = self.safe_float(
            revenue.get(
                "growth"
            )
        )

        eps_growth = self.safe_float(
            earnings.get(
                "growth"
            )
        )

        difference = None

        if (
            revenue_growth is not None
            and eps_growth is not None
        ):

            difference = (
                eps_growth
                - revenue_growth
            )

        if (
            revenue_growth is None
            or eps_growth is None
        ):

            status = "INSUFFICIENT_DATA"

        elif abs(difference) <= 0.05:

            status = "CONSISTENT"

        elif abs(difference) <= 0.15:

            status = "MODERATE_DIFFERENCE"

        else:

            status = "LARGE_DIFFERENCE"

        return {

            "period":
                period,

            "revenue_growth":
                revenue_growth,

            "eps_growth":
                eps_growth,

            "growth_difference":
                difference,

            "status":
                status,

            "revenue_analysts":
                revenue.get(
                    "numberOfAnalysts"
                ),

            "eps_analysts":
                earnings.get(
                    "analysts"
                ),

        }

    def build(self):

        periods = [
            "0q",
            "+1q",
            "0y",
            "+1y",
        ]

        comparisons = []

        for period in periods:

            comparisons.append(
                self.compare_period(
                    period
                )
            )

        valid = [
            item
            for item in comparisons
            if item["status"]
            != "INSUFFICIENT_DATA"
        ]

        consistent = sum(
            1
            for item in valid
            if item["status"]
            == "CONSISTENT"
        )

        large_differences = sum(
            1
            for item in valid
            if item["status"]
            == "LARGE_DIFFERENCE"
        )

        if not valid:

            overall = "INSUFFICIENT_DATA"

        elif large_differences > 0:

            overall = "REVIEW"

        elif consistent == len(valid):

            overall = "HIGH"

        else:

            overall = "MEDIUM"

        return {

            "overall_confidence":
                overall,

            "periods_checked":
                len(valid),

            "consistent_periods":
                consistent,

            "large_differences":
                large_differences,

            "comparisons":
                comparisons,

        }
