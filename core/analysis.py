from dataclasses import dataclass, field


@dataclass
class InvestmentAnalysis:

    ticker: str

    business_quality: float = 0.0
    valuation: float = 0.0
    technical: float = 0.0
    risk: float = 0.0
    news: float = 0.0
    catalyst: float = 0.0

    overall: float = 0.0

    rating: str = ""

    headlines: list = field(default_factory=list)

    catalysts: list = field(default_factory=list)

    summary: str = ""