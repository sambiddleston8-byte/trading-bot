from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.research.investment_research_pipeline import InvestmentResearchPipeline
from core.research.research_contract import ResearchContract


class ResearchService:
    """Application-facing entry point for a complete company research run."""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    OUTPUT_DIRECTORY = PROJECT_ROOT / "data" / "research" / "pipeline"

    @staticmethod
    def normalise_ticker(value: str) -> str:
        ticker = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
            raise ValueError("Enter a valid ticker, for example NVDA or BRK.B.")
        return ticker

    @classmethod
    def output_path(cls, ticker: str) -> Path:
        return cls.OUTPUT_DIRECTORY / f"{cls.normalise_ticker(ticker)}.json"

    @classmethod
    def load(cls, ticker: str) -> dict[str, Any] | None:
        path = cls.output_path(ticker)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def run(
        cls,
        ticker: str,
        pipeline: type[InvestmentResearchPipeline] = InvestmentResearchPipeline,
    ) -> dict[str, Any]:
        ticker = cls.normalise_ticker(ticker)
        result = pipeline.analyse(ticker, save=False)
        if not isinstance(result, dict):
            raise TypeError("Research pipeline returned an invalid result.")

        result = dict(result)
        result["ticker"] = ticker
        result["canonical"] = ResearchContract.from_pipeline_result(result)

        cls.OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = cls.output_path(ticker)
        path.write_text(
            json.dumps(result, indent=2, default=str),
            encoding="utf-8",
        )

        return {
            "ticker": ticker,
            "path": path,
            "result": result,
            "canonical": result["canonical"],
        }
