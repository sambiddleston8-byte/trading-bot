from __future__ import annotations

"""Audit whether a DCF output is reliable enough for portfolio construction."""

from datetime import datetime
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from core.decision_ledger import LedgerIntegrityError, canonical_timestamp


ASSUMPTION_FIELDS = {
    "risk_free_rate", "equity_risk_premium", "beta", "pre_tax_cost_of_debt",
    "marginal_tax_rate", "market_value_equity_weight", "market_value_debt_weight",
    "terminal_growth_rate",
}
UNIT_RATE_FIELDS = {
    "risk_free_rate", "equity_risk_premium", "pre_tax_cost_of_debt",
    "marginal_tax_rate", "market_value_equity_weight", "market_value_debt_weight",
    "terminal_growth_rate",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ValuationQualityEngine:
    VERSION = "2.1-authenticated-dcf-source-content"

    @staticmethod
    def _authenticated_sources(value: Any) -> dict[str, dict[str, Any]]:
        if value is None:
            return {}
        try:
            records = value.verify()
        except (AttributeError, LedgerIntegrityError):
            return {}
        return {
            item["content_evidence_id"]: item
            for item in records
            if item.get("source_bytes_authenticated") is True
        }

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _assumption_evidence(
        value: Any, data_cutoff: Any, authenticated_sources: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        evidence = value if isinstance(value, dict) else {}
        failures: list[str] = []
        try:
            cutoff = datetime.fromisoformat(canonical_timestamp(data_cutoff))
        except (TypeError, ValueError):
            return None, ["A valid valuation data cutoff is unavailable."]
        if set(evidence) != ASSUMPTION_FIELDS:
            return None, ["Complete point-in-time DCF assumption evidence is unavailable."]
        resolved: dict[str, Any] = {}
        for name in sorted(ASSUMPTION_FIELDS):
            item = evidence.get(name)
            if not isinstance(item, dict):
                failures.append(f"{name} evidence is invalid.")
                continue
            number = ValuationQualityEngine.number(item.get("value"))
            try:
                effective = datetime.fromisoformat(canonical_timestamp(item.get("effective_at")))
                public = datetime.fromisoformat(canonical_timestamp(item.get("publicly_available_at")))
                retrieved = datetime.fromisoformat(canonical_timestamp(item.get("retrieved_at")))
            except (TypeError, ValueError):
                failures.append(f"{name} timestamps are invalid.")
                continue
            source_uri = str(item.get("source_uri") or "")
            parsed = urlsplit(source_uri)
            source_hash = str(item.get("source_input_sha256") or "").lower()
            locator = str(item.get("source_locator") or "").strip()
            content_evidence_id = str(item.get("content_evidence_id") or "").strip()
            authenticated = authenticated_sources.get(content_evidence_id)
            authentication_invalid = (
                authenticated is None
                or authenticated.get("source_input_sha256") != source_hash
                or authenticated.get("source_uri") != source_uri
                or authenticated.get("retrieved_at") != retrieved.isoformat()
            )
            if (
                number is None or number < 0 or public > retrieved or retrieved > cutoff
                or effective > cutoff
                or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password
                or not SHA256_PATTERN.fullmatch(source_hash) or not locator
            ):
                failures.append(f"{name} point-in-time source evidence is invalid.")
                continue
            if authentication_invalid:
                failures.append(f"{name} source bytes are not authenticated.")
                continue
            resolved[name] = {
                "value": number,
                "effective_at": effective.isoformat(),
                "publicly_available_at": public.isoformat(),
                "retrieved_at": retrieved.isoformat(),
                "source_uri": source_uri,
                "source_input_sha256": source_hash,
                "source_locator": locator,
                "content_evidence_id": content_evidence_id,
                "content_evidence_record_hash": authenticated["record_hash"],
            }
            if name in UNIT_RATE_FIELDS and number > 1:
                failures.append(f"{name} must be a decimal rate between zero and one.")
            if name == "beta" and number > 10:
                failures.append("beta exceeds the permitted evidence sanity bound of 10.")
        if failures:
            return None, failures
        equity_weight = resolved["market_value_equity_weight"]["value"]
        debt_weight = resolved["market_value_debt_weight"]["value"]
        if abs(equity_weight + debt_weight - 1.0) > 1e-9:
            failures.append("Market-value capital weights must sum to one.")
        tax_rate = resolved["marginal_tax_rate"]["value"]
        if tax_rate > 1 or equity_weight > 1 or debt_weight > 1:
            failures.append("Tax rate and capital weights must be decimal rates between zero and one.")
        cost_equity = (
            resolved["risk_free_rate"]["value"]
            + resolved["beta"]["value"] * resolved["equity_risk_premium"]["value"]
        )
        wacc = (
            equity_weight * cost_equity
            + debt_weight * resolved["pre_tax_cost_of_debt"]["value"] * (1 - tax_rate)
        )
        if wacc <= resolved["terminal_growth_rate"]["value"]:
            failures.append("Evidence-derived WACC must exceed terminal growth.")
        if failures:
            return None, failures
        identity = hashlib.sha256(
            repr(sorted((name, item["source_input_sha256"], item["source_locator"])
                        for name, item in resolved.items())).encode("utf-8")
        ).hexdigest()
        return {
            "assumption_evidence_id": "DCF-" + identity[:32].upper(),
            "assumptions": resolved,
            "cost_of_equity": cost_equity,
            "evidence_derived_wacc": wacc,
            "terminal_growth_rate": resolved["terminal_growth_rate"]["value"],
            "valuation_data_cutoff": cutoff.isoformat(),
            "source_content_authentication_status": "ALL_SOURCE_BYTES_HASH_VERIFIED",
        }, []

    @staticmethod
    def _sensitivity(
        value: Any, *, base_wacc: float | None, base_growth: float | None
    ) -> tuple[dict[str, list[float]] | None, list[str]]:
        matrix = value if isinstance(value, dict) else {}
        failures: list[str] = []
        resolved: dict[str, list[float]] = {}
        for field in ("wacc_values", "terminal_growth_values"):
            raw = matrix.get(field)
            if not isinstance(raw, list) or len(raw) < 3:
                failures.append(f"{field} requires at least three sensitivity values.")
                continue
            values = [ValuationQualityEngine.number(item) for item in raw]
            if any(item is None or item < 0 or item > 1 for item in values):
                failures.append(f"{field} must contain decimal rates between zero and one.")
                continue
            numeric = [float(item) for item in values if item is not None]
            if numeric != sorted(set(numeric)):
                failures.append(f"{field} must be unique and strictly increasing.")
                continue
            resolved[field] = numeric
        if failures or base_wacc is None or base_growth is None:
            return None, failures or ["Reported base WACC and terminal growth are required."]
        if not resolved["wacc_values"][0] < base_wacc < resolved["wacc_values"][-1]:
            failures.append("WACC sensitivity values must bracket the reported base WACC.")
        if not (
            resolved["terminal_growth_values"][0]
            < base_growth
            < resolved["terminal_growth_values"][-1]
        ):
            failures.append(
                "Terminal-growth sensitivity values must bracket the reported base growth."
            )
        if resolved["terminal_growth_values"][-1] >= resolved["wacc_values"][0]:
            failures.append("Every sensitivity-grid WACC must exceed every terminal-growth rate.")
        return (resolved if not failures else None), failures

    @classmethod
    def assess(
        cls, valuation: Any, decision: Any, authenticated_source_ledger: Any = None
    ) -> dict[str, Any]:
        valuation_data = cls.mapping(valuation)
        decision_data = cls.mapping(decision)
        decision_valuation = cls.mapping(decision_data.get("valuation"))
        confidence = cls.mapping(decision_data.get("confidence"))
        forecast_validation = cls.mapping(valuation_data.get("Forecast Validation"))
        scenarios = cls.mapping(valuation_data.get("Scenarios"))
        base_scenario = cls.mapping(scenarios.get("Base"))

        current_price = cls.number(
            decision_valuation.get("current_price")
            if decision_valuation else valuation_data.get("Current Price")
        )
        intrinsic_value = cls.number(
            decision_valuation.get("base_intrinsic_value")
            if decision_valuation else cls.mapping(valuation_data.get("Intrinsic Value")).get("Base")
        )
        expected_return = cls.number(
            decision_valuation.get("expected_return")
            if decision_valuation else cls.mapping(valuation_data.get("Expected Return")).get("Base")
        )
        estimate_consistency = str(
            confidence.get("estimate_consistency")
            or forecast_validation.get("estimate_consistency")
            or forecast_validation.get("Overall Confidence")
            or "UNAVAILABLE"
        ).upper()
        terminal_value_contribution = cls.number(
            valuation_data.get("Terminal Value Contribution")
            or base_scenario.get("Terminal Value % of Enterprise Value")
        )
        assumption_evidence, assumption_failures = cls._assumption_evidence(
            valuation_data.get("DCF Assumption Evidence"),
            valuation_data.get("Valuation Data Cutoff"),
            cls._authenticated_sources(authenticated_source_ledger),
        )
        reported_wacc = cls.number(valuation_data.get("WACC"))
        reported_terminal_growth = cls.number(valuation_data.get("Terminal Growth"))
        sensitivity, sensitivity_failures = cls._sensitivity(
            valuation_data.get("DCF Sensitivity Matrix"),
            base_wacc=reported_wacc,
            base_growth=reported_terminal_growth,
        )

        failures: list[str] = []
        warnings: list[str] = []
        if current_price is None or current_price <= 0:
            failures.append("Current market price is unavailable.")
        if intrinsic_value is None or intrinsic_value <= 0:
            failures.append("Base intrinsic value is unavailable or invalid.")
        if expected_return is None:
            failures.append("Expected return is unavailable.")
        # This is internal estimate consistency, not realised forecast skill.
        if estimate_consistency in {"INSUFFICIENT_DATA", "UNAVAILABLE"}:
            failures.append("Forward estimate inputs do not have sufficient consistency evidence.")
        elif estimate_consistency in {"LOW", "REVIEW"}:
            warnings.append("Forward estimate inputs have limited internal consistency.")
        if terminal_value_contribution is not None and terminal_value_contribution > 0.85:
            warnings.append("More than 85% of enterprise value comes from terminal value.")
        elif terminal_value_contribution is not None and terminal_value_contribution > 0.75:
            warnings.append("More than 75% of enterprise value comes from terminal value.")
        if assumption_failures:
            failures.extend(assumption_failures)
        elif assumption_evidence is not None:
            if (
                reported_wacc is None
                or abs(reported_wacc - assumption_evidence["evidence_derived_wacc"]) > 1e-9
            ):
                failures.append("Reported WACC does not match the evidence-derived WACC.")
            if (
                reported_terminal_growth is None
                or abs(
                    reported_terminal_growth - assumption_evidence["terminal_growth_rate"]
                ) > 1e-9
            ):
                failures.append(
                    "Reported terminal growth does not match its point-in-time evidence."
                )
        failures.extend(sensitivity_failures)

        assessment = "FAIL" if failures else ("REVIEW" if warnings else "PASS")
        score = 0.0 if failures else (70.0 if warnings else 100.0)
        expected_return_eligible = (
            not failures and assumption_evidence is not None and sensitivity is not None
        )
        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "assessment": assessment,
            "score": score,
            "estimate_consistency": estimate_consistency,
            "forecast_accuracy_status": "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE",
            "dcf_assumption_evidence": assumption_evidence,
            "dcf_assumption_evidence_complete": assumption_evidence is not None,
            "sensitivity_matrix": sensitivity,
            "sensitivity_matrix_complete": sensitivity is not None,
            "portfolio_expected_return_eligible": expected_return_eligible,
            "expected_return_status": (
                "EVIDENCE_GROUNDED_BUT_FORECAST_ACCURACY_UNCALIBRATED"
                if expected_return_eligible
                else "SOURCE_CONTENT_AUTHENTICATION_REQUIRED_NOT_PORTFOLIO_ELIGIBLE"
            ),
            "terminal_value_contribution": terminal_value_contribution,
            "failures": failures,
            "warnings": warnings,
        }
