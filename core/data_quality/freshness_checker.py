from datetime import datetime, timezone


class FreshnessChecker:

    THRESHOLDS = {
        "SEC": {
            "fresh_hours": 24 * 30,
            "aging_hours": 24 * 90,
            "stale_hours": 24 * 180,
        },

        "YAHOO": {
            "fresh_hours": 24,
            "aging_hours": 24 * 3,
            "stale_hours": 24 * 7,
        },

        "ANALYST_CONSENSUS": {
            "fresh_hours": 24,
            "aging_hours": 24 * 3,
            "stale_hours": 24 * 7,
        },

        "CALCULATION": {
            "fresh_hours": 24,
            "aging_hours": 24 * 7,
            "stale_hours": 24 * 30,
        },
    }

    @classmethod
    def _source_key(cls, source):

        return str(
            source
        ).upper()

    @classmethod
    def _parse_timestamp(
        cls,
        timestamp,
    ):

        if timestamp is None:
            return None

        try:

            parsed = datetime.fromisoformat(
                str(timestamp).replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed.tzinfo is None:

                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed

        except (
            TypeError,
            ValueError,
        ):

            return None

    @classmethod
    def age_hours(
        cls,
        retrieved_at,
        now=None,
    ):

        timestamp = (
            cls._parse_timestamp(
                retrieved_at
            )
        )

        if timestamp is None:
            return None

        if now is None:

            now = datetime.now(
                timezone.utc
            )

        return (
            now - timestamp
        ).total_seconds() / 3600

    @classmethod
    def check(
        cls,
        source,
        retrieved_at,
        now=None,
    ):

        source_key = (
            cls._source_key(source)
        )

        thresholds = (
            cls.THRESHOLDS.get(
                source_key
            )
        )

        if thresholds is None:

            return {
                "status": "UNKNOWN_SOURCE",
                "age_hours": None,
                "source": source,
            }

        age = cls.age_hours(
            retrieved_at,
            now,
        )

        if age is None:

            return {
                "status": "MISSING_TIMESTAMP",
                "age_hours": None,
                "source": source,
            }

        if age < 0:

            return {
                "status": "FUTURE_TIMESTAMP",
                "age_hours": age,
                "source": source,
            }

        if (
            age
            <= thresholds["fresh_hours"]
        ):

            status = "FRESH"

        elif (
            age
            <= thresholds["aging_hours"]
        ):

            status = "AGING"

        elif (
            age
            <= thresholds["stale_hours"]
        ):

            status = "STALE"

        else:

            status = "VERY_STALE"

        return {
            "status": status,
            "age_hours": age,
            "source": source,
            "fresh_threshold_hours":
                thresholds["fresh_hours"],
            "aging_threshold_hours":
                thresholds["aging_hours"],
            "stale_threshold_hours":
                thresholds["stale_hours"],
        }

    @classmethod
    def confidence(
        cls,
        checks,
    ):

        if not checks:

            return "UNKNOWN"

        statuses = [
            item.get("status")
            for item in checks
        ]

        if "VERY_STALE" in statuses:

            return "LOW"

        if "STALE" in statuses:

            return "LOW"

        if "AGING" in statuses:

            return "MEDIUM"

        if all(
            status == "FRESH"
            for status in statuses
        ):

            return "HIGH"

        return "REVIEW"

    @classmethod
    def validate(
        cls,
        records,
        now=None,
    ):

        checks = []

        for record in records:

            check = cls.check(
                source=record.get(
                    "source"
                ),
                retrieved_at=record.get(
                    "retrieved_at"
                ),
                now=now,
            )

            checks.append(
                check
            )

        return {
            "overall_confidence":
                cls.confidence(checks),

            "records_checked":
                len(checks),

            "checks":
                checks,
        }


if __name__ == "__main__":

    now = datetime.now(
        timezone.utc
    )

    result = FreshnessChecker.validate(
        [
            {
                "source": "SEC",
                "retrieved_at":
                    now.isoformat(),
            },
            {
                "source": "YAHOO",
                "retrieved_at":
                    now.isoformat(),
            },
        ],
        now=now,
    )

    print()
    print("=" * 80)
    print("FRESHNESS CHECKER TEST")
    print("=" * 80)

    for item in result["checks"]:

        print(
            item["source"],
            "=>",
            item["status"],
        )

    print(
        "Overall:",
        result["overall_confidence"],
    )

    print()
    print("FRESHNESS CHECKER OK")
