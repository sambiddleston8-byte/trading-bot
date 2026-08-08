from dataclasses import dataclass, field


@dataclass
class BotResponse:

    name: str

    score: float

    confidence: int = 75

    recommendation: str = "HOLD"

    summary: str = ""

    strengths: list = field(default_factory=list)

    weaknesses: list = field(default_factory=list)

    metrics: dict = field(default_factory=dict)

    evidence: list = field(default_factory=list)

    red_flags: list = field(default_factory=list)