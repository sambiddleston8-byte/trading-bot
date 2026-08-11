from bots.news.analyser import NewsAnalyser


def main():
    news = NewsAnalyser()
    result = news.analyse("AAPL")
    print(result)


if __name__ == "__main__":
    main()
