from math import isfinite


class ConflictDetector:

    DEFAULT_TOLERANCES = {
        "revenue": 0.02,
        "operating_cash_flow": 0.02,
        "free_cash_flow": 0.02,
        "cash_and_equivalents": 0.02,
        "total_debt": 0.05,
        "shares_outstanding": 0.01,
        "current_price": 0.005,
        "market_cap": 0.02,
    }

    @classmethod
    def number(cls, value):

        try:

            if value is None:
                return None

            value = float(value)

            if not isfinite(value):
                return None

            return value

        except (
            TypeError,
            ValueError,
        ):

            return None

    @classmethod
    def difference_percent(
        cls,
        first,
        second,
    ):

        first = cls.number(first)
        second = cls.number(second)

        if first is None or second is None:
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

    @classmethod
    def compare(
        cls,
        field,
        first_source,
        first_value,
        second_source,
        second_value,
        tolerance=None,
    ):

        first = cls.number(first_value)
        second = cls.number(second_value)

        if tolerance is None:

            tolerance = (
                cls.DEFAULT_TOLERANCES
                .get(
                    field,
                    0.02,
                )
            )

        difference = (
            cls.difference_percent(
                first,
                second,
            )
        )

        if first is None or second is None:

            status = "INSUFFICIENT_DATA"

        elif difference <= tolerance:

            status = "AGREE"

        else:

            status = "DISCREPANCY"

        return {

            "field": field,

            "first": {
                "source": first_source,
                "value": first,
            },

            "second": {
                "source": second_source,
                "value": second,
            },

            "difference_percent":
                difference,

            "tolerance":
                tolerance,

            "status":
                status,
        }

    @classmethod
    def compare_many(
        cls,
        field_values,
        tolerance=None,
    ):

        """
        field_values format:

        {
            "SEC": 100,
            "Yahoo": 101,
            "Other": 99,
        }
        """

        sources = list(
            field_values.keys()
        )

        comparisons = []

        if len(sources) < 2:

            return {
                "field": None,
                "status":
                    "INSUFFICIENT_SOURCES",
                "comparisons": [],
            }

        for index in range(
            len(sources)
        ):

            for other_index in range(
                index + 1,
                len(sources),
            ):

                first_source = (
                    sources[index]
                )

                second_source = (
                    sources[other_index]
                )

                comparison = (
                    cls.compare(
                        field=None,
                        first_source=
                            first_source,
                        first_value=
                            field_values[
                                first_source
                            ],
                        second_source=
                            second_source,
                        second_value=
                            field_values[
                                second_source
                            ],
                        tolerance=tolerance,
                    )
                )

                comparisons.append(
                    comparison
                )

        statuses = [
            item["status"]
            for item in comparisons
        ]

        if "DISCREPANCY" in statuses:

            overall = "DISCREPANCY"

        elif (
            statuses
            and all(
                status == "AGREE"
                for status in statuses
            )
        ):

            overall = "AGREE"

        else:

            overall = "INSUFFICIENT_DATA"

        return {
            "field": None,
            "status": overall,
            "comparisons": comparisons,
        }


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("CONFLICT DETECTOR TEST")
    print("=" * 80)

    result = ConflictDetector.compare(
        field="revenue",
        first_source="SEC",
        first_value=100,
        second_source="Yahoo",
        second_value=101,
    )

    print(result)

    result = ConflictDetector.compare(
        field="total_debt",
        first_source="SEC",
        first_value=8468000000,
        second_source="Yahoo",
        second_value=11040000000,
    )

    print(result)

    print()
    print("CONFLICT DETECTOR OK")
