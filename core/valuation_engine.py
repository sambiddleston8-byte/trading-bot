import json
import math
import os
from datetime import datetime, timezone

import yfinance as yf


class ValuationEngine:

    def __init__(
        self,
        forecast_years=5,
        default_wacc=0.09,
        default_terminal_growth=0.03,
    ):

        self.forecast_years = forecast_years
        self.default_wacc = default_wacc
        self.default_terminal_growth = default_terminal_growth

        self.output_directory = "data/research/valuation"

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

    # ============================================================
    # TIME
    # ============================================================

    def utc_now(self):

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=None,
    ):

        try:

            if value is None:
                return default

            value = float(value)

            if math.isnan(value):
                return default

            if math.isinf(value):
                return default

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # COMPANY DATA
    # ============================================================

    def get_ticker(
        self,
        symbol,
    ):

        return yf.Ticker(
            symbol
        )

    # ============================================================
    # COMPANY INFO
    # ============================================================

    def get_info(
        self,
        symbol,
    ):

        try:

            ticker = self.get_ticker(
                symbol
            )

            return ticker.info

        except Exception as error:

            print(
                f"{symbol} info failed: {error}"
            )

            return {}

    # ============================================================
    # FINANCIAL STATEMENTS
    # ============================================================

    def get_income_statement(
        self,
        symbol,
    ):

        try:

            ticker = self.get_ticker(
                symbol
            )

            statement = ticker.income_stmt

            if statement is None:
                return None

            if statement.empty:
                return None

            return statement

        except Exception as error:

            print(
                f"{symbol} income statement failed: {error}"
            )

            return None

    # ============================================================

    def get_cash_flow(
        self,
        symbol,
    ):

        try:

            ticker = self.get_ticker(
                symbol
            )

            cash_flow = ticker.cashflow

            if cash_flow is None:
                return None

            if cash_flow.empty:
                return None

            return cash_flow

        except Exception as error:

            print(
                f"{symbol} cash flow failed: {error}"
            )

            return None

    # ============================================================
    # FIND FINANCIAL LINE
    # ============================================================

    def find_line(
        self,
        statement,
        names,
    ):

        if statement is None:
            return None

        for name in names:

            if name in statement.index:

                return statement.loc[name]

        return None

    # ============================================================
    # EXTRACT ANNUAL FINANCIAL DATA
    # ============================================================

    def get_annual_financials(
        self,
        income_statement,
        cash_flow,
    ):

        revenue_line = self.find_line(
            income_statement,
            [
                "Total Revenue",
                "Operating Revenue",
            ],
        )

        operating_cash_line = self.find_line(
            cash_flow,
            [
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
            ],
        )

        capex_line = self.find_line(
            cash_flow,
            [
                "Capital Expenditure",
                "Capital Expenditure Reported",
            ],
        )

        if revenue_line is None:
            return []

        periods = []

        for column in income_statement.columns:

            revenue = self.safe_float(
                revenue_line.get(
                    column
                )
            )

            if revenue is None or revenue <= 0:
                continue

            operating_cash = None
            capex = None
            fcf = None

            if operating_cash_line is not None:

                operating_cash = self.safe_float(
                    operating_cash_line.get(
                        column
                    )
                )

            if capex_line is not None:

                capex = self.safe_float(
                    capex_line.get(
                        column
                    )
                )

            if (
                operating_cash is not None
                and capex is not None
            ):

                # Yahoo generally reports capital expenditure
                # as a negative cash flow.
                fcf = (
                    operating_cash
                    + capex
                )

            periods.append({

                "Period":
                    str(column),

                "Revenue":
                    revenue,

                "Operating Cash Flow":
                    operating_cash,

                "Capital Expenditure":
                    capex,

                "Free Cash Flow":
                    fcf,

            })

        return periods

    # ============================================================
    # HISTORICAL REVENUE GROWTH
    # ============================================================

    def calculate_revenue_growth(
        self,
        historical_financials,
    ):

        usable = [

            item

            for item in historical_financials

            if (
                item.get("Revenue") is not None
                and item.get("Revenue") > 0
            )

        ]

        if len(usable) < 2:
            return None

        usable = list(
            reversed(
                usable
            )
        )

        first = usable[0]["Revenue"]
        last = usable[-1]["Revenue"]

        years = len(usable) - 1

        if (
            first <= 0
            or last <= 0
            or years <= 0
        ):

            return None

        try:

            return (
                (
                    last / first
                )
                ** (
                    1 / years
                )
                - 1
            )

        except Exception:

            return None

    # ============================================================
    # HISTORICAL FCF GROWTH
    # ============================================================

    def calculate_fcf_growth(
        self,
        historical_financials,
    ):

        usable = [

            item

            for item in historical_financials

            if (
                item.get("Free Cash Flow") is not None
                and item.get("Free Cash Flow") > 0
            )

        ]

        if len(usable) < 2:
            return None

        usable = list(
            reversed(
                usable
            )
        )

        first = usable[0]["Free Cash Flow"]
        last = usable[-1]["Free Cash Flow"]

        years = len(usable) - 1

        if (
            first <= 0
            or last <= 0
            or years <= 0
        ):

            return None

        try:

            return (
                (
                    last / first
                )
                ** (
                    1 / years
                )
                - 1
            )

        except Exception:

            return None

    # ============================================================
    # LATEST FINANCIAL PERIOD
    # ============================================================

    def get_latest_financial_period(
        self,
        historical_financials,
    ):

        usable = [

            item

            for item in historical_financials

            if item.get("Revenue") is not None

        ]

        if not usable:
            return None

        return usable[0]

    # ============================================================
    # CURRENT FCF
    # ============================================================

    def get_current_fcf(
        self,
        historical_financials,
    ):

        latest = (
            self.get_latest_financial_period(
                historical_financials
            )
        )

        if latest is None:
            return None

        return self.safe_float(
            latest.get(
                "Free Cash Flow"
            )
        )

    # ============================================================
    # REVENUE GROWTH ASSUMPTION
    # ============================================================

    def determine_revenue_growth(
        self,
        info,
        historical_revenue_growth,
    ):

        yahoo_growth = self.safe_float(
            info.get(
                "revenueGrowth"
            )
        )

        candidates = []

        if yahoo_growth is not None:
            candidates.append(
                yahoo_growth
            )

        if historical_revenue_growth is not None:
            candidates.append(
                historical_revenue_growth
            )

        if not candidates:
            return 0.08

        # --------------------------------------------------------
        # We no longer silently force growth to exactly 40%.
        #
        # Instead, use a blended forward/historical estimate and
        # apply broad sanity limits.
        # --------------------------------------------------------

        if (
            yahoo_growth is not None
            and historical_revenue_growth is not None
        ):

            growth = (
                yahoo_growth * 0.70
                +
                historical_revenue_growth * 0.30
            )

        else:

            growth = candidates[0]

        return max(
            -0.20,
            min(
                growth,
                0.80,
            ),
        )

    # ============================================================
    # FCF MARGIN
    # ============================================================

    def determine_fcf_margin(
        self,
        revenue,
        current_fcf,
    ):

        if (
            revenue is None
            or revenue <= 0
            or current_fcf is None
            or current_fcf <= 0
        ):

            return 0.15

        margin = (
            current_fcf
            / revenue
        )

        return max(
            0.01,
            min(
                margin,
                0.70,
            ),
        )

    # ============================================================
    # TARGET FCF MARGIN
    # ============================================================

    def determine_target_margin(
        self,
        historical_financials,
        current_margin,
    ):

        margins = []

        for item in historical_financials:

            revenue = self.safe_float(
                item.get(
                    "Revenue"
                )
            )

            fcf = self.safe_float(
                item.get(
                    "Free Cash Flow"
                )
            )

            if (
                revenue is not None
                and revenue > 0
                and fcf is not None
                and fcf > 0
            ):

                margin = (
                    fcf / revenue
                )

                margins.append(
                    margin
                )

        if not margins:
            return current_margin

        historical_average = (
            sum(margins)
            / len(margins)
        )

        # --------------------------------------------------------
        # Target margin is based on a blend of:
        #
        # 1. Current FCF margin
        # 2. Historical average FCF margin
        #
        # This is much more defensible than simply subtracting
        # an arbitrary five percentage points.
        # --------------------------------------------------------

        target_margin = (
            current_margin * 0.60
            +
            historical_average * 0.40
        )

        return max(
            0.01,
            min(
                target_margin,
                0.70,
            ),
        )

    # ============================================================
    # WACC
    # ============================================================

    def determine_wacc(
        self,
        info,
    ):

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

        wacc = self.default_wacc

        if beta is not None:

            if beta >= 2.0:
                wacc += 0.025

            elif beta >= 1.5:
                wacc += 0.015

            elif beta >= 1.2:
                wacc += 0.005

            elif beta < 0.8:
                wacc -= 0.01

        if debt_to_equity is not None:

            if debt_to_equity > 150:
                wacc += 0.01

            elif debt_to_equity > 75:
                wacc += 0.005

            elif debt_to_equity < 25:
                wacc -= 0.005

        return max(
            0.06,
            min(
                wacc,
                0.15,
            ),
        )

    # ============================================================
    # TERMINAL GROWTH
    # ============================================================

    def determine_terminal_growth(
        self,
    ):

        return max(
            0.01,
            min(
                self.default_terminal_growth,
                0.04,
            ),
        )

    # ============================================================
    # GROWTH PATH
    # ============================================================

    def build_growth_path(
        self,
        starting_growth,
        terminal_growth,
    ):

        path = []

        for year in range(
            1,
            self.forecast_years + 1,
        ):

            fade = (
                year
                / (
                    self.forecast_years
                    + 1
                )
            )

            growth = (
                starting_growth
                * (
                    1 - fade
                )
                +
                terminal_growth
                * fade
            )

            path.append(
                growth
            )

        return path

    # ============================================================
    # MARGIN PATH
    # ============================================================

    def build_margin_path(
        self,
        current_margin,
        target_margin,
    ):

        path = []

        for year in range(
            1,
            self.forecast_years + 1,
        ):

            fade = (
                year
                / self.forecast_years
            )

            margin = (
                current_margin
                * (
                    1 - fade
                )
                +
                target_margin
                * fade
            )

            path.append(
                margin
            )

        return path

    # ============================================================
    # FORECAST
    # ============================================================

    def forecast(
        self,
        revenue,
        current_margin,
        target_margin,
        growth_path,
    ):

        margin_path = (
            self.build_margin_path(
                current_margin,
                target_margin,
            )
        )

        current_revenue = revenue

        revenue_forecast = []

        fcf_forecast = []

        for index in range(
            self.forecast_years
        ):

            year = index + 1

            growth = (
                growth_path[index]
            )

            margin = (
                margin_path[index]
            )

            current_revenue = (
                current_revenue
                * (
                    1 + growth
                )
            )

            fcf = (
                current_revenue
                * margin
            )

            revenue_forecast.append({

                "Year":
                    year,

                "Growth":
                    growth,

                "Revenue":
                    current_revenue,

            })

            fcf_forecast.append({

                "Year":
                    year,

                "FCF Margin":
                    margin,

                "Free Cash Flow":
                    fcf,

            })

        return (
            revenue_forecast,
            fcf_forecast,
        )

    # ============================================================
    # DCF
    # ============================================================

    def calculate_dcf(
        self,
        fcf_forecast,
        wacc,
        terminal_growth,
        shares_outstanding,
        net_debt,
    ):

        if (
            not fcf_forecast
            or wacc <= terminal_growth
        ):

            return {

                "Status":
                    "FAILED",

                "Reason":
                    "Invalid DCF assumptions.",

            }

        present_values = []

        for item in fcf_forecast:

            year = item[
                "Year"
            ]

            fcf = item[
                "Free Cash Flow"
            ]

            discount_factor = (
                1
                / (
                    (
                        1 + wacc
                    )
                    ** year
                )
            )

            present_value = (
                fcf
                * discount_factor
            )

            present_values.append({

                "Year":
                    year,

                "Free Cash Flow":
                    fcf,

                "Discount Factor":
                    discount_factor,

                "Present Value":
                    present_value,

            })

        final_fcf = (
            fcf_forecast[-1][
                "Free Cash Flow"
            ]
        )

        terminal_value = (
            final_fcf
            * (
                1
                + terminal_growth
            )
            / (
                wacc
                - terminal_growth
            )
        )

        terminal_discount_factor = (
            1
            / (
                (
                    1 + wacc
                )
                ** self.forecast_years
            )
        )

        terminal_present_value = (
            terminal_value
            * terminal_discount_factor
        )

        forecast_pv = sum(
            item[
                "Present Value"
            ]
            for item in present_values
        )

        enterprise_value = (
            forecast_pv
            + terminal_present_value
        )

        equity_value = (
            enterprise_value
            - net_debt
        )

        intrinsic_value = None

        if (
            shares_outstanding is not None
            and shares_outstanding > 0
        ):

            intrinsic_value = (
                equity_value
                / shares_outstanding
            )

        terminal_percentage = None

        if enterprise_value > 0:

            terminal_percentage = (
                terminal_present_value
                / enterprise_value
            )

        return {

            "Status":
                "COMPLETE",

            "Forecast Present Values":
                present_values,

            "Terminal Value":
                terminal_value,

            "Terminal Present Value":
                terminal_present_value,

            "Forecast Cash Flow PV":
                forecast_pv,

            "Enterprise Value":
                enterprise_value,

            "Net Debt":
                net_debt,

            "Equity Value":
                equity_value,

            "Shares Outstanding":
                shares_outstanding,

            "Intrinsic Value Per Share":
                intrinsic_value,

            "Terminal Value % of Enterprise Value":
                terminal_percentage,

        }

    # ============================================================
    # SCENARIO
    # ============================================================

    def run_scenario(
        self,
        name,
        revenue,
        current_margin,
        target_margin,
        starting_growth,
        terminal_growth,
        wacc,
        shares_outstanding,
        net_debt,
    ):

        if name == "Bear":

            scenario_growth = (
                starting_growth
                - 0.08
            )

            scenario_target_margin = (
                target_margin
                - 0.04
            )

        elif name == "Bull":

            scenario_growth = (
                starting_growth
                + 0.08
            )

            scenario_target_margin = (
                target_margin
                + 0.04
            )

        else:

            scenario_growth = (
                starting_growth
            )

            scenario_target_margin = (
                target_margin
            )

        scenario_growth = max(
            -0.20,
            min(
                scenario_growth,
                0.80,
            ),
        )

        scenario_target_margin = max(
            0.01,
            min(
                scenario_target_margin,
                0.70,
            ),
        )

        growth_path = (
            self.build_growth_path(
                scenario_growth,
                terminal_growth,
            )
        )

        (
            revenue_forecast,
            fcf_forecast,
        ) = self.forecast(
            revenue,
            current_margin,
            scenario_target_margin,
            growth_path,
        )

        dcf = (
            self.calculate_dcf(
                fcf_forecast,
                wacc,
                terminal_growth,
                shares_outstanding,
                net_debt,
            )
        )

        dcf[
            "Scenario"
        ] = name

        dcf[
            "Starting Growth"
        ] = scenario_growth

        dcf[
            "Target FCF Margin"
        ] = scenario_target_margin

        dcf[
            "Growth Path"
        ] = growth_path

        dcf[
            "Revenue Forecast"
        ] = revenue_forecast

        dcf[
            "FCF Forecast"
        ] = fcf_forecast

        dcf[
            "WACC"
        ] = wacc

        dcf[
            "Terminal Growth"
        ] = terminal_growth

        return dcf

    # ============================================================
    # SENSITIVITY
    # ============================================================

    def sensitivity(
        self,
        revenue,
        current_margin,
        target_margin,
        starting_growth,
        shares_outstanding,
        net_debt,
    ):

        wacc_values = [

            0.07,
            0.08,
            0.09,
            0.10,
            0.11,
            0.12,

        ]

        growth_values = [

            max(
                -0.05,
                starting_growth - 0.10,
            ),

            max(
                -0.05,
                starting_growth - 0.05,
            ),

            starting_growth,

            min(
                0.50,
                starting_growth + 0.05,
            ),

            min(
                0.50,
                starting_growth + 0.10,
            ),

        ]

        matrix = []

        for growth in growth_values:

            row = {

                "Growth":
                    growth,

                "Values":
                    {},

            }

            for wacc in wacc_values:

                terminal_growth = min(
                    self.default_terminal_growth,
                    wacc - 0.01,
                )

                terminal_growth = max(
                    0.01,
                    terminal_growth,
                )

                growth_path = (
                    self.build_growth_path(
                        growth,
                        terminal_growth,
                    )
                )

                (
                    _,
                    fcf_forecast,
                ) = self.forecast(
                    revenue,
                    current_margin,
                    target_margin,
                    growth_path,
                )

                dcf = (
                    self.calculate_dcf(
                        fcf_forecast,
                        wacc,
                        terminal_growth,
                        shares_outstanding,
                        net_debt,
                    )
                )

                row[
                    "Values"
                ][
                    f"{wacc:.2%}"
                ] = dcf.get(
                    "Intrinsic Value Per Share"
                )

            matrix.append(
                row
            )

        return {

            "WACC":
                wacc_values,

            "Growth":
                growth_values,

            "Matrix":
                matrix,

        }

    # ============================================================
    # FULL ANALYSIS
    # ============================================================

    def analyse(
        self,
        symbol,
        info=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        print()
        print("=" * 80)
        print(
            f"VALUATION ENGINE — {symbol}"
        )
        print("=" * 80)

        if info is None:

            info = (
                self.get_info(
                    symbol
                )
            )

        if not info:

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "No company data available.",

            }

        income_statement = (
            self.get_income_statement(
                symbol
            )
        )

        cash_flow = (
            self.get_cash_flow(
                symbol
            )
        )

        historical_financials = (
            self.get_annual_financials(
                income_statement,
                cash_flow,
            )
        )

        if not historical_financials:

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "Annual financial data unavailable.",

            }

        latest_financials = (
            self.get_latest_financial_period(
                historical_financials
            )
        )

        revenue = (
            self.safe_float(
                latest_financials.get(
                    "Revenue"
                )
            )
        )

        current_fcf = (
            self.safe_float(
                latest_financials.get(
                    "Free Cash Flow"
                )
            )
        )

        if (
            revenue is None
            or revenue <= 0
        ):

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "Current revenue unavailable.",

            }

        if (
            current_fcf is None
            or current_fcf <= 0
        ):

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "A positive current FCF could not be established.",

            }

        historical_revenue_growth = (
            self.calculate_revenue_growth(
                historical_financials
            )
        )

        historical_fcf_growth = (
            self.calculate_fcf_growth(
                historical_financials
            )
        )

        revenue_growth = (
            self.determine_revenue_growth(
                info,
                historical_revenue_growth,
            )
        )

        current_margin = (
            self.determine_fcf_margin(
                revenue,
                current_fcf,
            )
        )

        target_margin = (
            self.determine_target_margin(
                historical_financials,
                current_margin,
            )
        )

        wacc = (
            self.determine_wacc(
                info
            )
        )

        terminal_growth = (
            self.determine_terminal_growth()
        )

        # --------------------------------------------------------
        # Shares
        #
        # Prefer sharesOutstanding, but fall back to market cap /
        # current price if necessary.
        # --------------------------------------------------------

        shares_outstanding = (
            self.safe_float(
                info.get(
                    "sharesOutstanding"
                )
            )
        )

        current_price = (
            self.safe_float(
                info.get(
                    "currentPrice"
                )
            )
        )

        if current_price is None:

            current_price = (
                self.safe_float(
                    info.get(
                        "regularMarketPrice"
                    )
                )
            )

        market_cap = (
            self.safe_float(
                info.get(
                    "marketCap"
                )
            )
        )

        if (
            (
                shares_outstanding is None
                or shares_outstanding <= 0
            )
            and
            market_cap is not None
            and current_price is not None
            and current_price > 0
        ):

            shares_outstanding = (
                market_cap
                / current_price
            )

        # --------------------------------------------------------
        # Capital structure
        # --------------------------------------------------------

        total_debt = (
            self.safe_float(
                info.get(
                    "totalDebt"
                ),
                0,
            )
        )

        cash = (
            self.safe_float(
                info.get(
                    "totalCash"
                ),
                0,
            )
        )

        net_debt = (
            total_debt
            - cash
        )

        # --------------------------------------------------------
        # Scenarios
        # --------------------------------------------------------

        bear = (
            self.run_scenario(
                "Bear",
                revenue,
                current_margin,
                target_margin,
                revenue_growth,
                terminal_growth,
                wacc,
                shares_outstanding,
                net_debt,
            )
        )

        base = (
            self.run_scenario(
                "Base",
                revenue,
                current_margin,
                target_margin,
                revenue_growth,
                terminal_growth,
                wacc,
                shares_outstanding,
                net_debt,
            )
        )

        bull = (
            self.run_scenario(
                "Bull",
                revenue,
                current_margin,
                target_margin,
                revenue_growth,
                terminal_growth,
                wacc,
                shares_outstanding,
                net_debt,
            )
        )

        bear_value = (
            bear.get(
                "Intrinsic Value Per Share"
            )
        )

        base_value = (
            base.get(
                "Intrinsic Value Per Share"
            )
        )

        bull_value = (
            bull.get(
                "Intrinsic Value Per Share"
            )
        )

        # --------------------------------------------------------
        # Expected return
        # --------------------------------------------------------

        base_return = None
        annualised_return = None

        if (
            current_price is not None
            and current_price > 0
            and base_value is not None
            and base_value > 0
        ):

            base_return = (
                base_value
                / current_price
                - 1
            )

            annualised_return = (
                (
                    base_value
                    / current_price
                )
                ** (
                    1
                    / self.forecast_years
                )
                - 1
            )

        # --------------------------------------------------------
        # Sensitivity
        # --------------------------------------------------------

        sensitivity = (
            self.sensitivity(
                revenue,
                current_margin,
                target_margin,
                revenue_growth,
                shares_outstanding,
                net_debt,
            )
        )

        terminal_value_share = (
            base.get(
                "Terminal Value % of Enterprise Value"
            )
        )

        valuation_gap = None

        if (
            current_price is not None
            and current_price > 0
            and base_value is not None
        ):

            valuation_gap = (
                base_value
                / current_price
                - 1
            )

        # ========================================================
        # RESULT
        # ========================================================

        result = {

            "Ticker":
                symbol,

            "Company":
                info.get(
                    "longName"
                ),

            "Status":
                "COMPLETE",

            "Analysed At":
                self.utc_now(),

            # ----------------------------------------------------
            # Market
            # ----------------------------------------------------

            "Current Price":
                current_price,

            "Market Capitalisation":
                market_cap,

            # ----------------------------------------------------
            # Financial period
            # ----------------------------------------------------

            "Financial Period Used":
                latest_financials.get(
                    "Period"
                ),

            # ----------------------------------------------------
            # Fundamentals
            # ----------------------------------------------------

            "Current Revenue":
                revenue,

            "Current Free Cash Flow":
                current_fcf,

            "Current FCF Margin":
                current_margin,

            "Target FCF Margin":
                target_margin,

            "Historical Financials":
                historical_financials,

            "Historical Revenue Growth":
                historical_revenue_growth,

            "Historical FCF Growth":
                historical_fcf_growth,

            # ----------------------------------------------------
            # Forecast
            # ----------------------------------------------------

            "Revenue Growth Assumption":
                revenue_growth,

            "Growth Assumption Sources": {

                "Yahoo Revenue Growth":
                    self.safe_float(
                        info.get(
                            "revenueGrowth"
                        )
                    ),

                "Historical Revenue CAGR":
                    historical_revenue_growth,

                "Method":
                    (
                        "70% Yahoo forward growth + "
                        "30% historical revenue CAGR"
                    ),

            },

            "WACC":
                wacc,

            "Terminal Growth":
                terminal_growth,

            "Forecast Years":
                self.forecast_years,

            # ----------------------------------------------------
            # Capital structure
            # ----------------------------------------------------

            "Shares Outstanding":
                shares_outstanding,

            "Total Debt":
                total_debt,

            "Cash":
                cash,

            "Net Debt":
                net_debt,

            # ----------------------------------------------------
            # Scenarios
            # ----------------------------------------------------

            "Bear Scenario":
                bear,

            "Base Scenario":
                base,

            "Bull Scenario":
                bull,

            "Bear Value":
                bear_value,

            "Base Value":
                base_value,

            "Bull Value":
                bull_value,

            # ----------------------------------------------------
            # Valuation
            # ----------------------------------------------------

            "Valuation Gap":
                (
                    valuation_gap * 100
                    if valuation_gap is not None
                    else None
                ),

            "Terminal Value %":
                (
                    terminal_value_share * 100
                    if terminal_value_share is not None
                    else None
                ),

            # ----------------------------------------------------
            # Expected return
            # ----------------------------------------------------

            "Expected Return":
                (
                    base_return * 100
                    if base_return is not None
                    else None
                ),

            "Expected Return Horizon Years":
                self.forecast_years,

            "Expected Return Horizon Days":
                self.forecast_years * 365,

            "Annualised Return":
                (
                    annualised_return * 100
                    if annualised_return is not None
                    else None
                ),

            # ----------------------------------------------------
            # Sensitivity
            # ----------------------------------------------------

            "Sensitivity":
                sensitivity,

        }

        # ========================================================
        # SAVE
        # ========================================================

        path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                result,
                file,
                indent=2,
                default=str,
            )

        result[
            "Output Path"
        ] = path

        # ========================================================
        # PRINT
        # ========================================================

        print()

        print(
            f"Financial period used: "
            f"{latest_financials.get('Period')}"
        )

        print(
            f"Current Revenue: "
            f"{revenue:,.2f}"
        )

        print(
            f"Current Free Cash Flow: "
            f"{current_fcf:,.2f}"
        )

        print()

        print(
            "HISTORICAL GROWTH"
        )

        print(
            f"Revenue CAGR: "
            f"{(
                historical_revenue_growth * 100
                if historical_revenue_growth is not None
                else 0
            ): .2f}%"
        )

        print(
            f"FCF CAGR: "
            f"{(
                historical_fcf_growth * 100
                if historical_fcf_growth is not None
                else 0
            ): .2f}%"
        )

        print()

        print(
            "VALUATION ENGINE"
        )

        print(
            f"Current Price: "
            f"{current_price}"
        )

        print()

        print(
            "INTRINSIC VALUE"
        )

        print(
            f"Bear: "
            f"{bear_value}"
        )

        print(
            f"Base: "
            f"{base_value}"
        )

        print(
            f"Bull: "
            f"{bull_value}"
        )

        print()

        print(
            "VALUATION ASSUMPTIONS"
        )

        print(
            f"Revenue growth: "
            f"{revenue_growth * 100:.2f}%"
        )

        print(
            f"Current FCF margin: "
            f"{current_margin * 100:.2f}%"
        )

        print(
            f"Target FCF margin: "
            f"{target_margin * 100:.2f}%"
        )

        print(
            f"WACC: "
            f"{wacc * 100:.2f}%"
        )

        print(
            f"Terminal growth: "
            f"{terminal_growth * 100:.2f}%"
        )

        print()

        print(
            "CAPITAL STRUCTURE"
        )

        print(
            f"Shares Outstanding: "
            f"{shares_outstanding}"
        )

        print(
            f"Total Debt: "
            f"{total_debt:,.2f}"
        )

        print(
            f"Cash: "
            f"{cash:,.2f}"
        )

        print(
            f"Net Debt: "
            f"{net_debt:,.2f}"
        )

        print()

        if base_return is not None:

            print(
                f"Base Expected Return: "
                f"{base_return * 100:.2f}%"
            )

            print(
                f"Horizon: "
                f"{self.forecast_years} years"
            )

            print(
                f"Annualised Return: "
                f"{annualised_return * 100:.2f}%"
            )

        print()

        if terminal_value_share is not None:

            print(
                f"Terminal Value Contribution: "
                f"{terminal_value_share * 100:.2f}%"
            )

        print()

        print(
            "SENSITIVITY"
        )

        print(
            f"{len(sensitivity['Matrix'])} growth cases x "
            f"{len(sensitivity['WACC'])} WACC cases"
        )

        print()

        print(
            f"Saved to: "
            f"{path}"
        )

        return result


if __name__ == "__main__":

    engine = ValuationEngine()

    engine.analyse(
        "NVDA"
    )