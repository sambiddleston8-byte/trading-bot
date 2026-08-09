from __future__ import annotations

"""Calibrate decisions only from completed, recorded paper-trade outcomes."""

import json
from pathlib import Path
from typing import Any


class OutcomeLearningAdapter:
    MINIMUM_OBSERVATIONS = 20
    DEFAULT_HISTORY_PATH = Path("data/outcome_history.json")

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
    def load_history(cls, path: Path | str | None = None) -> list[dict[str, Any]]:
        source = Path(path) if path is not None else cls.DEFAULT_HISTORY_PATH
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in cls.mapping(payload).get("Predictions", []) if isinstance(item, dict)]

    @classmethod
    def evaluate(cls, decision: str | None, history: list[dict[str, Any]]) -> dict[str, Any]:
        """Return a small calibration adjustment, never a substitute for research.

        Only explicitly CLOSED records with recorded actual returns are used.
        A new system therefore stays neutral until it has enough evidence.
        """
        recommendation = str(decision or "").upper().replace("_", " ")
        records = [
            record for record in history
            if str(record.get("Status", "")).upper() == "CLOSED"
            and str(record.get("Recommendation", "")).upper().replace("_", " ") == recommendation
            and cls.number(record.get("Actual Return")) is not None
        ]
        if len(records) < cls.MINIMUM_OBSERVATIONS:
            return {
                "status": "INSUFFICIENT_EVIDENCE",
                "adjustment": 0.0,
                "observations": len(records),
                "minimum_observations": cls.MINIMUM_OBSERVATIONS,
            }

        returns = [cls.number(record.get("Actual Return")) or 0.0 for record in records]
        correct = sum(record.get("Correct") is True for record in records)
        hit_rate = correct / len(records)
        average_return = sum(returns) / len(returns)
        adjustment = max(-5.0, min(5.0, ((hit_rate - 0.5) * 10.0) + (average_return / 10.0)))
        return {
            "status": "READY",
            "adjustment": round(adjustment, 2),
            "observations": len(records),
            "hit_rate": round(hit_rate, 3),
            "average_return": round(average_return, 3),
            "minimum_observations": cls.MINIMUM_OBSERVATIONS,
        }

    @classmethod
    def for_decision(cls, decision: str | None, path: Path | str | None = None) -> dict[str, Any]:
        return cls.evaluate(decision, cls.load_history(path))
