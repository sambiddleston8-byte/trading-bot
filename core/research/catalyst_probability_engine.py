from __future__ import annotations

"""Estimate the reliability of a catalyst without treating a headline as fact.

The score answers a narrow question: how well supported is the claim that a
specific event will occur or matter?  It is not a forecast of share-price
movement.  The result is deliberately only one input to catalyst materiality
and is visible in the saved research record.
"""

from typing import Any


class CatalystProbabilityEngine:
    VERSION = "1.0-evidence-led-catalyst-probability"

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def assess(cls, catalyst: Any) -> dict[str, Any]:
        """Return an evidence-based event probability and a plain explanation.

        A scheduled event supported by a calendar is more certain than an
        unverified press headline.  Independent sources can improve the score,
        but duplicate reporting of the same event cannot.  The upper bound is
        95% because even a scheduled event can be delayed, cancelled or
        superseded; this is an event-probability safeguard, not a cap on the
        portfolio decision rating.
        """

        item = cls.mapping(catalyst)
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, list) else []
        independent = max(
            0.0,
            cls.number(item.get("independent_source_count")) or 0.0,
        )
        category = str(item.get("category") or "").lower()
        expected_date = item.get("expected_date")

        factual = 0
        primary = 0
        tier_two = 0
        source_groups: set[str] = set()
        for raw in evidence:
            source = cls.mapping(raw)
            if str(source.get("evidence_type") or "").upper() == "FACT":
                factual += 1
            tier = cls.number(source.get("source_tier"))
            if tier == 1:
                primary += 1
            elif tier == 2:
                tier_two += 1
            group = str(source.get("underlying_source") or source.get("source") or "").strip()
            if group:
                source_groups.add(group)

        # A scheduled earnings event is a known information date, but the
        # outcome is unknown.  It receives a higher *event* probability only.
        scheduled_event = bool(expected_date) and category == "earnings"
        if scheduled_event:
            probability = 0.75
            basis = "A dated earnings event is recorded. Its outcome remains unknown."
        elif primary and factual:
            probability = 0.65
            basis = "The event has factual primary-source support."
        elif factual and tier_two:
            probability = 0.50
            basis = "The event has factual secondary-source support."
        elif factual:
            probability = 0.40
            basis = "The event has factual support, but source quality is limited."
        elif evidence:
            probability = 0.30
            basis = "The event is based on interpretive or incomplete evidence."
        else:
            probability = 0.15
            basis = "No supporting evidence is attached to the event claim."

        independent_groups = max(len(source_groups), int(independent))
        if independent_groups >= 3:
            probability += 0.12
        elif independent_groups >= 2:
            probability += 0.07
        elif independent_groups == 1 and not scheduled_event:
            probability += 0.02

        probability = round(max(0.05, min(0.95, probability)), 3)
        if probability >= 0.75:
            confidence = "HIGH"
        elif probability >= 0.50:
            confidence = "MEDIUM"
        elif probability >= 0.30:
            confidence = "LOW"
        else:
            confidence = "REVIEW"

        return {
            "version": cls.VERSION,
            "probability": probability,
            "confidence": confidence,
            "basis": basis,
            "evidence_count": len(evidence),
            "factual_evidence_count": factual,
            "primary_source_count": primary,
            "secondary_source_count": tier_two,
            "independent_source_count": independent_groups,
            "method": "EVENT_EVIDENCE_RELIABILITY_NOT_SHARE_PRICE_FORECAST",
        }
