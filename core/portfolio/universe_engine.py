from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re

import pandas as pd
import urllib.request


class UniverseEngine:
    """
    Builds investable equity universes.

    Supported:
        - S&P 500
        - Nasdaq-100
        - Both / combined

    Membership is kept separate from research so that the
    portfolio layer can later change its universe without
    changing the individual-stock research engine.
    """

    SP500_URL = (
        "https://en.wikipedia.org/wiki/"
        "List_of_S%26P_500_companies"
    )

    NASDAQ100_URL = (
        "https://en.wikipedia.org/wiki/"
        "Nasdaq-100"
    )

    @staticmethod
    def now():
        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def normalise_ticker(ticker):
        """
        Convert exchange notation to Yahoo Finance notation.

        Examples:
            BRK.B -> BRK-B
            BF.B  -> BF-B
        """

        ticker = str(
            ticker
        ).strip().upper()

        ticker = re.sub(
            r"\s+",
            "",
            ticker,
        )

        ticker = ticker.replace(
            ".",
            "-",
        )

        return ticker

    @classmethod
    def _load_sp500(cls):

        url = (
            "https://raw.githubusercontent.com/"
            "datasets/s-and-p-500-companies/"
            "main/data/constituents.csv"
        )

        try:

            table = pd.read_csv(
                url
            )

        except Exception as exc:

            raise RuntimeError(
                "Unable to retrieve S&P 500 "
                "constituents: "
                f"{exc}"
            ) from exc

        required = {
            "Symbol",
            "Security",
            "GICS Sector",
        }

        missing = (
            required
            - set(table.columns)
        )

        if missing:

            raise RuntimeError(
                "S&P 500 source is missing "
                f"columns: {sorted(missing)}"
            )

        rows = []

        for _, row in table.iterrows():

            ticker = cls.normalise_ticker(
                row["Symbol"]
            )

            if not ticker:
                continue

            rows.append(
                {
                    "ticker": ticker,
                    "name": str(
                        row["Security"]
                    ),
                    "index_membership": [
                        "SP500"
                    ],
                    "sector": str(
                        row["GICS Sector"]
                    ),
                    "source":
                        "GITHUB_DATASETS",
                }
            )

        if len(rows) < 450:

            raise RuntimeError(
                "S&P 500 source returned "
                f"only {len(rows)} constituents."
            )

        return rows

    @classmethod
    def _load_nasdaq100(cls):

        # ----------------------------------------------------
        # OFFICIAL NASDAQ SOURCE
        #
        # Nasdaq publishes the NDX constituent/weighting
        # file directly from nasdaq.com.
        #
        # We use the official index file rather than:
        #   - Wikipedia
        #   - third-party datasets
        #   - scraped websites
        #   - unofficial GitHub repositories
        # ----------------------------------------------------

        url = (
            "https://www.nasdaq.com/"
            "docs/2026/05/04/NDX.pdf"
        )

        try:

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                        "Mozilla/5.0 "
                        "(Macintosh; Intel Mac OS X) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/131 Safari/537.36",
                    "Accept":
                        "application/pdf,"
                        "*/*;q=0.8",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:

                content = response.read()

        except Exception as exc:

            raise RuntimeError(
                "Unable to retrieve the official "
                "Nasdaq-100 constituent file: "
                f"{exc}"
            ) from exc

        if not content.startswith(
            b"%PDF"
        ):

            raise RuntimeError(
                "Nasdaq returned content that "
                "does not appear to be a PDF."
            )

        # ----------------------------------------------------
        # Parse the official PDF.
        #
        # pdfplumber is preferred because the Nasdaq file
        # is a structured table rather than ordinary HTML.
        # ----------------------------------------------------

        try:

            import pdfplumber
            from io import BytesIO

            pdf = pdfplumber.open(
                BytesIO(content)
            )

        except Exception as exc:

            raise RuntimeError(
                "Unable to parse official Nasdaq "
                f"NDX PDF: {exc}"
            ) from exc

        rows = []

        try:

            for page in pdf.pages:

                tables = (
                    page.extract_tables()
                )

                for table in tables:

                    if not table:
                        continue

                    for row in table:

                        if not row:
                            continue

                        cleaned = [
                            (
                                str(value)
                                .strip()
                                if value is not None
                                else ""
                            )
                            for value in row
                        ]

                        if len(cleaned) < 2:
                            continue

                        symbol = None
                        name = None
                        weight = None

                        # Nasdaq's official PDF normally
                        # contains:
                        #
                        # Name | Symbol | Weight (%)
                        #
                        # Identify the symbol by looking
                        # for a plausible ticker field.
                        for index, value in enumerate(
                            cleaned
                        ):

                            candidate = (
                                cls.normalise_ticker(
                                    value
                                )
                            )

                            if (
                                candidate
                                and
                                1
                                <= len(candidate)
                                <= 6
                                and
                                candidate
                                not in {
                                    "NAME",
                                    "SYMBOL",
                                    "WEIGHT",
                                    "PERCENT",
                                }
                                and
                                all(
                                    character.isalpha()
                                    or
                                    character == "-"
                                    for character
                                    in candidate
                                )
                            ):

                                symbol = candidate

                                if index > 0:
                                    name = cleaned[
                                        index - 1
                                    ]

                                break

                        # Extract weight when present.
                        for value in cleaned:

                            candidate = (
                                value
                                .replace(
                                    "%",
                                    ""
                                )
                                .replace(
                                    ",",
                                    ""
                                )
                                .strip()
                            )

                            try:

                                numeric = float(
                                    candidate
                                )

                                if (
                                    0
                                    <
                                    numeric
                                    <=
                                    100
                                ):

                                    weight = numeric
                                    break

                            except (
                                TypeError,
                                ValueError,
                            ):

                                continue

                        if (
                            symbol is None
                            or
                            symbol
                            in {
                                "NAME",
                                "SYMBOL",
                            }
                        ):

                            continue

                        rows.append(
                            {
                                "ticker":
                                    symbol,

                                "name":
                                    name
                                    or
                                    symbol,

                                "index_membership":
                                    [
                                        "NASDAQ100"
                                    ],

                                "sector":
                                    "",

                                "weight":
                                    weight,

                                "source":
                                    "NASDAQ_OFFICIAL",
                            }
                        )

        finally:

            pdf.close()

        # ----------------------------------------------------
        # Deduplicate.
        # ----------------------------------------------------

        deduplicated = []
        seen = set()

        for row in rows:

            ticker = row[
                "ticker"
            ]

            if ticker in seen:
                continue

            seen.add(
                ticker
            )

            deduplicated.append(
                row
            )

        # Nasdaq-100 should never silently return a tiny
        # partial universe.
        if len(
            deduplicated
        ) < 90:

            raise RuntimeError(
                "Official Nasdaq-100 PDF produced "
                f"only {len(deduplicated)} "
                "recognised constituents."
            )

        return deduplicated

    @classmethod
    def get_universe(
        cls,
        universe="both",
    ):

        universe = str(
            universe
        ).lower().strip()

        if universe not in {
            "sp500",
            "nasdaq100",
            "both",
        }:
            raise ValueError(
                "Universe must be "
                "'sp500', 'nasdaq100', or 'both'."
            )

        rows = []

        if universe in {
            "sp500",
            "both",
        }:
            rows.extend(
                cls._load_sp500()
            )

        if universe in {
            "nasdaq100",
            "both",
        }:
            rows.extend(
                cls._load_nasdaq100()
            )

        # ----------------------------------------------------
        # Deduplicate by ticker while preserving membership.
        # ----------------------------------------------------

        companies = {}

        for row in rows:

            ticker = row[
                "ticker"
            ]

            if ticker not in companies:

                companies[
                    ticker
                ] = {
                    "ticker": ticker,
                    "name": row[
                        "name"
                    ],
                    "index_membership": [],
                    "sector": row.get(
                        "sector",
                        "",
                    ),
                    "sources": [],
                }

            for membership in row[
                "index_membership"
            ]:

                if membership not in companies[
                    ticker
                ][
                    "index_membership"
                ]:

                    companies[
                        ticker
                    ][
                        "index_membership"
                    ].append(
                        membership
                    )

            source = row.get(
                "source"
            )

            if (
                source
                and
                source not in companies[
                    ticker
                ][
                    "sources"
                ]
            ):

                companies[
                    ticker
                ][
                    "sources"
                ].append(
                    source
                )

        result = list(
            companies.values()
        )

        result.sort(
            key=lambda item:
                item["ticker"]
        )

        return {
            "universe": universe,
            "created_at": cls.now(),
            "count": len(result),
            "companies": result,
            "overlap_count": sum(
                1
                for item in result
                if len(
                    item[
                        "index_membership"
                    ]
                ) > 1
            ),
        }

    @classmethod
    def save(
        cls,
        data,
        path=None,
    ):

        if path is None:

            path = (
                "data/research/universe/"
                f"{data['universe']}.json"
            )

        path = Path(
            path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
            )

        return str(
            path
        )


if __name__ == "__main__":

    print("=" * 80)
    print("UNIVERSE ENGINE TEST")
    print("=" * 80)

    for universe in [
        "sp500",
        "nasdaq100",
        "both",
    ]:

        print()
        print(
            f"Loading {universe.upper()}..."
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
            "Count:",
            data["count"],
        )

        print(
            "Overlap:",
            data["overlap_count"],
        )

        print(
            "Saved:",
            saved,
        )

    print()
    print("=" * 80)
    print("UNIVERSE ENGINE OK")
    print("=" * 80)
