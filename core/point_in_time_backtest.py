import math
import json
import os
from statistics import mean

from core.market_data_cache import MarketDataCache
from core.historical_fundamentals import HistoricalFundamentals
from core.stock_universe import StockUniverse


class PointInTimeBacktest:

    def __init__(
        self,
        initial_capital=100000,
        benchmark="^GSPC",
        portfolio_size=10,
        minimum_score=60,
        transaction_cost=0.001,
    ):

        self.initial_capital = initial_capital
        self.benchmark = benchmark
        self.portfolio_size = portfolio_size
        self.minimum_score = minimum_score
        self.transaction_cost = transaction_cost

        self.market_cache = MarketDataCache()
        self.fundamental_cache = HistoricalFundamentals()

        self.market_data = {}

    # ============================================================
    # LOAD MARKET DATA
    # ============================================================

    def load_market_data(
        self,
        symbols,
        start="2017-01-01",
        end="2027-01-01",
    ):

        universe = list(
            dict.fromkeys(
                symbols + [self.benchmark]
            )
        )

        print()
        print("=" * 80)
        print("LOADING MARKET DATA")
        print("=" * 80)

        self.market_data = (
            self.market_cache.load_universe(
                universe,
                start=start,
                end=end,
            )
        )

        print(
            f"Loaded {len(self.market_data)} instruments."
        )

    # ============================================================
    # PRICE DATA AVAILABLE BEFORE DECISION DATE
    # ============================================================

    def price_history(
        self,
        symbol,
        decision_date,
    ):

        data = self.market_data.get(symbol)

        if data is None:
            return None

        historical = data[
            data.index < decision_date
        ]

        if historical.empty:
            return None

        return historical

    # ============================================================
    # MOMENTUM
    # ============================================================

    def momentum_score(
        self,
        prices,
    ):

        if prices is None:
            return 50

        if len(prices) < 200:
            return 50

        close = prices["Close"]

        current = float(
            close.iloc[-1]
        )

        price_50 = float(
            close.iloc[-51]
        )

        price_100 = float(
            close.iloc[-101]
        )

        price_200 = float(
            close.iloc[-201]
        )

        return_50 = (
            current / price_50 - 1
        )

        return_100 = (
            current / price_100 - 1
        )

        return_200 = (
            current / price_200 - 1
        )

        score = 50

        score += return_50 * 100
        score += return_100 * 50
        score += return_200 * 25

        # Trend confirmation

        ma_50 = float(
            close.iloc[-50:].mean()
        )

        ma_100 = float(
            close.iloc[-100:].mean()
        )

        ma_200 = float(
            close.iloc[-200:].mean()
        )

        if current > ma_50:
            score += 5

        else:
            score -= 5

        if ma_50 > ma_100:
            score += 5

        else:
            score -= 5

        if ma_100 > ma_200:
            score += 5

        else:
            score -= 5

        return self.clamp(
            score
        )

    # ============================================================
    # RISK SCORE
    # ============================================================

    def risk_score(
        self,
        prices,
    ):

        if prices is None:
            return 50

        if len(prices) < 60:
            return 50

        close = prices["Close"]

        returns = (
            close.pct_change()
            .dropna()
            .iloc[-60:]
        )

        if len(returns) < 30:
            return 50

        volatility = float(
            returns.std()
            * math.sqrt(252)
        )

        if volatility <= 0.15:
            score = 90

        elif volatility <= 0.20:
            score = 80

        elif volatility <= 0.25:
            score = 70

        elif volatility <= 0.30:
            score = 60

        elif volatility <= 0.40:
            score = 45

        elif volatility <= 0.50:
            score = 30

        else:
            score = 15

        return score

    # ============================================================
    # FUNDAMENTAL HELPERS
    # ============================================================

    def numeric(
        self,
        value,
    ):

        if value is None:
            return None

        try:
            return float(value)

        except (
            TypeError,
            ValueError,
        ):

            return None

    # ============================================================
    # FUNDAMENTAL SNAPSHOT
    # ============================================================

    def fundamentals(
        self,
        symbol,
        decision_date,
    ):

        return self.fundamental_cache.get(
            symbol,
            decision_date,
        )

    # ============================================================
    # FUNDAMENTAL RAW SCORES
    # ============================================================

    def fundamental_scores(
        self,
        symbol,
        decision_date,
    ):

        current = self.fundamentals(
            symbol,
            decision_date,
        )

        if not current:
            return None

        revenue = self.numeric(
            current.get("Revenue")
        )

        net_income = self.numeric(
            current.get("Net Income")
        )

        operating_income = self.numeric(
            current.get("Operating Income")
        )

        equity = self.numeric(
            current.get("Equity")
        )

        assets = self.numeric(
            current.get("Assets")
        )

        liabilities = self.numeric(
            current.get("Liabilities")
        )

        cash = self.numeric(
            current.get("Cash")
        )

        free_cash_flow = self.numeric(
            current.get("Free Cash Flow")
        )

        roe = self.numeric(
            current.get("ROE")
        )

        profit_margin = self.numeric(
            current.get("Profit Margin")
        )

        operating_margin = self.numeric(
            current.get("Operating Margin")
        )

        debt_to_equity = self.numeric(
            current.get("Debt To Equity")
        )

        # --------------------------------------------------------
        # Previous historical snapshot
        # --------------------------------------------------------

        previous = None

        for year in range(
            1,
            6,
        ):

            candidate_year = (
                int(
                    decision_date[:4]
                )
                - year
            )

            candidate_date = (
                f"{candidate_year}"
                f"-{decision_date[5:7]}"
                f"-{decision_date[8:10]}"
            )

            candidate = (
                self.fundamentals(
                    symbol,
                    candidate_date,
                )
            )

            if candidate:

                previous = candidate
                break

        # ========================================================
        # GROWTH
        # ========================================================

        growth_score = 50

        if previous:

            previous_revenue = self.numeric(
                previous.get(
                    "Revenue"
                )
            )

            if (
                revenue is not None
                and previous_revenue
                and previous_revenue > 0
            ):

                revenue_growth = (
                    revenue
                    / previous_revenue
                    - 1
                )

                if revenue_growth >= 0.30:
                    growth_score = 95

                elif revenue_growth >= 0.20:
                    growth_score = 85

                elif revenue_growth >= 0.10:
                    growth_score = 75

                elif revenue_growth >= 0.05:
                    growth_score = 65

                elif revenue_growth >= 0:
                    growth_score = 55

                elif revenue_growth >= -0.10:
                    growth_score = 40

                else:
                    growth_score = 25

        # ========================================================
        # PROFITABILITY
        # ========================================================

        profitability_score = 50

        profitability_components = []

        if profit_margin is not None:

            profitability_components.append(
                self.ratio_score(
                    profit_margin,
                    [
                        (-0.10, 10),
                        (0.00, 30),
                        (0.05, 50),
                        (0.10, 65),
                        (0.15, 75),
                        (0.20, 85),
                        (0.30, 95),
                    ],
                )
            )

        if operating_margin is not None:

            profitability_components.append(
                self.ratio_score(
                    operating_margin,
                    [
                        (-0.10, 10),
                        (0.00, 30),
                        (0.05, 50),
                        (0.10, 65),
                        (0.15, 75),
                        (0.20, 85),
                        (0.30, 95),
                    ],
                )
            )

        if profitability_components:

            profitability_score = mean(
                profitability_components
            )

        # ========================================================
        # FINANCIAL STRENGTH
        # ========================================================

        financial_score = 50

        components = []

        if roe is not None:

            components.append(
                self.ratio_score(
                    roe,
                    [
                        (-0.10, 20),
                        (0.00, 35),
                        (0.05, 50),
                        (0.10, 65),
                        (0.15, 75),
                        (0.20, 85),
                        (0.30, 95),
                    ],
                )
            )

        if free_cash_flow is not None:

            if free_cash_flow > 0:
                components.append(70)

            else:
                components.append(30)

        if components:

            financial_score = mean(
                components
            )

        # ========================================================
        # BALANCE SHEET
        # ========================================================

        balance_score = 50

        balance_components = []

        if debt_to_equity is not None:

            if debt_to_equity <= 20:
                balance_components.append(95)

            elif debt_to_equity <= 40:
                balance_components.append(85)

            elif debt_to_equity <= 60:
                balance_components.append(70)

            elif debt_to_equity <= 100:
                balance_components.append(55)

            elif debt_to_equity <= 150:
                balance_components.append(40)

            else:
                balance_components.append(20)

        if (
            cash is not None
            and liabilities is not None
            and liabilities > 0
        ):

            cash_ratio = (
                cash
                / liabilities
            )

            if cash_ratio >= 0.50:
                balance_components.append(90)

            elif cash_ratio >= 0.30:
                balance_components.append(80)

            elif cash_ratio >= 0.15:
                balance_components.append(65)

            elif cash_ratio >= 0.05:
                balance_components.append(50)

            else:
                balance_components.append(30)

        if balance_components:

            balance_score = mean(
                balance_components
            )

        # ========================================================
        # BUSINESS QUALITY
        # ========================================================

        business_components = [
            profitability_score,
            financial_score,
            balance_score,
        ]

        business_score = mean(
            business_components
        )

        # ========================================================
        # RETURN
        # ========================================================

        return {

            "Business Quality":
                self.clamp(
                    business_score
                ),

            "Financial Strength":
                self.clamp(
                    financial_score
                ),

            "Growth":
                self.clamp(
                    growth_score
                ),

            "Profitability":
                self.clamp(
                    profitability_score
                ),

            "Balance Sheet":
                self.clamp(
                    balance_score
                ),

        }

    # ============================================================
    # RATIO SCORE
    # ============================================================

    def ratio_score(
        self,
        value,
        thresholds,
    ):

        score = thresholds[0][1]

        for threshold, rating in thresholds:

            if value >= threshold:
                score = rating

            else:
                break

        return score

    # ============================================================
    # COMPLETE STOCK SCORE
    # ============================================================

    def score_stock(
        self,
        symbol,
        decision_date,
    ):

        prices = self.price_history(
            symbol,
            decision_date,
        )

        if prices is None:
            return None

        if len(prices) < 200:
            return None

        fundamentals = (
            self.fundamental_scores(
                symbol,
                decision_date,
            )
        )

        if fundamentals is None:
            return None

        momentum = (
            self.momentum_score(
                prices
            )
        )

        risk = (
            self.risk_score(
                prices
            )
        )

        # --------------------------------------------------------
        # Factors not yet available historically
        # --------------------------------------------------------

        valuation = 50
        size = 50
        dividend = 50

        # --------------------------------------------------------
        # Final factor weights
        # --------------------------------------------------------

        weights = {

            "Business Quality":
                0.15,

            "Financial Strength":
                0.15,

            "Valuation":
                0.10,

            "Growth":
                0.15,

            "Profitability":
                0.15,

            "Momentum":
                0.15,

            "Risk":
                0.10,

            "Size":
                0.025,

            "Balance Sheet":
                0.075,

            "Dividend":
                0.00,

        }

        factor_scores = {

            **fundamentals,

            "Valuation":
                valuation,

            "Momentum":
                momentum,

            "Risk":
                risk,

            "Size":
                size,

            "Dividend":
                dividend,

        }

        overall_score = sum(
            factor_scores[factor]
            * weight

            for factor, weight
            in weights.items()
        )

        return {

            "Ticker":
                symbol,

            "Score":
                round(
                    self.clamp(
                        overall_score
                    ),
                    2,
                ),

            "Factor Scores":
                factor_scores,

        }

    # ============================================================
    # RANK UNIVERSE
    # ============================================================

    def rank_universe(
        self,
        symbols,
        decision_date,
    ):

        results = []

        for symbol in symbols:

            result = (
                self.score_stock(
                    symbol,
                    decision_date,
                )
            )

            if result:

                results.append(
                    result
                )

        results.sort(
            key=lambda x: (
                x["Score"]
            ),
            reverse=True,
        )

        return results

    # ============================================================
    # BUILD PORTFOLIO
    # ============================================================

    def build_portfolio(
        self,
        rankings,
    ):

        eligible = [

            result

            for result in rankings

            if result["Score"]
            >= self.minimum_score

        ]

        selected = eligible[
            :self.portfolio_size
        ]

        if not selected:
            return []

        weight = (
            1
            / len(selected)
        )

        portfolio = []

        for result in selected:

            portfolio.append({

                "Ticker":
                    result["Ticker"],

                "Weight":
                    weight,

                "Score":
                    result["Score"],

                "Factor Scores":
                    result[
                        "Factor Scores"
                    ],

            })

        return portfolio

    # ============================================================
    # RETURN FOR A PORTFOLIO
    # ============================================================

    def portfolio_return(
        self,
        portfolio,
        start_date,
        end_date,
    ):

        if not portfolio:
            return 0

        total = 0

        for position in portfolio:

            symbol = position[
                "Ticker"
            ]

            weight = position[
                "Weight"
            ]

            data = self.market_data.get(
                symbol
            )

            if data is None:
                continue

            period = data[
                (
                    data.index
                    >= start_date
                )
                &
                (
                    data.index
                    < end_date
                )
            ]

            if len(period) < 2:
                continue

            first = float(
                period[
                    "Close"
                ].iloc[0]
            )

            last = float(
                period[
                    "Close"
                ].iloc[-1]
            )

            if first <= 0:
                continue

            stock_return = (
                last / first
                - 1
            )

            total += (
                weight
                * stock_return
            )

        return total

    # ============================================================
    # BENCHMARK RETURN
    # ============================================================

    def benchmark_return(
        self,
        start_date,
        end_date,
    ):

        data = self.market_data.get(
            self.benchmark
        )

        if data is None:
            return 0

        period = data[
            (
                data.index
                >= start_date
            )
            &
            (
                data.index
                < end_date
            )
        ]

        if len(period) < 2:
            return 0

        first = float(
            period[
                "Close"
            ].iloc[0]
        )

        last = float(
            period[
                "Close"
            ].iloc[-1]
        )

        if first <= 0:
            return 0

        return (
            last / first
            - 1
        )

    # ============================================================
    # REBALANCE DATES
    # ============================================================

    def rebalance_dates(
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

                dates.append(
                    f"{year}-{month:02d}-01"
                )

        return dates

    # ============================================================
    # MAXIMUM DRAWDOWN
    # ============================================================

    def maximum_drawdown(
        self,
        curve,
    ):

        if not curve:
            return 0

        peak = curve[0]
        maximum = 0

        for value in curve:

            if value > peak:
                peak = value

            if peak <= 0:
                continue

            drawdown = (
                value / peak
                - 1
            ) * 100

            maximum = min(
                maximum,
                drawdown,
            )

        return abs(
            maximum
        )

    # ============================================================
    # SHARPE
    # ============================================================

    def sharpe_ratio(
        self,
        returns,
    ):

        if len(returns) < 2:
            return 0

        average = mean(
            returns
        )

        variance = (
            sum(
                (
                    value
                    - average
                ) ** 2

                for value in returns
            )
            /
            (
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
            average
            / deviation
        ) * math.sqrt(4)

    # ============================================================
    # CLAMP
    # ============================================================

    def clamp(
        self,
        value,
    ):

        return max(
            0,
            min(
                100,
                value,
            ),
        )

    # ============================================================
    # MAIN BACKTEST
    # ============================================================

    def run(
        self,
        symbols,
        start_year=2020,
        end_year=2026,
    ):

        self.load_market_data(
            symbols
        )

        dates = (
            self.rebalance_dates(
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

        equity_curve = [
            capital
        ]

        benchmark_curve = [
            benchmark_capital
        ]

        strategy_returns = []

        periods = []

        for index in range(
            len(dates) - 1
        ):

            decision_date = (
                dates[index]
            )

            next_date = (
                dates[index + 1]
            )

            print()
            print(
                "=" * 80
            )

            print(
                f"REBALANCE DATE: "
                f"{decision_date}"
            )

            print(
                "=" * 80
            )

            rankings = (
                self.rank_universe(
                    symbols,
                    decision_date,
                )
            )

            portfolio = (
                self.build_portfolio(
                    rankings
                )
            )

            strategy_return = (
                self.portfolio_return(
                    portfolio,
                    decision_date,
                    next_date,
                )
            )

            benchmark_return = (
                self.benchmark_return(
                    decision_date,
                    next_date,
                )
            )

            # Transaction cost applied once
            # at each rebalance.

            net_strategy_return = (
                strategy_return
                - self.transaction_cost
            )

            capital *= (
                1
                + net_strategy_return
            )

            benchmark_capital *= (
                1
                + benchmark_return
            )

            equity_curve.append(
                capital
            )

            benchmark_curve.append(
                benchmark_capital
            )

            strategy_returns.append(
                net_strategy_return
            )

            periods.append({

                "Decision Date":
                    decision_date,

                "End Date":
                    next_date,

                "Strategy Return %":
                    round(
                        net_strategy_return
                        * 100,
                        2,
                    ),

                "Benchmark Return %":
                    round(
                        benchmark_return
                        * 100,
                        2,
                    ),

                "Alpha %":
                    round(
                        (
                            net_strategy_return
                            - benchmark_return
                        )
                        * 100,
                        2,
                    ),

                "Capital":
                    round(
                        capital,
                        2,
                    ),

                "Portfolio":
                    portfolio,

            })

            print()

            print(
                "TOP HOLDINGS"
            )

            for position in portfolio:

                print(
                    f"  "
                    f"{position['Ticker']:<6} "
                    f"{position['Weight'] * 100:>5.1f}% "
                    f"Score "
                    f"{position['Score']:>6.2f}"
                )

            print()

            print(
                f"Strategy Return: "
                f"{net_strategy_return * 100:.2f}%"
            )

            print(
                f"S&P 500 Return: "
                f"{benchmark_return * 100:.2f}%"
            )

        total_return = (
            (
                capital
                / self.initial_capital
            )
            - 1
        ) * 100

        benchmark_total_return = (
            (
                benchmark_capital
                / self.initial_capital
            )
            - 1
        ) * 100

        alpha = (
            total_return
            - benchmark_total_return
        )

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
                    benchmark_total_return,
                    2,
                ),

            "Alpha %":
                round(
                    alpha,
                    2,
                ),

            "Maximum Drawdown %":
                round(
                    self.maximum_drawdown(
                        equity_curve
                    ),
                    2,
                ),

            "Sharpe Ratio":
                round(
                    self.sharpe_ratio(
                        strategy_returns
                    ),
                    2,
                ),

            "Periods":
                periods,

            "Strategy Equity Curve":
                equity_curve,

            "Benchmark Equity Curve":
                benchmark_curve,

        }

        return report


# ================================================================
# RUN
# ================================================================

if __name__ == "__main__":

    universe = StockUniverse()

    symbols = universe.load()

    if not symbols:

        symbols = universe.build()

    backtest = (
        PointInTimeBacktest(
            initial_capital=100000,
            portfolio_size=10,
            minimum_score=60,
            transaction_cost=0.001,
        )
    )

    report = backtest.run(
        symbols,
        start_year=2020,
        end_year=2026,
    )

    print()
    print("=" * 80)
    print("FINAL POINT-IN-TIME BACKTEST")
    print("=" * 80)

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

    print(
        f"Maximum Drawdown: "
        f"{report['Maximum Drawdown %']}%"
    )

    print(
        f"Sharpe Ratio: "
        f"{report['Sharpe Ratio']}"
    )