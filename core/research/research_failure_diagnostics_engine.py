from __future__ import annotations

"""Explain why research cannot yet support a portfolio decision.

The output is operational: every issue includes the affected system component,
whether it blocks a holding or only lowers conviction, and a concrete next
evidence/data-source action.  It never invents missing data.
"""

from typing import Any

from core.research.research_contract import ResearchContract


class ResearchFailureDiagnosticsEngine:
    VERSION = "1.0-actionable-research-diagnostics"

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def analyse(cls, result: Any) -> dict[str, Any]:
        raw = cls.mapping(result)
        canonical = ResearchContract.from_pipeline_result(raw)
        issues: list[dict[str, Any]] = []

        def add(component: str, severity: str, finding: str, action: str, sources: list[str]):
            issues.append(
                {
                    "component": component,
                    "severity": severity,
                    "finding": finding,
                    "recommended_action": action,
                    "recommended_sources": sources,
                }
            )

        audit = cls.mapping(canonical.get("audit"))
        if audit.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}:
            add(
                "evidence_audit",
                "BLOCKER",
                "Evidence audit has not cleared the research.",
                "Resolve the listed metric discrepancy before using the company in a portfolio.",
                ["SEC EDGAR filings", "Company annual and quarterly reports", "Independent market-data provider"],
            )

        valuation_quality = cls.mapping(canonical.get("valuation_quality"))
        if valuation_quality.get("assessment") == "FAIL":
            add(
                "valuation",
                "BLOCKER",
                "Valuation inputs are structurally incomplete or invalid.",
                "Refresh price, share count, free-cash-flow and independently validated forecasts.",
                ["SEC EDGAR filings", "Company earnings releases", "Independent estimates provider"],
            )
        elif valuation_quality.get("assessment") == "REVIEW":
            add(
                "valuation",
                "CONVICTION_PENALTY",
                "Valuation has limited forecast validation or high terminal-value dependence.",
                "Add an independent forecast source and compare near-term revenue and earnings assumptions.",
                ["Company guidance", "Earnings-call transcript", "Independent estimates provider"],
            )

        thesis = cls.mapping(canonical.get("thesis"))
        material_negative = thesis.get("material_negative") or 0
        if thesis.get("thesis_survives") is False:
            add(
                "thesis_challenge",
                "BLOCKER" if material_negative >= 3 else "CONVICTION_PENALTY",
                "The adversarial thesis review identified unresolved bearish evidence.",
                "Review the challenge findings and define an observable condition that would resolve each concern.",
                ["SEC EDGAR risk factors", "Company earnings calls", "Industry and competitor evidence"],
            )

        expected_return = canonical.get("expected_return")
        if expected_return is not None and float(expected_return) < 0.05:
            add(
                "expected_return",
                "CONVICTION_PENALTY",
                "Modelled expected return is below the prototype preference threshold.",
                "Monitor valuation, estimate revisions and catalysts rather than forcing a larger position.",
                ["Current market price", "Company guidance", "Independent estimates provider"],
            )

        signals = cls.mapping(canonical.get("market_signals"))
        if signals.get("technical_score") is None or signals.get("risk_score") is None:
            add(
                "market_signals",
                "CONVICTION_PENALTY",
                "Technical or risk signal is unavailable.",
                "Refresh price history and benchmark data before assigning normal position size.",
                ["Daily adjusted-price history", "Market benchmark data"],
            )

        specialists = cls.mapping(canonical.get("specialist_research"))
        if specialists.get("status") != "COMPLETE":
            add(
                "specialist_research",
                "CONVICTION_PENALTY",
                "One or more specialist research bots did not complete.",
                "Retry only the failed specialist and retain its error as coverage evidence.",
                ["Company filings", "Earnings calendar", "Peer-company market data"],
            )

        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "ticker": canonical.get("ticker"),
            "issue_count": len(issues),
            "blocker_count": sum(issue["severity"] == "BLOCKER" for issue in issues),
            "issues": issues,
        }
