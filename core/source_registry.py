from __future__ import annotations


class SourceRegistry:

    SOURCES = {
        "SEC EDGAR": {
            "name": "SEC EDGAR",
            "type": "PRIMARY",
            "website": "https://www.sec.gov/edgar.shtml",
            "description": (
                "Primary regulatory filings and "
                "company financial data."
            ),
        },

        "Yahoo Finance": {
            "name": "Yahoo Finance",
            "type": "SECONDARY",
            "website": "https://finance.yahoo.com/",
            "description": (
                "Market data, financial statements "
                "and analyst consensus estimates."
            ),
        },

        "Trading Bot": {
            "name": "Trading Bot",
            "type": "CALCULATION",
            "website": None,
            "description": (
                "Metric calculated by the investment "
                "analysis system."
            ),
        },
    }

    @classmethod
    def get(cls, name):

        return cls.SOURCES.get(
            name,
            {
                "name": name,
                "type": "UNKNOWN",
                "website": None,
                "description": None,
            },
        )

    @classmethod
    def all(cls):

        return cls.SOURCES.copy()

    @classmethod
    def enrich(cls, provenance):

        if not isinstance(
            provenance,
            dict,
        ):

            return provenance

        result = dict(
            provenance
        )

        source_name = (
            result
            .get("provenance", {})
            .get("name")
        )

        if source_name:

            result[
                "source"
            ] = cls.get(
                source_name
            )

        validation = result.get(
            "validation"
        )

        if isinstance(
            validation,
            dict,
        ):

            sources = []

            for source in validation.get(
                "sources",
                [],
            ):

                if isinstance(
                    source,
                    dict,
                ):

                    name = source.get(
                        "name"
                    )

                    sources.append(
                        cls.get(name)
                    )

            result[
                "validation"
            ] = {
                **validation,
                "source_registry":
                    sources,
            }

        calculation = result.get(
            "calculation"
        )

        if isinstance(
            calculation,
            dict,
        ):

            source_registry = []

            for source in calculation.get(
                "sources",
                [],
            ):

                source_registry.append(
                    cls.get(source)
                )

            result[
                "calculation"
            ] = {
                **calculation,
                "source_registry":
                    source_registry,
            }

        return result
