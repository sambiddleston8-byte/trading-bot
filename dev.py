import sys

from core.decision_engine import DecisionEngine


VERSION = "2.0"


def analyse(ticker):

    engine = DecisionEngine()

    result = engine.analyse(ticker)

    print("\n========================================")
    print(f"{ticker}")
    print("========================================\n")

    print(f"Overall Score : {result['Overall Score']}")
    print(f"Rating        : {result['Rating']}")

    print("\nBusiness Quality :", result["Business Quality"])
    print("Valuation       :", result["Valuation"])
    print("Technical       :", result["Technical"])
    print("Risk            :", result["Risk"])
    print("News            :", result["News"])

    print("\nSummary\n")

    print(result["Summary"])


def scan():

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

    print("\n=========== SCAN ===========\n")

    for ticker in tickers:

        try:

            result = engine.analyse(ticker)

            print(
                f"{ticker:<6}"
                f"{result['Overall Score']:>6}"
                f"   {result['Rating']}"
            )

        except Exception as e:

            print(f"{ticker:<6} FAILED")

            print(e)


def version():

    print(f"Investment Intelligence Platform v{VERSION}")


def help_menu():

    print("""

Available Commands

python3 dev.py version

python3 dev.py analyse NVDA

python3 dev.py scan

""")


if __name__ == "__main__":

    if len(sys.argv) < 2:

        help_menu()

    elif sys.argv[1] == "version":

        version()

    elif sys.argv[1] == "scan":

        scan()

    elif sys.argv[1] == "analyse":

        analyse(sys.argv[2])

    else:

        help_menu()