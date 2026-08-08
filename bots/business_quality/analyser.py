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
                + profitability
                + balance_sheet
            ) / 3,
            1,
        )

        strengths = []

        weaknesses = []

        confidence = 70

        # ----------------------------
        # Growth
        # ----------------------------

        if growth >= 80:
            strengths.append("Excellent long-term revenue growth")
            confidence += 5
        elif growth < 50:
            weaknesses.append("Weak revenue growth")

        # ----------------------------
        # Profitability
        # ----------------------------

        if profitability >= 80:
            strengths.append("High profitability")
            confidence += 5
        elif profitability < 50:
            weaknesses.append("Low profitability")

        # ----------------------------
        # Balance Sheet
        # ----------------------------

        if balance_sheet >= 80:
            strengths.append("Strong balance sheet")
            confidence += 5
        elif balance_sheet < 50:
            weaknesses.append("Weak balance sheet")

        # ----------------------------
        # Overall Assessment
        # ----------------------------

        if quality >= 85:

            summary = (
                "The business demonstrates exceptional financial quality "
                "with strong growth, profitability and balance sheet metrics."
            )

        elif quality >= 70:

            summary = (
                "The company demonstrates strong business fundamentals "
                "with only minor weaknesses."
            )

        elif quality >= 55:

            summary = (
                "Business quality is average. Some areas perform well "
                "while others require improvement."
            )

        else:

            summary = (
                "The company's underlying business quality appears weak "
                "and requires caution."
            )

        confidence = min(confidence, 100)

        return {

            "Growth": growth,

            "Profitability": profitability,

            "Balance Sheet": balance_sheet,

            "Business Quality": quality,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Confidence": confidence,

            "Summary": summary,

        }