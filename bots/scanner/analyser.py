from core.decision_engine import DecisionEngine


class ScannerAnalyser:

    def __init__(self):

        self.engine = DecisionEngine()

    def scan(self, tickers):

        results = []

        print(f"\nScanning {len(tickers)} companies...\n")

        for ticker in tickers:

            try:

                analysis = self.engine.analyse(ticker)

                results.append(analysis)

                print(f"✓ {ticker}")

            except Exception as e:

                print(f"✗ {ticker}: {e}")

        results.sort(
            key=lambda stock: stock["Overall Score"],
            reverse=True,
        )

        return results