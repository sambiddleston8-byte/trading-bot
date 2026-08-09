from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.portfolio.universe_engine import UniverseEngine


class UniverseService:
    """Application-facing persistence and refresh operations for index universes."""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    OUTPUT_DIRECTORY = PROJECT_ROOT / "data" / "research" / "universe"

    @classmethod
    def path_for(cls, universe: str) -> Path:
        universe = str(universe).strip().lower()
        if universe not in {"sp500", "nasdaq100", "both"}:
            raise ValueError("Universe must be sp500, nasdaq100, or both.")
        return cls.OUTPUT_DIRECTORY / f"{universe}.json"

    @classmethod
    def load(cls, universe: str) -> dict[str, Any] | None:
        path = cls.path_for(universe)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def refresh(
        cls,
        universe: str,
        engine: type[UniverseEngine] = UniverseEngine,
    ) -> dict[str, Any]:
        universe = str(universe).strip().lower()
        data = engine.get_universe(universe)
        if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
            raise TypeError("Universe engine returned an invalid universe.")
        cls.OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = cls.path_for(universe)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {"data": data, "path": path}
