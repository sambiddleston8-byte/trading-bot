import math

import yfinance as yf

from core.historical_signal import HistoricalSignalEngine
from core.backtest_results import BacktestResults


class WalkForwardBacktest:

    def __init__(
        self,
        benchmark="^GSPC",
        initial_capital=100000,
    ):

        self.benchmark = benchmark
        self.initial_capital = initial_capital

        self.signal_engine = (
            HistoricalSignalEngine()
        )

        self.results_store = (
            BacktestResults()
        )

    # --------------------------------
    # Historical Data
    # --------------------------------

    def get_history(
        self,
        symbol,
        start,
        end,
    ):

        try:

            data = yf.Ticker(
                symbol
            ).history(
                start=start,
                end=end,
                auto_adjust=True,
            )

            if data.empty:
                return None

            return data

        except Exception as error:

            print(
                f"{symbol} failed: {error}"
            )

            return None

    # --------------------------------
    # Historical Signal
    # --------------------------------

    def generate_historical_signal(
        self,
        symbol,
        decision_date,
    ):

        try:

            data = yf.Ticker(
                symbol
            ).history(
                period="2y",
                end=decision_date,
                auto_adjust=True,
            )

            if data.empty:
                return None

            signal = (
                self.signal_engine.generate_signal(
                    data
                )
            )

            if signal["Signal"] == (
                "INSUFFICIENT DATA"
            ):

                return None

            return signal

        except Exception as error:

            print(
                f"Signal generation failed "
                f"for {symbol}: {error}"
            )

            return None

    # --------------------------------
    # Historical Decision
    # --------------------------------

    def evaluate_period(
        self,
        symbol,
        decision_date,
        exit_date,
    ):

        signal = (
            self.generate_historical_signal(
                symbol,
                decision_date,
            )
        )

        if signal is None:
            return None

        prices = self.get_history(
            symbol,
            decision_date,
            exit_date,
        )

        if prices is None:
            return None

        if len(prices) < 2:
            return None

        entry_price = float(
            prices["Close"].iloc[0]
        )

        exit_price = float(
            prices["Close"].iloc[-1]
        )

        if entry_price <= 0:
            return None

        stock_return = (
            (
                exit_price
                / entry_price
            ) - 1
        ) * 100

        return {

            "Ticker":
                symbol,

            "Decision Date":
                decision_date,

            "Exit Date":
                exit_date,

            "Signal":
                signal["Signal"],

            "Score":
                signal["Score"],

            "Fast MA":
                signal.get("Fast MA"),

            "Slow MA":
                signal.get("Slow MA"),

            "Momentum":
                signal.get("Momentum"),

            "Entry Price":
                round(
                    entry_price,
                    2,
                ),

            "Exit Price":
                round(
                    exit_price,
                    2,
                ),

            "Return %":
                round(
                    stock_return,
                    2,
                ),
        }

    # --------------------------------
    # Walk Forward
    # --------------------------------

    def run(
        self,
        symbols,
        start_year=2020,
        end_year=2025,
    ):

        results = []

        for year in range(
            start_year,
            end_year,
        ):

            decision_date = (
                f"{year}-01-01"
            )

            exit_date = (
                f"{year}-04-01"
            )

            print()

            print(
                f"BACKTEST PERIOD: "
                f"{decision_date} -> "
                f"{exit_date}"
            )

            for symbol in symbols:

                print(
                    f"  Analysing {symbol}..."
                )

                result = (
                    self.evaluate_period(
                        symbol,
                        decision_date,
                        exit_date,
                    )
                )

                if result:

                    result[
                        "Backtest Year"
                    ] = year

                    results.append(
                        result
                    )

        return results

    # --------------------------------
    # Benchmark Returns
    # --------------------------------

    def benchmark_returns(
        self,
        start_year,
        end_year,
    ):

        benchmark_data = (
            self.get_history(
                self.benchmark,
                f"{start_year}-01-01",
                f"{end_year}-01-01",
            )
        )

        if benchmark_data is None:
            return {}

        yearly = {}

        for year in range(
            start_year,
            end_year,
        ):

            year_data = benchmark_data[
                benchmark_data.index.year
                == year
            ]

            if year_data.empty:
                continue

            quarter = year_data[
                year_data.index.month <= 4
            ]

            if len(quarter) < 2:
                continue

            first_price = float(
                quarter[
                    "Close"
                ].iloc[0]
            )

            last_price = float(
                quarter[
                    "Close"
                ].iloc[-1]
            )

            if first_price <= 0:
                continue

            yearly[
                year
            ] = (

                (
                    last_price
                    / first_price
                ) - 1

            ) * 100

        return yearly

    # --------------------------------
    # Portfolio Simulation
    # --------------------------------

    def simulate_portfolio(
        self,
        results,
        start_year,
        end_year,
    ):

        capital = (
            self.initial_capital
        )

        benchmark = (
            self.benchmark_returns(
                start_year,
                end_year,
            )
        )

        yearly_results = []

        equity_curve = [
            capital
        ]

        benchmark_capital = (
            self.initial_capital
        )

        benchmark_curve = [
            benchmark_capital
        ]

        strategy_returns = []

        benchmark_returns = []

        for year in range(
            start_year,
            end_year,
        ):

            period_results = [

                result

                for result in results

                if result.get(
                    "Backtest Year"
                ) == year
            ]

            selected = [

                result

                for result in period_results

                if result.get(
                    "Signal"
                ) in (
                    "BUY",
                    "STRONG BUY",
                )
            ]

            if selected:

                strategy_return = (
                    sum(
                        result["Return %"]
                        for result in selected
                    )
                    / len(selected)
                )

            else:

                strategy_return = 0

            market_return = (
                benchmark.get(
                    year,
                    0,
                )
            )

            capital *= (
                1
                + strategy_return / 100
            )

            benchmark_capital *= (
                1
                + market_return / 100
            )

            equity_curve.append(
                capital
            )

            benchmark_curve.append(
                benchmark_capital
            )

            strategy_returns.append(
                strategy_return
            )

            benchmark_returns.append(
                market_return
            )

            yearly_results.append({

                "Year":
                    year,

                "Positions":
                    len(selected),

                "Strategy Return %":
                    round(
                        strategy_return,
                        2,
                    ),

                "Benchmark Return %":
                    round(
                        market_return,
                        2,
                    ),

                "Alpha %":
                    round(
                        strategy_return
                        - market_return,
                        2,
                    ),

                "Strategy Capital":
                    round(
                        capital,
                        2,
                    ),

                "Benchmark Capital":
                    round(
                        benchmark_capital,
                        2,
                    ),
            })

        strategy_total = (
            (
                capital
                / self.initial_capital
            ) - 1
        ) * 100

        benchmark_total = (
            (
                benchmark_capital
                / self.initial_capital
            ) - 1
        ) * 100

        alpha = (
            strategy_total
            - benchmark_total
        )

        max_drawdown = (
            self.calculate_max_drawdown(
                equity_curve
            )
        )

        volatility = (
            self.calculate_volatility(
                strategy_returns
            )
        )

        sharpe = (
            self.calculate_sharpe(
                strategy_returns
            )
        )

        win_rate = (
            self.calculate_win_rate(
                strategy_returns
            )
        )

        return {

            "Initial Capital":
                self.initial_capital,

            "Final Capital":
                round(
                    capital,
                    2,
                ),

            "Benchmark Final Capital":
                round(
                    benchmark_capital,
                    2,
                ),

            "Strategy Return %":
                round(
                    strategy_total,
                    2,
                ),

            "Benchmark Return %":
                round(
                    benchmark_total,
                    2,
                ),

            "Alpha %":
                round(
                    alpha,
                    2,
                ),

            "Maximum Drawdown %":
                round(
                    max_drawdown,
                    2,
                ),

            "Annualised Volatility %":
                round(
                    volatility,
                    2,
                ),

            "Sharpe Ratio":
                round(
                    sharpe,
                    2,
                ),

            "Win Rate %":
                round(
                    win_rate,
                    2,
                ),

            "Yearly Results":
                yearly_results,

            "Strategy Equity Curve":
                [
                    round(
                        value,
                        2,
                    )

                    for value
                    in equity_curve
                ],

            "Benchmark Equity Curve":
                [
                    round(
                        value,
                        2,
                    )

                    for value
                    in benchmark_curve
                ],
        }

    # --------------------------------
    # Save Backtest
    # --------------------------------

    def save_backtest(
        self,
        report,
        strategy_name="Historical Signal Strategy",
    ):

        return self.results_store.save(
            report,
            strategy_name,
        )

    # --------------------------------
    # Maximum Drawdown
    # --------------------------------

    def calculate_max_drawdown(
        self,
        equity_curve,
    ):

        if not equity_curve:
            return 0

        peak = equity_curve[0]

        maximum_drawdown = 0

        for value in equity_curve:

            if value > peak:
                peak = value

            if peak <= 0:
                continue

            drawdown = (
                (
                    value
                    / peak
                ) - 1
            ) * 100

            if drawdown < maximum_drawdown:

                maximum_drawdown = drawdown

        return abs(
            maximum_drawdown
        )

    # --------------------------------
    # Volatility
    # --------------------------------

    def calculate_volatility(
        self,
        returns,
    ):

        if len(returns) < 2:
            return 0

        mean = (
            sum(returns)
            / len(returns)
        )

        variance = (
            sum(
                (
                    value
                    - mean
                ) ** 2
                for value in returns
            )
            / (
                len(returns)
                - 1
            )
        )

        standard_deviation = math.sqrt(
            variance
        )

        return (
            standard_deviation
            * math.sqrt(4)
        )

    # --------------------------------
    # Sharpe Ratio
    # --------------------------------

    def calculate_sharpe(
        self,
        returns,
        risk_free_rate=0,
    ):

        if len(returns) < 2:
            return 0

        mean = (
            sum(returns)
            / len(returns)
        )

        variance = (
            sum(
                (
                    value
                    - mean
                ) ** 2
                for value in returns
            )
            / (
                len(returns)
                - 1
            )
        )

        standard_deviation = math.sqrt(
            variance
        )

        if standard_deviation == 0:
            return 0

        quarterly_rf = (
            risk_free_rate / 4
        )

        return (
            (
                mean
                - quarterly_rf
            )
            / standard_deviation
        ) * math.sqrt(4)

    # --------------------------------
    # Win Rate
    # --------------------------------

    def calculate_win_rate(
        self,
        returns,
    ):

        if not returns:
            return 0

        wins = sum(
            1
            for value in returns
            if value > 0
        )

        return (
            wins
            / len(returns)
        ) * 100


