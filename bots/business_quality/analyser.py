from bots.fundamental.growth import GrowthAnalyser
from bots.fundamental.profitability import ProfitabilityAnalyser
from bots.fundamental.balance_sheet import BalanceSheetAnalyser


class BusinessQualityAnalyser:

    def __init__(self):

        self.growth = GrowthAnalyser()
        self.profitability = ProfitabilityAnalyser()
        self.balance_sheet = BalanceSheetAnalyser()

    def analyse(self, symbol):

        growth = self.growth.score(symbol)

        profitability = self.profitability.score(symbol)

        balance_sheet = self.balance_sheet.score(symbol)

        quality = round(

            (

                growth

                +

                profitability

                +

                balance_sheet

            )

            / 3,

            1,

        )

        return {

            "Growth": growth,

            "Profitability": profitability,

            "Balance Sheet": balance_sheet,

            "Business Quality": quality,

        }