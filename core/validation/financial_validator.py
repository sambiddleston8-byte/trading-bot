import json
import os
from datetime import datetime, timezone


class FinancialValidator:

    def __init__(
        self,
        output_directory="data/research/validation",
    ):
        self.output_directory = output_directory

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

    def utc_now(self):
        return datetime.now(
            timezone.utc
        ).isoformat()

    def compare_values(
        self,
        name,
        source_a,
        source_b,
        tolerance=0.01,
    ):

        if source_a is None or source_b is None:
            return {
                "metric": name,
                "status": "INSUFFICIENT_DATA",
                "source_a": source_a,
                "source_b": source_b,
            }

        if source_a == 0:
            difference = (
                0
                if source_b == 0
                else None
            )
        else:
            difference = (
                source_b - source_a
            ) / abs(source_a)

        if difference is None:
            status = "INVESTIGATE"

        elif abs(difference) <= tolerance:
            status = "AGREE"

        else:
            status = "DISCREPANCY"

        return {
            "metric": name,
            "source_a": source_a,
            "source_b": source_b,
            "absolute_difference": (
                source_b - source_a
            ),
            "percentage_difference": (
                difference * 100
                if difference is not None
                else None
            ),
            "tolerance_percentage": (
                tolerance * 100
            ),
            "status": status,
        }

    def extract_yahoo(self, yahoo):

        latest = (
            yahoo
            .get("financials", {})
            .get("latest_annual")
            or {}
        )

        balance = (
            yahoo
            .get("balance_sheet", {})
        )

        market = (
            yahoo
            .get("market", {})
        )

        return {
            "revenue": latest.get(
                "revenue"
            ),

            "operating_cash_flow":
                latest.get(
                    "operating_cash_flow"
                ),

            "free_cash_flow":
                latest.get(
                    "free_cash_flow"
                ),

            "cash_and_equivalents":
                balance
                .get(
                    "cash_and_equivalents",
                    {}
                )
                .get("value"),

            "total_debt":
                balance
                .get(
                    "total_debt",
                    {}
                )
                .get("value"),

            "shares_outstanding":
                market.get(
                    "shares_outstanding"
                ),
        }

    def extract_sec(self, sec):

        financials = (
            sec.get(
                "financials",
                {}
            )
        )

        balance = (
            sec.get(
                "balance_sheet",
                {}
            )
        )

        return {
            "revenue":
                (
                    financials
                    .get("revenue")
                    or {}
                ).get("value"),

            "operating_cash_flow":
                (
                    financials
                    .get(
                        "operating_cash_flow"
                    )
                    or {}
                ).get("value"),

            "free_cash_flow":
                (
                    financials
                    .get(
                        "free_cash_flow"
                    )
                    or {}
                ).get("value"),

            "cash_and_equivalents":
                (
                    balance
                    .get(
                        "cash_and_equivalents"
                    )
                    or {}
                ).get("value"),

            "total_debt":
                (
                    balance
                    .get(
                        "total_debt"
                    )
                    or {}
                ).get("value"),

            "shares_outstanding":
                (
                    balance
                    .get(
                        "shares_outstanding"
                    )
                    or {}
                ).get("value"),
        }

    def validate(
        self,
        yahoo,
        sec,
        symbol,
    ):

        yahoo_values = self.extract_yahoo(
            yahoo
        )

        sec_values = self.extract_sec(
            sec
        )

        comparisons = []

        comparisons.append(
            self.compare_values(
                "Revenue",
                yahoo_values["revenue"],
                sec_values["revenue"],
                tolerance=0.005,
            )
        )

        comparisons.append(
            self.compare_values(
                "Operating Cash Flow",
                yahoo_values[
                    "operating_cash_flow"
                ],
                sec_values[
                    "operating_cash_flow"
                ],
                tolerance=0.005,
            )
        )

        comparisons.append(
            self.compare_values(
                "Free Cash Flow",
                yahoo_values[
                    "free_cash_flow"
                ],
                sec_values[
                    "free_cash_flow"
                ],
                tolerance=0.005,
            )
        )

        comparisons.append(
            self.compare_values(
                "Cash & Equivalents",
                yahoo_values[
                    "cash_and_equivalents"
                ],
                sec_values[
                    "cash_and_equivalents"
                ],
                tolerance=0.01,
            )
        )

        comparisons.append(
            self.compare_values(
                "Total Debt",
                yahoo_values[
                    "total_debt"
                ],
                sec_values[
                    "total_debt"
                ],
                tolerance=0.01,
            )
        )

        shares = self.compare_values(
            "Shares Outstanding",
            yahoo_values[
                "shares_outstanding"
            ],
            sec_values[
                "shares_outstanding"
            ],
            tolerance=0.02,
        )

        shares["interpretation"] = (
            "Shares outstanding are date-sensitive. "
            "Differences may be legitimate if the "
            "sources refer to different dates."
        )

        comparisons.append(
            shares
        )

        agreement_count = sum(
            1
            for item in comparisons
            if item["status"] == "AGREE"
        )

        discrepancy_count = sum(
            1
            for item in comparisons
            if item["status"] == "DISCREPANCY"
        )

        insufficient_count = sum(
            1
            for item in comparisons
            if item["status"]
            == "INSUFFICIENT_DATA"
        )

        comparable_count = (
            len(comparisons)
            - insufficient_count
        )

        if comparable_count <= 0:
            confidence = "LOW"

        elif discrepancy_count == 0:
            confidence = "HIGH"

        elif discrepancy_count <= 1:
            confidence = "MEDIUM"

        else:
            confidence = "LOW"

        result = {
            "ticker": symbol.upper(),

            "validated_at":
                self.utc_now(),

            "sources": {
                "source_a":
                    yahoo.get("source"),

                "source_b":
                    sec.get("source"),
            },

            "comparisons":
                comparisons,

            "summary": {
                "total_metrics":
                    len(comparisons),

                "agreements":
                    agreement_count,

                "discrepancies":
                    discrepancy_count,

                "insufficient_data":
                    insufficient_count,

                "overall_confidence":
                    confidence,
            },
        }

        path = os.path.join(
            self.output_directory,
            f"{symbol.upper()}.json",
        )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                result,
                file,
                indent=2,
                default=str,
            )

        result["output_path"] = path

        return result
