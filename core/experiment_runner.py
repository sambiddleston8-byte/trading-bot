from core.strategy_experiment import StrategyExperiment
from core.portfolio_backtest import PortfolioBacktest


class ExperimentRunner:

    def __init__(self):

        self.experimenter = (
            StrategyExperiment()
        )

    # --------------------------------
    # Run One Experiment
    # --------------------------------

    def run_experiment(
        self,
        experiment,
        symbols,
        start_year=2020,
        end_year=2026,
    ):

        print()
        print("=" * 70)
        print(
            f"EXPERIMENT: "
            f"{experiment['Name']}"
        )
        print("=" * 70)

        # --------------------------------
        # Build strategy-specific engine
        # --------------------------------

        engine = (
            self.experimenter.build_engine(
                experiment
            )
        )

        # --------------------------------
        # Build backtest
        # --------------------------------

        backtest = PortfolioBacktest()

        backtest.signal_engine = engine

        report = backtest.run(
            symbols,
            start_year=start_year,
            end_year=end_year,
        )

        return {

            "Experiment":
                experiment,

            "Report":
                report,

        }

    # --------------------------------
    # Run All Experiments
    # --------------------------------

    def run_all(
        self,
        symbols,
        start_year=2020,
        end_year=2026,
    ):

        experiments = (
            self.experimenter
            .generate_default_experiments()
        )

        results = []

        for experiment in experiments:

            result = (
                self.run_experiment(
                    experiment,
                    symbols,
                    start_year,
                    end_year,
                )
            )

            results.append(
                result
            )

        return results

    # --------------------------------
    # Rank Results
    # --------------------------------

    def rank_results(
        self,
        results,
    ):

        valid = [

            result

            for result in results

            if "Report" in result

            and "Error"
            not in result["Report"]
        ]

        return sorted(
            valid,
            key=lambda result: (
                result["Report"].get(
                    "Sharpe Ratio",
                    0,
                )
            ),
            reverse=True,
        )

    # --------------------------------
    # Print Results
    # --------------------------------

    def print_results(
        self,
        results,
    ):

        ranked = (
            self.rank_results(
                results
            )
        )

        print()
        print("=" * 80)
        print("STRATEGY EXPERIMENT RESULTS")
        print("=" * 80)

        print()

        for index, result in enumerate(
            ranked,
            start=1,
        ):

            experiment = (
                result["Experiment"]
            )

            report = (
                result["Report"]
            )

            print(
                f"{index}. "
                f"{experiment['Name']}"
            )

            print(
                f"   "
                f"Fast MA: "
                f"{experiment['Fast Period']} | "
                f"Slow MA: "
                f"{experiment['Slow Period']} | "
                f"Momentum: "
                f"{experiment['Momentum Period']}"
            )

            print(
                f"   Return: "
                f"{report.get('Strategy Return %', 0)}%"
            )

            print(
                f"   Alpha: "
                f"{report.get('Alpha %', 0)}%"
            )

            print(
                f"   Drawdown: "
                f"{report.get('Maximum Drawdown %', 0)}%"
            )

            print(
                f"   Sharpe: "
                f"{report.get('Sharpe Ratio', 0)}"
            )

            print(
                f"   Sortino: "
                f"{report.get('Sortino Ratio', 0)}"
            )

            print()

    # --------------------------------
    # Best Strategy
    # --------------------------------

    def best_strategy(
        self,
        results,
    ):

        ranked = (
            self.rank_results(
                results
            )
        )

        if not ranked:
            return None

        return ranked[0]


if __name__ == "__main__":

    symbols = [

        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",

    ]

    runner = ExperimentRunner()

    results = runner.run_all(
        symbols,
        start_year=2020,
        end_year=2026,
    )

    runner.print_results(
        results
    )

    best = (
        runner.best_strategy(
            results
        )
    )

    if best:

        print()
        print("=" * 70)
        print("BEST EXPERIMENT")
        print("=" * 70)

        print(
            best["Experiment"]
        )

        print(
            best["Report"]
        )