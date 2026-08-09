from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone
from pathlib import Path
import json
import traceback

from core.portfolio.universe_engine import (
    UniverseEngine,
)
from core.research.investment_research_pipeline import (
    InvestmentResearchPipeline,
)
from core.research.research_contract import (
    ResearchContract,
)


class UniverseScanner:
    """
    Runs the complete investment research pipeline across
    an equity universe.

    The scanner deliberately does NOT construct the portfolio.

    Its job is:

        universe
          ↓
        research
          ↓
        normalisation
          ↓
        audit filter
          ↓
        ranking dataset

    Portfolio construction comes afterwards.
    """

    def __init__(
        self,
        workers=2,
    ):

        self.workers = max(
            1,
            int(workers),
        )

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def _number(value):

        if isinstance(
            value,
            (int, float),
        ):
            return float(
                value
            )

        return None

    @classmethod
    def _extract_score(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return cls._number(
            canonical.get(
                "investment_case_score"
            )
        )

    @classmethod
    def _extract_decision(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return canonical.get(
            "decision"
        )

    @classmethod
    def _extract_expected_return(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return cls._number(
            canonical.get(
                "expected_return"
            )
        )

    @classmethod
    def _extract_intrinsic_value(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return cls._number(
            canonical.get(
                "base_intrinsic_value"
            )
        )

    @classmethod
    def _extract_current_price(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return cls._number(
            canonical.get(
                "current_price"
            )
        )

    @classmethod
    def _extract_thesis(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return canonical.get(
            "thesis",
            {},
        )

    @classmethod
    def _extract_audit(
        cls,
        result,
    ):

        canonical = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        return canonical.get(
            "audit",
            {},
        )

    @classmethod
    def _research_one(
        cls,
        company,
    ):

        ticker = company[
            "ticker"
        ]

        try:

            result = (
                InvestmentResearchPipeline
                .analyse(
                    ticker
                )
            )

            audit = (
                cls._extract_audit(
                    result
                )
            )

            thesis = (
                cls._extract_thesis(
                    result
                )
            )

            return {
                "ticker":
                    ticker,

                "name":
                    company.get(
                        "name"
                    ),

                "sector":
                    company.get(
                        "sector"
                    ),

                "index_membership":
                    company.get(
                        "index_membership",
                        [],
                    ),

                "investment_case_score":
                    cls._extract_score(
                        result
                    ),

                "decision":
                    cls._extract_decision(
                        result
                    ),

                "current_price":
                    cls._extract_current_price(
                        result
                    ),

                "base_intrinsic_value":
                    cls._extract_intrinsic_value(
                        result
                    ),

                "expected_return":
                    cls._extract_expected_return(
                        result
                    ),

                "thesis":
                    thesis,

                "audit":
                    audit,

                "research_status":
                    "COMPLETE",

                "researched_at":
                    cls.now(),
            }

        except Exception as exc:

            return {
                "ticker":
                    ticker,

                "name":
                    company.get(
                        "name"
                    ),

                "sector":
                    company.get(
                        "sector"
                    ),

                "index_membership":
                    company.get(
                        "index_membership",
                        [],
                    ),

                "investment_case_score":
                    None,

                "decision":
                    None,

                "current_price":
                    None,

                "base_intrinsic_value":
                    None,

                "expected_return":
                    None,

                "thesis":
                    {},

                "audit":
                    {
                        "status":
                            "ERROR",
                    },

                "research_status":
                    "ERROR",

                "error":
                    str(exc),

                "traceback":
                    traceback.format_exc(),

                "researched_at":
                    cls.now(),
            }

    @classmethod
    def _rank(
        cls,
        results,
    ):

        # ----------------------------------------------------
        # Only rank completed, audited names.
        #
        # A stock with a failed audit should never silently
        # become a top portfolio candidate.
        # ----------------------------------------------------

        eligible = []

        for item in results:

            if item.get(
                "research_status"
            ) != "COMPLETE":

                continue

            audit = item.get(
                "audit",
                {}
            )

            if audit.get(
                "status"
            ) != "PASS":

                continue

            score = item.get(
                "investment_case_score"
            )

            if score is None:

                continue

            eligible.append(
                item
            )

        eligible.sort(
            key=lambda item:
                item.get(
                    "investment_case_score",
                    -999,
                ),
            reverse=True,
        )

        for rank, item in enumerate(
            eligible,
            start=1,
        ):

            item[
                "rank"
            ] = rank

        return eligible

    def scan(
        self,
        universe,
        limit=None,
    ):

        companies = universe[
            "companies"
        ]

        if limit is not None:

            companies = companies[
                :int(limit)
            ]

        results = []

        print()
        print(
            "=" * 80
        )
        print(
            "UNIVERSE SCAN"
        )
        print(
            "=" * 80
        )
        print(
            "Universe:",
            universe[
                "universe"
            ],
        )
        print(
            "Companies:",
            len(companies),
        )
        print(
            "Workers:",
            self.workers,
        )
        print()

        with ThreadPoolExecutor(
            max_workers=self.workers
        ) as executor:

            futures = {
                executor.submit(
                    self._research_one,
                    company,
                ): company[
                    "ticker"
                ]
                for company in companies
            }

            completed = 0

            for future in as_completed(
                futures
            ):

                ticker = futures[
                    future
                ]

                result = future.result()

                results.append(
                    result
                )

                completed += 1

                if (
                    result.get(
                        "research_status"
                    )
                    ==
                    "COMPLETE"
                ):

                    print(
                        f"[{completed}/{len(companies)}] "
                        f"{ticker} ✓ "
                        f"score="
                        f"{result.get('investment_case_score')}"
                    )

                else:

                    print(
                        f"[{completed}/{len(companies)}] "
                        f"{ticker} ✗ "
                        f"{result.get('error')}"
                    )

        ranked = self._rank(
            results
        )

        return {
            "universe":
                universe[
                    "universe"
                ],

            "started_at":
                self.now(),

            "completed_at":
                self.now(),

            "requested_count":
                len(companies),

            "completed_count":
                sum(
                    1
                    for item in results
                    if item.get(
                        "research_status"
                    )
                    ==
                    "COMPLETE"
                ),

            "error_count":
                sum(
                    1
                    for item in results
                    if item.get(
                        "research_status"
                    )
                    ==
                    "ERROR"
                ),

            "audit_pass_count":
                sum(
                    1
                    for item in results
                    if item.get(
                        "audit",
                        {}
                    ).get(
                        "status"
                    )
                    ==
                    "PASS"
                ),

            "eligible_count":
                len(ranked),

            "results":
                results,

            "ranked":
                ranked,
        }

    @staticmethod
    def save(
        scan,
        path=None,
    ):

        if path is None:

            path = (
                "data/research/"
                "universe_scans/"
                f"{scan['universe']}.json"
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
                scan,
                f,
                indent=2,
            )

        return str(
            path
        )


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--index",
        choices=[
            "sp500",
            "nasdaq100",
            "both",
        ],
        default="sp500",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=2,
    )

    args = parser.parse_args()

    universe = (
        UniverseEngine
        .get_universe(
            args.index
        )
    )

    scanner = (
        UniverseScanner(
            workers=args.workers
        )
    )

    scan = scanner.scan(
        universe,
        limit=args.limit,
    )

    saved = (
        scanner.save(
            scan
        )
    )

    print()
    print(
        "=" * 80
    )
    print(
        "UNIVERSE SCAN COMPLETE"
    )
    print(
        "=" * 80
    )
    print(
        "Completed:",
        scan[
            "completed_count"
        ],
    )
    print(
        "Errors:",
        scan[
            "error_count"
        ],
    )
    print(
        "Audit PASS:",
        scan[
            "audit_pass_count"
        ],
    )
    print(
        "Eligible:",
        scan[
            "eligible_count"
        ],
    )
    print(
        "Saved:",
        saved,
    )

    print()
    print(
        "TOP CANDIDATES"
    )

    for item in scan[
        "ranked"
    ][:10]:

        print(
            item[
                "rank"
            ],
            "|",
            item[
                "ticker"
            ],
            "|",
            item[
                "investment_case_score"
            ],
            "|",
            item[
                "decision"
            ],
        )
