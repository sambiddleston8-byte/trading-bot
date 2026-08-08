from core.historical_signal import HistoricalSignalEngine


class StrategyExperiment:

    def __init__(self):

        self.experiments = []

    # --------------------------------
    # Create Experiment
    # --------------------------------

    def create(
        self,
        name,
        fast_period,
        slow_period,
        momentum_period,
    ):

        experiment = {

            "Name":
                name,

            "Fast Period":
                fast_period,

            "Slow Period":
                slow_period,

            "Momentum Period":
                momentum_period,

        }

        self.experiments.append(
            experiment
        )

        return experiment

    # --------------------------------
    # Validate Parameters
    # --------------------------------

    def validate(
        self,
        experiment,
    ):

        fast = experiment[
            "Fast Period"
        ]

        slow = experiment[
            "Slow Period"
        ]

        momentum = experiment[
            "Momentum Period"
        ]

        if fast <= 0:
            return False

        if slow <= fast:
            return False

        if momentum <= 0:
            return False

        return True

    # --------------------------------
    # Build Signal Engine
    # --------------------------------

    def build_engine(
        self,
        experiment,
    ):

        if not self.validate(
            experiment
        ):

            raise ValueError(
                "Invalid strategy parameters."
            )

        return HistoricalSignalEngine(

            fast_period=experiment[
                "Fast Period"
            ],

            slow_period=experiment[
                "Slow Period"
            ],

            momentum_period=experiment[
                "Momentum Period"
            ],
        )

    # --------------------------------
    # Run Signal Experiment
    # --------------------------------

    def test_signal(
        self,
        prices,
        experiment,
    ):

        engine = self.build_engine(
            experiment
        )

        return engine.generate_signal(
            prices
        )

    # --------------------------------
    # Generate Experiment Set
    # --------------------------------

    def generate_default_experiments(
        self,
    ):

        experiments = []

        configurations = [

            (
                "Fast Trend",
                10,
                30,
                10,
            ),

            (
                "Balanced Trend",
                20,
                50,
                20,
            ),

            (
                "Medium Trend",
                30,
                75,
                30,
            ),

            (
                "Slow Trend",
                50,
                100,
                50,
            ),

            (
                "Long Trend",
                50,
                200,
                60,
            ),

        ]

        for configuration in configurations:

            experiment = self.create(
                configuration[0],
                configuration[1],
                configuration[2],
                configuration[3],
            )

            experiments.append(
                experiment
            )

        return experiments

    # --------------------------------
    # Print Experiments
    # --------------------------------

    def print_experiments(
        self,
    ):

        print()
        print("=" * 60)
        print("STRATEGY EXPERIMENTS")
        print("=" * 60)

        for experiment in (
            self.experiments
        ):

            print()

            print(
                experiment["Name"]
            )

            print(
                f"  Fast MA: "
                f"{experiment['Fast Period']}"
            )

            print(
                f"  Slow MA: "
                f"{experiment['Slow Period']}"
            )

            print(
                f"  Momentum: "
                f"{experiment['Momentum Period']}"
            )


if __name__ == "__main__":

    experimenter = (
        StrategyExperiment()
    )

    experiments = (
        experimenter
        .generate_default_experiments()
    )

    experimenter.print_experiments()