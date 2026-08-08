from pprint import pprint

from core.decision_engine import DecisionEngine

engine = DecisionEngine()

result = engine.analyse("NVDA")

print("\n========== RESULT ==========\n")

pprint(result)