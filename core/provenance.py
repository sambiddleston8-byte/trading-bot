from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class Provenance:

    @staticmethod
    def now():
        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def source(
        name,
        source_type,
        retrieved_at=None,
        period=None,
        field=None,
        url=None,
    ):

        return {
            "name": name,
            "type": source_type,
            "retrieved_at": (
                retrieved_at
                or Provenance.now()
            ),
            "period": period,
            "field": field,
            "url": url,
        }

    @staticmethod
    def reported(
        value,
        source,
        field=None,
        period=None,
        confidence="HIGH",
    ):

        return {
            "value": value,
            "provenance": {
                **source,
                "field": (
                    field
                    or source.get("field")
                ),
                "period": (
                    period
                    or source.get("period")
                ),
            },
            "calculation": None,
            "status": "REPORTED",
            "confidence": confidence,
        }

    @staticmethod
    def calculated(
        value,
        method,
        inputs,
        sources=None,
        confidence="HIGH",
    ):

        return {
            "value": value,
            "provenance": {
                "name": "Trading Bot",
                "type": "CALCULATION",
                "retrieved_at": (
                    Provenance.now()
                ),
                "field": None,
                "period": None,
            },
            "calculation": {
                "method": method,
                "inputs": inputs,
                "sources": sources or [],
            },
            "status": "CALCULATED",
            "confidence": confidence,
        }

    @staticmethod
    def validated(
        value,
        primary_source,
        secondary_source=None,
        status="AGREE",
        confidence="HIGH",
        notes=None,
    ):

        sources = [
            primary_source
        ]

        if secondary_source is not None:

            sources.append(
                secondary_source
            )

        return {
            "value": value,
            "provenance": {
                "name": primary_source.get(
                    "name"
                ),
                "type": primary_source.get(
                    "type"
                ),
                "retrieved_at": primary_source.get(
                    "retrieved_at"
                ),
                "field": primary_source.get(
                    "field"
                ),
                "period": primary_source.get(
                    "period"
                ),
            },
            "validation": {
                "status": status,
                "confidence": confidence,
                "sources": sources,
                "notes": notes,
            },
            "calculation": None,
        }


class ProvenanceBuilder:

    def __init__(self):

        self.fields = {}

    def add_reported(
        self,
        name,
        value,
        source_name,
        source_type,
        field=None,
        period=None,
        url=None,
        confidence="HIGH",
    ):

        source = Provenance.source(
            name=source_name,
            source_type=source_type,
            field=field,
            period=period,
            url=url,
        )

        self.fields[name] = (
            Provenance.reported(
                value=value,
                source=source,
                field=field,
                period=period,
                confidence=confidence,
            )
        )

        return self

    def add_calculated(
        self,
        name,
        value,
        method,
        inputs,
        sources=None,
        confidence="HIGH",
    ):

        self.fields[name] = (
            Provenance.calculated(
                value=value,
                method=method,
                inputs=inputs,
                sources=sources,
                confidence=confidence,
            )
        )

        return self

    def add_validated(
        self,
        name,
        value,
        primary_source,
        secondary_source=None,
        status="AGREE",
        confidence="HIGH",
        notes=None,
    ):

        self.fields[name] = (
            Provenance.validated(
                value=value,
                primary_source=primary_source,
                secondary_source=secondary_source,
                status=status,
                confidence=confidence,
                notes=notes,
            )
        )

        return self

    def build(self):

        return self.fields
