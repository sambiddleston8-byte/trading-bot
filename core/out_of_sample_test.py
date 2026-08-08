from core.strategy_experiment import StrategyExperiment
from core.portfolio_backtest import PortfolioBacktest


class OutOfSampleTest:

    def __init__(self):

        self.experimenter = (
            StrategyExperiment()
        )

    # --------------------------------
    # Training Period
    # --------------------------------

    def run_training(
        self,
        symbols,
    ):

        print()
        print("=" * 70)
        print("TRAINING PERIOD")
        print("2020 -> 2025")
        print("=" * 70)

        experiments = (
            self.experimenter
            .generate_default_experiments()
        )

        results = []

        for experiment in experiments:

            print()
            print(
                f"Testing training strategy: "
                f"{experiment['Name']}"
            )

            engine = (
                self.experimenter
                .build_engine(
                    experiment
                )
            )

            backtest = (
                PortfolioBacktest()
            )

            backtest.signal_engine = engine

            report = backtest.run(
                symbols,
                start_year=2020,
                end_year=2025,
            )

            results.append({

                "Experiment":
                    experiment,

                "Report":
                    report,

            })

        return results

    # --------------------------------
    # Rank Training Results
    # --------------------------------

    def rank_training_results(
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
    # Select Strategy
    # --------------------------------

    def select_strategy(
        self,
        results,
    ):

        ranked = (
            self.rank_training_results(
                results
            )
        )

        if not ranked:

            return None

        return ranked[0]

    # --------------------------------
    # Test Selected Strategy
    # --------------------------------

    def run_test(
        self,
        selected,
        symbols,
    ):

        experiment = (
            selected["Experiment"]
        )

        print()
        print("=" * 70)
        print("OUT-OF-SAMPLE TEST")
        print("2025 -> 2026")
        print("=" * 70)

        print()
        print(
            "LOCKED STRATEGY:"
        )

        print(
            experiment
        )

        engine = (
            self.experimenter
            .build_engine(
                experiment
            )
        )

        backtest = (
            PortfolioBacktest()
        )

        backtest.signal_engine = engine

        report = backtest.run(
            symbols,
            start_year=2025,
            end_year=2026,
        )

        return report

    # --------------------------------
    # Print Training Results
    # --------------------------------

    def print_training(
        self,
        results,
    ):

        ranked = (
            self.rank_training_results(
                results
            )
        )

        print()
        print("=" * 70)
        print("TRAINING RESULTS")
        print("=" * 70)

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
                f"Fast: "
                f"{experiment['Fast Period']} | "
                f"Slow: "
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

            print()

    # --------------------------------
    # Print Test Results
    # --------------------------------

    def print_test(
        self,
        selected,
        report,
    ):

        experiment = (
            selected["Experiment"]
        )

        print()
        print("=" * 70)
        print("FINAL OUT-OF-SAMPLE RESULT")
        print("=" * 70)

        print()

        print(
            "Locked Strategy:"
        )

        print(
            f"  {experiment['Name']}"
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

        print()

        print(
            f"Strategy Return: "
            f"{report['Strategy Return %']}%"
        )

        print(
            f"S&P 500 Return: "
            f"{report['Benchmark Return %']}%"
        )

        print(
            f"Alpha: "
            f"{report['Alpha %']}%"
        )

        print(
            f"CAGR: "
            f"{report['CAGR %']}%"
        )

        print(
            f"Maximum Drawdown: "
            f"{report['Maximum Drawdown %']}%"
        )

        print(
            f"Volatility: "
            f"{report['Annualised Volatility %']}%"
        )

        print(
            f"Sharpe: "
            f"{report['Sharpe Ratio']}"
        )

        print(
            f"Sortino: "
            f"{report['Sortino Ratio']}"
        )

        print(
            f"Win Rate: "
            f"{report['Win Rate %']}%"
        )

        print()

        print(
            "This result was produced "
            "using parameters selected "
            "only from the training period."
        )


if __name__ == "__main__":

    symbols = [

        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",

    ]

    tester = OutOfSampleTest()

    # --------------------------------
    # TRAIN
    # --------------------------------

    training_results = (
        tester.run_training(
            symbols
        )
    )

    tester.print_training(
        training_results
    )

    # --------------------------------
    # SELECT
    # --------------------------------

    selected = (
        tester.select_strategy(
            training_results
        )
    )

    if selected is None:

        raise RuntimeError(
            "No valid strategy found."
        )

    # --------------------------------
    # TEST
    # --------------------------------

    test_report = (
        tester.run_test(
            selected,
            symbols,
        )
    )

    tester.print_test(
        selected,
        test_report
    )