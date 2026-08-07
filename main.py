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

print("\n" + "=" * 120)
print("AI INVESTMENT INTELLIGENCE PLATFORM")
print("=" * 120)

print(
    "{:<8} {:>8} {:>8} {:>8} {:>8} {:>8} {:>10} {:>10} {}".format(
        "Ticker",
        "Quality",
        "Value",
        "Tech",
        "Risk",
        "News",
        "Overall",
        "Rating",
        "",
    )
)

print("-" * 120)

for stock in results:

    print(
        "{:<8} {:>8.1f} {:>8.1f} {:>8.1f} {:>8.1f} {:>8.1f} {:>10.1f} {:>10}".format(
            stock["Ticker"],
            stock["Business Quality"],
            stock["Valuation"],
            stock["Technical"],
            stock["Risk"],
            stock["News"],
            stock["Overall Score"],
            stock["Rating"],
        )
    )

    print("")

    print("  Strengths:")

    if stock["Strengths"]:
        for strength in stock["Strengths"]:
            print(f"    • {strength}")
    else:
        print("    • None")

    print("")

    print("  Weaknesses:")

    if stock["Weaknesses"]:
        for weakness in stock["Weaknesses"]:
            print(f"    • {weakness}")
    else:
        print("    • None")

    print("")

    print("Investment Report:")
    print(stock["Report"])

    print("-" * 120)