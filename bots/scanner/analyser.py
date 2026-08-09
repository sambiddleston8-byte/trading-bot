"""Research scanner backed by the portfolio's official evidence-first path."""

from core.application.research_service import ResearchService
from core.research.research_contract import ResearchContract


class ScannerAnalyser:
    def scan(self, tickers):
        results = []
        for ticker in tickers:
            try:
                result = ResearchService.run(str(ticker).upper())
                results.append(ResearchContract.from_pipeline_result(result))
            except Exception as exc:
                results.append(
                    {
                        "ticker": str(ticker).upper(),
                        "research_status": "ERROR",
                        "error": str(exc),
                    }
                )
        return sorted(
            results,
            key=lambda stock: stock.get("investment_case_score") or -1,
            reverse=True,
        )
