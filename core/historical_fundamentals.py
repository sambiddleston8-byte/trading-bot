import json
import os
from datetime import datetime


class HistoricalFundamentals:

    def __init__(
        self,
        cache_path="data/historical_fundamentals.json",
    ):

        self.cache_path = cache_path

        directory = os.path.dirname(
            cache_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        self.data = self.load()

    # --------------------------------
    # Load
    # --------------------------------

    def load(self):

        if not os.path.exists(
            self.cache_path
        ):

            return {}

        try:

            with open(
                self.cache_path,
                "r",
            ) as file:

                return json.load(
                    file
                )

        except Exception:

            return {}

    # --------------------------------
    # Save
    # --------------------------------

    def save(self):

        with open(
            self.cache_path,
            "w",
        ) as file:

            json.dump(
                self.data,
                file,
                indent=2,
            )

    # --------------------------------
    # Add Snapshot
    # --------------------------------

    def add_snapshot(
        self,
        symbol,
        date,
        fundamentals,
        source="SEC",
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        if symbol not in self.data:

            self.data[
                symbol
            ] = []

        snapshot = {

            "Date":
                date,

            "Source":
                source,

            "Fundamentals":
                fundamentals,

            "Recorded":
                datetime.now().isoformat(),

        }

        self.data[
            symbol
        ].append(
            snapshot
        )

        self.data[
            symbol
        ].sort(
            key=lambda item: (
                item["Date"]
            )
        )

        self.save()

    # --------------------------------
    # Get Historical Snapshot
    # --------------------------------

    def get(
        self,
        symbol,
        decision_date,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        snapshots = (
            self.data.get(
                symbol,
                [],
            )
        )

        if not snapshots:

            return {}

        valid = []

        for snapshot in snapshots:

            snapshot_date = (
                snapshot.get(
                    "Date"
                )
            )

            if not snapshot_date:
                continue

            if snapshot_date <= (
                decision_date
            ):

                valid.append(
                    snapshot
                )

        if not valid:

            return {}

        latest = valid[-1]

        return latest.get(
            "Fundamentals",
            {},
        )

    # --------------------------------
    # Check Availability
    # --------------------------------

    def available(
        self,
        symbol,
        decision_date,
    ):

        return bool(
            self.get(
                symbol,
                decision_date,
            )
        )


if __name__ == "__main__":

    engine = (
        HistoricalFundamentals()
    )

    print()
    print(
        "=" * 70
    )
    print(
        "HISTORICAL FUNDAMENTALS"
    )
    print(
        "=" * 70
    )

    print()

    print(
        "Historical fundamentals cache loaded."
    )

    print(
        f"Companies cached: "
        f"{len(engine.data)}"
    )