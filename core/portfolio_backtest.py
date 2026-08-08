import math

from core.historical_signal import HistoricalSignalEngine
from core.market_data_cache import MarketDataCache
from core.backtest_results import BacktestResults


class PortfolioBacktest:

    def __init__(
        self,
        benchmark="^GSPC",
        initial_capital=100000,
        transaction_cost=0.001,
        max_position=0.10,
    ):

        self.benchmark = benchmark
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.max_position = max_position

        self.signal_engine = (
            HistoricalSignalEngine()
        )

        self.data_cache = (
            MarketDataCache()
        )

        self.results_store = (
            BacktestResults()
        )

        self.market_data = {}

    # --------------------------------
    # Load All Data Once
    # --------------------------------

    def load_data(
        self,
        symbols,
        start="2018-01-01",
        end="2026-12-31",
    ):

        universe = list(
            dict.fromkeys(
                symbols + [
                    self.benchmark
                ]
            )
        )

        print()
        print(
            "LOADING MARKET DATA"
        )
        print(
            "-" * 50
        )

        self.market_data = (
            self.data_cache.load_universe(
                universe,
                start=start,
                end=end,
            )
        )

        print()
        print(
            f"Loaded "
            f"{len(self.market_data)} "
            f"instruments."
        )

    # --------------------------------
    # Get Cached Data
    # --------------------------------

    def get_history(
        self,
        symbol,
        start=None,
        end=None,
    ):

        data = self.market_data.get(
            symbol
        )

        if data is None:
            return None

        result = data

        if start:

            result = result[
                result.index >= start
            ]

        if end:

            result = result[
                result.index < end
            ]

        return result

    # --------------------------------
    # Historical Signal
    # --------------------------------

    def get_signal(
        self,
        symbol,
        decision_date,
    ):

        data = self.get_history(
            symbol,
            end=decision_date,
        )

        if data is None:
            return None

        if data.empty:
            return None

        signal = (
            self.signal_engine.generate_signal(
                data
            )
        )

        if signal.get(
            "Signal"
        ) == "INSUFFICIENT DATA":

            return None

        return signal

    # --------------------------------
    # Quarter Dates
    # --------------------------------

    def quarter_dates(
        self,
        start_year,
        end_year,
    ):

        dates = []

        for year in range(
            start_year,
            end_year + 1,
        ):

            for month in (
                1,
                4,
                7,
                10,
            ):

                if (
                    year == end_year
                    and month >= 10
                ):
                    continue

                dates.append(
                    f"{year}-{month:02d}-01"
                )

        return dates

    # --------------------------------
    # Build Portfolio
    # --------------------------------

    def build_portfolio(
        self,
        symbols,
        decision_date,
    ):

        candidates = []

        for symbol in symbols:

            signal = self.get_signal(
                symbol,
                decision_date,
            )

            if signal is None:
                continue

            recommendation = signal.get(
                "Signal",
                "AVOID",
            )

            score = signal.get(
                "Score",
                50,
            )

            if recommendation not in (
                "BUY",
                "STRONG BUY",
            ):
                continue

            candidates.append({

                "Ticker":
                    symbol,

                "Signal":
                    recommendation,

                "Score":
                    score,

            })

        candidates.sort(
            key=lambda x: x["Score"],
            reverse=True,
        )

        if not candidates:
            return None

        # --------------------------------
        # Maximum Position Limit
        # --------------------------------

        position_count = min(
            len(candidates),
            int(
                1
                / self.max_position
            ),
        )

        candidates = candidates[
            :position_count
        ]

        if not candidates:
            return None

        weight = min(
            1.0 / len(candidates),
            self.max_position,
        )

        positions = []

        for candidate in candidates:

            positions.append({

                "Ticker":
                    candidate["Ticker"],

                "Signal":
                    candidate["Signal"],

                "Score":
                    candidate["Score"],

                "Weight":
                    weight,

            })

        invested = sum(
            position["Weight"]
            for position in positions
        )

        return {

            "Positions":
                positions,

            "Invested":
                invested,

            "Cash":
                max(
                    0,
                    1 - invested,
                ),

        }

    # --------------------------------
    # Portfolio Return
    # --------------------------------

    def calculate_period_return(
        self,
        portfolio,
        start_date,
        end_date,
    ):

        if not portfolio:
            return 0.0

        positions = portfolio.get(
            "Positions",
            [],
        )

        portfolio_return = 0.0

        for position in positions:

            ticker = position[
                "Ticker"
            ]

            weight = position[
                "Weight"
            ]

            data = self.get_history(
                ticker,
                start=start_date,
                end=end_date,
            )

            if data is None:
                continue

            if len(data) < 2:
                continue

            start_price = float(
                data["Close"].iloc[0]
            )

            end_price = float(
                data["Close"].iloc[-1]
            )

            if start_price <= 0:
                continue

            stock_return = (
                end_price
                / start_price
                - 1
            )

            portfolio_return += (
                weight
                * stock_return
            )

        return portfolio_return

    # --------------------------------
    # Transaction Costs
    # --------------------------------

    def calculate_transaction_cost(
        self,
        old_portfolio,
        new_portfolio,
    ):

        old_weights = {}
        new_weights = {}

        if old_portfolio:

            for position in (
                old_portfolio.get(
                    "Positions",
                    [],
                )
            ):

                old_weights[
                    position["Ticker"]
                ] = position["Weight"]

        if new_portfolio:

            for position in (
                new_portfolio.get(
                    "Positions",
                    [],
                )
            ):

                new_weights[
                    position["Ticker"]
                ] = position["Weight"]

        tickers = (
            set(old_weights)
            | set(new_weights)
        )

        turnover = 0.0

        for ticker in tickers:

            old_weight = (
                old_weights.get(
                    ticker,
                    0.0,
                )
            )

            new_weight = (
                new_weights.get(
                    ticker,
                    0.0,
                )
            )

            turnover += abs(
                new_weight
                - old_weight
            )

        return (
            turnover
            * self.transaction_cost
        )

    # --------------------------------
    # Benchmark Return
    # --------------------------------

    def benchmark_return(
        self,
        start_date,
        end_date,
    ):

        data = self.get_history(
            self.benchmark,
            start=start_date,
            end=end_date,
        )

        if data is None:
            return 0.0

        if len(data) < 2:
            return 0.0

        first = float(
            data["Close"].iloc[0]
        )

        last = float(
            data["Close"].iloc[-1]
        )

        if first <= 0:
            return 0.0

        return (
            last
            / first
            - 1
        )

    # --------------------------------
    # Main Backtest
    # --------------------------------

    def run(
        self,
        symbols,
        start_year=2020,
        end_year=2026,
    ):

        self.load_data(
            symbols
        )

        quarters = (
            self.quarter_dates(
                start_year,
                end_year,
            )
        )

        capital = (
            self.initial_capital
        )

        benchmark_capital = (
            self.initial_capital
        )

        old_portfolio = None

        equity_curve = [
            capital
        ]

        benchmark_curve = [
            benchmark_capital
        ]

        period_returns = []

        all_periods = []

        for index in range(
            len(quarters) - 1
        ):

            decision_date = (
                quarters[index]
            )

            next_date = (
                quarters[index + 1]
            )

            print(
                f"Testing "
                f"{decision_date} -> "
                f"{next_date}"
            )

            portfolio = (
                self.build_portfolio(
                    symbols,
                    decision_date,
                )
            )

            if portfolio is None:

                portfolio = {

                    "Positions":
                        [],

                    "Invested":
                        0.0,

                    "Cash":
                        1.0,
                }

            transaction_cost = (
                self.calculate_transaction_cost(
                    old_portfolio,
                    portfolio,
                )
            )

            gross_return = (
                self.calculate_period_return(
                    portfolio,
                    decision_date,
                    next_date,
                )
            )

            net_return = (
                gross_return
                - transaction_cost
            )

            market_return = (
                self.benchmark_return(
                    decision_date,
                    next_date,
                )
            )

            capital *= (
                1
                + net_return
            )

            benchmark_capital *= (
                1
                + market_return
            )

            equity_curve.append(
                capital
            )

            benchmark_curve.append(
                benchmark_capital
            )

            period_returns.append(
                net_return * 100
            )

            all_periods.append({

                "Decision Date":
                    decision_date,

                "End Date":
                    next_date,

                "Positions":
                    portfolio["Positions"],

                "Invested %":
                    round(
                        portfolio["Invested"]
                        * 100,
                        2,
                    ),

                "Cash %":
                    round(
                        portfolio["Cash"]
                        * 100,
                        2,
                    ),

                "Net Return %":
                    round(
                        net_return * 100,
                        2,
                    ),

                "Benchmark Return %":
                    round(
                        market_return * 100,
                        2,
                    ),

                "Alpha %":
                    round(
                        (
                            net_return
                            - market_return
                        ) * 100,
                        2,
                    ),

                "Transaction Cost %":
                    round(
                        transaction_cost * 100,
                        4,
                    ),

                "Capital":
                    round(
                        capital,
                        2,
                    ),
            })

            old_portfolio = portfolio

        total_return = (
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
            total_return
            - benchmark_total
        )

        max_drawdown = (
            self.calculate_max_drawdown(
                equity_curve
            )
        )

        volatility = (
            self.calculate_volatility(
                period_returns
            )
        )

        sharpe = (
            self.calculate_sharpe(
                period_returns
            )
        )

        sortino = (
            self.calculate_sortino(
                period_returns
            )
        )

        win_rate = (
            self.calculate_win_rate(
                period_returns
            )
        )

        years = (
            end_year
            - start_year
        )

        if years > 0:

            cagr = (
                (
                    capital
                    / self.initial_capital
                )
                ** (
                    1 / years
                )
                - 1
            ) * 100

        else:

            cagr = 0

        report = {

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
                    total_return,
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

            "CAGR %":
                round(
                    cagr,
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

            "Sortino Ratio":
                round(
                    sortino,
                    2,
                ),

            "Win Rate %":
                round(
                    win_rate,
                    2,
                ),

            "Periods":
                all_periods,

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

        return report

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
                value
                / peak
                - 1
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

        return (
            math.sqrt(
                variance
            )
            * math.sqrt(4)
        )

    # --------------------------------
    # Sharpe
    # --------------------------------

    def calculate_sharpe(
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

        deviation = math.sqrt(
            variance
        )

        if deviation == 0:
            return 0

        return (
            mean
            / deviation
        ) * math.sqrt(4)

    # --------------------------------
    # Sortino
    # --------------------------------

    def calculate_sortino(
        self,
        returns,
    ):

        downside = [

            value

            for value in returns

            if value < 0
        ]

        if not downside:
            return 0

        downside_deviation = math.sqrt(
            sum(
                value ** 2
                for value in downside
            )
            / len(downside)
        )

        if downside_deviation == 0:
            return 0

        mean = (
            sum(returns)
            / len(returns)
        )

        return (
            mean
            / downside_deviation
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