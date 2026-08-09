from __future__ import annotations

from typing import Any


class SentimentSignalEngine:
    """Translate validated news evidence into a bounded sentiment signal.

    This is deliberately evidence-led: several articles repeating the same
    underlying announcement do not create artificial conviction.
    """

    VERSION = "1.0"

    CONFIDENCE_VALUES = {
        "VERY_HIGH": 1.0,
        "HIGH": 0.85,
        "MEDIUM": 0.65,
        "LOW": 0.40,
        "REVIEW": 0.25,
    }

    @staticmethod
    def number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def analyse(cls, news: dict[str, Any] | None) -> dict[str, Any]:
        news = news if isinstance(news, dict) else {}
        summary = news.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}

        positive = max(0.0, cls.number(summary.get("positive")))
        negative = max(0.0, cls.number(summary.get("negative")))
        independent = max(0.0, cls.number(summary.get("independent_source_count")))
        quality = summary.get("quality") or {}
        if not isinstance(quality, dict):
            quality = {}

        confidence = str(quality.get("confidence") or "REVIEW").upper()
        quality_multiplier = cls.CONFIDENCE_VALUES.get(confidence, 0.25)
        directional_total = positive + negative

        if directional_total == 0:
            score = 50.0
        else:
            directional_balance = (positive - negative) / directional_total
            evidence_multiplier = min(1.0, independent / 2.0)
            score = 50.0 + 40.0 * directional_balance * evidence_multiplier * quality_multiplier

        score = round(max(0.0, min(100.0, score)), 2)

        if score >= 60:
            label = "POSITIVE"
        elif score <= 40:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"

        return {
            "status": "COMPLETE" if news.get("status") == "COMPLETE" else "LIMITED",
            "score": score,
            "label": label,
            "confidence": confidence,
            "evidence_count": int(cls.number(summary.get("evidence_count"))),
            "independent_source_count": int(independent),
            "positive_evidence": int(positive),
            "negative_evidence": int(negative),
            "method": "INDEPENDENT_EVIDENCE_WEIGHTED_NEWS_SENTIMENT",
        }
