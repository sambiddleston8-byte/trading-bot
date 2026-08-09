from datetime import datetime, timezone


class SourceRegistry:

    SOURCES = {

        "SEC": {
            "name": "SEC EDGAR",
            "type": "PRIMARY",
            "tier": 1,
            "role": "Historical reported financial statements",
            "freshness_hours": 24 * 30,
            "authoritative_fields": {
                "revenue",
                "operating_income",
                "operating_cash_flow",
                "free_cash_flow",
                "cash_and_equivalents",
                "total_debt",
                "shares_outstanding",
                "equity",
            },
        },

        "YAHOO": {
            "name": "Yahoo Finance",
            "type": "SECONDARY",
            "tier": 2,
            "role": "Market data and independent financial cross-check",
            "freshness_hours": 24,
            "authoritative_fields": {
                "current_price",
                "market_cap",
                "beta",
                "analyst_consensus",
                "historical_prices",
            },
        },

        "ANALYST_CONSENSUS": {
            "name": "Yahoo Finance Analyst Consensus",
            "type": "CONSENSUS",
            "tier": 2,
            "role": "Forward revenue and earnings expectations",
            "freshness_hours": 24,
            "authoritative_fields": {
                "forward_revenue_growth",
                "forward_eps_growth",
                "eps_estimate",
                "revenue_estimate",
            },
        },

        "FMP": {
            "name": "Financial Modeling Prep",
            "type": "SECONDARY",
            "tier": 2,
            "role": "Independent financial-statement and analyst-estimate cross-check",
            "freshness_hours": 24,
            "authoritative_fields": {
                "analyst_consensus",
                "revenue_estimate",
                "eps_estimate",
                "historical_prices",
            },
        },

        "ALPHA_VANTAGE": {
            "name": "Alpha Vantage",
            "type": "SECONDARY",
            "tier": 2,
            "role": "Earnings transcripts, calendar and independent financial-data cross-check",
            "freshness_hours": 24,
            "authoritative_fields": {
                "earnings_transcript",
                "earnings_calendar",
            },
        },

        "POLYGON": {
            "name": "Massive (formerly Polygon)",
            "type": "MARKET_DATA",
            "tier": 2,
            "role": "Independent historical price, volume and liquidity data",
            "freshness_hours": 24,
            "authoritative_fields": {
                "current_price",
                "historical_prices",
                "volume",
                "liquidity",
            },
        },

        "FRED": {
            "name": "Federal Reserve Economic Data",
            "type": "MACRO",
            "tier": 1,
            "role": "Macroeconomic rates, inflation, credit and recession indicators",
            "freshness_hours": 24 * 7,
            "authoritative_fields": {
                "macro_rates",
                "inflation",
                "credit_conditions",
            },
        },

        "CALCULATION": {
            "name": "Trading Bot",
            "type": "CALCULATION",
            "tier": 1,
            "role": "Derived metrics calculated from validated inputs",
            "freshness_hours": 24,
            "authoritative_fields": set(),
        },

    }

    @classmethod
    def get(cls, source):

        if source is None:
            return None

        key = str(source).upper()

        return cls.SOURCES.get(key)

    @classmethod
    def exists(cls, source):

        return cls.get(source) is not None

    @classmethod
    def describe(cls, source):

        record = cls.get(source)

        if record is None:
            return None

        return {
            "name": record["name"],
            "type": record["type"],
            "tier": record["tier"],
            "role": record["role"],
            "freshness_hours": record["freshness_hours"],
        }

    @classmethod
    def is_authoritative(
        cls,
        source,
        field,
    ):

        record = cls.get(source)

        if record is None:
            return False

        return (
            field
            in record["authoritative_fields"]
        )

    @classmethod
    def freshness_status(
        cls,
        source,
        retrieved_at,
        now=None,
    ):

        record = cls.get(source)

        if record is None:
            return {
                "status": "UNKNOWN_SOURCE",
                "age_hours": None,
                "max_age_hours": None,
            }

        if retrieved_at is None:
            return {
                "status": "MISSING_TIMESTAMP",
                "age_hours": None,
                "max_age_hours":
                    record["freshness_hours"],
            }

        try:

            if now is None:
                now = datetime.now(
                    timezone.utc
                )

            timestamp = (
                datetime.fromisoformat(
                    str(retrieved_at)
                    .replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(
                    tzinfo=timezone.utc
                )

            age_seconds = (
                now - timestamp
            ).total_seconds()

            age_hours = (
                age_seconds / 3600
            )

            if age_hours < 0:
                return {
                    "status": "FUTURE_TIMESTAMP",
                    "age_hours": age_hours,
                    "max_age_hours":
                        record["freshness_hours"],
                }

            if (
                age_hours
                <= record["freshness_hours"]
            ):

                status = "FRESH"

            else:

                status = "STALE"

            return {
                "status": status,
                "age_hours": age_hours,
                "max_age_hours":
                    record["freshness_hours"],
            }

        except (
            TypeError,
            ValueError,
        ):

            return {
                "status": "INVALID_TIMESTAMP",
                "age_hours": None,
                "max_age_hours":
                    record["freshness_hours"],
            }

    @classmethod
    def validate_record(
        cls,
        source,
        field,
        retrieved_at,
    ):

        source_record = cls.get(source)

        if source_record is None:

            return {
                "valid": False,
                "reason": "UNKNOWN_SOURCE",
            }

        freshness = (
            cls.freshness_status(
                source,
                retrieved_at,
            )
        )

        return {
            "valid":
                freshness["status"]
                == "FRESH",
            "source":
                source_record["name"],
            "source_type":
                source_record["type"],
            "tier":
                source_record["tier"],
            "field":
                field,
            "authoritative":
                cls.is_authoritative(
                    source,
                    field,
                ),
            "freshness":
                freshness,
        }


if __name__ == "__main__":

    now = datetime.now(
        timezone.utc
    )

    result = (
        SourceRegistry.validate_record(
            "SEC",
            "revenue",
            now.isoformat(),
        )
    )

    print()
    print("=" * 80)
    print("SOURCE REGISTRY TEST")
    print("=" * 80)
    print(result)
    print()
    print("SOURCE REGISTRY OK")
