from __future__ import annotations

import json
from typing import Any

from core.data_sources.analyst_source import AnalystSource
from core.data_sources.earnings_source import EarningsSource
from core.financial_data import FinancialDataEngine
from core.validation.forecast_validator import ForecastValidator
from core.provenance_fundamentals import FundamentalProvenance
from core.source_registry import SourceRegistry


class FundamentalAnalysisEngine:

    def __init__(self):

        self.financial_data = FinancialDataEngine()

    # ========================================================
    # SAFE NUMBER
    # ========================================================

    @staticmethod
    def safe_float(
        value: Any,
        default=None,
    ):

        try:

            if value is None:
                return default

            value = float(value)

            if value != value:
                return default

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ========================================================
    # RATIO
    # ========================================================

    @staticmethod
    def ratio(
        numerator,
        denominator,
    ):

        if (
            numerator is None
            or denominator is None
            or denominator == 0
        ):

            return None

        return numerator / denominator

    # ========================================================
    # LATEST STATEMENT VALUE
    # ========================================================

    def latest_value(
        self,
        statement,
        names,
    ):

        if statement is None:
            return None

        for name in names:

            if name in statement.index:

                row = statement.loc[name]

                if len(row) == 0:
                    continue

                return self.safe_float(
                    row.iloc[0]
                )

        return None

    # ========================================================
    # HISTORICAL CAGR
    # ========================================================

    def calculate_cagr(
        self,
        values,
    ):

        usable = []

        for value in values:

            value = self.safe_float(
                value
            )

            if (
                value is not None
                and value > 0
            ):

                usable.append(
                    value
                )

        if len(usable) < 2:
            return None

        first = usable[-1]
        last = usable[0]

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

    # ========================================================
    # HISTORICAL DATA
    # ========================================================

    def get_historical_data(
        self,
        income_statement,
        cash_flow,
    ):

        revenue_history = []
        fcf_history = []

        revenue_line = None

        if income_statement is not None:

            for name in [
                "Total Revenue",
                "Operating Revenue",
            ]:

                if name in income_statement.index:

                    revenue_line = (
                        income_statement.loc[name]
                    )

                    break

        if revenue_line is not None:

            revenue_history = [
                self.safe_float(
                    value
                )
                for value in revenue_line.tolist()
            ]

        operating_cash_line = None
        capex_line = None

        if cash_flow is not None:

            for name in [
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
            ]:

                if name in cash_flow.index:

                    operating_cash_line = (
                        cash_flow.loc[name]
                    )

                    break

            for name in [
                "Capital Expenditure",
                "Capital Expenditure Reported",
            ]:

                if name in cash_flow.index:

                    capex_line = (
                        cash_flow.loc[name]
                    )

                    break

        if (
            operating_cash_line is not None
            and capex_line is not None
        ):

            for (
                operating_cash,
                capex,
            ) in zip(
                operating_cash_line.tolist(),
                capex_line.tolist(),
            ):

                operating_cash = self.safe_float(
                    operating_cash
                )

                capex = self.safe_float(
                    capex
                )

                if (
                    operating_cash is not None
                    and capex is not None
                ):

                    fcf_history.append(
                        operating_cash + capex
                    )

        return {
            "revenue": revenue_history,
            "free_cash_flow": fcf_history,
        }

    # ========================================================
    # SCORE
    # ========================================================

    def calculate_score(
        self,
        fcf_margin,
        roic,
        net_debt_to_fcf,
        forward_revenue_growth,
        forward_eps_growth,
    ):

        score = 50.0
        drivers = []

        # FCF margin
        if fcf_margin is not None:

            if fcf_margin >= 0.25:

                score += 12
                drivers.append(
                    "strong FCF margin"
                )

            elif fcf_margin >= 0.15:

                score += 6
                drivers.append(
                    "healthy FCF margin"
                )

            elif fcf_margin < 0.05:

                score -= 8
                drivers.append(
                    "weak FCF margin"
                )

        # ROIC
        if roic is not None:

            if roic >= 0.20:

                score += 12
                drivers.append(
                    "high ROIC"
                )

            elif roic >= 0.10:

                score += 6
                drivers.append(
                    "positive ROIC"
                )

            elif roic < 0:

                score -= 10
                drivers.append(
                    "negative ROIC"
                )

        # Debt
        if net_debt_to_fcf is not None:

            if net_debt_to_fcf <= 0:

                score += 8
                drivers.append(
                    "net cash position"
                )

            elif net_debt_to_fcf > 4:

                score -= 10
                drivers.append(
                    "high net debt relative to FCF"
                )

        # Forward revenue growth
        if forward_revenue_growth is not None:

            if forward_revenue_growth >= 0.20:

                score += 8
                drivers.append(
                    "strong forward revenue growth"
                )

            elif forward_revenue_growth < 0:

                score -= 8
                drivers.append(
                    "negative forward revenue growth"
                )

        # Forward EPS growth
        if forward_eps_growth is not None:

            if forward_eps_growth >= 0.20:

                score += 8
                drivers.append(
                    "strong forward EPS growth"
                )

            elif forward_eps_growth < 0:

                score -= 8
                drivers.append(
                    "negative forward EPS growth"
                )

        score = max(
            0.0,
            min(
                score,
                100.0,
            ),
        )

        if score >= 75:

            quality = "STRONG"

        elif score >= 60:

            quality = "GOOD"

        elif score >= 40:

            quality = "MIXED"

        else:

            quality = "WEAK"

        return score, quality, drivers

    # ========================================================
    # FULL ANALYSIS
    # ========================================================

    def analyse(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        print()
        print("=" * 80)
        print(
            f"FUNDAMENTAL ANALYSIS — {symbol}"
        )
        print("=" * 80)

        context = (
            self.financial_data
            .build_context(
                symbol
            )
        )

        validation = getattr(
            context,
            "validated_financial_data",
            None,
        )

        selected = {}

        validation_summary = {}

        if isinstance(
            validation,
            dict,
        ):

            selected = (
                validation.get(
                    "selected_financials",
                    {},
                )
            )

            validation_summary = (
                validation.get(
                    "summary",
                    {},
                )
            )

        income_statement = (
            context.financials
        )

        balance_sheet = (
            context.balance_sheet
        )

        cash_flow = (
            context.cashflow
        )

        # ----------------------------------------------------
        # VALIDATED FINANCIALS
        # ----------------------------------------------------

        revenue = self.safe_float(
            selected.get(
                "revenue"
            )
        )

        operating_cash_flow = (
            self.safe_float(
                selected.get(
                    "operating_cash_flow"
                )
            )
        )

        free_cash_flow = (
            self.safe_float(
                selected.get(
                    "free_cash_flow"
                )
            )
        )

        cash = self.safe_float(
            selected.get(
                "cash_and_equivalents"
            )
        )

        debt = self.safe_float(
            selected.get(
                "total_debt"
            )
        )

        net_debt = self.safe_float(
            selected.get(
                "net_debt"
            )
        )

        shares = self.safe_float(
            selected.get(
                "shares_outstanding"
            )
        )

        # ----------------------------------------------------
        # OTHER FUNDAMENTALS
        # ----------------------------------------------------

        net_income = (
            self.latest_value(
                income_statement,
                [
                    "Net Income",
                    "Net Income Common Stockholders",
                ],
            )
        )

        operating_income = (
            self.latest_value(
                income_statement,
                [
                    "Operating Income",
                    "Operating Income Loss",
                ],
            )
        )

        gross_profit = (
            self.latest_value(
                income_statement,
                [
                    "Gross Profit",
                ],
            )
        )

        equity = (
            self.latest_value(
                balance_sheet,
                [
                    "Stockholders Equity",
                    "Total Stockholder Equity",
                ],
            )
        )

        # ----------------------------------------------------
        # FALLBACK REVENUE
        # ----------------------------------------------------

        if revenue is None:

            revenue = (
                self.latest_value(
                    income_statement,
                    [
                        "Total Revenue",
                        "Operating Revenue",
                    ],
                )
            )

        # ----------------------------------------------------
        # PROFITABILITY
        # ----------------------------------------------------

        gross_margin = (
            self.ratio(
                gross_profit,
                revenue,
            )
        )

        operating_margin = (
            self.ratio(
                operating_income,
                revenue,
            )
        )

        net_margin = (
            self.ratio(
                net_income,
                revenue,
            )
        )

        fcf_margin = (
            self.ratio(
                free_cash_flow,
                revenue,
            )
        )

        roe = (
            self.ratio(
                net_income,
                equity,
            )
        )

        # ----------------------------------------------------
        # ROIC
        # ----------------------------------------------------

        invested_capital = None

        if (
            equity is not None
            and debt is not None
            and cash is not None
        ):

            invested_capital = (
                equity
                + debt
                - cash
            )

        roic = (
            self.ratio(
                operating_income,
                invested_capital,
            )
        )

        # ----------------------------------------------------
        # DEBT
        # ----------------------------------------------------

        debt_to_fcf = None

        if (
            debt is not None
            and free_cash_flow not in (
                None,
                0,
            )
        ):

            debt_to_fcf = (
                debt
                / free_cash_flow
            )

        net_debt_to_fcf = None

        if (
            net_debt is not None
            and free_cash_flow not in (
                None,
                0,
            )
        ):

            net_debt_to_fcf = (
                net_debt
                / free_cash_flow
            )

        # ----------------------------------------------------
        # HISTORICAL GROWTH
        # ----------------------------------------------------

        historical = (
            self.get_historical_data(
                income_statement,
                cash_flow,
            )
        )

        historical_revenue_cagr = (
            self.calculate_cagr(
                historical[
                    "revenue"
                ]
            )
        )

        historical_fcf_cagr = (
            self.calculate_cagr(
                historical[
                    "free_cash_flow"
                ]
            )
        )

        # ----------------------------------------------------
        # ANALYST FORECASTS
        # ----------------------------------------------------

        analyst_data = (
            AnalystSource()
            .fetch(symbol)
        )

        revenue_estimates = (
            analyst_data.get(
                "revenue_estimates",
                {},
            )
        )

        earnings_data = (
            EarningsSource()
            .fetch(symbol)
        )

        earnings_estimates = (
            earnings_data.get(
                "earnings_estimates",
                {},
            )
        )

        forecast_validation = (
            ForecastValidator(
                revenue_estimates,
                earnings_estimates,
            )
            .build()
        )

        forward_revenue_growth = (
            self.safe_float(
                revenue_estimates
                .get(
                    "+1y",
                    {},
                )
                .get(
                    "growth"
                )
            )
        )

        forward_eps_growth = (
            self.safe_float(
                earnings_estimates
                .get(
                    "+1y",
                    {},
                )
                .get(
                    "growth"
                )
            )
        )

        current_eps = (
            self.safe_float(
                earnings_estimates
                .get(
                    "0y",
                    {},
                )
                .get(
                    "eps_avg"
                )
            )
        )

        next_eps = (
            self.safe_float(
                earnings_estimates
                .get(
                    "+1y",
                    {},
                )
                .get(
                    "eps_avg"
                )
            )
        )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        (
            fundamental_score,
            fundamental_quality,
            key_drivers,
        ) = self.calculate_score(
            fcf_margin,
            roic,
            net_debt_to_fcf,
            forward_revenue_growth,
            forward_eps_growth,
        )

        # ----------------------------------------------------
        # PROVENANCE
        # ----------------------------------------------------

        provenance = (
            FundamentalProvenance()
            .build(
                financials={
                    **{
                        "revenue":
                            revenue,

                        "net_income":
                            net_income,

                        "operating_income":
                            operating_income,

                        "gross_profit":
                            gross_profit,

                        "operating_cash_flow":
                            operating_cash_flow,

                        "free_cash_flow":
                            free_cash_flow,

                        "cash":
                            cash,

                        "debt":
                            debt,

                        "net_debt":
                            net_debt,

                        "shares_outstanding":
                            shares,

                        "equity":
                            equity,
                    }
                },

                profitability={
                    "gross_margin":
                        gross_margin,

                    "operating_margin":
                        operating_margin,

                    "net_margin":
                        net_margin,

                    "fcf_margin":
                        fcf_margin,

                    "roe":
                        roe,

                    "roic":
                        roic,
                },

                growth={
                    "historical_revenue_cagr":
                        historical_revenue_cagr,

                    "historical_fcf_cagr":
                        historical_fcf_cagr,

                    "historical_revenue":
                        historical.get(
                            "revenue",
                            [],
                        ),

                    "historical_fcf":
                        historical.get(
                            "free_cash_flow",
                            [],
                        ),
                },

                balance_sheet={
                    "debt_to_fcf":
                        debt_to_fcf,

                    "net_debt_to_fcf":
                        net_debt_to_fcf,
                },

                analyst_consensus={
                    "current_year_eps":
                        current_eps,

                    "next_year_eps":
                        next_eps,

                    "forward_revenue_growth":
                        forward_revenue_growth,

                    "forward_eps_growth":
                        forward_eps_growth,
                },

                validation={
                    "overall_confidence":
                        validation_summary.get(
                            "overall_confidence"
                        ),
                },
            )
        )

        # ----------------------------------------------------
        # ENRICH PROVENANCE
        # ----------------------------------------------------

        provenance = {
            name:
                SourceRegistry.enrich(
                    metric
                )
            for name, metric
            in provenance.items()
        }

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        result = {

            "ticker":
                symbol,

            "validation": {

                "overall_confidence":
                    validation_summary.get(
                        "overall_confidence"
                    ),

                "summary":
                    validation_summary,

            },

            "financials": {

                "revenue":
                    revenue,

                "net_income":
                    net_income,

                "operating_income":
                    operating_income,

                "gross_profit":
                    gross_profit,

                "equity":
                    equity,

                "operating_cash_flow":
                    operating_cash_flow,

                "free_cash_flow":
                    free_cash_flow,

                "cash":
                    cash,

                "debt":
                    debt,

                "net_debt":
                    net_debt,

                "shares_outstanding":
                    shares,

            },

            "profitability": {

                "gross_margin":
                    gross_margin,

                "operating_margin":
                    operating_margin,

                "net_margin":
                    net_margin,

                "fcf_margin":
                    fcf_margin,

                "roe":
                    roe,

                "roic":
                    roic,

            },

            "growth": {

                "historical_revenue_cagr":
                    historical_revenue_cagr,

                "historical_fcf_cagr":
                    historical_fcf_cagr,

                "historical_revenue":
                    historical.get(
                        "revenue",
                        [],
                    ),

                "historical_fcf":
                    historical.get(
                        "free_cash_flow",
                        [],
                    ),

                "forward_revenue_growth":
                    forward_revenue_growth,

                "forward_eps_growth":
                    forward_eps_growth,

            },

            "balance_sheet": {

                "debt_to_fcf":
                    debt_to_fcf,

                "net_debt_to_fcf":
                    net_debt_to_fcf,

            },

            "analyst_consensus": {

                "current_year_eps":
                    current_eps,

                "next_year_eps":
                    next_eps,

                "forward_revenue_growth":
                    forward_revenue_growth,

                "forward_eps_growth":
                    forward_eps_growth,

            },

            "forecast_validation":
                forecast_validation,

            "provenance":
                provenance,

            "fundamental_score":
                fundamental_score,

            "fundamental_quality":
                fundamental_quality,

            "key_drivers":
                key_drivers,

        }

        # ----------------------------------------------------
        # CONSOLE REPORT
        # ----------------------------------------------------

        print()
        print("VALIDATION")
        print(
            "Confidence:",
            validation_summary.get(
                "overall_confidence"
            ),
        )

        print()
        print("PROFITABILITY")

        print(
            f"Gross margin: "
            f"{gross_margin:.2%}"
            if gross_margin is not None
            else "Gross margin: N/A"
        )

        print(
            f"Operating margin: "
            f"{operating_margin:.2%}"
            if operating_margin is not None
            else "Operating margin: N/A"
        )

        print(
            f"Net margin: "
            f"{net_margin:.2%}"
            if net_margin is not None
            else "Net margin: N/A"
        )

        print(
            f"FCF margin: "
            f"{fcf_margin:.2%}"
            if fcf_margin is not None
            else "FCF margin: N/A"
        )

        print(
            f"ROE: "
            f"{roe:.2%}"
            if roe is not None
            else "ROE: N/A"
        )

        print(
            f"ROIC: "
            f"{roic:.2%}"
            if roic is not None
            else "ROIC: N/A"
        )

        print()
        print("GROWTH")

        print(
            f"Historical revenue CAGR: "
            f"{historical_revenue_cagr:.2%}"
            if historical_revenue_cagr is not None
            else "Historical revenue CAGR: N/A"
        )

        print(
            f"Historical FCF CAGR: "
            f"{historical_fcf_cagr:.2%}"
            if historical_fcf_cagr is not None
            else "Historical FCF CAGR: N/A"
        )

        print(
            f"Forward revenue growth: "
            f"{forward_revenue_growth:.2%}"
            if forward_revenue_growth is not None
            else "Forward revenue growth: N/A"
        )

        print(
            f"Forward EPS growth: "
            f"{forward_eps_growth:.2%}"
            if forward_eps_growth is not None
            else "Forward EPS growth: N/A"
        )

        print()
        print("BALANCE SHEET")

        print(
            f"Debt: "
            f"${debt:,.0f}"
            if debt is not None
            else "Debt: N/A"
        )

        print(
            f"Cash: "
            f"${cash:,.0f}"
            if cash is not None
            else "Cash: N/A"
        )

        print(
            f"Net debt: "
            f"${net_debt:,.0f}"
            if net_debt is not None
            else "Net debt: N/A"
        )

        print(
            f"Debt / FCF: "
            f"{debt_to_fcf:.2f}x"
            if debt_to_fcf is not None
            else "Debt / FCF: N/A"
        )

        print(
            f"Net debt / FCF: "
            f"{net_debt_to_fcf:.2f}x"
            if net_debt_to_fcf is not None
            else "Net debt / FCF: N/A"
        )

        print()
        print("FORECAST VALIDATION")

        print(
            "Confidence:",
            forecast_validation.get(
                "overall_confidence"
            ),
        )

        print(
            "Consistent periods:",
            forecast_validation.get(
                "consistent_periods"
            ),
            "/",
            forecast_validation.get(
                "periods_checked"
            ),
        )

        print()
        print("FUNDAMENTAL SCORE")

        print(
            f"Score: "
            f"{fundamental_score:.1f}/100"
        )

        print(
            f"Quality: "
            f"{fundamental_quality}"
        )

        print(
            "Drivers:",
            ", ".join(
                key_drivers
            )
            if key_drivers
            else "None",
        )

        return result


if __name__ == "__main__":

    result = (
        FundamentalAnalysisEngine()
        .analyse(
            "NVDA"
        )
    )

    print()
    print("FULL RESULT")
    print("=" * 80)

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )
