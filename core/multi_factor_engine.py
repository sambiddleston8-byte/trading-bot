import json
import math
import os

import yfinance as yf

from core.data_engine import MarketDataEngine
from core.adaptive_weights_engine import AdaptiveWeightsEngine
from core.expected_return_engine import ExpectedReturnEngine


class MultiFactorEngine:

    def __init__(self):

        self.data_cache = MarketDataEngine()

        # --------------------------------
        # Base Weights
        # --------------------------------

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

        # --------------------------------
        # Adaptive Weights
        # --------------------------------

        self.weights = (
            self.load_adaptive_weights()
        )

        # --------------------------------
        # Expected Return Engine
        # --------------------------------

        self.expected_return_engine = (
            ExpectedReturnEngine()
        )

    # ============================================================
    # ADAPTIVE WEIGHTS
    # ============================================================

    def load_adaptive_weights(self):

        path = (
            "data/adaptive_weights.json"
        )

        if not os.path.exists(path):

            return self.base_weights.copy()

        try:

            with open(
                path,
                "r",
            ) as file:

                data = json.load(
                    file
                )

            weights = data.get(
                "Weights",
                {}
            )

            if not weights:

                return self.base_weights.copy()

            valid = {}

            for factor in (
                self.base_weights
            ):

                value = weights.get(
                    factor
                )

                if value is None:

                    value = self.base_weights[
                        factor
                    ]

                valid[factor] = float(
                    value
                )

            total = sum(
                valid.values()
            )

            if total <= 0:

                return self.base_weights.copy()

            for factor in valid:

                valid[factor] = (
                    valid[factor]
                    / total
                )

            return valid

        except Exception as error:

            print(
                f"Adaptive weights failed: "
                f"{error}"
            )

            return self.base_weights.copy()

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
    ):

        try:

            if value is None:

                return None

            value = float(
                value
            )

            if math.isnan(value):

                return None

            if math.isinf(value):

                return None

            return value

        except (
            TypeError,
            ValueError,
        ):

            return None

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
    # BUSINESS QUALITY
    # ============================================================

    def business_quality(
        self,
        info,
    ):

        score = 50

        revenue_growth = self.safe_float(
            info.get(
                "revenueGrowth"
            )
        )

        profit_margin = self.safe_float(
            info.get(
                "profitMargins"
            )
        )

        operating_margin = self.safe_float(
            info.get(
                "operatingMargins"
            )
        )

        if revenue_growth is not None:

            if revenue_growth > 0.20:

                score += 10

            elif revenue_growth > 0.10:

                score += 6

            elif revenue_growth > 0:

                score += 2

            elif revenue_growth < -0.10:

                score -= 8

        if profit_margin is not None:

            if profit_margin > 0.25:

                score += 10

            elif profit_margin > 0.15:

                score += 6

            elif profit_margin > 0.05:

                score += 2

            elif profit_margin < 0:

                score -= 10

        if operating_margin is not None:

            if operating_margin > 0.25:

                score += 8

            elif operating_margin > 0.15:

                score += 5

            elif operating_margin < 0:

                score -= 8

        return self.clamp(
            score
        )

    # ============================================================
    # FINANCIAL STRENGTH
    # ============================================================

    def financial_strength(
        self,
        info,
    ):

        score = 50

        current_ratio = self.safe_float(
            info.get(
                "currentRatio"
            )
        )

        quick_ratio = self.safe_float(
            info.get(
                "quickRatio"
            )
        )

        operating_cash_flow = self.safe_float(
            info.get(
                "operatingCashflow"
            )
        )

        free_cash_flow = self.safe_float(
            info.get(
                "freeCashflow"
            )
        )

        if current_ratio is not None:

            if current_ratio >= 2:

                score += 12

            elif current_ratio >= 1.5:

                score += 8

            elif current_ratio >= 1:

                score += 3

            elif current_ratio < 0.75:

                score -= 10

        if quick_ratio is not None:

            if quick_ratio >= 1.5:

                score += 8

            elif quick_ratio >= 1:

                score += 4

            elif quick_ratio < 0.7:

                score -= 8

        if operating_cash_flow is not None:

            if operating_cash_flow > 0:

                score += 8

            else:

                score -= 8

        if free_cash_flow is not None:

            if free_cash_flow > 0:

                score += 8

            else:

                score -= 8

        return self.clamp(
            score
        )

    # ============================================================
    # VALUATION
    # ============================================================

    def valuation(
        self,
        info,
    ):

        score = 50

        forward_pe = self.safe_float(
            info.get(
                "forwardPE"
            )
        )

        trailing_pe = self.safe_float(
            info.get(
                "trailingPE"
            )
        )

        price_to_book = self.safe_float(
            info.get(
                "priceToBook"
            )
        )

        peg = self.safe_float(
            info.get(
                "pegRatio"
            )
        )

        if forward_pe is not None:

            if 0 < forward_pe < 15:

                score += 15

            elif forward_pe < 20:

                score += 10

            elif forward_pe < 30:

                score += 4

            elif forward_pe < 45:

                score -= 5

            elif forward_pe >= 45:

                score -= 12

        if trailing_pe is not None:

            if 0 < trailing_pe < 15:

                score += 8

            elif trailing_pe < 25:

                score += 5

            elif trailing_pe < 40:

                score -= 2

            elif trailing_pe >= 40:

                score -= 8

        if peg is not None:

            if 0 < peg < 1:

                score += 12

            elif peg < 1.5:

                score += 7

            elif peg < 2:

                score += 2

            elif peg > 3:

                score -= 8

        if price_to_book is not None:

            if 0 < price_to_book < 3:

                score += 4

            elif price_to_book > 10:

                score -= 5

        return self.clamp(
            score
        )

    # ============================================================
    # GROWTH
    # ============================================================

    def growth(
        self,
        info,
    ):

        score = 50

        revenue_growth = self.safe_float(
            info.get(
                "revenueGrowth"
            )
        )

        earnings_growth = self.safe_float(
            info.get(
                "earningsGrowth"
            )
        )

        earnings_quarterly = self.safe_float(
            info.get(
                "earningsQuarterlyGrowth"
            )
        )

        if revenue_growth is not None:

            if revenue_growth > 0.30:

                score += 15

            elif revenue_growth > 0.15:

                score += 10

            elif revenue_growth > 0.05:

                score += 5

            elif revenue_growth < 0:

                score -= 8

        if earnings_growth is not None:

            if earnings_growth > 0.30:

                score += 15

            elif earnings_growth > 0.15:

                score += 10

            elif earnings_growth > 0:

                score += 4

            elif earnings_growth < 0:

                score -= 10

        if earnings_quarterly is not None:

            if earnings_quarterly > 0.20:

                score += 8

            elif earnings_quarterly > 0:

                score += 3

            elif earnings_quarterly < -0.10:

                score -= 8

        return self.clamp(
            score
        )

    # ============================================================
    # PROFITABILITY
    # ============================================================

    def profitability(
        self,
        info,
    ):

        score = 50

        roe = self.safe_float(
            info.get(
                "returnOnEquity"
            )
        )

        roa = self.safe_float(
            info.get(
                "returnOnAssets"
            )
        )

        operating_margin = self.safe_float(
            info.get(
                "operatingMargins"
            )
        )

        profit_margin = self.safe_float(
            info.get(
                "profitMargins"
            )
        )

        if roe is not None:

            if roe > 0.30:

                score += 15

            elif roe > 0.20:

                score += 10

            elif roe > 0.10:

                score += 5

            elif roe < 0:

                score -= 10

        if roa is not None:

            if roa > 0.15:

                score += 10

            elif roa > 0.08:

                score += 5

            elif roa < 0:

                score -= 8

        if operating_margin is not None:

            if operating_margin > 0.25:

                score += 8

            elif operating_margin > 0.15:

                score += 5

            elif operating_margin < 0:

                score -= 8

        if profit_margin is not None:

            if profit_margin > 0.20:

                score += 7

            elif profit_margin > 0.10:

                score += 4

            elif profit_margin < 0:

                score -= 8

        return self.clamp(
            score
        )

    # ============================================================
    # MOMENTUM
    # ============================================================

    def momentum(
        self,
        prices,
    ):

        if prices is None:

            return 50

        if len(prices) < 200:

            return 50

        close = prices["Close"]

        current = self.safe_float(
            close.iloc[-1]
        )

        ma_50 = self.safe_float(
            close.tail(50).mean()
        )

        ma_200 = self.safe_float(
            close.tail(200).mean()
        )

        if (
            current is None
            or ma_50 is None
            or ma_200 is None
        ):

            return 50

        score = 50

        if current > ma_50:

            score += 15

        else:

            score -= 10

        if current > ma_200:

            score += 15

        else:

            score -= 15

        if ma_50 > ma_200:

            score += 10

        else:

            score -= 10

        return self.clamp(
            score
        )

    # ============================================================
    # RISK
    # ============================================================

    def risk(
        self,
        info,
        prices,
    ):

        score = 50

        beta = self.safe_float(
            info.get(
                "beta"
            )
        )

        debt_to_equity = self.safe_float(
            info.get(
                "debtToEquity"
            )
        )

        if beta is not None:

            if beta < 0.8:

                score += 12

            elif beta < 1.2:

                score += 5

            elif beta < 1.8:

                score -= 5

            else:

                score -= 12

        if debt_to_equity is not None:

            if debt_to_equity < 30:

                score += 10

            elif debt_to_equity < 70:

                score += 5

            elif debt_to_equity < 150:

                score -= 5

            else:

                score -= 12

        if prices is not None:

            if len(prices) >= 60:

                returns = (
                    prices["Close"]
                    .pct_change()
                    .dropna()
                    .tail(60)
                )

                if len(returns) > 10:

                    volatility = (
                        returns.std()
                        * math.sqrt(252)
                    )

                    if volatility < 0.20:

                        score += 8

                    elif volatility < 0.30:

                        score += 3

                    elif volatility > 0.50:

                        score -= 10

        return self.clamp(
            score
        )

    # ============================================================
    # SIZE
    # ============================================================

    def size(
        self,
        info,
    ):

        market_cap = self.safe_float(
            info.get(
                "marketCap"
            )
        )

        if market_cap is None:

            return 50

        if market_cap >= 500_000_000_000:

            return 90

        if market_cap >= 100_000_000_000:

            return 80

        if market_cap >= 50_000_000_000:

            return 70

        if market_cap >= 20_000_000_000:

            return 60

        if market_cap >= 5_000_000_000:

            return 50

        return 40

    # ============================================================
    # BALANCE SHEET
    # ============================================================

    def balance_sheet(
        self,
        info,
    ):

        score = 50

        debt_to_equity = self.safe_float(
            info.get(
                "debtToEquity"
            )
        )

        current_ratio = self.safe_float(
            info.get(
                "currentRatio"
            )
        )

        free_cash_flow = self.safe_float(
            info.get(
                "freeCashflow"
            )
        )

        if debt_to_equity is not None:

            if debt_to_equity < 30:

                score += 15

            elif debt_to_equity < 70:

                score += 8

            elif debt_to_equity > 150:

                score -= 15

            elif debt_to_equity > 100:

                score -= 8

        if current_ratio is not None:

            if current_ratio > 2:

                score += 10

            elif current_ratio > 1.2:

                score += 5

            elif current_ratio < 0.8:

                score -= 10

        if free_cash_flow is not None:

            if free_cash_flow > 0:

                score += 10

            else:

                score -= 10

        return self.clamp(
            score
        )

    # ============================================================
    # DIVIDEND
    # ============================================================

    def dividend(
        self,
        info,
    ):

        yield_value = self.safe_float(
            info.get(
                "dividendYield"
            )
        )

        if yield_value is None:

            return 50

        if yield_value <= 0:

            return 45

        if yield_value < 0.01:

            return 50

        if yield_value < 0.03:

            return 65

        if yield_value < 0.05:

            return 75

        if yield_value < 0.08:

            return 65

        return 50

    # ============================================================
    # YAHOO FUNDAMENTALS
    # ============================================================

    def get_info(
        self,
        symbol,
    ):

        try:

            ticker = yf.Ticker(
                symbol
            )

            return ticker.info

        except Exception as error:

            print(
                f"{symbol} info failed: "
                f"{error}"
            )

            return {}

    # ============================================================
    # ANALYSE
    # ============================================================

    def analyse(
        self,
        symbol,
        prices=None,
        info=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        if info is None:

            info = self.get_info(
                symbol
            )

        if not info:

            return None

        if prices is None:

            try:

                prices = (
                    self.data_cache.load(
                        symbol,
                        start="2018-01-01",
                    )
                )

            except Exception:

                prices = None

        # ========================================================
        # FACTOR SCORES
        # ========================================================

        scores = {

            "Business Quality":
                self.business_quality(
                    info
                ),

            "Financial Strength":
                self.financial_strength(
                    info
                ),

            "Valuation":
                self.valuation(
                    info
                ),

            "Growth":
                self.growth(
                    info
                ),

            "Profitability":
                self.profitability(
                    info
                ),

            "Momentum":
                self.momentum(
                    prices
                ),

            "Risk":
                self.risk(
                    info,
                    prices
                ),

            "Size":
                self.size(
                    info
                ),

            "Balance Sheet":
                self.balance_sheet(
                    info
                ),

            "Dividend":
                self.dividend(
                    info
                ),

        }

        # ========================================================
        # MULTI-FACTOR SCORE
        # ========================================================

        weighted_score = 0

        for factor, score in (
            scores.items()
        ):

            weighted_score += (

                score

                * self.weights.get(
                    factor,
                    0,
                )

            )

        weighted_score = round(
            weighted_score,
            2,
        )

        # ========================================================
        # EXPECTED RETURN
        # ========================================================

        expected_return_analysis = (
            self.expected_return_engine.estimate(

                factor_scores=scores,

                info=info,

                prices=prices,

            )
        )

        expected_return = (
            expected_return_analysis.get(
                "Expected Return",
                0,
            )
        )

        expected_return_confidence = (
            expected_return_analysis.get(
                "Confidence",
                50,
            )
        )

        # ========================================================
        # RESULT
        # ========================================================

        return {

            "Ticker":
                symbol,

            "Company":
                info.get(
                    "longName"
                ),

            "Sector":
                info.get(
                    "sector"
                ),

            "Industry":
                info.get(
                    "industry"
                ),

            "Overall Score":
                weighted_score,

            "Factor Scores":
                scores,

            "Weights":
                self.weights,

            "Expected Return":
                expected_return,

            "Expected Return Confidence":
                expected_return_confidence,

            "Expected Return Analysis":
                expected_return_analysis,

            "Market Cap":
                info.get(
                    "marketCap"
                ),

            "Forward PE":
                info.get(
                    "forwardPE"
                ),

            "Revenue Growth":
                info.get(
                    "revenueGrowth"
                ),

            "Earnings Growth":
                info.get(
                    "earningsGrowth"
                ),

            "ROE":
                info.get(
                    "returnOnEquity"
                ),

            "Debt To Equity":
                info.get(
                    "debtToEquity"
                ),

        }

    # ============================================================
    # UNIVERSE
    # ============================================================

    def analyse_universe(
        self,
        symbols,
    ):

        symbols = list(
            dict.fromkeys(
                symbols
            )
        )

        print()
        print("=" * 70)
        print(
            "MULTI-FACTOR UNIVERSE ANALYSIS"
        )
        print("=" * 70)

        print()
        print(
            f"Stocks: {len(symbols)}"
        )

        print()
        print(
            "Loading cached market data..."
        )

        price_data = (
            self.data_cache.load_universe(
                symbols,
                start="2018-01-01",
            )
        )

        print()
        print(
            "Analysing stocks..."
        )

        results = []

        total = len(
            symbols
        )

        for index, symbol in enumerate(
            symbols,
            start=1,
        ):

            print(
                f"[{index}/{total}] "
                f"{symbol}"
            )

            info = self.get_info(
                symbol
            )

            prices = price_data.get(
                symbol
            )

            result = self.analyse(
                symbol,
                prices=prices,
                info=info,
            )

            if result is not None:

                results.append(
                    result
                )

        results.sort(
            key=lambda item:
                item.get(
                    "Overall Score",
                    0,
                ),
            reverse=True,
        )

        for rank, result in enumerate(
            results,
            start=1,
        ):

            result[
                "Rank"
            ] = rank

        return results

    # ============================================================
    # SAVE
    # ============================================================

    def save_universe_results(
        self,
        results,
        path="data/multi_factor_rankings.json",
    ):

        directory = os.path.dirname(
            path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

        output = {

            "Stock Count":
                len(results),

            "Weights":
                self.weights,

            "Rankings":
                results,

        }

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        return path


if __name__ == "__main__":

    from core.stock_universe import (
        StockUniverse
    )

    universe = StockUniverse()

    symbols = universe.load()

    if not symbols:

        symbols = universe.build()

    engine = MultiFactorEngine()

    print()
    print(
        "ACTIVE FACTOR WEIGHTS"
    )

    for factor, weight in (
        engine.weights.items()
    ):

        print(
            f"{factor:<25} "
            f"{weight * 100:>6.2f}%"
        )

    results = (
        engine.analyse_universe(
            symbols
        )
    )

    path = (
        engine.save_universe_results(
            results
        )
    )

    print()
    print("=" * 80)
    print("TOP 20 MULTI-FACTOR STOCKS")
    print("=" * 80)
    print()

    for result in results[:20]:

        print(
            f"{result['Rank']:>3}. "
            f"{result['Ticker']:<6} "
            f"{result['Overall Score']:>6.2f} "
            f"{result.get('Company', '')}"
        )

    print()
    print(
        f"Ranked {len(results)} stocks."
    )

    print(
        f"Saved to: {path}"
    )