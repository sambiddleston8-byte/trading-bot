class AdaptiveWeights:

    DEFAULT_WEIGHTS = {

        "Business Quality": 0.15,

        "Financial Strength": 0.15,

        "Valuation": 0.15,

        "Growth": 0.10,

        "Profitability": 0.10,

        "Momentum": 0.10,

        "Risk": 0.10,

        "Size": 0.05,

        "Balance Sheet": 0.05,

        "Dividend": 0.05,

    }

    MIN_PREDICTIONS = 10

    # ============================================================
    # CALCULATE
    # ============================================================

    def calculate(
        self,
        factor_performance,
    ):

        weights = (
            self.DEFAULT_WEIGHTS.copy()
        )

        if not factor_performance:

            return weights

        reliable = {}

        # --------------------------------------------------------
        # Only learn from factors with sufficient evidence
        # --------------------------------------------------------

        for factor, data in (
            factor_performance.items()
        ):

            predictions = data.get(
                "Predictions",
                0,
            )

            if predictions < (
                self.MIN_PREDICTIONS
            ):

                continue

            accuracy = data.get(
                "Accuracy",
                50,
            )

            average_return = data.get(
                "Average Return",
                0,
            )

            reliable[factor] = {

                "Accuracy":
                    float(
                        accuracy
                    ),

                "Average Return":
                    float(
                        average_return
                    ),

            }

        if not reliable:

            return weights

        # --------------------------------------------------------
        # Calculate performance score
        # --------------------------------------------------------

        performance = {}

        for factor, data in (
            reliable.items()
        ):

            accuracy_score = (
                data["Accuracy"]
                / 100
            )

            bounded_return = max(
                -50,
                min(
                    data["Average Return"],
                    50,
                ),
            )

            return_score = (
                0.5
                + (
                    bounded_return
                    / 100
                )
            )

            performance[factor] = (

                accuracy_score
                * 0.70

                + return_score
                * 0.30

            )

        # --------------------------------------------------------
        # Adjust gradually
        # --------------------------------------------------------

        for factor, score in (
            performance.items()
        ):

            if factor not in weights:

                continue

            base_weight = weights[
                factor
            ]

            adjustment = (
                score
                - 0.50
            ) * 0.20

            new_weight = (
                base_weight
                + adjustment
            )

            # Prevent any individual
            # factor from dominating.

            new_weight = max(

                base_weight
                * 0.50,

                min(

                    new_weight,

                    base_weight
                    * 1.50,

                ),

            )

            weights[
                factor
            ] = new_weight

        # --------------------------------------------------------
        # Normalise
        # --------------------------------------------------------

        total = sum(
            weights.values()
        )

        if total <= 0:

            return (
                self.DEFAULT_WEIGHTS.copy()
            )

        for factor in weights:

            weights[factor] = round(

                weights[factor]
                / total,

                6,

            )

        return weights