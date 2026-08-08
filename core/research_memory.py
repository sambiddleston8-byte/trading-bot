import json
from datetime import datetime
from pathlib import Path


class ResearchMemory:

    def __init__(self):

        self.base_path = Path(
            "data/research"
        )

        self.base_path.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save(self, result):

        ticker = result["Ticker"].upper()

        ticker_path = (
            self.base_path / ticker
        )

        ticker_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S-%f"
        )

        file_path = (
            ticker_path
            / f"{timestamp}.json"
        )

        with open(
            file_path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                result,
                file,
                indent=4,
                default=str,
            )

        return file_path

    def load_latest(self, ticker):

        ticker_path = (
            self.base_path
            / ticker.upper()
        )

        if not ticker_path.exists():

            return None

        files = sorted(
            ticker_path.glob("*.json"),
            reverse=True,
        )

        for file_path in files:

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8",
                ) as file:

                    return json.load(file)

            except (
                json.JSONDecodeError,
                OSError,
            ):

                # Ignore corrupted research files
                # and continue looking for the
                # previous valid analysis.

                continue

        return None

    def list_history(self, ticker):

        ticker_path = (
            self.base_path
            / ticker.upper()
        )

        if not ticker_path.exists():

            return []

        return sorted(
            ticker_path.glob("*.json"),
            reverse=True,
        )