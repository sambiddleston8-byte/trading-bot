from datetime import datetime, timezone


class ValidatedFinancialData:

    VERSION = "1.1"

    DEFAULT_TOLERANCE = 0.005

    def __init__(self, yahoo_data, sec_data):
        self.yahoo = yahoo_data or {}
        self.sec = sec_data or {}

    @staticmethod
    def _number(value):
        try:
            if value is None:
                return None
            value = float(value)
            if value != value:
                return None
            return value
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percent_difference(a, b):
        if a is None or b is None:
            return None
        if a == 0:
            return 0.0 if b == 0 else None
        return ((b - a) / abs(a)) * 100.0

    @staticmethod
    def _status_from_difference(difference, tolerance):
        if difference is None:
            return "INSUFFICIENT_DATA"
        if abs(difference) <= tolerance * 100:
            return "AGREE"
        return "DISCREPANCY"

    # ============================================================
    # YAHOO
    # ============================================================

    def _yahoo_latest(self):
        financials = self.yahoo.get("financials", {})
        latest = financials.get("latest_annual") or {}

        balance = self.yahoo.get("balance_sheet", {})
        market = self.yahoo.get("market", {})

        cash = balance.get("cash_and_equivalents") or {}
        total_debt = balance.get("total_debt") or {}

        return {
            "revenue": self._number(
                latest.get("revenue")
            ),
            "operating_cash_flow": self._number(
                latest.get("operating_cash_flow")
            ),
            "free_cash_flow": self._number(
                latest.get("free_cash_flow")
            ),
            "cash_and_equivalents": self._number(
                cash.get("value")
            ),
            "total_cash_and_short_term_investments":
                self._number(
                    balance.get(
                        "cash_cash_equivalents_and_short_term_investments"
                    )
                ),
            "total_debt": self._number(
                total_debt.get("value")
            ),
            "long_term_debt": self._number(
                balance.get("long_term_debt")
            ),
            "current_debt": self._number(
                balance.get("current_debt")
            ),
            "capital_lease_obligations": self._number(
                balance.get("capital_lease_obligations")
            ),
            "shares_outstanding": self._number(
                market.get("shares_outstanding")
            ),
        }

    # ============================================================
    # SEC
    # ============================================================

    def _sec_latest(self):
        financials = self.sec.get("financials", {})
        balance = self.sec.get("balance_sheet", {})

        revenue = financials.get("revenue") or {}
        operating_cash_flow = (
            financials.get("operating_cash_flow") or {}
        )
        free_cash_flow = (
            financials.get("free_cash_flow") or {}
        )
        cash = balance.get("cash_and_equivalents") or {}
        debt = balance.get("total_debt") or {}
        shares = balance.get("shares_outstanding") or {}

        return {
            "revenue": self._number(
                revenue.get("value")
            ),
            "operating_cash_flow": self._number(
                operating_cash_flow.get("value")
            ),
            "free_cash_flow": self._number(
                free_cash_flow.get("value")
            ),
            "cash_and_equivalents": self._number(
                cash.get("value")
            ),
            "total_debt": self._number(
                debt.get("value")
            ),
            "shares_outstanding": self._number(
                shares.get("value")
            ),
        }

    # ============================================================
    # PERIOD / PROVENANCE
    # ============================================================

    def _period_metadata(self):
        yahoo_latest = (
            self.yahoo
            .get("financials", {})
            .get("latest_annual")
            or {}
        )

        sec_financials = self.sec.get("financials", {})
        sec_revenue = sec_financials.get("revenue") or {}

        return {
            "yahoo_period": yahoo_latest.get("period"),
            "sec_start": sec_revenue.get("start"),
            "sec_end": sec_revenue.get("end"),
            "sec_fiscal_year": sec_revenue.get(
                "fiscal_year"
            ),
            "sec_fiscal_period": sec_revenue.get(
                "fiscal_period"
            ),
            "sec_filed": sec_revenue.get("filed"),
            "sec_accession": sec_revenue.get(
                "accession"
            ),
        }

    # ============================================================
    # GENERIC COMPARISON
    # ============================================================

    def _compare(
        self,
        name,
        yahoo_value,
        sec_value,
        tolerance=None,
    ):
        if tolerance is None:
            tolerance = self.DEFAULT_TOLERANCE

        yahoo_value = self._number(yahoo_value)
        sec_value = self._number(sec_value)

        if (
            yahoo_value is None
            or sec_value is None
        ):
            return {
                "metric": name,
                "yahoo": yahoo_value,
                "sec": sec_value,
                "selected": (
                    sec_value
                    if sec_value is not None
                    else yahoo_value
                ),
                "selected_source": (
                    "SEC"
                    if sec_value is not None
                    else "Yahoo"
                ),
                "difference_percent": None,
                "status": "INSUFFICIENT_DATA",
                "confidence": "LOW",
            }

        difference = self._percent_difference(
            yahoo_value,
            sec_value,
        )

        status = self._status_from_difference(
            difference,
            tolerance,
        )

        return {
            "metric": name,
            "yahoo": yahoo_value,
            "sec": sec_value,
            "selected": sec_value,
            "selected_source": "SEC",
            "difference_percent": difference,
            "status": status,
            "confidence": (
                "HIGH"
                if status == "AGREE"
                else "REVIEW"
            ),
        }

    # ============================================================
    # DEBT
    # ============================================================

    def _debt_comparison(self, yahoo, sec):
        yahoo_long_term = yahoo.get("long_term_debt")
        yahoo_current = yahoo.get("current_debt")
        yahoo_leases = yahoo.get(
            "capital_lease_obligations"
        )
        yahoo_total = yahoo.get("total_debt")
        sec_total = sec.get("total_debt")

        underlying_yahoo_debt = None

        if (
            yahoo_long_term is not None
            and yahoo_current is not None
        ):
            underlying_yahoo_debt = (
                yahoo_long_term
                + yahoo_current
            )

        result = {
            "metric": "Total Debt",
            "yahoo_reported_total": yahoo_total,
            "yahoo_underlying_debt":
                underlying_yahoo_debt,
            "yahoo_long_term_debt":
                yahoo_long_term,
            "yahoo_current_debt":
                yahoo_current,
            "yahoo_capital_lease_obligations":
                yahoo_leases,
            "sec_reported_debt": sec_total,
        }

        if (
            underlying_yahoo_debt is not None
            and sec_total is not None
        ):
            difference = self._percent_difference(
                underlying_yahoo_debt,
                sec_total,
            )

            if abs(difference) <= 1.0:
                result.update({
                    "status":
                        "DEFINITION_DIFFERENCE",
                    "interpretation":
                        (
                            "Yahoo Total Debt includes "
                            "capital lease obligations. "
                            "Underlying debt excluding "
                            "leases agrees with SEC."
                        ),
                    "selected": sec_total,
                    "selected_source": "SEC",
                    "confidence": "HIGH",
                    "underlying_debt_difference_percent":
                        difference,
                })
                return result

        result.update({
            "status": "DISCREPANCY",
            "selected": (
                sec_total
                if sec_total is not None
                else yahoo_total
            ),
            "selected_source": (
                "SEC"
                if sec_total is not None
                else "Yahoo"
            ),
            "confidence": "REVIEW",
        })

        return result

    # ============================================================
    # CASH / LIQUIDITY
    # ============================================================

    def _cash_analysis(self, yahoo, sec):
        yahoo_cash = yahoo.get(
            "cash_and_equivalents"
        )
        yahoo_liquidity = yahoo.get(
            "total_cash_and_short_term_investments"
        )
        sec_cash = sec.get(
            "cash_and_equivalents"
        )

        cash_difference = self._percent_difference(
            yahoo_cash,
            sec_cash,
        )

        if cash_difference is not None:
            if abs(cash_difference) <= 1.0:
                status = "AGREE"
                confidence = "HIGH"
            else:
                status = (
                    "DEFINITION_OR_TIMING_DIFFERENCE"
                )
                confidence = "REVIEW"
        else:
            status = "INSUFFICIENT_DATA"
            confidence = "LOW"

        short_term_investments = None

        if (
            yahoo_liquidity is not None
            and yahoo_cash is not None
        ):
            short_term_investments = (
                yahoo_liquidity
                - yahoo_cash
            )

        return {
            "metric": "Cash & Liquidity",
            "sec_cash_and_equivalents":
                sec_cash,
            "yahoo_cash_and_equivalents":
                yahoo_cash,
            "yahoo_cash_plus_short_term_investments":
                yahoo_liquidity,
            "estimated_yahoo_short_term_investments":
                short_term_investments,
            "difference_percent":
                cash_difference,
            "status": status,
            "confidence": confidence,
            "selected_cash": (
                sec_cash
                if sec_cash is not None
                else yahoo_cash
            ),
            "selected_source": (
                "SEC"
                if sec_cash is not None
                else "Yahoo"
            ),
        }

    # ============================================================
    # BUILD VALIDATED DATASET
    # ============================================================

    def build(self, symbol):
        yahoo = self._yahoo_latest()
        sec = self._sec_latest()

        comparisons = [
            self._compare(
                "Revenue",
                yahoo["revenue"],
                sec["revenue"],
            ),
            self._compare(
                "Operating Cash Flow",
                yahoo["operating_cash_flow"],
                sec["operating_cash_flow"],
            ),
            self._compare(
                "Free Cash Flow",
                yahoo["free_cash_flow"],
                sec["free_cash_flow"],
            ),
            self._cash_analysis(
                yahoo,
                sec,
            ),
            self._debt_comparison(
                yahoo,
                sec,
            ),
            self._compare(
                "Shares Outstanding",
                yahoo["shares_outstanding"],
                sec["shares_outstanding"],
                tolerance=0.02,
            ),
        ]

        agreements = sum(
            1
            for item in comparisons
            if item["status"] == "AGREE"
        )

        definition_differences = sum(
            1
            for item in comparisons
            if item["status"]
            in (
                "DEFINITION_DIFFERENCE",
                "DEFINITION_OR_TIMING_DIFFERENCE",
            )
        )

        discrepancies = sum(
            1
            for item in comparisons
            if item["status"] == "DISCREPANCY"
        )

        insufficient = sum(
            1
            for item in comparisons
            if item["status"]
            == "INSUFFICIENT_DATA"
        )

        if (
            discrepancies == 0
            and agreements + definition_differences >= 5
        ):
            overall_confidence = "HIGH"
        elif discrepancies <= 1:
            overall_confidence = "MEDIUM"
        else:
            overall_confidence = "LOW"

        selected_cash = (
            sec["cash_and_equivalents"]
            if sec["cash_and_equivalents"]
            is not None
            else yahoo["cash_and_equivalents"]
        )

        selected_debt = (
            sec["total_debt"]
            if sec["total_debt"] is not None
            else yahoo["total_debt"]
        )

        selected_net_debt = None

        if (
            selected_cash is not None
            and selected_debt is not None
        ):
            selected_net_debt = (
                selected_debt
                - selected_cash
            )

        return {
            "ticker": symbol.upper(),
            "validation_version": self.VERSION,
            "validated_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "sources": {
                "primary_financial_source":
                    "SEC EDGAR",
                "secondary_cross_check":
                    "Yahoo Finance / yfinance",
            },

            "period":
                self._period_metadata(),

            "metrics":
                comparisons,

            "selected_financials": {
                "revenue":
                    sec["revenue"]
                    if sec["revenue"] is not None
                    else yahoo["revenue"],

                "operating_cash_flow":
                    sec["operating_cash_flow"]
                    if sec[
                        "operating_cash_flow"
                    ] is not None
                    else yahoo[
                        "operating_cash_flow"
                    ],

                "free_cash_flow":
                    sec["free_cash_flow"]
                    if sec[
                        "free_cash_flow"
                    ] is not None
                    else yahoo[
                        "free_cash_flow"
                    ],

                "cash_and_equivalents":
                    selected_cash,

                "total_debt":
                    selected_debt,

                "net_debt":
                    selected_net_debt,

                "shares_outstanding":
                    sec["shares_outstanding"]
                    if sec[
                        "shares_outstanding"
                    ] is not None
                    else yahoo[
                        "shares_outstanding"
                    ],
            },

            "liquidity": {
                "cash_and_equivalents":
                    yahoo[
                        "cash_and_equivalents"
                    ],

                "cash_plus_short_term_investments":
                    yahoo[
                        "total_cash_and_short_term_investments"
                    ],

                "estimated_short_term_investments":
                    (
                        yahoo[
                            "total_cash_and_short_term_investments"
                        ]
                        - yahoo[
                            "cash_and_equivalents"
                        ]
                        if (
                            yahoo[
                                "total_cash_and_short_term_investments"
                            ]
                            is not None
                            and yahoo[
                                "cash_and_equivalents"
                            ] is not None
                        )
                        else None
                    ),
            },

            "debt": {
                "sec_debt":
                    sec["total_debt"],

                "yahoo_long_term_debt":
                    yahoo["long_term_debt"],

                "yahoo_current_debt":
                    yahoo["current_debt"],

                "yahoo_capital_lease_obligations":
                    yahoo[
                        "capital_lease_obligations"
                    ],

                "yahoo_total_debt":
                    yahoo["total_debt"],

                "selected_debt":
                    selected_debt,

                "selected_debt_source":
                    "SEC",
            },

            "summary": {
                "metrics_checked":
                    len(comparisons),

                "agreements":
                    agreements,

                "definition_differences":
                    definition_differences,

                "discrepancies":
                    discrepancies,

                "insufficient_data":
                    insufficient,

                "overall_confidence":
                    overall_confidence,
            },

            "methodology": {
                "primary_source_rule":
                    (
                        "SEC is preferred for "
                        "historical reported "
                        "financial statement values."
                    ),

                "secondary_source_rule":
                    (
                        "Yahoo is retained as "
                        "a secondary cross-check."
                    ),

                "definition_rule":
                    (
                        "Differences caused by "
                        "classification, timing "
                        "or scope are recorded "
                        "rather than automatically "
                        "treated as source errors."
                    ),

                "debt_rule":
                    (
                        "Debt and capital lease "
                        "obligations are kept "
                        "separate where the "
                        "source provides the "
                        "necessary fields."
                    ),

                "liquidity_rule":
                    (
                        "Cash and cash equivalents "
                        "are distinguished from "
                        "cash plus short-term "
                        "investments."
                    ),
            },
        }
