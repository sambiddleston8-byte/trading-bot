from core.financial_data import FinancialDataEngine


class ValuationAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def trailing_pe_score(self, symbol):

        pe = self.engine.get_trailing_pe(symbol)

        if pe is None:
            return 50

        if pe <= 15:
            return 100
        elif pe <= 20:
            return 80
        elif pe <= 30:
            return 60
        elif pe <= 40:
            return 40
        else:
            return 20

    def forward_pe_score(self, symbol):

        pe = self.engine.get_forward_pe(symbol)

        if pe is None:
            return 50

        if pe <= 15:
            return 100
        elif pe <= 20:
            return 80
        elif pe <= 30:
            return 60
        elif pe <= 40:
            return 40
        else:
            return 20

    def peg_score(self, symbol):

        peg = self.engine.get_peg_ratio(symbol)

        if peg is None:
            return 50

        if peg <= 1:
            return 100
        elif peg <= 1.5:
            return 80
        elif peg <= 2:
            return 60
        elif peg <= 3:
            return 40
        else:
            return 20

    def price_to_book_score(self, symbol):

        pb = self.engine.get_price_to_book(symbol)

        if pb is None:
            return 50

        if pb <= 2:
            return 100
        elif pb <= 4:
            return 80
        elif pb <= 6:
            return 60
        elif pb <= 10:
            return 40
        else:
            return 20

    def price_to_sales_score(self, symbol):

        ps = self.engine.get_price_to_sales(symbol)

        if ps is None:
            return 50

        if ps <= 2:
            return 100
        elif ps <= 4:
            return 80
        elif ps <= 8:
            return 60
        elif ps <= 12:
            return 40
        else:
            return 20

    def analyse(self, symbol):

        trailing = self.trailing_pe_score(symbol)
        forward = self.forward_pe_score(symbol)
        peg = self.peg_score(symbol)
        pb = self.price_to_book_score(symbol)
        ps = self.price_to_sales_score(symbol)

        valuation = round(
            (
                trailing +
                forward +
                peg +
                pb +
                ps
            ) / 5,
            1
        )

        return {

            "Trailing PE": trailing,

            "Forward PE": forward,

            "PEG": peg,

            "Price to Book": pb,

            "Price to Sales": ps,

            "Valuation Score": valuation

        }