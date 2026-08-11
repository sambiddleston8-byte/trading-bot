from core.decision_engine import DecisionEngine


def main():
    engine = DecisionEngine()
    tickers = [
        "AAPL",
        "MSFT",
        "NVDA",
        "META",
        "AMZN",
        "GOOG",
        "RKLB",
        "JOBY",
    ]

    print("\n===== SCAN TEST =====\n")
    for ticker in tickers:
        try:
            result = engine.analyse(ticker)
            print(f"{ticker:<6}{result['Overall Score']:>6}   {result['Rating']}")
        except Exception as error:
            print(f"{ticker}: FAILED")
            print(error)


if __name__ == "__main__":
    main()
