from core.decision_engine import DecisionEngine

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

        print(
            f"{ticker:<6}"
            f"{result['Overall Score']:>6}"
            f"   {result['Rating']}"
        )

    except Exception as e:
        print(f"{ticker}: FAILED")
        print(e)