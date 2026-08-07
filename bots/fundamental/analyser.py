from bots.fundamental.growth import GrowthAnalyser
from bots.fundamental.profitability import ProfitabilityAnalyser
from bots.fundamental.balance_sheet import BalanceSheetAnalyser
from bots.fundamental.quality import QualityAnalyser
from bots.fundamental.company import CompanyAnalyser


class FundamentalAnalyser:

    def __init__(self):

        self.company = CompanyAnalyser()
        self.growth = GrowthAnalyser()
        self.profitability = ProfitabilityAnalyser()
        self.balance = BalanceSheetAnalyser()
        self.quality = QualityAnalyser()

    def analyse(self, symbol):

        info = self.company.summary(symbol)

        revenue = self.growth.revenue_score(symbol)
        earnings = self.growth.net_income_score(symbol)

        gross_margin = self.profitability.gross_margin_score(symbol)
        operating_margin = self.profitability.operating_margin_score(symbol)
        roe = self.profitability.roe_score(symbol)

        debt = self.balance.debt_score(symbol)

        eps = self.quality.eps_score(symbol)

        total = round(

            (
                revenue +
                earnings +
                gross_margin +
                operating_margin +
                roe +
                debt +
                eps

            ) / 7,

            1

        )

        info["Revenue Growth"] = revenue
        info["Net Income Growth"] = earnings
        info["Gross Margin"] = gross_margin
        info["Operating Margin"] = operating_margin
        info["ROE"] = roe
        info["Debt Score"] = debt
        info["EPS Score"] = eps
        info["Fundamental Score"] = total

        return info