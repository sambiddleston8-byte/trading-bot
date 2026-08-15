from __future__ import annotations

"""Research-led proposed-portfolio reviews, alerts and paper reallocations.

The service may update the saved *proposed paper portfolio* when refreshed
research supports a balanced reallocation.  It is deliberately not connected
to a broker and cannot place an investment order.
"""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.application.research_service import ResearchService
from core.application.portfolio_construction_service import PortfolioConstructionService
from core.application.portfolio_market_exposure_service import (
    PortfolioMarketExposureService,
)
from core.data_sources.yahoo_fast_info_access import YahooFastInfoClient
from core.portfolio.portfolio_engine import PortfolioEngine
from core.research.research_contract import ResearchContract


class PortfolioMonitorService:
    VERSION = "1.4-research-led-paper-reallocation"
    MAX_RESEARCH_AGE_DAYS = 14
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    SNAPSHOT_DIRECTORY = PROJECT_ROOT / "data" / "research" / "portfolio_monitoring"

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def current_price(cls, ticker: str, *, price_client: Any = None) -> float | None:
        """Fetch only the latest paper-monitoring price, not a tradeable quote."""
        try:
            symbol = str(ticker).upper()
            client = (
                price_client
                if price_client is not None
                else YahooFastInfoClient()
            )
            return client.last_price(symbol).last_price
        except Exception:
            return None

    @classmethod
    def research_age_days(cls, raw: dict[str, Any]) -> float | None:
        """Return the age of saved research where its timestamp is valid."""
        value = raw.get("completed_at") or raw.get("started_at")
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400.0)

    @classmethod
    def thesis_alert(cls, ticker: str) -> tuple[str | None, str | None]:
        """Return a saved-research alert without running a new research job."""
        raw = ResearchService.load(ticker)
        if raw is None:
            return "RESEARCH_REFRESH", "No saved research record is available."

        age_days = cls.research_age_days(raw)
        if age_days is not None and age_days > cls.MAX_RESEARCH_AGE_DAYS:
            return (
                "RESEARCH_REFRESH",
                f"Saved research is {age_days:.0f} days old and should be refreshed before an allocation decision.",
            )

        record = ResearchContract.from_pipeline_result(raw)
        audit = record.get("audit") or {}
        thesis = record.get("thesis") or {}
        master = PortfolioConstructionService.current_master_decision(raw, record)
        thesis_result = str(thesis.get("result") or "").upper()
        material_negative = cls.number(thesis.get("material_negative")) or 0.0

        if thesis_result in {"THESIS_REJECTED", "THESIS_INVALIDATED"} or material_negative >= 3:
            return "EXIT_REVIEW", "The saved adversarial thesis review identifies a fatal investment-case issue."
        if str(audit.get("status") or "").upper() in {"FAIL", "REVIEW"}:
            return "RESEARCH_REVIEW", "The saved evidence audit is no longer clear for portfolio use."
        master_recommendation = str(master.get("portfolio_recommendation") or "").upper()
        if master_recommendation == "EXCLUDE":
            return "RESEARCH_REVIEW", "The saved master decision currently excludes the company."
        if master_recommendation == "WATCHLIST":
            return (
                "RESEARCH_REVIEW",
                "The latest combined fundamental, valuation, technical, thesis or catalyst evidence no longer supports a full portfolio allocation.",
            )
        return None, None

    @classmethod
    def allocation_recommendation(cls, holding: dict[str, Any]) -> dict[str, Any]:
        """Recommend a reviewable allocation change from saved research.

        The method intentionally reuses the audited research record rather
        than turning a price move alone into an automatic sell signal.  A
        recommendation may change the *proposed* allocation. It never places
        an order; applying it updates only the dated paper portfolio record.
        """
        ticker = str(holding.get("ticker") or "").upper()
        current_weight = cls.number(holding.get("weight")) or 0.0
        raw = ResearchService.load(ticker)
        if raw is None:
            return {
                "action": "RESEARCH_REFRESH",
                "current_weight": current_weight,
                "suggested_weight": current_weight,
                "allocation_change": 0.0,
                "reason": "No current saved research record is available to support an allocation decision.",
            }

        record = ResearchContract.from_pipeline_result(raw)
        record["master_decision"] = PortfolioConstructionService.current_master_decision(raw, record)
        master = record["master_decision"]
        audit = record.get("audit") or {}
        thesis = record.get("thesis") or {}
        signals = record.get("market_signals") or {}
        catalysts = (raw.get("research") or {}).get("catalysts") or {}
        catalyst_summary = catalysts.get("summary") or {}
        current_detail = PortfolioEngine.decision_rating_detail(record)
        saved_detail = holding.get("decision_rating") or {}
        saved_rating = cls.number(saved_detail.get("score"))
        if saved_rating is None:
            saved_rating = cls.number(holding.get("portfolio_conviction")) or current_detail["score"]
        current_rating = current_detail["score"]
        rating_change = round(current_rating - saved_rating, 1)
        recommendation = str(master.get("portfolio_recommendation") or "").upper()

        if (
            recommendation == "EXCLUDE"
            or str(audit.get("status") or "").upper() in {"FAIL", "REVIEW"}
            or thesis.get("thesis_survives") is False
        ):
            suggested_weight = 0.0
            action = "EXIT_REVIEW"
            reason = (
                "The updated fundamental, valuation, thesis, audit or catalyst evidence no longer clears the portfolio safeguards. "
                "The proposed paper allocation should move out of this holding unless a portfolio constraint blocks it."
            )
        elif recommendation != "ELIGIBLE":
            suggested_weight = round(current_weight * 0.50, 4)
            action = "REDUCE_REVIEW"
            reason = (
                "The updated combined fundamental, technical, thesis and catalyst research no longer supports a full allocation. "
                "The proposed paper allocation should be reduced while the research is refreshed."
            )
        elif rating_change <= -10.0:
            suggested_weight = round(current_weight * 0.70, 4)
            action = "REDUCE_REVIEW"
            reason = (
                f"The decision rating fell by {abs(rating_change):.1f} points after the latest saved research review. "
                "The proposed paper allocation should be reduced, with the supporting research changes retained in the decision log."
            )
        elif rating_change >= 10.0:
            suggested_weight = round(min(0.15, current_weight * 1.20), 4)
            action = "INCREASE_REVIEW"
            reason = (
                f"The decision rating improved by {rating_change:.1f} points after the latest saved research review. "
                "The proposed paper allocation should increase if funding and portfolio limits allow it."
            )
        else:
            suggested_weight = current_weight
            action = "NO_CHANGE"
            reason = "The latest saved research still supports the existing proposed allocation."

        return {
            "action": action,
            "current_weight": current_weight,
            "suggested_weight": suggested_weight,
            "allocation_change": round(suggested_weight - current_weight, 4),
            "decision_rating": current_rating,
            "decision_rating_change": rating_change,
            "master_recommendation": recommendation or "UNRATED",
            "audit_status": audit.get("status") or "UNRATED",
            "thesis_result": thesis.get("result") or "UNRATED",
            "technical_score": cls.number(signals.get("technical_score")),
            "risk_score": cls.number(signals.get("risk_score")),
            "positive_catalyst_score": cls.number(catalyst_summary.get("positive_score")),
            "negative_catalyst_score": cls.number(catalyst_summary.get("negative_score")),
            "reason": reason,
        }

    @classmethod
    def approved_replacement_candidates(
        cls,
        portfolio: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return only saved, independently approved candidates not held now.

        This makes an exit recommendation actionable without creating cash or
        replacing a holding with a merely popular name. Candidates arrive from
        the same audited master-decision process used at construction time.
        """
        held = {
            str(holding.get("ticker") or "").upper()
            for holding in portfolio.get("holdings") or []
            if isinstance(holding, dict)
        }
        held_issuers = {
            PortfolioEngine.issuer_key(holding)
            for holding in portfolio.get("holdings") or []
            if isinstance(holding, dict)
        }
        scan = PortfolioConstructionService.research_scan()
        candidates = []
        for candidate in scan.get("ranked") or []:
            ticker = str(candidate.get("ticker") or "").upper()
            if (
                not ticker
                or ticker in held
                or PortfolioEngine.issuer_key(candidate) in held_issuers
            ):
                continue
            candidates.append(candidate)
        return candidates

    @classmethod
    def replacement_holding(cls, candidate: dict[str, Any]) -> dict[str, Any]:
        """Create a normal portfolio-holding record for a vetted replacement."""
        return {
            "ticker": str(candidate.get("ticker") or "").upper(),
            "name": candidate.get("name"),
            "sector": candidate.get("sector"),
            "industry": candidate.get("industry"),
            "index_membership": candidate.get("index_membership", []),
            "weight": 0.0,
            "portfolio_conviction": candidate.get("portfolio_conviction"),
            "opportunity_score": candidate.get("opportunity_score"),
            "research_confidence": candidate.get("research_confidence"),
            "decision_rating": candidate.get("decision_rating") or PortfolioEngine.decision_rating_detail(candidate),
            "provider_evidence": candidate.get("provider_evidence", {}),
            "portfolio_decision": "AUTOMATIC_RESEARCH_APPROVED_REPLACEMENT",
            "investment_case_score": candidate.get("investment_case_score"),
            "current_price": candidate.get("current_price"),
            "base_intrinsic_value": candidate.get("base_intrinsic_value"),
            "expected_return": candidate.get("expected_return"),
            "annualised_expected_return": candidate.get("annualised_expected_return"),
            "valuation_horizon_years": candidate.get("valuation_horizon_years"),
            "valuation_upside": candidate.get("valuation_upside"),
            "decision": candidate.get("decision"),
            "thesis": candidate.get("thesis", {}),
            "audit": candidate.get("audit", {}),
            "market_signals": candidate.get("market_signals", {}),
            "sentiment": candidate.get("sentiment", {}),
            "monitoring_conditions": candidate.get("monitoring_conditions", []),
            "position_sizing": PortfolioEngine.position_sizing_detail(candidate),
            "reasoning": PortfolioEngine.build_reasoning(candidate),
        }

    @classmethod
    def reallocation_plan(
        cls,
        positions: list[dict[str, Any]],
        *,
        replacement_candidates: list[dict[str, Any]] | None = None,
        max_weight: float | None = None,
        min_weight: float | None = None,
    ) -> dict[str, Any]:
        """Turn evidence-led position recommendations into balanced transfers.

        This is deliberately stricter than a list of target weights.  Capital
        is moved only from holdings whose own refreshed research recommends a
        reduction or exit; the system will not fund an increase by trimming an
        otherwise supported position. A valid balanced transfer is eligible to
        update the proposed paper portfolio automatically, never a broker.
        """
        donors = []
        recipients = []
        for position in positions:
            recommendation = position.get("allocation_recommendation") or {}
            current = cls.number(recommendation.get("current_weight"))
            suggested = cls.number(recommendation.get("suggested_weight"))
            action = str(recommendation.get("action") or "").upper()
            if current is None or suggested is None:
                continue
            change = round(suggested - current, 6)
            item = {
                "ticker": str(position.get("ticker") or "").upper(),
                "company": position.get("company") or position.get("ticker"),
                "current_weight": current,
                "suggested_weight": suggested,
                "available": abs(change),
                "reason": recommendation.get("reason") or "No research rationale was saved.",
                "decision_rating": cls.number(recommendation.get("decision_rating")),
                "action": action,
            }
            if change < -0.000001 and action in {"REDUCE_REVIEW", "EXIT_REVIEW"}:
                donors.append(item)
            elif change > 0.000001 and action == "INCREASE_REVIEW":
                recipients.append(item)

        # Exit recommendations fund before ordinary reductions. Within each
        # group, weaker current ratings fund first, leaving stronger research
        # intact unless it independently calls for a reduction.
        donors.sort(
            key=lambda item: (
                0 if item["action"] == "EXIT_REVIEW" else 1,
                item["decision_rating"] if item["decision_rating"] is not None else float("inf"),
                item["ticker"],
            )
        )
        recipients.sort(
            key=lambda item: (
                -(item["decision_rating"] if item["decision_rating"] is not None else 0.0),
                item["ticker"],
            )
        )

        transfers = []
        remaining_donors = [dict(item) for item in donors]
        total_requested = round(sum(item["available"] for item in recipients), 6)
        total_available = round(sum(item["available"] for item in donors), 6)

        def fund(recipients_to_fund: list[dict[str, Any]]) -> None:
            for recipient in recipients_to_fund:
                remaining_need = recipient["available"]
                for donor in remaining_donors:
                    if remaining_need <= 0.000001:
                        break
                    available = donor["available"]
                    if available <= 0.000001:
                        continue
                    amount = round(min(available, remaining_need), 6)
                    transfer = {
                        "from_ticker": donor["ticker"],
                        "from_company": donor["company"],
                        "to_ticker": recipient["ticker"],
                        "to_company": recipient["company"],
                        "weight": amount,
                        "from_reason": donor["reason"],
                        "to_reason": recipient["reason"],
                    }
                    if recipient.get("replacement_holding"):
                        transfer["replacement_holding"] = recipient["replacement_holding"]
                    transfers.append(transfer)
                    donor["available"] = round(available - amount, 6)
                    remaining_need = round(remaining_need - amount, 6)

        fund(recipients)

        # When a holding needs to be reduced or exited but no existing holding
        # independently deserves more capital, select an audited external
        # replacement. This keeps the user-selected fully invested policy
        # without routing capital to cash or to an unresearched company.
        replacement_candidates = replacement_candidates or []
        remaining_capital = round(sum(item["available"] for item in remaining_donors), 6)
        max_weight = cls.number(max_weight) or PortfolioEngine.DEFAULT_MAX_WEIGHT
        min_weight = cls.number(min_weight) or PortfolioEngine.DEFAULT_MIN_WEIGHT
        replacement_recipients = []
        if remaining_capital >= min_weight - 0.000001 and replacement_candidates:
            ranked_replacements = sorted(
                replacement_candidates,
                key=lambda candidate: (
                    PortfolioEngine.position_sizing_signal(candidate),
                    cls.number((candidate.get("decision_rating") or {}).get("score")) or 0.0,
                ),
                reverse=True,
            )
            desired_count = max(1, int((remaining_capital / max(max_weight * 0.75, min_weight)) + 0.999999))
            desired_count = min(3, desired_count, len(ranked_replacements))
            desired_count = min(desired_count, max(1, int(remaining_capital / min_weight)))
            selected_replacements = ranked_replacements[:desired_count]
            signals = [PortfolioEngine.position_sizing_signal(item) for item in selected_replacements]
            signal_total = sum(signals) or float(len(selected_replacements))
            unallocated = remaining_capital
            for index, candidate in enumerate(selected_replacements):
                remaining_slots = len(selected_replacements) - index - 1
                minimum_for_others = remaining_slots * min_weight
                proposed = remaining_capital * signals[index] / signal_total
                allocation = min(max_weight, max(min_weight, proposed))
                allocation = min(allocation, max(0.0, unallocated - minimum_for_others))
                if allocation < min_weight - 0.000001:
                    continue
                holding = cls.replacement_holding(candidate)
                replacement_recipients.append(
                    {
                        "ticker": holding["ticker"],
                        "company": holding.get("name") or holding["ticker"],
                        "available": round(allocation, 6),
                        "reason": (
                            "An audit-cleared, master-approved replacement was selected using the same risk-adjusted "
                            "opportunity, evidence confidence, expected-return, volatility and risk-quality sizing inputs."
                        ),
                        "replacement_holding": holding,
                    }
                )
                unallocated = round(unallocated - allocation, 6)
            total_requested = round(
                total_requested + sum(item["available"] for item in replacement_recipients),
                6,
            )
            fund(replacement_recipients)

        funded = round(sum(item["weight"] for item in transfers), 6)
        if not donors and not recipients:
            status = "NO_CHANGE"
        elif funded > 0:
            status = "READY_TO_APPLY_TO_PROPOSED_PORTFOLIO"
        else:
            status = "RESEARCH_REVIEW_REQUIRED"

        return {
            "status": status,
            "automatic_proposed_portfolio_update": funded > 0,
            "transfers": transfers,
            "total_requested_increase": total_requested,
            "total_evidence_led_reduction": total_available,
            "total_transfer_proposed": funded,
            "unfunded_increase": round(max(0.0, total_requested - funded), 6),
            "unused_reduction": round(max(0.0, total_available - funded), 6),
            "replacement_candidates_considered": len(replacement_candidates),
            "replacement_transfers": sum(
                1 for item in transfers if item.get("replacement_holding")
            ),
            "policy": (
                "Transfers are proposed only between holdings whose refreshed research supports opposite allocation changes. "
                "A balanced transfer updates the proposed paper portfolio automatically after its portfolio limits are checked. "
                "No broker order is ever sent."
            ),
        }

    @classmethod
    def apply_reallocation(
        cls,
        portfolio: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a validated model rebalance to a copy of a paper portfolio.

        The monitor never touches a brokerage account.  This method only
        creates the next dated proposed-portfolio record, preserving the
        prior record and an explanation of every allocation change.
        """
        plan = (snapshot or {}).get("reallocation_plan") or {}
        transfers = plan.get("transfers") or []
        if plan.get("status") != "READY_TO_APPLY_TO_PROPOSED_PORTFOLIO" or not transfers:
            return {
                "status": "NO_CHANGE",
                "portfolio": portfolio,
                "changes": [],
                "reallocation_plan": plan,
            }

        updated = copy.deepcopy(portfolio)
        holdings = updated.get("holdings") or []
        by_ticker = {
            str(holding.get("ticker") or "").upper(): holding
            for holding in holdings
            if isinstance(holding, dict) and holding.get("ticker")
        }
        changes: dict[str, dict[str, Any]] = {}

        for transfer in transfers:
            from_ticker = str(transfer.get("from_ticker") or "").upper()
            to_ticker = str(transfer.get("to_ticker") or "").upper()
            amount = cls.number(transfer.get("weight"))
            if (
                not from_ticker
                or not to_ticker
                or from_ticker == to_ticker
                or amount is None
                or amount <= 0
                or from_ticker not in by_ticker
            ):
                return {
                    "status": "NOT_APPLIED_DATA_REVIEW",
                    "portfolio": portfolio,
                    "changes": [],
                    "reallocation_plan": plan,
                    "reason": "A proposed transfer does not match two valid current holdings.",
                }

            if to_ticker not in by_ticker:
                replacement = transfer.get("replacement_holding")
                if (
                    not isinstance(replacement, dict)
                    or str(replacement.get("ticker") or "").upper() != to_ticker
                ):
                    return {
                        "status": "NOT_APPLIED_DATA_REVIEW",
                        "portfolio": portfolio,
                        "changes": [],
                        "reallocation_plan": plan,
                        "reason": "A proposed replacement does not contain a valid audit-approved holding record.",
                    }
                new_holding = copy.deepcopy(replacement)
                new_holding["ticker"] = to_ticker
                new_holding["weight"] = 0.0
                holdings.append(new_holding)
                by_ticker[to_ticker] = new_holding

            for ticker, direction, reason in (
                (from_ticker, -1.0, transfer.get("from_reason")),
                (to_ticker, 1.0, transfer.get("to_reason")),
            ):
                holding = by_ticker[ticker]
                before = cls.number(holding.get("weight")) or 0.0
                holding["weight"] = round(before + (direction * amount), 6)
                change = changes.setdefault(
                    ticker,
                    {
                        "ticker": ticker,
                        "company": holding.get("name") or holding.get("company") or ticker,
                        "before_weight": before,
                        "after_weight": before,
                        "allocation_change": 0.0,
                        "reasons": [],
                    },
                )
                change["after_weight"] = holding["weight"]
                change["allocation_change"] = round(
                    change["after_weight"] - change["before_weight"], 6
                )
                if isinstance(reason, str) and reason and reason not in change["reasons"]:
                    change["reasons"].append(reason)

        constraints = updated.get("constraints") or {}
        max_weight = cls.number(constraints.get("max_weight"))
        max_sector_weight = cls.number(constraints.get("max_sector_weight"))
        total_weight = round(sum(cls.number(item.get("weight")) or 0.0 for item in holdings), 6)
        sector_weights: dict[str, float] = {}
        for holding in holdings:
            ticker = str(holding.get("ticker") or "").upper()
            weight = cls.number(holding.get("weight")) or 0.0
            sector = str(holding.get("sector") or "Unknown")
            sector_weights[sector] = round(sector_weights.get(sector, 0.0) + weight, 6)
            if weight < -0.000001 or (max_weight is not None and weight > max_weight + 0.000001):
                return {
                    "status": "NOT_APPLIED_CONSTRAINT_REVIEW",
                    "portfolio": portfolio,
                    "changes": [],
                    "reallocation_plan": plan,
                    "reason": f"{ticker} would breach the individual position-size limit.",
                }

        # Constructed weights are persisted to six decimal places, so a
        # fully invested portfolio can legitimately serialise as 99.9999%.
        # Treat only a material deviation as a funding failure.
        if abs(total_weight - 1.0) > 0.00001:
            return {
                "status": "NOT_APPLIED_CONSTRAINT_REVIEW",
                "portfolio": portfolio,
                "changes": [],
                "reallocation_plan": plan,
                "reason": "The proposed transfers would no longer leave the paper portfolio fully invested.",
            }
        if max_sector_weight is not None:
            over_limit = next(
                (
                    sector
                    for sector, weight in sector_weights.items()
                    if weight > max_sector_weight + 0.000001
                ),
                None,
            )
            if over_limit is not None:
                return {
                    "status": "NOT_APPLIED_CONSTRAINT_REVIEW",
                    "portfolio": portfolio,
                    "changes": [],
                    "reallocation_plan": plan,
                    "reason": f"The {over_limit} sector would breach its diversification limit.",
                }

        # A completed exit is no longer an active proposed holding. Its
        # before/after record remains in ``changes`` and ``rebalance_history``
        # below, while the displayed portfolio contains only active positions.
        holdings[:] = [
            holding
            for holding in holdings
            if (cls.number(holding.get("weight")) or 0.0) > 0.000001
        ]
        updated["number_of_stocks"] = len(holdings)
        updated["sector_weights"] = {}
        weighted_expected_return = 0.0
        weighted_annualised_return = 0.0
        annualised_weight_coverage = 0.0
        for rank, holding in enumerate(holdings, start=1):
            holding["rank"] = rank
            sector = str(holding.get("sector") or "Unknown")
            weight = cls.number(holding.get("weight")) or 0.0
            updated["sector_weights"][sector] = round(
                updated["sector_weights"].get(sector, 0.0) + weight,
                6,
            )
            expected_return = cls.number(holding.get("expected_return"))
            if expected_return is not None:
                weighted_expected_return += weight * expected_return
            annualised_return = cls.number(holding.get("annualised_expected_return"))
            if annualised_return is not None:
                weighted_annualised_return += weight * annualised_return
                annualised_weight_coverage += weight
        updated["portfolio_expected_return"] = round(weighted_expected_return, 6)
        updated["portfolio_annualised_expected_return"] = (
            round(weighted_annualised_return, 6)
            if annualised_weight_coverage >= 0.999999
            else None
        )

        applied_at = cls.now()
        applied_plan = copy.deepcopy(plan)
        applied_plan["status"] = "APPLIED_TO_PROPOSED_PORTFOLIO"
        applied_plan["applied_at"] = applied_at
        for position in snapshot.get("positions") or []:
            ticker = str(position.get("ticker") or "").upper()
            holding = by_ticker.get(ticker)
            recommendation = position.get("allocation_recommendation") or {}
            rating = cls.number(recommendation.get("decision_rating"))
            if holding is None or rating is None:
                continue
            existing_rating = holding.get("decision_rating")
            if isinstance(existing_rating, dict):
                holding["decision_rating"] = {
                    **existing_rating,
                    "score": rating,
                }
            else:
                holding["decision_rating"] = {"score": rating}
        updated["updated_at"] = applied_at
        updated["last_rebalance"] = {
            "applied_at": applied_at,
            "checked_at": snapshot.get("checked_at"),
            "total_transfer_weight": plan.get("total_transfer_proposed"),
            "transfers": transfers,
            "changes": list(changes.values()),
            "policy": "Automatic model update to the proposed paper portfolio only; no broker order was sent.",
        }
        history = updated.setdefault("rebalance_history", [])
        if isinstance(history, list):
            history.append(updated["last_rebalance"])

        return {
            "status": "APPLIED",
            "portfolio": updated,
            "changes": list(changes.values()),
            "reallocation_plan": applied_plan,
        }

    @classmethod
    def evaluate(
        cls,
        portfolio: dict[str, Any],
        *,
        price_lookup: Callable[[str], float | None] | None = None,
        research_alert_lookup: Callable[[str], tuple[str | None, str | None]] | None = None,
        allocation_recommendation_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        benchmark_price_lookup: Callable[[str], float | None] | None = None,
        market_exposure_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one saved portfolio against prices and saved research gates."""
        portfolio = portfolio if isinstance(portfolio, dict) else {}
        price_lookup = price_lookup or cls.current_price
        research_alert_lookup = research_alert_lookup or cls.thesis_alert
        allocation_recommendation_lookup = (
            allocation_recommendation_lookup or cls.allocation_recommendation
        )
        benchmark_price_lookup = benchmark_price_lookup or price_lookup
        market_exposure_lookup = market_exposure_lookup or PortfolioMarketExposureService.review
        positions = []

        for holding in portfolio.get("holdings", []):
            ticker = str(holding.get("ticker") or "").upper()
            entry_price = cls.number(holding.get("current_price"))
            market_price = price_lookup(ticker) if ticker else None
            action = "HOLD"
            alerts: list[str] = []

            if entry_price is None or entry_price <= 0:
                action = "DATA_UNAVAILABLE"
                alerts.append("The construction-date price is unavailable; price performance cannot be calculated.")
                change = None
            elif market_price is None:
                action = "DATA_UNAVAILABLE"
                alerts.append("A current monitoring price could not be retrieved.")
                change = None
            else:
                change = (market_price / entry_price) - 1.0

            thesis_action, thesis_message = research_alert_lookup(ticker) if ticker else (None, None)
            if thesis_action:
                alerts.append(thesis_message or "Saved research requires review.")
                priority = {
                    "HOLD": 0,
                    "DATA_UNAVAILABLE": 1,
                    "RESEARCH_REFRESH": 2,
                    "RESEARCH_REVIEW": 4,
                    "EXIT_REVIEW": 5,
                }
                if priority.get(thesis_action, 0) > priority.get(action, 0):
                    action = thesis_action

            allocation = allocation_recommendation_lookup(holding)
            allocation_action = str(allocation.get("action") or "NO_CHANGE")
            if allocation_action in {"EXIT_REVIEW", "REDUCE_REVIEW"}:
                alerts.append(allocation.get("reason") or "Allocation review is required.")
                priority = {
                    "HOLD": 0,
                    "DATA_UNAVAILABLE": 1,
                    "RESEARCH_REFRESH": 2,
                    "REDUCE_REVIEW": 4,
                    "RESEARCH_REVIEW": 5,
                    "EXIT_REVIEW": 6,
                }
                if priority.get(allocation_action, 0) > priority.get(action, 0):
                    action = allocation_action

            positions.append(
                {
                    "ticker": ticker,
                    "company": holding.get("name") or ticker,
                    "weight": cls.number(holding.get("weight")) or 0.0,
                    "entry_price": entry_price,
                    "current_price": market_price,
                    "price_change": change,
                    "action": action,
                    "alerts": alerts,
                    "allocation_recommendation": allocation,
                }
            )

        counts: dict[str, int] = {}
        for position in positions:
            action = position["action"]
            counts[action] = counts.get(action, 0) + 1

        replacement_candidates = cls.approved_replacement_candidates(portfolio)
        constraints = portfolio.get("constraints") or {}
        reallocation_plan = cls.reallocation_plan(
            positions,
            replacement_candidates=replacement_candidates,
            max_weight=cls.number(constraints.get("max_weight")),
            min_weight=cls.number(constraints.get("min_weight")),
        )
        market_exposure = market_exposure_lookup(portfolio)

        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "checked_at": cls.now(),
            "portfolio_created_at": portfolio.get("created_at"),
            "policy": {
                "basis": [
                    "Refreshed fundamental and valuation research",
                    "Technical and risk signals",
                    "Catalyst and sentiment evidence",
                    "Thesis challenge and evidence audit",
                    "Observed correlation, economic-exposure and liquidity review",
                ],
                "price_use": "Price movement is shown as context only; it does not create a sell rule on its own.",
                "research_freshness": (
                    f"Saved research older than {cls.MAX_RESEARCH_AGE_DAYS} days is flagged for refresh; "
                    "a refresh does not create an automatic trade."
                ),
                "portfolio_changes": "A balanced evidence-led transfer automatically updates the proposed paper portfolio after its limits are checked. The monitor cannot place a broker order.",
                "execution": "ALERT_ONLY_NO_AUTOMATIC_TRADES",
            },
            "summary": {
                "position_count": len(positions),
                "action_counts": counts,
                "alerts_required": sum(
                    position["action"] not in {"HOLD", "DATA_UNAVAILABLE"}
                    for position in positions
                ),
                "allocation_changes_required": sum(
                    (position.get("allocation_recommendation") or {}).get("action")
                    not in {"NO_CHANGE", "RESEARCH_REFRESH"}
                    for position in positions
                ),
                "proposed_transfer_count": len(reallocation_plan["transfers"]),
                "proposed_transfer_weight": reallocation_plan["total_transfer_proposed"],
                "replacement_candidates_available": len(replacement_candidates),
            },
            "benchmark": {
                "name": "S&P 500 Index",
                "ticker": "^GSPC",
                "price": benchmark_price_lookup("^GSPC"),
            },
            "positions": positions,
            "reallocation_plan": reallocation_plan,
            "market_exposure": market_exposure,
            "replacement_candidates": [
                {
                    "ticker": candidate.get("ticker"),
                    "name": candidate.get("name"),
                    "sector": candidate.get("sector"),
                    "decision_rating": (candidate.get("decision_rating") or {}).get("score"),
                }
                for candidate in replacement_candidates
            ],
        }

    @classmethod
    def save(cls, snapshot: dict[str, Any]) -> Path:
        cls.SNAPSHOT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = cls.SNAPSHOT_DIRECTORY / f"portfolio_health_{stamp}.json"
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return path

    @classmethod
    def latest(cls) -> tuple[dict[str, Any] | None, Path | None]:
        paths = sorted(cls.SNAPSHOT_DIRECTORY.glob("portfolio_health_*.json"), reverse=True)
        for path in paths:
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(snapshot, dict) and snapshot.get("version") == cls.VERSION:
                return snapshot, path
        return None, None