if __name__ == "__main__":

    backtest = WalkForwardBacktest()

    symbols = [
        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",
    ]

    start_year = 2020
    end_year = 2025

    results = backtest.run(
        symbols,
        start_year=start_year,
        end_year=end_year,
    )

    report = (
        backtest.simulate_portfolio(
            results,
            start_year,
            end_year,
        )
    )

    # --------------------------------
    # Save Results
    # --------------------------------

    saved = backtest.save_backtest(
        report,
        "Historical Signal Strategy",
    )

    print()
    print("=" * 60)
    print("SYSTEM PERFORMANCE REPORT")
    print("=" * 60)

    print()

    print(
        f"Initial Capital: "
        f"${report['Initial Capital']:,.2f}"
    )

    print(
        f"Final Capital: "
        f"${report['Final Capital']:,.2f}"
    )

    print(
        f"S&P 500 Final Capital: "
        f"${report['Benchmark Final Capital']:,.2f}"
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

    print()

    print(
        f"Maximum Drawdown: "
        f"{report['Maximum Drawdown %']}%"
    )

    print(
        f"Annualised Volatility: "
        f"{report['Annualised Volatility %']}%"
    )

    print(
        f"Sharpe Ratio: "
        f"{report['Sharpe Ratio']}"
    )

    print(
        f"Win Rate: "
        f"{report['Win Rate %']}%"
    )

    print()
    print("YEARLY PERFORMANCE")
    print("-" * 60)

    for year in report[
        "Yearly Results"
    ]:

        print(
            f"{year['Year']}: "
            f"Strategy "
            f"{year['Strategy Return %']}% | "
            f"Benchmark "
            f"{year['Benchmark Return %']}% | "
            f"Alpha "
            f"{year['Alpha %']}% | "
            f"Positions "
            f"{year['Positions']}"
        )

    print()
    print(
        f"Backtest saved as Run "
        f"{saved['Run ID']}"
    )

    print()
    print("=" * 60)