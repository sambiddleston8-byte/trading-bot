from core.company_context import CompanyContext


class FinancialIntelligenceAnalyser:

    def analyse(self, context: CompanyContext):

        financials = context.financials
        balance_sheet = context.balance_sheet
        cashflow = context.cashflow
        info = context.info

        strengths = []
        weaknesses = []

        # --------------------------------
        # Revenue
        # --------------------------------

        revenue = None
        revenue_growth = None

        if (
            financials is not None
            and "Total Revenue" in financials.index
        ):

            revenue = (
                financials
                .loc["Total Revenue"]
                .dropna()
            )

            if len(revenue) >= 2:

                oldest = revenue.iloc[-1]
                newest = revenue.iloc[0]

                if oldest > 0:

                    years = len(revenue) - 1

                    revenue_growth = (
                        (newest / oldest)
                        ** (1 / years)
                        - 1
                    ) * 100

                    if revenue_growth >= 15:

                        strengths.append(
                            "Strong multi-year revenue growth."
                        )

                    elif revenue_growth < 5:

                        weaknesses.append(
                            "Revenue growth is relatively weak."
                        )

        # --------------------------------
        # Net Income
        # --------------------------------

        net_income = None
        earnings_growth = None

        if (
            financials is not None
            and "Net Income" in financials.index
        ):

            net_income = (
                financials
                .loc["Net Income"]
                .dropna()
            )

            if len(net_income) >= 2:

                oldest = net_income.iloc[-1]
                newest = net_income.iloc[0]

                if oldest > 0 and newest > 0:

                    years = len(net_income) - 1

                    earnings_growth = (
                        (newest / oldest)
                        ** (1 / years)
                        - 1
                    ) * 100

                    if earnings_growth >= 15:

                        strengths.append(
                            "Strong multi-year earnings growth."
                        )

                    elif earnings_growth < 5:

                        weaknesses.append(
                            "Earnings growth is relatively weak."
                        )

        # --------------------------------
        # Operating Cash Flow
        # --------------------------------

        operating_cash_flow = None

        if (
            cashflow is not None
            and "Operating Cash Flow" in cashflow.index
        ):

            operating_cash_flow = (
                cashflow
                .loc["Operating Cash Flow"]
                .dropna()
            )

            if len(operating_cash_flow) > 0:

                latest_ocf = operating_cash_flow.iloc[0]

                if latest_ocf > 0:

                    strengths.append(
                        "The company generates positive operating cash flow."
                    )

                else:

                    weaknesses.append(
                        "Operating cash flow is negative."
                    )

        # --------------------------------
        # Free Cash Flow
        # --------------------------------

        free_cash_flow = None

        if (
            cashflow is not None
            and "Free Cash Flow" in cashflow.index
        ):

            free_cash_flow = (
                cashflow
                .loc["Free Cash Flow"]
                .dropna()
            )

            if len(free_cash_flow) > 0:

                latest_fcf = free_cash_flow.iloc[0]

                if latest_fcf > 0:

                    strengths.append(
                        "The company generates positive free cash flow."
                    )

                else:

                    weaknesses.append(
                        "Free cash flow is negative."
                    )

        # --------------------------------
        # Margins
        # --------------------------------

        profit_margin = info.get(
            "profitMargins"
        )

        operating_margin = info.get(
            "operatingMargins"
        )

        if profit_margin is not None:

            if profit_margin >= 0.20:

                strengths.append(
                    "Strong net profit margin."
                )

            elif profit_margin < 0.05:

                weaknesses.append(
                    "Low net profit margin."
                )

        if operating_margin is not None:

            if operating_margin >= 0.20:

                strengths.append(
                    "Strong operating margin."
                )

            elif operating_margin < 0.10:

                weaknesses.append(
                    "Low operating margin."
                )

        # --------------------------------
        # ROIC
        # --------------------------------

        roic = info.get(
            "returnOnInvestedCapital"
        )

        if roic is not None:

            if roic >= 0.15:

                strengths.append(
                    "High return on invested capital."
                )

            elif roic < 0.08:

                weaknesses.append(
                    "Low return on invested capital."
                )

        # --------------------------------
        # Debt / Cash
        # --------------------------------

        total_debt = None
        cash = None

        if balance_sheet is not None:

            if "Total Debt" in balance_sheet.index:

                total_debt = (
                    balance_sheet
                    .loc["Total Debt"]
                    .iloc[0]
                )

            if "Cash And Cash Equivalents" in balance_sheet.index:

                cash = (
                    balance_sheet
                    .loc["Cash And Cash Equivalents"]
                    .iloc[0]
                )

        net_debt = None

        if (
            total_debt is not None
            and cash is not None
        ):

            net_debt = total_debt - cash

            if net_debt < 0:

                strengths.append(
                    "The company has a net cash position."
                )

            elif net_debt > 0:

                weaknesses.append(
                    "The company has net debt."
                )

        # --------------------------------
        # Financial Quality Score
        # --------------------------------

        score = 50

        if revenue_growth is not None:

            if revenue_growth >= 20:
                score += 15

            elif revenue_growth >= 10:
                score += 10

            elif revenue_growth < 5:
                score -= 10

        if earnings_growth is not None:

            if earnings_growth >= 20:
                score += 15

            elif earnings_growth >= 10:
                score += 10

            elif earnings_growth < 5:
                score -= 10

        if profit_margin is not None:

            if profit_margin >= 0.20:
                score += 10

            elif profit_margin < 0.05:
                score -= 10

        if roic is not None:

            if roic >= 0.15:
                score += 10

            elif roic < 0.08:
                score -= 10

        if net_debt is not None:

            if net_debt < 0:
                score += 10

        score = max(
            0,
            min(score, 100)
        )

        # --------------------------------
        # JSON-Safe Historical Data
        # --------------------------------

        revenue_history = {}

        if revenue is not None:

            revenue_history = {
                str(date): float(value)
                for date, value in revenue.items()
            }

        net_income_history = {}

        if net_income is not None:

            net_income_history = {
                str(date): float(value)
                for date, value in net_income.items()
            }

        operating_cash_flow_history = {}

        if operating_cash_flow is not None:

            operating_cash_flow_history = {
                str(date): float(value)
                for date, value in operating_cash_flow.items()
            }

        free_cash_flow_history = {}

        if free_cash_flow is not None:

            free_cash_flow_history = {
                str(date): float(value)
                for date, value in free_cash_flow.items()
            }

        # --------------------------------
        # Summary
        # --------------------------------

        if score >= 80:

            summary = (
                "Financial fundamentals are exceptionally strong."
            )

        elif score >= 65:

            summary = (
                "Financial fundamentals are generally strong."
            )

        elif score >= 50:

            summary = (
                "Financial fundamentals are mixed."
            )

        else:

            summary = (
                "Financial fundamentals show significant weaknesses."
            )

        return {

            "Financial Intelligence Score": score,

            "Revenue History": revenue_history,

            "Revenue CAGR": revenue_growth,

            "Net Income History": net_income_history,

            "Earnings CAGR": earnings_growth,

            "Operating Cash Flow": operating_cash_flow_history,

            "Free Cash Flow": free_cash_flow_history,

            "Profit Margin": profit_margin,

            "Operating Margin": operating_margin,

            "ROIC": roic,

            "Total Debt": total_debt,

            "Cash": cash,

            "Net Debt": net_debt,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

        }