from bots.scanner.analyser import ScannerAnalyser

from core.universe import Universe

tickers = Universe(
    "data/universes/nasdaq100.txt"
).load()

scanner = ScannerAnalyser()

results = scanner.scan(tickers)

print("\n" + "=" * 90)
print("TOP OPPORTUNITIES")
print("=" * 90)

for stock in results:

    print(
        f"{stock['Ticker']:<8}"
        f"{stock['Overall Score']:>8}"
        f"   {stock['Rating']}"
    )