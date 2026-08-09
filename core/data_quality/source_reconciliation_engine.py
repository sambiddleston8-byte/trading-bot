from core.data_quality.source_escalation_engine import SourceEscalationEngine
from datetime import datetime, timezone


class SourceReconciliationEngine:

    VERSION = "1.0"

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self):
        pass

    # ========================================================
    # TIME
    # ========================================================

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # NUMBER
    # ========================================================

    @staticmethod
    def number(value):

        try:

            if value is None:
                return None

            value = float(value)

            if value != value:
                return None

            return value

        except (
            TypeError,
            ValueError,
        ):

            return None

    # ========================================================
    # DIFFERENCE
    # ========================================================

    @classmethod
    def difference_percent(
        cls,
        first,
        second,
    ):

        first = cls.number(first)
        second = cls.number(second)

        if (
            first is None
            or second is None
        ):
            return None

        denominator = max(
            abs(first),
            abs(second),
        )

        if denominator == 0:

            return 0.0

        return (
            abs(first - second)
            / denominator
        )

    # ========================================================
    # BASIC RECONCILIATION
    # ========================================================

    @classmethod
    def reconcile(
        cls,
        field,
        sources,
        selected_source=None,
        selected_value=None,
        reason=None,
    ):

        cleaned = {}

        for name, value in sources.items():

            cleaned[name] = cls.number(
                value
            )

        available = {
            name: value
            for name, value
            in cleaned.items()
            if value is not None
        }

        if not available:

            return {
                "field": field,
                "status": "INSUFFICIENT_DATA",
                "confidence": "LOW",
                "sources": cleaned,
                "selected": None,
                "selected_source": None,
                "reason": (
                    "No usable source values "
                    "were available."
                ),
                "reconciled_at": cls.now(),
            }

        if len(available) == 1:

            source = next(
                iter(available)
            )

            return {
                "field": field,
                "status": "SINGLE_SOURCE",
                "confidence": "MEDIUM",
                "sources": cleaned,
                "selected":
                    available[source],
                "selected_source":
                    source,
                "reason": (
                    "Only one usable source "
                    "value was available."
                ),
                "reconciled_at": cls.now(),
            }

        values = list(
            available.values()
        )

        minimum = min(values)
        maximum = max(values)

        difference = (
            cls.difference_percent(
                minimum,
                maximum,
            )
        )

        # ----------------------------------------------------
        # Automatically agree when sources are effectively
        # identical.
        # ----------------------------------------------------

        if (
            difference is not None
            and difference <= 0.02
        ):

            if selected_source is None:

                selected_source = next(
                    iter(available)
                )

            selected_value = (
                available.get(
                    selected_source
                )
            )

            return {
                "field": field,
                "status": "AGREED",
                "confidence": "HIGH",
                "sources": cleaned,
                "difference_percent":
                    difference,
                "selected":
                    selected_value,
                "selected_source":
                    selected_source,
                "reason": (
                    reason
                    or
                    "Independent source values "
                    "agree within tolerance."
                ),
                "reconciled_at": cls.now(),
            }

        # ----------------------------------------------------
        # If an explicit resolution has been supplied,
        # preserve it rather than hiding the disagreement.
        # ----------------------------------------------------

        if (
            selected_source is not None
            and selected_value is not None
        ):

            return {
                "field": field,
                "status": "RESOLVED",
                "confidence": "HIGH",
                "sources": cleaned,
                "difference_percent":
                    difference,
                "selected":
                    cls.number(
                        selected_value
                    ),
                "selected_source":
                    selected_source,
                "reason":
                    reason
                    or
                    "Source discrepancy was "
                    "resolved using supporting evidence.",
                "reconciled_at": cls.now(),
            }

        # ----------------------------------------------------
        # No defensible resolution.
        #
        # Automatically escalate the discrepancy so the next
        # layer knows which additional sources should be
        # investigated.
        # ----------------------------------------------------

        escalation = (
            SourceEscalationEngine
            .assess(
                field,
                {
                    "status":
                        "UNRESOLVED",

                    "confidence":
                        "REVIEW",
                },
            )
        )

        return {
            "field": field,
            "status": "UNRESOLVED",
            "confidence": "REVIEW",
            "sources": cleaned,
            "difference_percent":
                difference,
            "selected": None,
            "selected_source": None,
            "reason": (
                "Sources disagree materially and "
                "no defensible resolution has "
                "yet been established."
            ),
            "escalation":
                escalation,
            "reconciled_at": cls.now(),
        }

    # ========================================================
    # DEBT RECONCILIATION
    # ========================================================

    @classmethod
    def reconcile_debt(
        cls,
        sec_debt,
        yahoo_total_debt,
        yahoo_long_term_debt,
        yahoo_current_debt,
        yahoo_leases,
        sec_noncurrent_debt=None,
        sec_current_debt=None,
    ):

        sec_debt = cls.number(
            sec_debt
        )

        yahoo_total_debt = cls.number(
            yahoo_total_debt
        )

        yahoo_long_term_debt = cls.number(
            yahoo_long_term_debt
        )

        yahoo_current_debt = cls.number(
            yahoo_current_debt
        )

        yahoo_leases = cls.number(
            yahoo_leases
        )

        sec_noncurrent_debt = cls.number(
            sec_noncurrent_debt
        )

        sec_current_debt = cls.number(
            sec_current_debt
        )

        underlying_yahoo_debt = None

        if (
            yahoo_long_term_debt is not None
            and yahoo_current_debt is not None
        ):

            underlying_yahoo_debt = (
                yahoo_long_term_debt
                + yahoo_current_debt
            )

        elif yahoo_long_term_debt is not None:

            # yfinance does not consistently expose a separate current-debt
            # field.  Keep the disclosed long-term amount as a comparable
            # component instead of treating it as an invented zero balance.
            underlying_yahoo_debt = yahoo_long_term_debt

        sources = {
            "SEC EDGAR":
                sec_debt,
            "Yahoo Finance":
                yahoo_total_debt,
        }

        # ----------------------------------------------------
        # First check whether Yahoo's underlying debt
        # excluding leases agrees with SEC.
        # ----------------------------------------------------

        component_difference = None

        if (
            yahoo_long_term_debt is not None
            and sec_noncurrent_debt is not None
        ):

            component_difference = cls.difference_percent(
                yahoo_long_term_debt,
                sec_noncurrent_debt,
            )

            # If Yahoo's Total Debt is explained by its disclosed long-term
            # debt plus lease obligations, compare that long-term component
            # with SEC non-current debt.  This is the common Amazon-style
            # presentation where Yahoo omits the current debt component while
            # including finance leases in Total Debt.
            yahoo_total_explained_by_leases = (
                yahoo_total_debt is not None
                and yahoo_leases is not None
                and cls.difference_percent(
                    yahoo_total_debt,
                    yahoo_long_term_debt + yahoo_leases,
                ) is not None
                and cls.difference_percent(
                    yahoo_total_debt,
                    yahoo_long_term_debt + yahoo_leases,
                ) <= 0.02
            )

            if (
                yahoo_total_explained_by_leases
                and component_difference is not None
                and component_difference <= 0.02
            ):

                return {
                    "field": "Total Debt",
                    "status": "RESOLVED_DEFINITION_DIFFERENCE",
                    "confidence": "HIGH",
                    "sources": sources,
                    "supporting_values": {
                        "yahoo_underlying_debt": underlying_yahoo_debt,
                        "yahoo_capital_lease_obligations": yahoo_leases,
                        "sec_debt": sec_debt,
                        "sec_noncurrent_debt": sec_noncurrent_debt,
                        "sec_current_debt": sec_current_debt,
                    },
                    "selected": sec_debt,
                    "selected_source": "SEC EDGAR",
                    "reason": (
                        "Yahoo Finance Total Debt is explained by long-term debt "
                        "plus finance leases, while its disclosed long-term debt "
                        "agrees with SEC non-current debt. SEC total debt is used "
                        "because it separately includes the current debt component."
                    ),
                    "underlying_difference_percent": component_difference,
                    "reconciled_at": cls.now(),
                }

        if (
            underlying_yahoo_debt is not None
            and sec_debt is not None
        ):

            underlying_difference = (
                cls.difference_percent(
                    underlying_yahoo_debt,
                    sec_debt,
                )
            )

            if (
                underlying_difference is not None
                and underlying_difference <= 0.02
            ):

                return {
                    "field":
                        "Total Debt",

                    "status":
                        "RESOLVED_DEFINITION_DIFFERENCE",

                    "confidence":
                        "HIGH",

                    "sources":
                        sources,

                    "supporting_values": {
                        "yahoo_underlying_debt":
                            underlying_yahoo_debt,

                        "yahoo_capital_lease_obligations":
                            yahoo_leases,

                        "sec_debt":
                            sec_debt,

                        "sec_noncurrent_debt":
                            sec_noncurrent_debt,

                        "sec_current_debt":
                            sec_current_debt,
                    },

                    "selected":
                        sec_debt,

                    "selected_source":
                        "SEC EDGAR",

                    "reason": (
                        "Yahoo Finance's reported total "
                        "debt differs from SEC debt, but "
                        "Yahoo's underlying long-term plus "
                        "current debt agrees with SEC within "
                        "2%. The difference is therefore "
                        "consistent with Yahoo including "
                        "capital lease obligations."
                    ),

                    "underlying_difference_percent":
                        underlying_difference,

                    "reconciled_at":
                        cls.now(),
                }

        # ----------------------------------------------------
        # Otherwise the discrepancy remains unresolved.
        # ----------------------------------------------------

        result = cls.reconcile(
            field="Total Debt",
            sources=sources,
        )

        result["supporting_values"] = {
            "yahoo_underlying_debt":
                underlying_yahoo_debt,

            "yahoo_long_term_debt":
                yahoo_long_term_debt,

            "yahoo_current_debt":
                yahoo_current_debt,

            "yahoo_capital_lease_obligations":
                yahoo_leases,

            "sec_noncurrent_debt":
                sec_noncurrent_debt,

            "sec_current_debt":
                sec_current_debt,
        }

        return result

    # ========================================================
    # MULTI-SOURCE RECONCILIATION
    # ========================================================

    @classmethod
    def reconcile_many(
        cls,
        field,
        sources,
    ):

        return cls.reconcile(
            field=field,
            sources=sources,
        )


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("SOURCE RECONCILIATION ENGINE TEST")
    print("=" * 80)

    result = (
        SourceReconciliationEngine
        .reconcile_debt(
            sec_debt=8468000000,
            yahoo_total_debt=11040000000,
            yahoo_long_term_debt=8468000000,
            yahoo_current_debt=0,
            yahoo_leases=1940000000,
        )
    )

    print()
    print(result)

    print()
    print(
        "STATUS:",
        result["status"],
    )

    print(
        "CONFIDENCE:",
        result["confidence"],
    )

    print(
        "SELECTED:",
        result["selected"],
    )

    print(
        "SELECTED SOURCE:",
        result["selected_source"],
    )

    print()
    print(
        "SOURCE RECONCILIATION ENGINE OK"
    )
