from core.watchlist import Watchlist
from core.decision_engine import DecisionEngine

engine = DecisionEngine()

watchlist = Watchlist("data/watchlists/growth.txt")

results = []

print("\nAnalysing watchlist...\n")

for ticker in watchlist.load():

    try:
        result = engine.analyse(ticker)
        results.append(result)
        print(f"✓ {ticker}")

    except Exception as e:
        print(f"✗ {ticker}: {e}")

results.sort(
    key=lambda x: x["Overall Score"],
    reverse=True,
)

print("\n" + "=" * 70)
print("AI INVESTMENT INTELLIGENCE PLATFORM")
print("=" * 70)

print(
    f"{'Ticker':<10}"
    f"{'Quality':<10}"
    f"{'Value':<10}"
    f"{'Tech':<10}"
    f"{'Overall':<10}"
    f"{'Rating'}"
)
print("-" * 70)

for stock in results:
    print(
        f"{stock['Ticker']:<10}"
        f"{stock['Business Quality']:<10}"
        f"{stock['Valuation']:<10}"
        f"{stock['Technical']:<10}"
        f"{stock['Overall Score']:<10}"
        f"{stock['Rating']}"
    )