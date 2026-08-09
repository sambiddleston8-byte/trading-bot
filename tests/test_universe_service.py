from __future__ import annotations

import tempfile
from pathlib import Path

from core.application.universe_service import UniverseService


class FakeUniverseEngine:
    @staticmethod
    def get_universe(universe):
        assert universe == "both"
        return {
            "universe": "both",
            "count": 2,
            "overlap_count": 1,
            "companies": [{"ticker": "NVDA"}, {"ticker": "AAPL"}],
        }


def test_refresh_and_load():
    previous_directory = UniverseService.OUTPUT_DIRECTORY
    with tempfile.TemporaryDirectory() as directory:
        UniverseService.OUTPUT_DIRECTORY = Path(directory)
        refreshed = UniverseService.refresh("both", engine=FakeUniverseEngine)
        assert refreshed["path"].exists()
        assert UniverseService.load("both")["count"] == 2
    UniverseService.OUTPUT_DIRECTORY = previous_directory


if __name__ == "__main__":
    test_refresh_and_load()
    print("UNIVERSE SERVICE TESTS PASSED")
