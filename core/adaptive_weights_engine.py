import json
import os
from datetime import datetime


class AdaptiveWeightsEngine:

    def __init__(
        self,
        learning_path="data/factor_learning.json",
        output_path="data/adaptive_weights.json",
    ):

        self.learning_path = (
            learning_path
        )

        self.output_path = (
            output_path
        )

        output_directory = (
            os.path.dirname(
                output_path
            )
        )

        if output_directory:

            os.makedirs(
                output_directory,
                exist_ok=True,
            )

        # ========================================================
        # BASE WEIGHTS
        # ========================================================

        self.base_weights = {

            "Business Quality": 0.15,

            "Financial Strength": 0.15,

            "Valuation": 0.10,

            "Growth": 0.15,

            "Profitability": 0.15,

            "Momentum": 0.15,

            "Risk": 0.10,

            "Size": 0.025,

            "Balance Sheet": 0.075,

            "Dividend": 0.00,

        }

        # ========================================================
        # SAFETY PARAMETERS
        # ========================================================

        # Require substantial evidence before allowing the
        # learning system to change production weights.

        self.minimum_observations = 50

        # Maximum proportional adjustment from the base weight.

        self.maximum_adjustment = 0.25

        # Minimum weight for factors that already have a
        # positive base weight.

        self.minimum_weight = 0.025

        # No factor can become more than 25% of the model.

        self.maximum_weight = 0.25

    # ============================================================
    # LOAD FACTOR LEARNING
    # ============================================================

    def load_learning(self):

        if not os.path.exists(
            self.learning_path
        ):

            return {}

        try:

            with open(
                self.learning_path,
                "r",
            ) as file:

                return json.load(
                    file
                )

        except Exception as error:

            print(
                f"Could not load factor learning: "
                f"{error}"
            )

            return {}

    # ============================================================
    # FACTOR QUALITY
    # ============================================================

    def factor_quality(
        self,
        data,
    ):

        observations = int(
            data.get(
                "Predictions",
                0,
            )
        )

        if observations < (
            self.minimum_observations
        ):

            return None

        predictive_score = float(
            data.get(
                "Predictive Score",
                0.5,
            )
        )

        # Convert the predictive score from
        # 0-1 into a centred -1 to +1 signal.

        quality = (
            predictive_score
            - 0.50
        ) * 2

        return quality

    # ============================================================
    # CALCULATE ADJUSTMENT
    # ============================================================

    def calculate_adjustment(
        self,
        quality,
    ):

        if quality is None:

            return 0

        adjustment = (
            quality
            * self.maximum_adjustment
        )

        adjustment = max(
            -self.maximum_adjustment,
            min(
                self.maximum_adjustment,
                adjustment,
            ),
        )

        return adjustment

    # ============================================================
    # BUILD ADAPTIVE WEIGHTS
    # ============================================================

    def build_weights(self):

        learning = (
            self.load_learning()
        )

        factor_data = (
            learning.get(
                "Factors",
                {},
            )
        )

        weights = (
            self.base_weights.copy()
        )

        changes = {}

        for factor, base_weight in (
            self.base_weights.items()
        ):

            data = factor_data.get(
                factor
            )

            # ----------------------------------------------------
            # No learning data
            # ----------------------------------------------------

            if not data:

                changes[factor] = {

                    "Base Weight":
                        base_weight,

                    "Adjustment":
                        0,

                    "New Weight":
                        base_weight,

                    "Reason":
                        "No factor learning data.",

                }

                continue

            observations = int(
                data.get(
                    "Predictions",
                    0,
                )
            )

            # ----------------------------------------------------
            # Insufficient evidence
            # ----------------------------------------------------

            if observations < (
                self.minimum_observations
            ):

                changes[factor] = {

                    "Base Weight":
                        base_weight,

                    "Adjustment":
                        0,

                    "New Weight":
                        base_weight,

                    "Observations":
                        observations,

                    "Reason":
                        "Insufficient observations.",

                }

                continue

            # ----------------------------------------------------
            # Factors with zero base weight remain disabled.
            #
            # This deliberately preserves the current decision
            # to exclude Dividend from the model.
            # ----------------------------------------------------

            if base_weight <= 0:

                changes[factor] = {

                    "Base Weight":
                        base_weight,

                    "Adjustment":
                        0,

                    "New Weight":
                        0,

                    "Observations":
                        observations,

                    "Reason":
                        "Factor disabled by base model.",

                }

                weights[factor] = 0

                continue

            # ----------------------------------------------------
            # Calculate evidence quality
            # ----------------------------------------------------

            quality = (
                self.factor_quality(
                    data
                )
            )

            if quality is None:

                changes[factor] = {

                    "Base Weight":
                        base_weight,

                    "Adjustment":
                        0,

                    "New Weight":
                        base_weight,

                    "Observations":
                        observations,

                    "Reason":
                        "Unable to calculate factor quality.",

                }

                continue

            adjustment = (
                self.calculate_adjustment(
                    quality
                )
            )

            # ----------------------------------------------------
            # Apply adjustment
            # ----------------------------------------------------

            new_weight = (
                base_weight
                * (
                    1
                    + adjustment
                )
            )

            new_weight = max(
                self.minimum_weight,
                min(
                    self.maximum_weight,
                    new_weight,
                ),
            )

            weights[factor] = (
                new_weight
            )

            changes[factor] = {

                "Base Weight":
                    round(
                        base_weight,
                        6,
                    ),

                "Adjustment":
                    round(
                        adjustment,
                        6,
                    ),

                "New Weight":
                    round(
                        new_weight,
                        6,
                    ),

                "Quality":
                    round(
                        quality,
                        6,
                    ),

                "Predictive Score":
                    data.get(
                        "Predictive Score",
                        0.5,
                    ),

                "Predictions":
                    observations,

                "Accuracy":
                    data.get(
                        "Accuracy",
                        50,
                    ),

                "Average Return":
                    data.get(
                        "Average Return",
                        0,
                    ),

            }

        # ========================================================
        # NORMALISE
        # ========================================================

        total = sum(
            weights.values()
        )

        if total <= 0:

            return (
                self.base_weights.copy(),
                changes,
            )

        for factor in weights:

            weights[factor] = (
                weights[factor]
                / total
            )

        return (
            weights,
            changes,
        )

    # ============================================================
    # SAVE
    # ============================================================

    def save(
        self,
        weights,
        changes,
    ):

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Learning Source":
                self.learning_path,

            "Minimum Observations":
                self.minimum_observations,

            "Maximum Adjustment":
                self.maximum_adjustment,

            "Weights":
                {
                    factor:
                        round(
                            weight,
                            6,
                        )

                    for factor, weight
                    in weights.items()
                },

            "Changes":
                changes,

        }

        with open(
            self.output_path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
            )

        return output

    # ============================================================
    # RUN
    # ============================================================

    def run(self):

        weights, changes = (
            self.build_weights()
        )

        output = self.save(
            weights,
            changes,
        )

        print()
        print("=" * 80)
        print(
            "ADAPTIVE FACTOR WEIGHTS"
        )
        print("=" * 80)

        print()

        for factor, weight in (
            weights.items()
        ):

            print(
                f"{factor:<25} "
                f"{weight * 100:>6.2f}%"
            )

        print()

        print(
            "Learning source: "
            f"{self.learning_path}"
        )

        print(
            "Minimum observations required: "
            f"{self.minimum_observations}"
        )

        print()

        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return output


if __name__ == "__main__":

    AdaptiveWeightsEngine().run()