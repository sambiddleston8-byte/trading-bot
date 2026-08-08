class PositionSizingEngine:

    def __init__(
        self,
        max_position=0.10,
        min_position=0.01,
        max_sector=0.25,
    ):

        self.max_position = max_position
        self.min_position = min_position
        self.max_sector = max_sector

    def calculate(
        self,
        score,
        confidence,
        risk,
        volatility=None,
    ):

        # --------------------------------
        # Base allocation from conviction
        # --------------------------------

        if score >= 90:
            allocation = 0.10

        elif score >= 85:
            allocation = 0.08

        elif score >= 80:
            allocation = 0.06

        elif score >= 75:
            allocation = 0.05

        elif score >= 70:
            allocation = 0.03

        elif score >= 65:
            allocation = 0.02

        else:
            allocation = 0.0

        # --------------------------------
        # Confidence adjustment
        # --------------------------------

        confidence_factor = (
            confidence / 100
        )

        allocation *= confidence_factor

        # --------------------------------
        # Risk adjustment
        # --------------------------------

        if risk < 30:

            allocation *= 0.25

        elif risk < 40:

            allocation *= 0.50

        elif risk < 50:

            allocation *= 0.75

        # --------------------------------
        # Volatility adjustment
        # --------------------------------

        if volatility is not None:

            try:

                volatility = float(
                    volatility
                )

                if volatility >= 80:

                    allocation *= 0.50

                elif volatility >= 60:

                    allocation *= 0.65

                elif volatility >= 40:

                    allocation *= 0.80

            except (
                TypeError,
                ValueError,
            ):

                pass

        # --------------------------------
        # Position limits
        # --------------------------------

        allocation = max(
            0.0,
            min(
                allocation,
                self.max_position,
            ),
        )

        if (
            allocation > 0
            and allocation < self.min_position
        ):

            allocation = self.min_position

        # --------------------------------
        # Return
        # --------------------------------

        return {
            "Allocation":
                round(
                    allocation,
                    4,
                ),

            "Allocation %":
                round(
                    allocation * 100,
                    2,
                ),

            "Max Position %":
                self.max_position * 100,

            "Min Position %":
                self.min_position * 100,

            "Max Sector %":
                self.max_sector * 100,
        }

    def apply_sector_limit(
        self,
        positions,
    ):

        """
        positions should be a list of dictionaries:

        {
            "Ticker": "NVDA",
            "Sector": "Technology",
            "Allocation": 0.08
        }
        """

        sector_totals = {}

        for position in positions:

            sector = position.get(
                "Sector",
                "Unknown",
            )

            allocation = position.get(
                "Allocation",
                0,
            )

            sector_totals[
                sector
            ] = (
                sector_totals.get(
                    sector,
                    0,
                )
                + allocation
            )

        # --------------------------------
        # Scale sectors exceeding limit
        # --------------------------------

        for position in positions:

            sector = position.get(
                "Sector",
                "Unknown",
            )

            total = sector_totals.get(
                sector,
                0,
            )

            if total <= self.max_sector:
                continue

            scale = (
                self.max_sector
                / total
            )

            position[
                "Allocation"
            ] = round(
                position.get(
                    "Allocation",
                    0,
                ) * scale,
                4,
            )

        return positions