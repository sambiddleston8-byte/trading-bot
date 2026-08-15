import json
import math
import os
from datetime import datetime, timezone

# yfinance remains required for the income-statement and cash-flow reads
# below, which this batch does not touch. The profile read no longer uses it.
import yfinance as yf

from core.financial_data import FinancialDataEngine
from core.data_sources.analyst_source import AnalystSource
from core.data_sources.earnings_source import EarningsSource
from core.data_sources.yahoo_info_access import YahooInfoClient
from core.validation.forecast_validator import ForecastValidator


class ValuationEngine:

    def __init__(
        self,
        forecast_years=5,
        default_wacc=0.09,
        default_terminal_growth=0.03,
        output_directory="data/research/valuation",
        info_client=None,
    ):

        self.info_client = (
            info_client
            if info_client is not None
            else YahooInfoClient()
        )

        self.forecast_years = forecast_years
        self.default_wacc = default_wacc
        self.default_terminal_growth = default_terminal_growth

        self.output_directory = str(output_directory)

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

    # ========================================================
    # TIME
    # ========================================================

    def utc_now(self):

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # SAFE FLOAT
    # ========================================================

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

    # ========================================================
    # COMPANY DATA
    # ========================================================

    def get_ticker(
        self,
        symbol,
    ):

        return yf.Ticker(
            symbol
        )

    def get_info(
        self,
        symbol,
    ):
        """Return allowlisted profile scalars, or ``{}`` when unusable.

        ``analyse`` already treats an empty mapping as "no company data
        available", so the unavailable-data contract is unchanged.  The failure
        log no longer echoes the supplied symbol or the raw exception, and a
        fresh plain dict keeps callers away from the boundary observation.
        """

        try:

            observation = (
                self.info_client.info(
                    symbol
                )
            )

            return dict(
                observation.fields
            )

        except Exception:

            print(
                "Yahoo info failed."
            )

            return {}

    # ========================================================
    # HISTORICAL YAHOO DATA
    # ========================================================

    def get_income_statement(
        self,
        symbol,
    ):

        try:

            statement = (
                self.get_ticker(
                    symbol
                ).income_stmt
            )

            if statement is None or statement.empty:
                return None

            return statement

        except Exception as error:

            print(
                f"{symbol} income statement failed: {error}"
            )

            return None

    def get_cash_flow(
        self,
        symbol,
    ):

        try:

            cash_flow = (
                self.get_ticker(
                    symbol
                ).cashflow
            )

            if cash_flow is None or cash_flow.empty:
                return None

            return cash_flow

        except Exception as error:

            print(
                f"{symbol} cash flow failed: {error}"
            )

            return None

    # ========================================================
    # FIND FINANCIAL LINE
    # ========================================================

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

    # ========================================================
    # ANNUAL YAHOO FINANCIAL DATA
    # ========================================================

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

        # Prefer a reported free-cash-flow total.  Reconstructing OCF + capex
        # remains a fallback for statements that do not expose that line.
        free_cash_flow_line = self.find_line(
            cash_flow,
            [
                "Free Cash Flow",
                "FreeCashFlow",
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

            if free_cash_flow_line is not None:

                fcf = self.safe_float(
                    free_cash_flow_line.get(
                        column
                    )
                )

            if (
                fcf is None
                and operating_cash is not None
                and capex is not None
            ):

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

    # ========================================================
    # GROWTH
    # ========================================================

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

        if first <= 0 or last <= 0 or years <= 0:
            return None

        return (
            (
                last / first
            )
            ** (
                1 / years
            )
            - 1
        )

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

        if first <= 0 or last <= 0 or years <= 0:
            return None

        return (
            (
                last / first
            )
            ** (
                1 / years
            )
            - 1
        )

    # ========================================================
    # ASSUMPTIONS
    # ========================================================

    def get_analyst_revenue_growth(
        self,
        symbol,
    ):

        try:

            data = (
                AnalystSource()
                .fetch(symbol)
            )

            estimates = (
                data.get(
                    "revenue_estimates",
                    {},
                )
            )

            current_year = estimates.get(
                "0y",
                {},
            )

            next_year = estimates.get(
                "+1y",
                {},
            )

            current_growth = self.safe_float(
                current_year.get(
                    "growth"
                )
            )

            next_growth = self.safe_float(
                next_year.get(
                    "growth"
                )
            )

            return {

                "revenue_estimates":
                    estimates,

                "current_year": current_growth,

                "next_year": next_growth,

                "current_year_revenue":
                    self.safe_float(
                        current_year.get(
                            "avg"
                        )
                    ),

                "next_year_revenue":
                    self.safe_float(
                        next_year.get(
                            "avg"
                        )
                    ),

                "current_year_analysts":
                    self.safe_float(
                        current_year.get(
                            "numberOfAnalysts"
                        )
                    ),

                "next_year_analysts":
                    self.safe_float(
                        next_year.get(
                            "numberOfAnalysts"
                        )
                    ),

            }

        except Exception as error:

            print(
                f"{symbol} analyst estimates failed: {error}"
            )

            return {}

    # ========================================================
    # REVENUE GROWTH ASSUMPTION
    # ========================================================

    def determine_revenue_growth(
        self,
        info,
        historical_revenue_growth,
        analyst_growth=None,
    ):

        yahoo_growth = self.safe_float(
            info.get(
                "revenueGrowth"
            )
        )

        analyst_growth = self.safe_float(
            analyst_growth
        )

        candidates = []

        if analyst_growth is not None:
            candidates.append(
                analyst_growth
            )

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

        # Analyst consensus receives the greatest weight,
        # because it represents explicit forward estimates.
        if (
            analyst_growth is not None
            and yahoo_growth is not None
            and historical_revenue_growth is not None
        ):

            growth = (
                analyst_growth * 0.60
                +
                yahoo_growth * 0.25
                +
                historical_revenue_growth * 0.15
            )

        elif analyst_growth is not None:

            if historical_revenue_growth is not None:

                growth = (
                    analyst_growth * 0.70
                    +
                    historical_revenue_growth * 0.30
                )

            else:

                growth = analyst_growth

        elif yahoo_growth is not None:

            if historical_revenue_growth is not None:

                growth = (
                    yahoo_growth * 0.70
                    +
                    historical_revenue_growth * 0.30
                )

            else:

                growth = yahoo_growth

        else:

            growth = historical_revenue_growth

        return max(
            -0.20,
            min(
                growth,
                0.60,
            ),
        )

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

    def determine_target_margin(
        self,
        historical_financials,
        current_margin,
    ):

        margins = []

        for item in historical_financials:

            revenue = self.safe_float(
                item.get("Revenue")
            )

            fcf = self.safe_float(
                item.get("Free Cash Flow")
            )

            if (
                revenue is not None
                and revenue > 0
                and fcf is not None
                and fcf > 0
            ):

                margins.append(
                    fcf / revenue
                )

        if not margins:
            return current_margin

        historical_average = (
            sum(margins)
            / len(margins)
        )

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

    def determine_wacc(
        self,
        info,
    ):

        beta = self.safe_float(
            info.get("beta")
        )

        debt_to_equity = self.safe_float(
            info.get("debtToEquity")
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

    # ========================================================
    # FORECAST
    # ========================================================

    def build_growth_path(
        self,
        starting_growth,
        terminal_growth,
        analyst_current_growth=None,
        analyst_next_growth=None,
    ):

        path = []

        # ----------------------------------------------------
        # If analyst consensus is available, explicitly use
        # the near-term forecast before fading toward mature
        # long-term growth.
        # ----------------------------------------------------

        if (
            analyst_current_growth is not None
            and analyst_next_growth is not None
        ):

            path.append(
                analyst_current_growth
            )

            if self.forecast_years >= 2:

                path.append(
                    analyst_next_growth
                )

            remaining_years = max(
                0,
                self.forecast_years - 2,
            )

            if remaining_years > 0:

                for index in range(
                    remaining_years
                ):

                    fade = (
                        index + 1
                    ) / (
                        remaining_years + 1
                    )

                    growth = (
                        analyst_next_growth
                        * (1 - fade)
                        +
                        terminal_growth
                        * fade
                    )

                    path.append(
                        growth
                    )

            return path

        # ----------------------------------------------------
        # Fallback when analyst estimates are unavailable.
        # ----------------------------------------------------

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
                * (1 - fade)
                +
                terminal_growth
                * fade
            )

            path.append(
                growth
            )

        return path

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
                * (1 - fade)
                +
                target_margin
                * fade
            )

            path.append(
                margin
            )

        return path

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
            growth = growth_path[index]
            margin = margin_path[index]

            current_revenue = (
                current_revenue
                * (1 + growth)
            )

            fcf = (
                current_revenue
                * margin
            )

            revenue_forecast.append({

                "Year": year,
                "Growth": growth,
                "Revenue": current_revenue,

            })

            fcf_forecast.append({

                "Year": year,
                "FCF Margin": margin,
                "Free Cash Flow": fcf,

            })

        return (
            revenue_forecast,
            fcf_forecast,
        )

    # ========================================================
    # DCF
    # ========================================================

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
                "Status": "FAILED",
                "Reason": "Invalid DCF assumptions.",
            }

        present_values = []

        for item in fcf_forecast:

            year = item["Year"]
            fcf = item["Free Cash Flow"]

            discount_factor = (
                1
                / (
                    (1 + wacc)
                    ** year
                )
            )

            present_value = (
                fcf
                * discount_factor
            )

            present_values.append({

                "Year": year,
                "Free Cash Flow": fcf,
                "Discount Factor": discount_factor,
                "Present Value": present_value,

            })

        final_fcf = fcf_forecast[-1][
            "Free Cash Flow"
        ]

        terminal_value = (
            final_fcf
            * (1 + terminal_growth)
            / (wacc - terminal_growth)
        )

        terminal_discount_factor = (
            1
            / (
                (1 + wacc)
                ** self.forecast_years
            )
        )

        terminal_present_value = (
            terminal_value
            * terminal_discount_factor
        )

        forecast_pv = sum(
            item["Present Value"]
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

            "Status": "COMPLETE",

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

    # ========================================================
    # SCENARIO
    # ========================================================

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
        analyst_current_growth=None,
        analyst_next_growth=None,
    ):

        if name == "Bear":

            scenario_growth = (
                starting_growth - 0.08
            )

            scenario_target_margin = (
                target_margin - 0.04
            )

        elif name == "Bull":

            scenario_growth = (
                starting_growth + 0.08
            )

            scenario_target_margin = (
                target_margin + 0.04
            )

        else:

            scenario_growth = starting_growth
            scenario_target_margin = target_margin

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

        scenario_current_growth = (
            analyst_current_growth
        )

        scenario_next_growth = (
            analyst_next_growth
        )

        if name == "Bear":

            if scenario_current_growth is not None:
                scenario_current_growth -= 0.08

            if scenario_next_growth is not None:
                scenario_next_growth -= 0.05

        elif name == "Bull":

            if scenario_current_growth is not None:
                scenario_current_growth += 0.08

            if scenario_next_growth is not None:
                scenario_next_growth += 0.05

        growth_path = (
            self.build_growth_path(
                scenario_growth,
                terminal_growth,
                scenario_current_growth,
                scenario_next_growth,
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

        dcf["Scenario"] = name
        dcf["Starting Growth"] = scenario_growth
        dcf["Target FCF Margin"] = scenario_target_margin
        dcf["Growth Path"] = growth_path
        dcf["Revenue Forecast"] = revenue_forecast
        dcf["FCF Forecast"] = fcf_forecast
        dcf["WACC"] = wacc
        dcf["Terminal Growth"] = terminal_growth

        return dcf

    # ========================================================
    # ANALYSIS
    # ========================================================

    def analyse(
        self,
        symbol,
        info=None,
    ):

        symbol = symbol.upper().strip()

        print()
        print("=" * 80)
        print(
            f"VALUATION ENGINE — {symbol}"
        )
        print("=" * 80)

        if info is None:
            info = self.get_info(symbol)

        if not info:

            return {
                "Ticker": symbol,
                "Status": "FAILED",
                "Reason": "No company data available.",
            }

        # ----------------------------------------------------
        # VALIDATED FINANCIAL DATA
        # ----------------------------------------------------

        context = (
            FinancialDataEngine()
            .build_context(
                symbol
            )
        )

        validation = (
            context.validated_financial_data
        )

        selected = {}

        validation_confidence = None

        if isinstance(
            validation,
            dict,
        ):

            selected = validation.get(
                "selected_financials",
                {},
            )

            validation_confidence = (
                validation
                .get("summary", {})
                .get("overall_confidence")
            )

        # ----------------------------------------------------
        # Yahoo historical data remains available for
        # historical growth calculations.
        # ----------------------------------------------------

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
                "Ticker": symbol,
                "Status": "FAILED",
                "Reason": "Annual financial data unavailable.",
            }

        latest_financials = (
            historical_financials[0]
        )

        # ----------------------------------------------------
        # CORE FINANCIAL INPUTS
        # SEC/Yahoo validation takes priority.
        # ----------------------------------------------------

        revenue = self.safe_float(
            selected.get(
                "revenue"
            )
        )

        current_fcf = self.safe_float(
            selected.get(
                "free_cash_flow"
            )
        )

        if revenue is None:

            revenue = self.safe_float(
                latest_financials.get(
                    "Revenue"
                )
            )

        if current_fcf is None:

            current_fcf = self.safe_float(
                latest_financials.get(
                    "Free Cash Flow"
                )
            )

        if revenue is None or revenue <= 0:

            return {
                "Ticker": symbol,
                "Status": "FAILED",
                "Reason": "Current revenue unavailable.",
            }

        if current_fcf is None or current_fcf <= 0:

            return {
                "Ticker": symbol,
                "Status": "FAILED",
                "Reason":
                    "A positive current FCF could not be established.",
            }

        # ----------------------------------------------------
        # HISTORICAL GROWTH
        # ----------------------------------------------------

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

        analyst_estimates = (
            self.get_analyst_revenue_growth(
                symbol
            )
        )

        earnings_estimates = (
            EarningsSource()
            .fetch(symbol)
            .get(
                "earnings_estimates",
                {},
            )
        )

        forecast_validation = (
            ForecastValidator(
                analyst_estimates.get(
                    "revenue_estimates",
                    {}
                ),
                earnings_estimates,
            ).build()
        )


        current_eps_estimate = (
            earnings_estimates
            .get("0y", {})
            .get("eps_avg")
        )

        next_eps_estimate = (
            earnings_estimates
            .get("+1y", {})
            .get("eps_avg")
        )

        current_eps_growth = (
            earnings_estimates
            .get("0y", {})
            .get("growth")
        )

        next_eps_growth = (
            earnings_estimates
            .get("+1y", {})
            .get("growth")
        )

        analyst_forward_growth = (
            self.safe_float(
                analyst_estimates.get(
                    "next_year"
                )
            )
        )

        revenue_growth = (
            self.determine_revenue_growth(
                info,
                historical_revenue_growth,
                analyst_forward_growth,
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

        # ----------------------------------------------------
        # MARKET DATA
        # ----------------------------------------------------

        current_price = self.safe_float(
            info.get(
                "currentPrice"
            )
        )

        if current_price is None:

            current_price = self.safe_float(
                info.get(
                    "regularMarketPrice"
                )
            )

        market_cap = self.safe_float(
            info.get(
                "marketCap"
            )
        )

        # ----------------------------------------------------
        # CAPITAL STRUCTURE
        # ----------------------------------------------------

        shares_outstanding = self.safe_float(
            selected.get(
                "shares_outstanding"
            )
        )

        if (
            shares_outstanding is None
            or shares_outstanding <= 0
        ):

            shares_outstanding = self.safe_float(
                info.get(
                    "sharesOutstanding"
                )
            )

        total_debt = self.safe_float(
            selected.get(
                "total_debt"
            )
        )

        cash = self.safe_float(
            selected.get(
                "cash_and_equivalents"
            )
        )

        net_debt = self.safe_float(
            selected.get(
                "net_debt"
            )
        )

        if total_debt is None:

            total_debt = self.safe_float(
                info.get(
                    "totalDebt"
                ),
                0,
            )

        if cash is None:

            cash = self.safe_float(
                info.get(
                    "totalCash"
                ),
                0,
            )

        if net_debt is None:

            net_debt = (
                total_debt
                - cash
            )

        # ----------------------------------------------------
        # SCENARIOS
        # ----------------------------------------------------

        bear = self.run_scenario(
            "Bear",
            revenue,
            current_margin,
            target_margin,
            revenue_growth,
            terminal_growth,
            wacc,
            shares_outstanding,
            net_debt,
            analyst_estimates.get(
                "current_year"
            ),
            analyst_estimates.get(
                "next_year"
            ),
        )

        base = self.run_scenario(
            "Base",
            revenue,
            current_margin,
            target_margin,
            revenue_growth,
            terminal_growth,
            wacc,
            shares_outstanding,
            net_debt,
            analyst_estimates.get(
                "current_year"
            ),
            analyst_estimates.get(
                "next_year"
            ),
        )

        bull = self.run_scenario(
            "Bull",
            revenue,
            current_margin,
            target_margin,
            revenue_growth,
            terminal_growth,
            wacc,
            shares_outstanding,
            net_debt,
            analyst_estimates.get(
                "current_year"
            ),
            analyst_estimates.get(
                "next_year"
            ),
        )

        bear_value = bear.get(
            "Intrinsic Value Per Share"
        )

        base_value = base.get(
            "Intrinsic Value Per Share"
        )

        bull_value = bull.get(
            "Intrinsic Value Per Share"
        )

        # ----------------------------------------------------
        # EXPECTED RETURN
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        result = {

            "Ticker": symbol,

            "Company": info.get(
                "longName"
            ),

            "Status": "COMPLETE",

            "Analysed At":
                self.utc_now(),

            "Current Price":
                current_price,

            "Market Capitalisation":
                market_cap,

            "Financial Data Validation": {

                "Overall Confidence":
                    validation_confidence,

                "Summary":
                    (
                        validation.get(
                            "summary"
                        )
                        if isinstance(
                            validation,
                            dict,
                        )
                        else None
                    ),

                "Selected Financials":
                    selected,

            },

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

            "Revenue Growth Assumption":
                revenue_growth,

            "Forecast Validation": {

                "Overall Confidence":
                    forecast_validation.get(
                        "overall_confidence"
                    ),

                "Periods Checked":
                    forecast_validation.get(
                        "periods_checked"
                    ),

                "Consistent Periods":
                    forecast_validation.get(
                        "consistent_periods"
                    ),

                "Large Differences":
                    forecast_validation.get(
                        "large_differences"
                    ),

                "Comparisons":
                    forecast_validation.get(
                        "comparisons"
                    ),

            },

            "Earnings Estimates": {

                "Current Year EPS":
                    current_eps_estimate,

                "Next Year EPS":
                    next_eps_estimate,

                "Current Year EPS Growth":
                    current_eps_growth,

                "Next Year EPS Growth":
                    next_eps_growth,

                "Current Year Analysts":
                    earnings_estimates
                    .get("0y", {})
                    .get("analysts"),

                "Next Year Analysts":
                    earnings_estimates
                    .get("+1y", {})
                    .get("analysts"),

            },

            "Growth Assumption Sources": {

                "Analyst Forward Revenue Growth":
                    analyst_forward_growth,

                "Analyst Current Year Revenue":
                    analyst_estimates.get(
                        "current_year_revenue"
                    ),

                "Analyst Next Year Revenue":
                    analyst_estimates.get(
                        "next_year_revenue"
                    ),

                "Analyst Current Year Count":
                    analyst_estimates.get(
                        "current_year_analysts"
                    ),

                "Analyst Next Year Count":
                    analyst_estimates.get(
                        "next_year_analysts"
                    ),

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
                        "60% analyst forward growth + "
                        "25% Yahoo growth + "
                        "15% historical CAGR"
                    ),

            },

            "WACC":
                wacc,

            "Terminal Growth":
                terminal_growth,

            "Forecast Years":
                self.forecast_years,

            "Shares Outstanding":
                shares_outstanding,

            "Total Debt":
                total_debt,

            "Cash":
                cash,

            "Net Debt":
                net_debt,

            "Intrinsic Value": {

                "Bear":
                    bear_value,

                "Base":
                    base_value,

                "Bull":
                    bull_value,

            },

            "Scenarios": {

                "Bear": bear,
                "Base": base,
                "Bull": bull,

            },

            "Expected Return": {

                "Base":
                    base_return,

                "Annualised":
                    annualised_return,

                "Horizon Years":
                    self.forecast_years,

            },

            "Terminal Value Contribution":
                base.get(
                    "Terminal Value % of Enterprise Value"
                ),

        }

        output_path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                result,
                file,
                indent=4,
                default=str,
            )

        print()
        print(
            f"Validation confidence: "
            f"{validation_confidence}"
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
        print("INTRINSIC VALUE")

        print(
            f"Bear: {bear_value}"
        )

        print(
            f"Base: {base_value}"
        )

        print(
            f"Bull: {bull_value}"
        )

        print()
        print("VALUATION ASSUMPTIONS")

        print(
            "REVENUE GROWTH PATH"
        )

        base_growth_path = base.get(
            "Growth Path",
            [],
        )

        for index, growth in enumerate(
            base_growth_path,
            start=1,
        ):

            print(
                f"Year {index}: "
                f"{growth:.2%}"
            )

        print(
            f"Analyst current-year growth: "
            f"{analyst_estimates.get('current_year'):.2%}"
            if analyst_estimates.get("current_year") is not None
            else "Analyst current-year growth: N/A"
        )

        print(
            f"Analyst next-year growth: "
            f"{analyst_estimates.get('next_year'):.2%}"
            if analyst_estimates.get("next_year") is not None
            else "Analyst next-year growth: N/A"
        )

        print(
            f"Current FCF margin: "
            f"{current_margin:.2%}"
        )

        print()
        print("FORECAST VALIDATION")

        print(
            f"Confidence: "
            f"{forecast_validation.get('overall_confidence')}"
        )

        print(
            f"Periods checked: "
            f"{forecast_validation.get('periods_checked')}"
        )

        print(
            f"Consistent: "
            f"{forecast_validation.get('consistent_periods')}"
        )

        print(
            f"Large differences: "
            f"{forecast_validation.get('large_differences')}"
        )

        print()
        print("EARNINGS CONSENSUS")

        print(
            f"Current-year EPS: "
            f"${current_eps_estimate:.4f}"
            if current_eps_estimate is not None
            else "Current-year EPS: N/A"
        )

        print(
            f"Next-year EPS: "
            f"${next_eps_estimate:.4f}"
            if next_eps_estimate is not None
            else "Next-year EPS: N/A"
        )

        print(
            f"Current-year EPS growth: "
            f"{current_eps_growth:.2%}"
            if current_eps_growth is not None
            else "Current-year EPS growth: N/A"
        )

        print(
            f"Next-year EPS growth: "
            f"{next_eps_growth:.2%}"
            if next_eps_growth is not None
            else "Next-year EPS growth: N/A"
        )

        print(
            f"Target FCF margin: "
            f"{target_margin:.2%}"
        )

        print(
            f"WACC: {wacc:.2%}"
        )

        print(
            f"Terminal growth: "
            f"{terminal_growth:.2%}"
        )

        print()
        print("CAPITAL STRUCTURE")

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
        print(
            f"Base Expected Return: "
            f"{base_return:.2%}"
            if base_return is not None
            else "Base Expected Return: N/A"
        )

        print(
            f"Horizon: "
            f"{self.forecast_years} years"
        )

        print(
            f"Annualised Return: "
            f"{annualised_return:.2%}"
            if annualised_return is not None
            else "Annualised Return: N/A"
        )

        print()
        print(
            f"Saved to: {output_path}"
        )

        return result

    # ========================================================
    # RUN
    # ========================================================

    def run(
        self,
        symbol="NVDA",
    ):

        return self.analyse(
            symbol
        )


if __name__ == "__main__":

    ValuationEngine().run(
        "NVDA"
    )
