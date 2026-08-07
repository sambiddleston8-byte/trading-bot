import yfinance as yf


class NewsAnalyser:

    def analyse(self, symbol):

        company = yf.Ticker(symbol)

        try:
            news = company.news
        except Exception:
            news = []

        return {

            "Headline Count": len(news),

            "News": news

        }