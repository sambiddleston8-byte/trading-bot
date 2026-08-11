from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re

from core.research.master_portfolio_decision_engine import (
    MasterPortfolioDecisionEngine,
)


class PortfolioEngine:

    """
    Portfolio construction engine.

    Pipeline:

        researched universe
              ↓
        audited candidates
              ↓
        opportunity score
              ↓
        diversification constraints
              ↓
        position sizing
              ↓
        portfolio

    This is the first real portfolio-selection layer.

    It deliberately does NOT perform company research itself.
    Research remains the responsibility of UniverseScanner /
    InvestmentResearchPipeline.
    """

    VERSION = "0.7-true-hundred-point-rating"

    DEFAULT_HOLDINGS = 15

    DEFAULT_MAX_WEIGHT = 0.15

    DEFAULT_MIN_WEIGHT = 0.025

    DEFAULT_MAX_SECTOR_WEIGHT = 0.50

    DEFAULT_CASH_WEIGHT = 0.00

    MIN_EXPECTED_RETURN = 0.05

    # Lower-scoring but audit-cleared companies remain on the watchlist.  This
    # prevents the constructor from filling positions purely to reach a count.
    MIN_PROTOTYPE_CONVICTION = 55.0
    SOFT_SECTOR_WEIGHT = 0.25

    ELIGIBLE_DECISIONS = {
        "BUY",
        "STRONG_BUY",
    }

    # A warning-only audit has no high or critical finding.  It can enter the
    # candidate set, but the disclosed warnings reduce allocation confidence and remain
    # visible in the portfolio and company research views.
    ELIGIBLE_AUDIT_STATUSES = {
        "PASS",
        "PASS_WITH_WARNINGS",
    }

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def number(
        value
    ):

        try:

            if value is None:
                return None

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

    @staticmethod
    def safe_dict(
        value
    ):

        if isinstance(
            value,
            dict,
        ):
            return value

        return {}

    @classmethod
    def audit_clears(cls, item):
        audit = cls.safe_dict(item.get("audit"))
        return audit.get("status") in cls.ELIGIBLE_AUDIT_STATUSES

    # ========================================================
    # CANDIDATE QUALITY
    # ========================================================

    @classmethod
    def eligibility_reason(
        cls,
        item,
    ):
        """Return the single authoritative reason a company cannot be held."""
        if item.get("research_status") != "COMPLETE":
            return "Research did not complete successfully."
        if not cls.audit_clears(item):
            return "Evidence audit has not cleared for portfolio use."

        current_price = cls.number(item.get("current_price"))
        intrinsic_value = cls.number(item.get("base_intrinsic_value"))
        expected_return = cls.number(item.get("expected_return"))
        if current_price is None or current_price <= 0:
            return "Current market price is unavailable."
        if intrinsic_value is None or intrinsic_value <= 0:
            return "Base intrinsic value is unavailable or invalid."
        if expected_return is None:
            return "Expected return is unavailable."

        valuation_quality = cls.safe_dict(item.get("valuation_quality"))
        if valuation_quality.get("assessment") == "FAIL":
            return "Valuation quality review has not cleared the model for portfolio use."

        thesis = cls.safe_dict(item.get("thesis"))
        thesis_result = str(thesis.get("result") or "").upper()
        material_negative = cls.number(thesis.get("material_negative")) or 0
        if (
            thesis_result in {"THESIS_REJECTED", "THESIS_INVALIDATED"}
            or material_negative >= 3
        ):
            return "Adversarial thesis review found a fatal investment-case issue."

        master = cls.safe_dict(item.get("master_decision"))
        if master.get("status") != "COMPLETE":
            return "Master portfolio decision is incomplete or missing."
        if master.get("version") != MasterPortfolioDecisionEngine.VERSION:
            return "Master portfolio decision was produced by an older policy and needs refresh."
        if str(master.get("portfolio_recommendation") or "").upper() != "ELIGIBLE":
            reasons = master.get("hard_gate_reasons")
            if isinstance(reasons, list) and reasons:
                return f"Master portfolio decision excludes the company: {reasons[0]}"
            return "Master portfolio decision has not approved the company for allocation."
        return None

    @classmethod
    def candidate_score(
        cls,
        item,
    ):
        """
        Convert the research output into a portfolio
        opportunity score.

        The research engine remains the primary source of
        investment quality.

        Portfolio construction then rewards:

        - high investment case score
        - positive expected return
        - surviving thesis
        - clean audit
        - valuation upside

        and penalises:

        - negative expected return
        - material thesis negatives
        - failed / incomplete research
        """

        if cls.eligibility_reason(item) is not None:
            return None

        base = cls.number(
            item.get(
                "investment_case_score"
            )
        )

        if base is None:

            return None

        score = base

        master_decision = cls.safe_dict(item.get("master_decision"))
        master_conviction = cls.number(master_decision.get("conviction_score"))
        if master_conviction is not None:
            confidence_detail = cls.safe_dict(master_decision.get("research_confidence"))
            research_confidence = cls.number(confidence_detail.get("score"))
            if research_confidence is None:
                research_confidence = cls.number(master_decision.get("confidence"))
            if research_confidence is None:
                return None
            # This preserves the differences between the master decision and
            # research confidence instead of compressing strong candidates
            # into the same score band. It is not clipped below 100.
            combined = max(
                0.0,
                (master_conviction * 0.80) + (research_confidence * 0.20),
            )
            return round(combined, 2)

        expected_return = cls.number(
            item.get(
                "expected_return"
            )
        )

        # ----------------------------------------------------
        # Expected return
        # ----------------------------------------------------

        if expected_return is not None:

            if expected_return >= 0.40:

                score += 12

            elif expected_return >= 0.25:

                score += 9

            elif expected_return >= 0.15:

                score += 6

            elif expected_return >= 0.05:

                score += 2

            elif expected_return < 0:

                score -= 12

            elif expected_return < 0.05:

                score -= 6

        # ----------------------------------------------------
        # Thesis challenge
        # ----------------------------------------------------

        thesis = cls.safe_dict(
            item.get(
                "thesis"
            )
        )

        material_negative = (
            cls.number(
                thesis.get(
                    "material_negative"
                )
            )
            or 0
        )

        score -= min(
            material_negative * 2,
            12,
        )

        audit = cls.safe_dict(item.get("audit"))
        medium_findings = cls.number(audit.get("medium")) or 0
        if audit.get("status") == "PASS_WITH_WARNINGS":
            score -= min(medium_findings * 2, 6)

        if thesis.get(
            "thesis_survives"
        ) is True:

            score += 4

        elif thesis.get(
            "thesis_survives"
        ) is False:

            score -= 8

        decision = str(item.get("decision") or "").upper()
        if decision == "STRONG_BUY":
            score += 5
        elif decision == "BUY":
            score += 2
        elif decision == "AVOID":
            score -= 10

        valuation_quality = cls.safe_dict(item.get("valuation_quality"))
        if valuation_quality.get("assessment") == "REVIEW":
            score -= 6

        valuation_confidence = str(item.get("valuation_confidence") or "").upper()
        if valuation_confidence in {"REVIEW", "LOW"}:
            score -= 5

        market_signals = cls.safe_dict(
            item.get("market_signals")
        )

        risk_score = cls.number(
            market_signals.get("risk_score")
        )

        if risk_score is not None:

            if risk_score < 40:
                score -= 12

            elif risk_score < 60:
                score -= 5

        else:
            score -= 8

        technical_score = cls.number(market_signals.get("technical_score"))
        if technical_score is None:
            score -= 5

        sentiment = cls.safe_dict(
            item.get("sentiment")
        )

        sentiment_score = cls.number(
            sentiment.get("score")
        )

        sentiment_confidence = str(
            sentiment.get("confidence") or ""
        ).upper()

        # News can only make a small difference when independent evidence is
        # strong.  It cannot turn an ineligible company into a holding.
        if sentiment_confidence in {"HIGH", "VERY_HIGH"}:
            if sentiment_score is not None and sentiment_score >= 65:
                score += 2
            elif sentiment_score is not None and sentiment_score <= 35:
                score -= 2

        # ----------------------------------------------------
        # Valuation upside
        # ----------------------------------------------------

        current_price = cls.number(
            item.get(
                "current_price"
            )
        )

        intrinsic_value = cls.number(
            item.get(
                "base_intrinsic_value"
            )
        )

        valuation_upside = None

        if (
            current_price is not None
            and intrinsic_value is not None
            and current_price > 0
        ):

            valuation_upside = (
                intrinsic_value
                / current_price
            ) - 1

            if valuation_upside >= 0.50:

                score += 8

            elif valuation_upside >= 0.25:

                score += 5

            elif valuation_upside >= 0.10:

                score += 3

            elif valuation_upside < 0:

                score -= 8

        return round(
            max(
                0,
                min(
                    100,
                    score,
                ),
            ),
            2,
        )

    @classmethod
    def decision_rating_detail(cls, item):
        """Return the visible 0-100 decision rating and its disclosed inputs.

        This rating is a ranking aid, not a probability of success.  It is
        deliberately separate from sizing and evidence confidence so the
        application does not imply that a high-quality research record is a
        certain investment outcome.
        """
        candidate = cls.safe_dict(item)
        opportunity = cls.number(candidate.get("portfolio_conviction"))
        if opportunity is None:
            opportunity = cls.number(candidate.get("opportunity_score"))
        if opportunity is None:
            master = cls.safe_dict(candidate.get("master_decision"))
            opportunity = cls.number(master.get("opportunity_score"))
        opportunity_score = min(100.0, max(0.0, opportunity or 0.0))

        confidence = cls.number(candidate.get("research_confidence"))
        if confidence is None:
            master = cls.safe_dict(candidate.get("master_decision"))
            confidence = cls.number(
                cls.safe_dict(master.get("research_confidence")).get("score")
            ) or cls.number(master.get("confidence"))
        # Research confidence is already a 0–100 evidence-quality measure.
        # Do not rescale it to a legacy 90-point ceiling; doing so would make
        # incomplete evidence look more certain than the underlying record.
        evidence_score = min(100.0, max(0.0, confidence or 0.0))

        expected_return = cls.number(candidate.get("expected_return"))
        expected_return_score = 35.0 if expected_return is None else min(
            100.0,
            max(0.0, ((expected_return + 0.20) / 0.50) * 100.0),
        )

        valuation_quality = cls.safe_dict(candidate.get("valuation_quality"))
        valuation_assessment = str(valuation_quality.get("assessment") or "").upper()
        valuation_score = {
            "PASS": 100.0,
            "REVIEW": 55.0,
            "FAIL": 0.0,
        }.get(valuation_assessment, 45.0)

        signals = cls.safe_dict(candidate.get("market_signals"))
        risk_score = min(100.0, max(0.0, cls.number(signals.get("risk_score")) or 35.0))
        technical_score = min(100.0, max(0.0, cls.number(signals.get("technical_score")) or 35.0))

        thesis = cls.safe_dict(candidate.get("thesis"))
        if thesis.get("thesis_survives") is True:
            thesis_score = 100.0
        elif thesis.get("thesis_survives") is False:
            thesis_score = 25.0
        else:
            thesis_score = 50.0

        evidence = cls.safe_dict(candidate.get("provider_evidence"))
        source_count = cls.number(evidence.get("completed_evidence_count"))
        if source_count is None:
            source_count = cls.number(evidence.get("completed_source_count"))
        source_breadth_score = 35.0 if source_count is None else min(
            100.0,
            max(0.0, source_count / 8.0 * 100.0),
        )

        raw = (
            opportunity_score * 0.30
            + evidence_score * 0.20
            + expected_return_score * 0.15
            + valuation_score * 0.10
            + risk_score * 0.08
            + technical_score * 0.05
            + thesis_score * 0.07
            + source_breadth_score * 0.05
        )
        # The weighted average preserves material differences between
        # candidates. A small, explicit residual-uncertainty deduction means
        # no investment is presented as a false 100/100 certainty score.
        uncertainty_inputs = [
            evidence_score,
            valuation_score,
            risk_score,
            technical_score,
            thesis_score,
            source_breadth_score,
        ]
        residual_uncertainty = max(
            0.25,
            sum(100.0 - value for value in uncertainty_inputs) / len(uncertainty_inputs) * 0.05,
        )
        score = round(max(0.0, raw - residual_uncertainty), 1)
        return {
            "score": score,
            "maximum_score": 100.0,
            "label": "Decision rating",
            "meaning": "A 0-100 ranking score with an explicit residual-uncertainty deduction; it is not a probability of return.",
            "formula": {
                "opportunity": 0.30,
                "evidence_quality": 0.20,
                "expected_return": 0.15,
                "valuation_reliability": 0.10,
                "risk_quality": 0.08,
                "technical_context": 0.05,
                "thesis_strength": 0.07,
                "source_breadth": 0.05,
            },
            "components": {
                "opportunity": round(opportunity_score, 1),
                "evidence_quality": round(evidence_score, 1),
                "expected_return": round(expected_return_score, 1),
                "valuation_reliability": valuation_score,
                "risk_quality": round(risk_score, 1),
                "technical_context": round(technical_score, 1),
                "thesis_strength": thesis_score,
                "source_breadth": round(source_breadth_score, 1),
                "residual_uncertainty_deduction": round(residual_uncertainty, 2),
            },
        }

    # ========================================================
    # CANDIDATE ENRICHMENT
    # ========================================================

    @classmethod
    def prepare_candidates(
        cls,
        scan,
    ):

        candidates = []

        for item in scan.get(
            "results",
            [],
        ):

            score = (
                cls.candidate_score(
                    item
                )
            )

            if (
                score is None
                or score < cls.MIN_PROTOTYPE_CONVICTION
            ):
                continue

            candidate = dict(
                item
            )

            candidate[
                "portfolio_conviction"
            ] = score

            master_decision = cls.safe_dict(candidate.get("master_decision"))
            confidence_detail = cls.safe_dict(master_decision.get("research_confidence"))
            candidate["opportunity_score"] = cls.number(master_decision.get("opportunity_score"))
            candidate["research_confidence"] = (
                cls.number(confidence_detail.get("score"))
                or cls.number(master_decision.get("confidence"))
            )
            candidate["decision_rating"] = cls.decision_rating_detail(candidate)

            current_price = cls.number(
                candidate.get(
                    "current_price"
                )
            )

            intrinsic_value = cls.number(
                candidate.get(
                    "base_intrinsic_value"
                )
            )

            if (
                current_price
                and intrinsic_value
                and current_price > 0
            ):

                candidate[
                    "valuation_upside"
                ] = (
                    intrinsic_value
                    / current_price
                ) - 1

            else:

                candidate[
                    "valuation_upside"
                ] = None

            candidates.append(
                candidate
            )

        candidates.sort(
            key=lambda item:
                cls.number(cls.safe_dict(item.get("decision_rating")).get("score"))
                or cls.number(item.get("portfolio_conviction"))
                or -1,
            reverse=True,
        )

        return candidates

    # ========================================================
    # DIVERSIFICATION
    # ========================================================

    @staticmethod
    def issuer_key(candidate):
        """Identify a single economic issuer across multiple share classes.

        An index can contain two listed lines for one business (for example,
        Class A and Class B shares).  Treating them as independent holdings
        would create a false appearance of diversification.
        """
        name = str(
            candidate.get("name")
            or candidate.get("company_name")
            or candidate.get("ticker")
            or ""
        ).strip()
        name = re.sub(
            r"\s*\((?:class|series)\s+[a-z0-9]+\)\s*$",
            "",
            name,
            flags=re.IGNORECASE,
        )
        name = re.sub(
            r"\s+(?:class|series)\s+[a-z0-9]+\s*$",
            "",
            name,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", name).strip().casefold()

    @classmethod
    def can_add(
        cls,
        candidate,
        sector_counts,
        max_positions_per_sector,
        issuer_keys=None,
    ):

        sector = (
            candidate.get(
                "sector"
            )
            or "Unknown"
        )

        current_sector_count = (
            sector_counts.get(
                sector,
                0,
            )
        )

        issuer_keys = issuer_keys or set()
        return (
            current_sector_count < max_positions_per_sector
            and cls.issuer_key(candidate) not in issuer_keys
        )

    # ========================================================
    # SELECTION
    # ========================================================

    @classmethod
    def select(
        cls,
        candidates,
        number_of_stocks,
        max_weight,
        max_sector_weight,
        minimum_positions=None,
        cash_weight=0.0,
    ):

        selected = []

        sector_counts = {}
        issuer_keys = set()

        investable_weight = 1.0 - (cls.number(cash_weight) or 0.0)
        minimum_distinct_sectors = int(
            (investable_weight + max_sector_weight - 0.000000001)
            / max_sector_weight
        )
        if minimum_distinct_sectors < 1:
            raise ValueError(
                "The sector limit is lower than the maximum "
                "position weight, so no holding can be sized safely."
            )

        def add(candidate):
            issuer = cls.issuer_key(candidate)
            if issuer in issuer_keys or len(selected) >= number_of_stocks:
                return False
            sector = candidate.get("sector") or "Unknown"
            selected.append(candidate)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            issuer_keys.add(issuer)
            return True

        # Start with the strongest company in each needed sector.  The former
        # implementation limited every sector by the *maximum* position size,
        # which incorrectly blocked a well-diversified 15-stock portfolio even
        # where the 50% sector-weight limit left ample capacity for smaller
        # positions.
        for candidate in candidates:
            sector = candidate.get("sector") or "Unknown"
            if sector not in sector_counts:
                add(candidate)
            if len(sector_counts) >= minimum_distinct_sectors:
                break

        if len(sector_counts) < minimum_distinct_sectors:
            raise ValueError(
                "Insufficient sector-diverse candidates for the requested "
                "portfolio under the stated sector limit."
            )

        for candidate in candidates:
            if len(selected) >= number_of_stocks:
                break
            add(candidate)

        # ----------------------------------------------------
        minimum_positions = (
            number_of_stocks
            if minimum_positions is None
            else int(minimum_positions)
        )

        if len(selected) < minimum_positions:
            raise ValueError(
                "Insufficient sector-diverse candidates for the requested "
                "portfolio under the stated position and sector limits."
            )

        return selected

    # ========================================================
    # POSITION SIZING
    # ========================================================

    @classmethod
    def position_sizing_signal(
        cls,
        item,
    ):
        """Return a transparent risk-adjusted sizing signal.

        This is intentionally distinct from research confidence.  Expected
        return, evidence quality, volatility and risk quality all influence
        size, so two similarly ranked companies do not receive identical
        weights merely because an older score reached a ceiling.
        """

        opportunity = cls.number(item.get("portfolio_conviction"))
        if opportunity is None:
            master = cls.safe_dict(item.get("master_decision"))
            opportunity = cls.number(master.get("opportunity_score"))
            if opportunity is None:
                opportunity = cls.number(master.get("conviction_score"))
        opportunity = opportunity or 0.0
        confidence = cls.number(item.get("research_confidence"))
        if confidence is None:
            master = cls.safe_dict(item.get("master_decision"))
            confidence = cls.number(cls.safe_dict(master.get("research_confidence")).get("score"))
            if confidence is None:
                confidence = cls.number(master.get("confidence")) or 45.0

        expected_return = cls.number(item.get("expected_return")) or 0.0
        signals = cls.safe_dict(item.get("market_signals"))
        volatility = cls.number(signals.get("annualised_volatility"))
        volatility = min(0.80, max(0.08, volatility if volatility is not None else 0.30))
        risk_score = min(100.0, max(0.0, cls.number(signals.get("risk_score")) or 50.0))

        valuation_quality = cls.safe_dict(item.get("valuation_quality"))
        valuation_multiplier = 0.80 if str(valuation_quality.get("assessment") or "").upper() == "REVIEW" else 1.0
        terminal_value = cls.number(valuation_quality.get("terminal_value_contribution"))
        if terminal_value is not None and terminal_value > 0.75:
            valuation_multiplier *= 0.80
        thesis = cls.safe_dict(item.get("thesis"))
        if thesis.get("thesis_survives") is False:
            valuation_multiplier *= 0.75
        opportunity_component = max(0.01, opportunity / 100.0) ** 1.20
        confidence_component = 0.55 + (min(100.0, confidence) / 200.0)
        expected_component = 1.0 + min(0.50, max(-0.25, expected_return))
        volatility_component = (0.20 / volatility) ** 0.65
        risk_component = 0.55 + (risk_score / 200.0)

        signal = (
            opportunity_component
            * confidence_component
            * expected_component
            * volatility_component
            * risk_component
            * valuation_multiplier
        )
        return round(max(0.01, signal), 6)

    @classmethod
    def position_sizing_detail(cls, item):
        signals = cls.safe_dict(item.get("market_signals"))
        return {
            "method": "RISK_ADJUSTED_OPPORTUNITY_WITH_EVIDENCE_CONFIDENCE",
            "signal": cls.position_sizing_signal(item),
            "opportunity_score": cls.number(item.get("opportunity_score")) or cls.number(item.get("portfolio_conviction")),
            "research_confidence": cls.number(item.get("research_confidence")),
            "expected_return": cls.number(item.get("expected_return")),
            "annualised_volatility": cls.number(signals.get("annualised_volatility")),
            "risk_score": cls.number(signals.get("risk_score")),
        }

    @classmethod
    def calculate_weights(
        cls,
        selected,
        max_weight,
        min_weight,
        cash_weight=0.0,
        max_sector_weight=None,
        soft_sector_weight=SOFT_SECTOR_WEIGHT,
    ):

        if not selected:

            return []

        max_weight = cls.number(
            max_weight
        )

        min_weight = cls.number(
            min_weight
        )

        cash_weight = cls.number(
            cash_weight
        )

        count = len(
            selected
        )

        if (
            max_weight is None
            or min_weight is None
            or min_weight < 0
            or max_weight <= 0
            or min_weight > max_weight
            or cash_weight is None
            or cash_weight < 0
            or cash_weight >= 1
        ):

            raise ValueError(
                "Invalid position-weight constraints."
            )

        investable_weight = 1.0 - cash_weight

        if (
            count * max_weight
            < investable_weight - 0.000001
        ):

            raise ValueError(
                "Infeasible portfolio constraints: "
                f"{count} holdings with a "
                f"{max_weight:.2%} maximum weight "
                "cannot allocate the investable portion of the portfolio."
            )

        if (
            count * min_weight
            > investable_weight + 0.000001
        ):

            raise ValueError(
                "Infeasible portfolio constraints: "
                f"{count} holdings with a "
                f"{min_weight:.2%} minimum weight "
                "exceed the investable portion of the portfolio."
            )

        max_sector_weight = cls.number(max_sector_weight)
        soft_sector_weight = cls.number(soft_sector_weight)
        if max_sector_weight is not None and not 0 < max_sector_weight <= 1:
            raise ValueError("Invalid sector weight constraint.")
        if soft_sector_weight is None or not 0 < soft_sector_weight <= 1:
            raise ValueError("Invalid soft sector weight constraint.")

        raw = [cls.position_sizing_signal(item) for item in selected]
        sector_indices = {}
        for index, item in enumerate(selected):
            sector = item.get("sector") or "Unknown"
            sector_indices.setdefault(sector, []).append(index)

        # Apply a smooth sector concentration penalty before the hard ceiling.
        sector_totals = {
            sector: sum(raw[index] for index in indices)
            for sector, indices in sector_indices.items()
        }
        raw_total = sum(raw) or 1.0
        for sector, indices in sector_indices.items():
            sector_share = sector_totals[sector] / raw_total
            if sector_share > soft_sector_weight:
                for index in indices:
                    raw[index] *= (soft_sector_weight / sector_share) ** 0.5

        weights = [min_weight for _ in selected]
        remaining = investable_weight - sum(weights)
        position_capacity = [max_weight - min_weight for _ in selected]

        # Allocate additional capital to sectors first, then distribute each
        # sector allocation among its constituents.  This enforces the stated
        # sector limit directly rather than relying on an accidental count cap.
        sector_capacity = {}
        sector_scores = {}
        for sector, indices in sector_indices.items():
            base_weight = sum(weights[index] for index in indices)
            available = max_sector_weight - base_weight
            if available < -0.000000001:
                raise ValueError(
                    "Infeasible portfolio constraints: the selected holdings' "
                    "minimum weights exceed the sector limit."
                )
            sector_capacity[sector] = min(
                available,
                sum(position_capacity[index] for index in indices),
            )
            sector_scores[sector] = sum(raw[index] for index in indices)

        sector_additions = {sector: 0.0 for sector in sector_indices}
        while remaining > 0.000000001:
            active = [
                sector for sector, capacity in sector_capacity.items()
                if capacity > 0.000000001
            ]
            if not active:
                raise ValueError(
                    "Unable to allocate the remaining portfolio weight within the sector limits."
                )
            active_score = sum(sector_scores[sector] for sector in active)
            if active_score <= 0:
                active_score = float(len(active))
                proposals = {sector: remaining / active_score for sector in active}
            else:
                proposals = {
                    sector: remaining * sector_scores[sector] / active_score
                    for sector in active
                }
            capped = [
                sector for sector in active
                if proposals[sector] > sector_capacity[sector] + 0.000000001
            ]
            if not capped:
                for sector in active:
                    sector_additions[sector] += proposals[sector]
                remaining = 0.0
                break
            for sector in capped:
                allocation = sector_capacity[sector]
                sector_additions[sector] += allocation
                remaining -= allocation
                sector_capacity[sector] = 0.0

        for sector, indices in sector_indices.items():
            sector_remaining = sector_additions[sector]
            while sector_remaining > 0.000000001:
                active = [
                    index for index in indices
                    if position_capacity[index] > 0.000000001
                ]
                if not active:
                    raise ValueError(
                        "Unable to allocate a sector within position-weight limits."
                    )
                active_score = sum(raw[index] for index in active)
                if active_score <= 0:
                    active_score = float(len(active))
                    proposals = {index: sector_remaining / active_score for index in active}
                else:
                    proposals = {
                        index: sector_remaining * raw[index] / active_score
                        for index in active
                    }
                capped = [
                    index for index in active
                    if proposals[index] > position_capacity[index] + 0.000000001
                ]
                if not capped:
                    for index in active:
                        weights[index] += proposals[index]
                    sector_remaining = 0.0
                    break
                for index in capped:
                    allocation = position_capacity[index]
                    weights[index] += allocation
                    sector_remaining -= allocation
                    position_capacity[index] = 0.0

        return [
            round(
                weight,
                6,
            )
            for weight in weights
        ]

    # ========================================================
    # REASONING
    # ========================================================

    @classmethod
    def build_reasoning(
        cls,
        item,
    ):

        conviction = cls.number(
            item.get(
                "portfolio_conviction"
            )
        )

        investment_score = cls.number(
            item.get(
                "investment_case_score"
            )
        )

        expected_return = cls.number(
            item.get(
                "expected_return"
            )
        )

        valuation_upside = cls.number(
            item.get(
                "valuation_upside"
            )
        )

        thesis = cls.safe_dict(
            item.get(
                "thesis"
            )
        )

        reasons = []

        if (
            investment_score is not None
            and investment_score >= 80
        ):

            reasons.append(
                "Very strong underlying "
                "investment case."
            )

        elif (
            investment_score is not None
            and investment_score >= 70
        ):

            reasons.append(
                "Strong underlying "
                "investment case."
            )

        if (
            expected_return is not None
            and expected_return >= 0.20
        ):

            reasons.append(
                "Attractive expected return."
            )

        elif (
            expected_return is not None
            and expected_return >= 0.05
        ):

            reasons.append(
                "Positive expected return."
            )

        if (
            valuation_upside is not None
            and valuation_upside >= 0.25
        ):

            reasons.append(
                "Material upside to base "
                "intrinsic value."
            )

        if thesis.get(
            "thesis_survives"
        ) is True:

            reasons.append(
                "Investment thesis survived "
                "adversarial review."
            )

        master = cls.safe_dict(item.get("master_decision"))
        cautions = master.get("caution_reasons")
        if isinstance(cautions, list) and cautions:
            reasons.append("Conditional allocation: " + str(cautions[0]))

        if not reasons:

            reasons.append(
                "Selected based on relative risk-adjusted opportunity "
                "and evidence confidence."
            )

        return {
            "summary":
                " ".join(
                    reasons
                ),

            "portfolio_conviction":
                conviction,

            # Retained alongside the legacy key above so saved portfolio
            # records explain what the score represents to the website.
            "opportunity_score": conviction,

            "investment_case_score":
                investment_score,

            "expected_return":
                expected_return,

            "valuation_upside":
                valuation_upside,

            "thesis_result":
                thesis.get(
                    "result"
                ),

            "material_negative":
                thesis.get(
                    "material_negative",
                    0,
                ),

            "technical_score": cls.number(
                cls.safe_dict(
                    item.get("market_signals")
                ).get("technical_score")
            ),

            "risk_score": cls.number(
                cls.safe_dict(
                    item.get("market_signals")
                ).get("risk_score")
            ),

            "sentiment_score": cls.number(
                cls.safe_dict(
                    item.get("sentiment")
                ).get("score")
            ),
        }

    # ========================================================
    # CONSTRUCT
    # ========================================================

    @classmethod
    def construct(
        cls,
        scan,
        number_of_stocks=DEFAULT_HOLDINGS,
        max_weight=DEFAULT_MAX_WEIGHT,
        min_weight=DEFAULT_MIN_WEIGHT,
        max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT,
        cash_weight=DEFAULT_CASH_WEIGHT,
    ):

        max_weight = cls.number(max_weight)
        min_weight = cls.number(min_weight)
        max_sector_weight = cls.number(max_sector_weight)
        cash_weight = cls.number(cash_weight)

        if (
            max_weight is None
            or min_weight is None
            or max_sector_weight is None
            or max_sector_weight <= 0
            or max_sector_weight > 1
            or max_sector_weight + 0.000000001 < max_weight
            or cash_weight is None
            or cash_weight < 0
            or cash_weight >= 1
        ):
            raise ValueError(
                "Invalid portfolio constraints: the sector limit must be "
                "between the maximum individual position weight and 100%."
            )

        if abs(cash_weight) > 0.000000001:
            raise ValueError(
                "Cash allocation is disabled by the prototype portfolio policy."
            )

        candidates = (
            cls.prepare_candidates(
                scan
            )
        )

        if not candidates:

            raise RuntimeError(
                "No eligible audited "
                "portfolio candidates "
                "were found."
            )

        number_of_stocks = min(
            int(
                number_of_stocks
            ),
            len(candidates),
        )

        selected = cls.select(
            candidates,
            number_of_stocks,
            max_weight,
            max_sector_weight,
            minimum_positions=min(5, number_of_stocks),
            cash_weight=cash_weight,
        )

        weights = cls.calculate_weights(
            selected,
            max_weight,
            min_weight,
            cash_weight=cash_weight,
            max_sector_weight=max_sector_weight,
        )

        holdings = []

        for rank, (
            item,
            weight,
        ) in enumerate(
            zip(
                selected,
                weights,
            ),
            start=1,
        ):

            holdings.append(
                {
                    "rank":
                        rank,

                    "ticker":
                        item[
                            "ticker"
                        ],

                    "name":
                        item.get(
                            "name"
                        ),

                    "sector":
                        item.get(
                            "sector"
                        ),

                    "industry":
                        item.get(
                            "industry"
                        ),

                    "index_membership":
                        item.get(
                            "index_membership",
                            [],
                        ),

                    "weight":
                        weight,

                    "portfolio_conviction":
                        item[
                            "portfolio_conviction"
                        ],

                    "opportunity_score": item.get(
                        "opportunity_score",
                        item["portfolio_conviction"],
                    ),

                    "research_confidence": item.get(
                        "research_confidence"
                    ),

                    "decision_rating": item.get(
                        "decision_rating"
                    ) or cls.decision_rating_detail(item),

                    "provider_evidence": item.get(
                        "provider_evidence",
                        {},
                    ),

                    "portfolio_decision": "SELECTED",

                    "investment_case_score":
                        item.get(
                            "investment_case_score"
                        ),

                    "current_price":
                        item.get(
                            "current_price"
                        ),

                    "base_intrinsic_value":
                        item.get(
                            "base_intrinsic_value"
                        ),

                    "expected_return":
                        item.get(
                            "expected_return"
                        ),

                    "annualised_expected_return":
                        item.get(
                            "annualised_expected_return"
                        ),

                    "valuation_horizon_years":
                        item.get(
                            "valuation_horizon_years"
                        ),

                    "valuation_upside":
                        item.get(
                            "valuation_upside"
                        ),

                    "decision":
                        item.get(
                            "decision"
                        ),

                    "thesis":
                        item.get(
                            "thesis",
                            {},
                        ),

                    "audit":
                        item.get(
                            "audit",
                            {},
                        ),

                    "market_signals":
                        item.get(
                            "market_signals",
                            {},
                        ),

                    "sentiment":
                        item.get(
                            "sentiment",
                            {},
                        ),

                    "monitoring_conditions":
                        item.get(
                            "monitoring_conditions",
                            [],
                        ),

                    "decision_reason": item.get("decision_reason"),

                    "bull_case": item.get("bull_case"),

                    "bear_case": item.get("bear_case"),

                    "catalysts": item.get("catalysts", []),

                    "data_as_of": item.get("data_as_of"),

                    "research_pipeline_version": item.get(
                        "research_pipeline_version"
                    ),

                    "position_sizing": cls.position_sizing_detail(item),

                    "reasoning":
                        cls.build_reasoning(
                            item
                        ),
                }
            )

        # ----------------------------------------------------
        # Portfolio statistics
        # ----------------------------------------------------

        expected_returns = [
            cls.number(
                item.get(
                    "expected_return"
                )
            )
            for item in holdings
        ]

        expected_returns = [
            value
            for value in expected_returns
            if value is not None
        ]

        weighted_expected_return = 0.0
        weighted_annualised_return = 0.0
        annualised_weight_coverage = 0.0

        for holding in holdings:

            expected = cls.number(
                holding.get(
                    "expected_return"
                )
            )

            if expected is not None:

                weighted_expected_return += (
                    holding[
                        "weight"
                    ]
                    * expected
                )

            annualised = cls.number(
                holding.get(
                    "annualised_expected_return"
                )
            )
            if annualised is not None:
                weighted_annualised_return += (
                    holding["weight"] * annualised
                )
                annualised_weight_coverage += holding["weight"]

        valuation_horizons = {
            cls.number(holding.get("valuation_horizon_years"))
            for holding in holdings
            if cls.number(holding.get("valuation_horizon_years")) is not None
        }
        valuation_horizon_years = (
            next(iter(valuation_horizons))
            if len(valuation_horizons) == 1
            else None
        )

        sector_weights = {}

        for holding in holdings:

            sector = (
                holding.get(
                    "sector"
                )
                or "Unknown"
            )

            sector_weights[
                sector
            ] = (
                sector_weights.get(
                    sector,
                    0.0,
                )
                + holding[
                    "weight"
                ]
            )

        if any(
            value > max_sector_weight + 0.000001
            for value in sector_weights.values()
        ):
            raise RuntimeError(
                "Portfolio construction produced a sector concentration "
                "above its stated limit."
            )

        return {
            "version":
                cls.VERSION,

            "created_at":
                cls.now(),

            "status":
                "RESEARCH_GATED_READY",

            "universe":
                scan.get(
                    "universe"
                ),

            "universe_count":
                scan.get(
                    "requested_count"
                ),

            "researched_count":
                scan.get(
                    "completed_count"
                ),

            "audit_pass_count":
                scan.get(
                    "audit_pass_count"
                ),

            "eligible_count":
                len(
                    candidates
                ),

            "number_of_stocks":
                len(
                    holdings
                ),

            "constraints":
                {
                    "max_weight":
                        max_weight,

                    "min_weight":
                        min_weight,

                    "max_sector_weight":
                        max_sector_weight,

                    "cash_weight":
                        cash_weight,
                },

            "cash_weight": cash_weight,

            "portfolio_expected_return":
                round(
                    weighted_expected_return,
                    6,
                ),

            "portfolio_annualised_expected_return": (
                round(weighted_annualised_return, 6)
                if annualised_weight_coverage >= 0.999999
                else None
            ),

            "valuation_horizon_years": (
                valuation_horizon_years
            ),

            "return_disclosure": {
                "label": "Model-implied valuation gap",
                "meaning": (
                    "A weighted comparison of current prices with the base-case "
                    "DCF values. It is not a promise or a time-certain forecast."
                ),
                "forecast_horizon_years": (
                    valuation_horizon_years
                ),
                "annualised_coverage": round(annualised_weight_coverage, 6),
            },

            "sector_weights":
                {
                    key:
                        round(
                            value,
                            6,
                        )
                    for key, value
                    in sector_weights.items()
                },

            "holdings":
                holdings,

            "method": "RISK_ADJUSTED_EVIDENCE_WEIGHTED_DIVERSIFIED_SELECTION",

            "selection_logic":
                [
                    "Research must complete.",
                    "Evidence audit must PASS or PASS_WITH_WARNINGS.",
                    "Warning-only audits are disclosed and receive a conviction penalty.",
                    "Master portfolio decision must mark the company ELIGIBLE.",
                    "Expected return must clear the master portfolio threshold.",
                    "Evidence confidence is capped below certainty.",
                    "Position sizing uses opportunity, evidence confidence, expected return, volatility and risk quality.",
                    "Sector concentration has a 25% soft penalty and a stated hard limit.",
                    "Individual position size is constrained.",
                    "The prototype is fully invested; cash is not an allocation option.",
                    "The completed portfolio receives a separate risk review.",
                ],
        }

    # ========================================================
    # SAVE
    # ========================================================

    @staticmethod
    def save(
        portfolio,
        path=None,
    ):

        if path is None:

            path = (
                "data/research/"
                "portfolios/"
                "prototype.json"
            )

        path = Path(
            path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                portfolio,
                file,
                indent=2,
            )

        return str(
            path
        )


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scan",
        default=(
            "data/research/"
            "universe_scans/"
            "sp500.json"
        ),
    )

    parser.add_argument(
        "--stocks",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--max-weight",
        type=float,
        default=0.15,
    )

    parser.add_argument(
        "--min-weight",
        type=float,
        default=0.03,
    )

    parser.add_argument(
        "--max-sector-weight",
        type=float,
        default=0.50,
    )

    args = parser.parse_args()

    scan_path = Path(
        args.scan
    )

    if not scan_path.exists():

        raise SystemExit(
            "Scan file does not exist: "
            f"{scan_path}\n"
            "Run UniverseScanner first."
        )

    with scan_path.open(
        "r",
        encoding="utf-8",
    ) as file:

        scan = json.load(
            file
        )

    portfolio = (
        PortfolioEngine.construct(
            scan,
            number_of_stocks=args.stocks,
            max_weight=args.max_weight,
            min_weight=args.min_weight,
            max_sector_weight=(
                args.max_sector_weight
            ),
        )
    )

    saved = (
        PortfolioEngine.save(
            portfolio
        )
    )

    print()
    print("=" * 80)
    print("PORTFOLIO CONSTRUCTION COMPLETE")
    print("=" * 80)

    print()
    print(
        "Universe:",
        portfolio[
            "universe"
        ],
    )

    print(
        "Researched:",
        portfolio[
            "researched_count"
        ],
    )

    print(
        "Audit PASS:",
        portfolio[
            "audit_pass_count"
        ],
    )

    print(
        "Eligible:",
        portfolio[
            "eligible_count"
        ],
    )

    print(
        "Holdings:",
        portfolio[
            "number_of_stocks"
        ],
    )

    print()
    print(
        "EXPECTED PORTFOLIO RETURN:",
        portfolio[
            "portfolio_expected_return"
        ],
    )

    print()
    print("HOLDINGS")

    for holding in portfolio[
        "holdings"
    ]:

        print(
            holding[
                "rank"
            ],
            "|",
            holding[
                "ticker"
            ],
            "|",
            f"{holding['weight']:.2%}",
            "|",
            holding[
                "portfolio_conviction"
            ],
            "|",
            holding[
                "reasoning"
            ][
                "summary"
            ],
        )

    print()
    print(
        "Saved:",
        saved,
    )

    print()
    print("=" * 80)
    print("PORTFOLIO ENGINE PASSED")
    print("=" * 80)
