from pprint import pprint

from core.decision_engine import DecisionEngine


def main():
    engine = DecisionEngine()
    result = engine.analyse("NVDA")
    print("\n========== RESULT ==========\n")
    pprint(result)


if __name__ == "__main__":
    main()
