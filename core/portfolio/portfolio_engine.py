from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json


class PortfolioEngine:

    """
    Portfolio candidate/ranking engine.

    This is deliberately separated from research.

    Research answers:

        "How good is this company?"

    Portfolio construction answers:

        "Given all researched companies,
         which ones should enter the portfolio,
         in what weights, and why?"

    The final portfolio engine will become more sophisticated
    over time. The first version is deliberately transparent.
    """

    VERSION = "0.1-prototype"

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def number(
        value
    ):

        try:

            if value is None:
                return None

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

    @classmethod
    def score_candidate(
        cls,
        item,
    ):

        base = cls.number(
            item.get(
                "investment_case_score"
            )
        )

        expected_return = cls.number(
            item.get(
                "expected_return"
            )
        )

        thesis = item.get(
            "thesis",
            {},
        )

        thesis_negative = cls.number(
            thesis.get(
                "material_negative"
            )
        ) or 0

        audit = item.get(
            "audit",
            {},
        )

        audit_status = audit.get(
            "status"
        )

        # ----------------------------------------------------
        # Never allow failed audits to become portfolio picks.
        # ----------------------------------------------------

        if audit_status != "PASS":
            return None

        if base is None:
            return None

        score = base

        # Expected return is a major portfolio consideration.
        if expected_return is not None:

            if expected_return >= 0.30:
                score += 10

            elif expected_return >= 0.15:
                score += 6

            elif expected_return >= 0.05:
                score += 3

            elif expected_return < 0:
                score -= 10

        # Adversarial negatives matter.
        score -= min(
            thesis_negative * 2,
            15,
        )

        return round(
            max(
                0,
                min(
                    100,
                    score,
                ),
            ),
            2,
        )

    @classmethod
    def rank(
        cls,
        scan,
    ):

        ranked = []

        for item in scan.get(
            "ranked",
            [],
        ):

            candidate = dict(
                item
            )

            score = (
                cls.score_candidate(
                    candidate
                )
            )

            if score is None:
                continue

            candidate[
                "portfolio_score"
            ] = score

            ranked.append(
                candidate
            )

        ranked.sort(
            key=lambda item:
                item.get(
                    "portfolio_score",
                    -1,
                ),
            reverse=True,
        )

        for rank, item in enumerate(
            ranked,
            start=1,
        ):

            item[
                "portfolio_rank"
            ] = rank

        return ranked

    @classmethod
    def construct(
        cls,
        scan,
        number_of_stocks=10,
        max_weight=0.15,
    ):

        ranked = cls.rank(
            scan
        )

        selected = ranked[
            :int(
                number_of_stocks
            )
        ]

        if not selected:

            raise RuntimeError(
                "No audited portfolio "
                "candidates are available."
            )

        # ----------------------------------------------------
        # Equal starting allocation.
        #
        # Tomorrow we will replace this with the proper
        # conviction/risk/diversification optimiser.
        # ----------------------------------------------------

        equal_weight = (
            1.0
            /
            len(selected)
        )

        weight = min(
            equal_weight,
            float(
                max_weight
            ),
        )

        # If max_weight prevents full allocation,
        # renormalise across selected names.
        weights = [
            weight
            for _ in selected
        ]

        total = sum(
            weights
        )

        weights = [
            value / total
            for value in weights
        ]

        holdings = []

        for item, weight in zip(
            selected,
            weights,
        ):

            holdings.append(
                {
                    "rank":
                        item[
                            "portfolio_rank"
                        ],

                    "ticker":
                        item[
                            "ticker"
                        ],

                    "name":
                        item.get(
                            "name"
                        ),

                    "sector":
                        item.get(
                            "sector"
                        ),

                    "index_membership":
                        item.get(
                            "index_membership",
                            [],
                        ),

                    "portfolio_score":
                        item[
                            "portfolio_score"
                        ],

                    "investment_case_score":
                        item.get(
                            "investment_case_score"
                        ),

                    "expected_return":
                        item.get(
                            "expected_return"
                        ),

                    "decision":
                        item.get(
                            "decision"
                        ),

                    "weight":
                        round(
                            weight,
                            6,
                        ),

                    "reasoning":
                        {
                            "investment_case":
                                item.get(
                                    "investment_case_score"
                                ),

                            "expected_return":
                                item.get(
                                    "expected_return"
                                ),

                            "thesis":
                                item.get(
                                    "thesis",
                                    {},
                                ),

                            "audit":
                                item.get(
                                    "audit",
                                    {},
                                ),
                        },
                }
            )

        return {
            "version":
                cls.VERSION,

            "created_at":
                cls.now(),

            "universe":
                scan.get(
                    "universe"
                ),

            "source_policy":
                "OFFICIAL_INDEX_PROVIDER_ONLY",

            "number_of_stocks":
                len(holdings),

            "holdings":
                holdings,

            "method":
                "TRANSPARENT_EQUAL_WEIGHT_PROTOTYPE",

            "status":
                "PROTOTYPE",
        }

    @staticmethod
    def save(
        portfolio,
        path=None,
    ):

        if path is None:

            path = (
                "data/research/"
                "portfolios/"
                "prototype.json"
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
        ) as file:

            json.dump(
                portfolio,
                file,
                indent=2,
            )

        return str(
            path
        )
