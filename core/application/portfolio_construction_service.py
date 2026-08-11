from __future__ import annotations

import json
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.portfolio.portfolio_engine import PortfolioEngine
from core.portfolio.portfolio_watchlist_engine import PortfolioWatchlistEngine
from core.application.portfolio_risk_review_service import PortfolioRiskReviewService
from core.application.portfolio_benchmark_service import PortfolioBenchmarkService
from core.research.research_contract import ResearchContract
from core.research.master_portfolio_decision_engine import MasterPortfolioDecisionEngine
from core.research.investment_research_pipeline import InvestmentResearchPipeline
from core.decision_ledger import (
    InvestmentDecision,
    InvestmentDecisionLedger,
    ModelVersion,
)


class PortfolioConstructionService:
    """Build a traceable portfolio directly from every saved research record."""

    MIN_CONSTRUCTIBLE_HOLDINGS = 5
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    PIPELINE_DIRECTORY = PROJECT_ROOT / "data" / "research" / "pipeline"
    UNIVERSE_PATH = PROJECT_ROOT / "data" / "research" / "universe" / "both.json"
    PORTFOLIO_DIRECTORY = PROJECT_ROOT / "data" / "research" / "portfolios"
    DECISION_LEDGER_PATH = PROJECT_ROOT / "data" / "decision_ledger.jsonl"

    @staticmethod
    def timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def portfolio_snapshot_id(cls, portfolio: dict[str, Any]) -> str:
        material = {
            "created_at": portfolio.get("created_at"),
            "holdings": [
                {"ticker": item.get("ticker"), "weight": item.get("weight")}
                for item in portfolio.get("holdings", [])
            ],
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12].upper()
        timestamp = str(portfolio.get("created_at") or cls.timestamp())
        compact = "".join(character for character in timestamp if character.isdigit())[:14]
        return f"PORT-{compact}-{digest}"

    @staticmethod
    def confidence_label(value: float | None) -> str:
        if value is None:
            return "UNRATED"
        if value >= 80:
            return "VERY_STRONG"
        if value >= 65:
            return "STRONG"
        if value >= 50:
            return "MODERATE"
        return "LIMITED"

    @classmethod
    def load_json(cls, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def latest_portfolio(cls) -> tuple[dict[str, Any] | None, Path | None]:
        """Load the newest constructed portfolio for the portfolio-first UI."""
        paths = sorted(
            cls.PORTFOLIO_DIRECTORY.glob("research_portfolio_*.json"),
            reverse=True,
        )
        for path in paths:
            portfolio = cls.load_json(path)
            if portfolio is not None and portfolio.get("version") == PortfolioEngine.VERSION:
                return portfolio, path
        return None, None

    @classmethod
    def save_proposed_update(
        cls,
        portfolio: dict[str, Any],
        portfolio_class: type[PortfolioEngine] = PortfolioEngine,
    ) -> Path:
        """Save the next dated proposed-paper-portfolio record.

        Earlier portfolio records remain intact, which makes every automatic
        model allocation change independently traceable.
        """
        cls.PORTFOLIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
        stem = f"research_portfolio_{cls.timestamp()}"
        path = cls.PORTFOLIO_DIRECTORY / f"{stem}.json"
        sequence = 1
        while path.exists():
            path = cls.PORTFOLIO_DIRECTORY / f"{stem}_{sequence}.json"
            sequence += 1
        portfolio_class.save(portfolio, path=path)
        return path

    @classmethod
    def universe_metadata(cls) -> dict[str, dict[str, Any]]:
        universe = cls.load_json(cls.UNIVERSE_PATH) or {}
        return {
            str(company.get("ticker")).upper(): company
            for company in universe.get("companies", [])
            if isinstance(company, dict) and company.get("ticker")
        }

    @classmethod
    def current_master_decision(
        cls,
        raw: dict[str, Any],
        canonical: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Re-evaluate only a stale saved policy from the existing audited data.

        A policy update must not make a saved research file look current merely
        because it exists.  Re-running the lightweight master decision against
        its preserved research inputs keeps the candidate list consistent while
        the slower source refresh works through the universe.
        """
        canonical = canonical or ResearchContract.from_pipeline_result(raw)
        stored = canonical.get("master_decision") or {}
        if stored.get("version") == MasterPortfolioDecisionEngine.VERSION:
            return stored
        research = raw.get("research") or {}
        return MasterPortfolioDecisionEngine.evaluate(
            canonical,
            catalysts=research.get("catalysts") or {},
        )

    @staticmethod
    def portfolio_market_context(results: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a transparent consensus from saved market-context snapshots."""

        def consensus(area: str) -> dict[str, Any]:
            values = [
                (item.get("market_context") or {}).get(area) or {}
                for item in results
            ]
            complete = [
                value
                for value in values
                if value.get("status") == "COMPLETE"
                and value.get("regime")
            ]

            if not complete:
                return {
                    "status": "UNAVAILABLE",
                    "regime": "UNAVAILABLE",
                    "coverage": 0,
                }

            counts = Counter(str(value["regime"]) for value in complete)
            regime, count = counts.most_common(1)[0]
            representative = next(
                value for value in complete if value.get("regime") == regime
            )
            return {
                **representative,
                "coverage": len(complete),
                "agreement": round(count / len(complete), 2),
            }

        return {
            "market_regime": consensus("market_regime"),
            "macro_environment": consensus("macro_environment"),
        }

    @classmethod
    def cash_policy(cls, market_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "cash_weight": 0.0,
            "reason": "Prototype policy is fully invested; market context changes selection and sizing, not cash allocation.",
        }

    @classmethod
    def portfolio_readiness(
        cls,
        scan: dict[str, Any],
        target_holdings: int = PortfolioEngine.DEFAULT_HOLDINGS,
    ) -> dict[str, Any]:
        """Explain plainly whether saved research can support construction.

        The portfolio layer must never fill an incomplete portfolio with weak
        or unaudited companies just to make the interface look complete.
        This small status object gives the application a safe, non-technical
        explanation of what needs to happen next.
        """

        target = max(cls.MIN_CONSTRUCTIBLE_HOLDINGS, int(target_holdings))
        eligible = int(scan.get("eligible_count") or 0)
        shortfall = max(0, target - eligible)
        constructible_holdings = min(target, eligible)
        target_reached = shortfall == 0

        if target_reached:
            message = "Enough independently researched companies are ready for construction."
        elif eligible < cls.MIN_CONSTRUCTIBLE_HOLDINGS:
            message = (
                "Fewer than five companies currently clear every research and risk gate; "
                "the system will not force an under-diversified portfolio."
            )
        else:
            message = (
                f"{eligible} master-approved, audit-cleared companies are available for a fully invested "
                f"{eligible}-holding portfolio. {shortfall} more candidate"
                f"{'s are' if shortfall != 1 else ' is'} needed to reach the {target}-holding target."
            )

        return {
            "target_holdings": target,
            "constructible_holdings": constructible_holdings,
            "eligible_count": eligible,
            "shortfall": shortfall,
            "target_reached": target_reached,
            "ready": eligible >= cls.MIN_CONSTRUCTIBLE_HOLDINGS,
            "message": message,
        }

    @classmethod
    def research_scan(cls) -> dict[str, Any]:
        metadata = cls.universe_metadata()
        results: list[dict[str, Any]] = []

        for path in sorted(cls.PIPELINE_DIRECTORY.glob("*.json")):
            raw = cls.load_json(path)
            if raw is None:
                continue
            canonical = ResearchContract.from_pipeline_result(raw)
            canonical["master_decision"] = cls.current_master_decision(
                raw,
                canonical,
            )
            ticker = canonical.get("ticker") or path.stem.upper()
            company = metadata.get(str(ticker).upper(), {})

            results.append(
                {
                    "ticker": ticker,
                    "name": company.get("name"),
                    "sector": company.get("sector", "Unknown"),
                    "industry": company.get("industry"),
                    "index_membership": company.get("index_membership", []),
                    "research_status": canonical.get("research_status"),
                    "investment_case_score": canonical.get("investment_case_score"),
                    "decision": canonical.get("decision"),
                    "current_price": canonical.get("current_price"),
                    "base_intrinsic_value": canonical.get("base_intrinsic_value"),
                    "expected_return": canonical.get("expected_return"),
                    "annualised_expected_return": canonical.get(
                        "annualised_expected_return"
                    ),
                    "valuation_horizon_years": canonical.get(
                        "valuation_horizon_years"
                    ),
                    "valuation_confidence": canonical.get("valuation_confidence"),
                    "valuation_quality": canonical.get("valuation_quality", {}),
                    "thesis": canonical.get("thesis", {}),
                    "market_signals": canonical.get("market_signals", {}),
                    "sentiment": canonical.get("sentiment", {}),
                    "provider_evidence": canonical.get("provider_evidence", {}),
                    "market_context": canonical.get("market_context", {}),
                    "specialist_research": canonical.get("specialist_research", {}),
                    "master_decision": canonical.get("master_decision", {}),
                    "diagnostics": canonical.get("diagnostics", {}),
                    "monitoring_conditions": canonical.get("monitoring_conditions", []),
                    "decision_reason": canonical.get("decision_reason"),
                    "data_as_of": raw.get("completed_at") or raw.get("started_at"),
                    "research_pipeline_version": raw.get("pipeline_version"),
                    "audit": canonical.get("audit", {}),
                    "record_path": str(path),
                }
            )

        watchlist_data = PortfolioWatchlistEngine.build(results)
        prepared_candidates = PortfolioEngine.prepare_candidates({"results": results})

        # Multiple listed share classes are one economic exposure.  Keep the
        # highest-ranked class only before readiness is calculated; otherwise
        # the application could say it has two candidates when it has one.
        ranked = []
        issuer_duplicates: dict[str, str] = {}
        seen_issuers: set[str] = set()
        for candidate in prepared_candidates:
            issuer = PortfolioEngine.issuer_key(candidate)
            if issuer in seen_issuers:
                issuer_duplicates[str(candidate["ticker"])] = issuer
                continue
            seen_issuers.add(issuer)
            ranked.append(candidate)

        ranked_by_ticker = {
            item["ticker"]: item for item in ranked
        }

        for result in results:
            prepared = ranked_by_ticker.get(result["ticker"])
            confidence = prepared.get("research_confidence") if prepared else None
            result["opportunity_score"] = prepared.get("opportunity_score") if prepared else None
            result["decision_rating"] = prepared.get("decision_rating") if prepared else None
            result["research_confidence"] = confidence
            result["confidence_label"] = cls.confidence_label(confidence)
            result["portfolio_eligible"] = prepared is not None
            if prepared is not None:
                result["portfolio_eligibility_reason"] = None
            elif str(result["ticker"]) in issuer_duplicates:
                result["portfolio_eligibility_reason"] = (
                    "A higher-ranked share class of the same economic issuer "
                    "is already represented."
                )
            else:
                result["portfolio_eligibility_reason"] = (
                    PortfolioEngine.eligibility_reason(result)
                )

            classified = next(
                (
                    item
                    for group in watchlist_data.values()
                    for item in group
                    if item["ticker"] == result["ticker"]
                ),
                {},
            )
            result["watchlist_status"] = classified.get("watchlist_status")
            result["watchlist_reason"] = classified.get("watchlist_reason")
            result["watchlist_priority"] = classified.get("watchlist_priority")

        return {
            "universe": "saved_research_records",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_count": len(results),
            "completed_count": sum(
                item.get("research_status") == "COMPLETE" for item in results
            ),
            "audit_pass_count": sum(
                PortfolioEngine.audit_clears(item)
                for item in results
            ),
            "eligible_count": len(ranked),
            "issuer_duplicate_count": len(issuer_duplicates),
            "watchlist_count": len(watchlist_data["watchlist"]),
            "watchlist": watchlist_data["watchlist"],
            "research_required": watchlist_data["research_required"],
            "market_context": cls.portfolio_market_context(results),
            "results": results,
            "ranked": ranked,
        }

    @classmethod
    def construct(
        cls,
        target_holdings: int = PortfolioEngine.DEFAULT_HOLDINGS,
        portfolio_class: type[PortfolioEngine] = PortfolioEngine,
        risk_reviewer: type[PortfolioRiskReviewService] = PortfolioRiskReviewService,
    ) -> dict[str, Any]:
        scan = cls.research_scan()
        readiness = cls.portfolio_readiness(scan, target_holdings)
        target_holdings = readiness["constructible_holdings"]
        if not readiness["ready"]:
            return {
                "status": "BLOCKED",
                "scan": scan,
                "portfolio": None,
                "reason": readiness["message"],
                "readiness": readiness,
            }

        try:
            cash_policy = cls.cash_policy(scan["market_context"])
            portfolio = portfolio_class.construct(
                scan,
                number_of_stocks=target_holdings,
                cash_weight=cash_policy["cash_weight"],
            )
        except (RuntimeError, ValueError) as exc:
            return {
                "status": "BLOCKED",
                "scan": scan,
                "portfolio": None,
                "reason": str(exc),
            }

        for holding in portfolio.get("holdings", []):
            confidence = holding.get("research_confidence")
            holding["research_confidence"] = confidence
            holding["confidence_label"] = cls.confidence_label(confidence)

        portfolio["market_context"] = scan["market_context"]
        portfolio["cash_policy"] = cash_policy
        portfolio["construction_readiness"] = readiness
        portfolio["benchmark"] = PortfolioBenchmarkService.disclosure(portfolio)
        portfolio.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        portfolio.setdefault("version", getattr(portfolio_class, "VERSION", "UNKNOWN"))
        portfolio["portfolio_id"] = cls.portfolio_snapshot_id(portfolio)

        risk_review = risk_reviewer.review(portfolio)
        portfolio["risk_review"] = risk_review

        if not risk_review.get("Pass"):
            flags = risk_review.get("Flags") or ["Portfolio risk review failed."]
            return {
                "status": "BLOCKED",
                "scan": scan,
                "portfolio": portfolio,
                "reason": "Portfolio risk review blocked construction: " + " ".join(flags),
            }

        path = cls.save_proposed_update(portfolio, portfolio_class)
        ledger = InvestmentDecisionLedger(cls.DECISION_LEDGER_PATH)
        ledger_records = []
        for holding in portfolio.get("holdings", []):
            decision = InvestmentDecision.from_canonical(holding, holding=holding)
            versions = [
                ModelVersion(
                    component="research_pipeline",
                    version=str(
                        holding.get("research_pipeline_version")
                        or InvestmentResearchPipeline.VERSION
                    ),
                ),
                ModelVersion(
                    component="research_contract",
                    version=ResearchContract.VERSION,
                ),
                ModelVersion(
                    component="master_portfolio_decision",
                    version=MasterPortfolioDecisionEngine.VERSION,
                ),
                ModelVersion(
                    component="portfolio",
                    version=str(portfolio.get("version") or "UNKNOWN"),
                ),
            ]
            ledger_records.append(
                ledger.append_investment_decision(
                    decision,
                    model_versions=versions,
                    data_as_of=str(
                        holding.get("data_as_of")
                        or scan.get("created_at")
                    ),
                    portfolio_version=portfolio["portfolio_id"],
                    decision_id=f"{portfolio['portfolio_id']}-{decision.ticker}",
                )
            )

        return {
            "status": "CONSTRUCTED",
            "scan": scan,
            "portfolio": portfolio,
            "path": path,
            "readiness": readiness,
            "ledger_path": cls.DECISION_LEDGER_PATH,
            "ledger_records": ledger_records,
        }
