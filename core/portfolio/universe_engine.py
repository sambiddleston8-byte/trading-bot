from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import csv
import io
import json

from core.data_sources.public_read_access import (
    GITHUB_UNIVERSE_ENDPOINT,
    PublicTextClient,
)


class UniverseEngine:

    VERSION = "8.0-github-raw"

    # VERIFIED LIVE GITHUB RAW FILES
    SP500_URL = (
        "https://raw.githubusercontent.com/"
        "datasets/s-and-p-500-companies/"
        "main/data/constituents.csv"
    )

    NASDAQ100_URL = (
        "https://raw.githubusercontent.com/"
        "Gary-Strauss/NASDAQ100_Constituents/"
        "master/data/nasdaq100_constituents.csv"
    )

    MIN_SP500 = 450
    MIN_NASDAQ100 = 90

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def normalise_ticker(
        value
    ):

        if value is None:
            return None

        value = (
            str(value)
            .strip()
            .upper()
            .replace(
                ".",
                "-"
            )
        )

        return value or None

    @classmethod
    def download_csv(
        cls,
        url,
        label,
        public_client=None,
    ):

        print(
            f"Downloading {label}..."
        )

        client = public_client or PublicTextClient(
            GITHUB_UNIVERSE_ENDPOINT
        )
        text = client.get_text(
            url,
            accept="text/csv,text/plain",
        )

        if not text.strip():

            raise RuntimeError(
                f"{label} returned an empty file."
            )

        return text

    @classmethod
    def load_sp500(
        cls,
        public_client=None,
    ):

        retrieved_at = cls.now()

        text = cls.download_csv(
            cls.SP500_URL,
            "S&P 500",
            public_client,
        )

        reader = csv.DictReader(
            io.StringIO(text)
        )

        if not reader.fieldnames:

            raise RuntimeError(
                "S&P 500 CSV has no header."
            )

        print(
            "Columns:",
            reader.fieldnames,
        )

        rows = []
        seen = set()

        for item in reader:

            ticker = (
                cls.normalise_ticker(
                    item.get(
                        "Symbol"
                    )
                )
            )

            if (
                not ticker
                or ticker in seen
            ):
                continue

            seen.add(
                ticker
            )

            rows.append(
                {
                    "ticker":
                        ticker,

                    "name":
                        item.get(
                            "Security",
                            ticker,
                        ),

                    "sector":
                        item.get(
                            "GICS Sector",
                            "",
                        ),

                    "industry":
                        item.get(
                            "GICS Sub-Industry",
                            "",
                        ),

                    "index_membership":
                        [
                            "SP500"
                        ],

                    "source":
                        "GITHUB_DATASETS_SP500",

                    "source_url":
                        cls.SP500_URL,

                    "retrieved_at":
                        retrieved_at,
                }
            )

        print(
            "S&P 500 rows:",
            len(rows),
        )

        if len(rows) < cls.MIN_SP500:

            raise RuntimeError(
                "S&P 500 validation failed. "
                f"Only {len(rows)} "
                "constituents received."
            )

        return rows

    @classmethod
    def load_nasdaq100(
        cls,
        public_client=None,
    ):

        retrieved_at = cls.now()

        text = cls.download_csv(
            cls.NASDAQ100_URL,
            "NASDAQ-100",
            public_client,
        )

        reader = csv.DictReader(
            io.StringIO(text)
        )

        if not reader.fieldnames:

            raise RuntimeError(
                "NASDAQ-100 CSV has no header."
            )

        print(
            "Columns:",
            reader.fieldnames,
        )

        rows = []
        seen = set()

        for item in reader:

            ticker = (
                cls.normalise_ticker(
                    item.get(
                        "Ticker"
                    )
                )
            )

            if (
                not ticker
                or ticker in seen
            ):
                continue

            seen.add(
                ticker
            )

            rows.append(
                {
                    "ticker":
                        ticker,

                    "name":
                        item.get(
                            "Company",
                            ticker,
                        ),

                    "sector":
                        item.get(
                            "GICS_Sector",
                            "",
                        ),

                    "industry":
                        item.get(
                            "GICS_Sub_Industry",
                            "",
                        ),

                    "index_membership":
                        [
                            "NASDAQ100"
                        ],

                    "source":
                        "GITHUB_GARY_STRAUSS",

                    "source_url":
                        cls.NASDAQ100_URL,

                    "retrieved_at":
                        retrieved_at,
                }
            )

        print(
            "NASDAQ-100 rows:",
            len(rows),
        )

        if len(rows) < cls.MIN_NASDAQ100:

            raise RuntimeError(
                "NASDAQ-100 validation failed. "
                f"Only {len(rows)} "
                "constituents received."
            )

        return rows

    @staticmethod
    def merge(
        rows
    ):

        companies = {}

        for row in rows:

            ticker = row[
                "ticker"
            ]

            if ticker not in companies:

                companies[ticker] = {
                    "ticker":
                        ticker,

                    "name":
                        row["name"],

                    "sector":
                        row["sector"],

                    "industry":
                        row["industry"],

                    "index_membership":
                        [],

                    "sources":
                        [],

                    "source_urls":
                        [],

                    "retrieved_at":
                        [],
                }

            company = companies[
                ticker
            ]

            membership = (
                row[
                    "index_membership"
                ][0]
            )

            if membership not in (
                company[
                    "index_membership"
                ]
            ):

                company[
                    "index_membership"
                ].append(
                    membership
                )

            for key, value in (
                (
                    "sources",
                    row["source"],
                ),
                (
                    "source_urls",
                    row["source_url"],
                ),
                (
                    "retrieved_at",
                    row["retrieved_at"],
                ),
            ):

                if value not in (
                    company[key]
                ):

                    company[key].append(
                        value
                    )

        return sorted(
            companies.values(),
            key=lambda item:
                item["ticker"],
        )

    @classmethod
    def get_universe(
        cls,
        universe="both",
        public_client=None,
    ):

        universe = (
            str(
                universe
            )
            .lower()
            .strip()
        )

        if universe not in {
            "sp500",
            "nasdaq100",
            "both",
        }:

            raise ValueError(
                "Universe must be "
                "sp500, nasdaq100 "
                "or both."
            )

        rows = []
        client = public_client or PublicTextClient(
            GITHUB_UNIVERSE_ENDPOINT
        )

        if universe in {
            "sp500",
            "both",
        }:

            rows.extend(
                cls.load_sp500(client)
            )

        if universe in {
            "nasdaq100",
            "both",
        }:

            rows.extend(
                cls.load_nasdaq100(client)
            )

        companies = cls.merge(
            rows
        )

        overlap_count = sum(
            1
            for company in companies
            if len(
                company[
                    "index_membership"
                ]
            ) > 1
        )

        return {
            "universe":
                universe,

            "provider":
                "GITHUB_RAW_CSV",

            "version":
                cls.VERSION,

            "created_at":
                cls.now(),

            "count":
                len(companies),

            "overlap_count":
                overlap_count,

            "companies":
                companies,

            "source_policy":
                "PUBLIC_INDEX_CONSTITUENT_DATA",

            "point_in_time":
                False,

            "survivorship_safe":
                False,

            "replay_eligible":
                False,

            "validation":
                {
                    "sp500_minimum":
                        cls.MIN_SP500,

                    "nasdaq100_minimum":
                        cls.MIN_NASDAQ100,

                    "partial_universe":
                        False,
                },
        }

    @classmethod
    def save(
        cls,
        data,
    ):

        directory = Path(
            "data/research/universe"
        )

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = (
            directory
            / (
                f"{data['universe']}.json"
            )
        )

        path.write_text(
            json.dumps(
                data,
                indent=2,
            ),
            encoding="utf-8",
        )

        return path


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("UNIVERSE ENGINE TEST")
    print("=" * 80)

    for universe in (
        "sp500",
        "nasdaq100",
        "both",
    ):

        print()
        print(
            "TESTING:",
            universe.upper(),
        )

        data = (
            UniverseEngine
            .get_universe(
                universe
            )
        )

        saved = (
            UniverseEngine
            .save(
                data
            )
        )

        print(
            "Provider:",
            data[
                "provider"
            ],
        )

        print(
            "Count:",
            data[
                "count"
            ],
        )

        print(
            "Overlap:",
            data[
                "overlap_count"
            ],
        )

        print(
            "Saved:",
            saved,
        )

    print()
    print("=" * 80)
    print("UNIVERSE ENGINE PASSED")
    print("=" * 80)
