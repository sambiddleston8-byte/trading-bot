import json
import os
from datetime import datetime


class BacktestResults:

    def __init__(
        self,
        path="data/backtest_results.json",
    ):

        self.path = path

        directory = os.path.dirname(
            self.path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        if not os.path.exists(
            self.path
        ):

            self._save([])

    # --------------------------------
    # Storage
    # --------------------------------

    def _load(self):

        try:

            with open(
                self.path,
                "r",
            ) as file:

                data = json.load(file)

                if isinstance(
                    data,
                    list,
                ):

                    return data

                return []

        except (
            FileNotFoundError,
            json.JSONDecodeError,
        ):

            return []

    def _save(
        self,
        results,
    ):

        with open(
            self.path,
            "w",
        ) as file:

            json.dump(
                results,
                file,
                indent=2,
                default=str,
            )

    # --------------------------------
    # Save Backtest
    # --------------------------------

    def save(
        self,
        report,
        strategy_name="Default Strategy",
    ):

        history = self._load()

        record = {

            "Run ID":
                len(history) + 1,

            "Timestamp":
                datetime.now().isoformat(),

            "Strategy":
                strategy_name,

            "Initial Capital":
                report.get(
                    "Initial Capital"
                ),

            "Final Capital":
                report.get(
                    "Final Capital"
                ),

            "Benchmark Final Capital":
                report.get(
                    "Benchmark Final Capital"
                ),

            "Strategy Return %":
                report.get(
                    "Strategy Return %"
                ),

            "Benchmark Return %":
                report.get(
                    "Benchmark Return %"
                ),

            "Alpha %":
                report.get(
                    "Alpha %"
                ),

            "Maximum Drawdown %":
                report.get(
                    "Maximum Drawdown %"
                ),

            "Annualised Volatility %":
                report.get(
                    "Annualised Volatility %"
                ),

            "Sharpe Ratio":
                report.get(
                    "Sharpe Ratio"
                ),

            "Win Rate %":
                report.get(
                    "Win Rate %"
                ),

            "Yearly Results":
                report.get(
                    "Yearly Results",
                    [],
                ),
        }

        history.append(
            record
        )

        self._save(
            history
        )

        return record

    # --------------------------------
    # Retrieve History
    # --------------------------------

    def history(self):

        return self._load()

    # --------------------------------
    # Best Runs
    # --------------------------------

    def best_runs(
        self,
        metric="Alpha %",
        limit=10,
    ):

        history = self._load()

        return sorted(
            history,
            key=lambda x: (
                x.get(
                    metric,
                    float("-inf"),
                )
            ),
            reverse=True,
        )[:limit]

    # --------------------------------
    # Print Comparison
    # --------------------------------

    def print_comparison(self):

        history = self._load()

        print()
        print("=" * 70)
        print("BACKTEST HISTORY")
        print("=" * 70)
        print()

        if not history:

            print(
                "No backtests saved yet."
            )

            return

        for result in history:

            print(
                f"Run {result.get('Run ID')}: "
                f"{result.get('Strategy')}"
            )

            print(
                f"  Date: "
                f"{result.get('Timestamp')}"
            )

            print(
                f"  Return: "
                f"{result.get('Strategy Return %')}%"
            )

            print(
                f"  Benchmark: "
                f"{result.get('Benchmark Return %')}%"
            )

            print(
                f"  Alpha: "
                f"{result.get('Alpha %')}%"
            )

            print(
                f"  Drawdown: "
                f"{result.get('Maximum Drawdown %')}%"
            )

            print(
                f"  Sharpe: "
                f"{result.get('Sharpe Ratio')}"
            )

            print()