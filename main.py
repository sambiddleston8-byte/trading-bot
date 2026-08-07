from core.decision_engine import DecisionEngine

engine = DecisionEngine()

result = engine.analyse("AAPL")

print()

print("=" * 40)
print("INVESTMENT ANALYSIS")
print("=" * 40)

for key, value in result.items():
    print(f"{key:<22} {value}")

print("=" * 40)