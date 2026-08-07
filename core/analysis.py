from dataclasses import dataclass


@dataclass
class InvestmentAnalysis:

    ticker: str

    business_quality: float = 0.0

    valuation: float = 0.0

    technical: float = 0.0

    catalyst: float = 0.0

    sentiment: float = 0.0

    macro: float = 0.0

    risk: float = 0.0

    overall: float = 0.0

    confidence: float = 0.0

    rating: str = ""