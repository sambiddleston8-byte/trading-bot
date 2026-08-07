from core.decision_engine import DecisionEngine

engine = DecisionEngine()

stock = engine.analyse("AAPL")

print()

print("=" * 65)

print(f"{stock['Ticker']} INVESTMENT ANALYSIS")

print("=" * 65)

print(f"Fundamental Score : {stock['Fundamental Score']}")

print(f"Technical Score   : {stock['Technical Score']}")

print()

print(f"Overall Score     : {stock['Overall Score']}")

print()

print(f"Investment Rating : {stock['Rating']}")

print("=" * 65)