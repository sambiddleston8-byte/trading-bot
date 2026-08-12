from __future__ import annotations

"""Cash-flow-neutral return for the matched-capital S&P counterfactual."""

from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance.portfolio_benchmark_valuation import (
    SimulatedPortfolioBenchmarkValuationLedger,
)
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.portfolio_return import (
    TimeWeightedPortfolioReturnLedger,
    _as_datetime,
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
)


PORTFOLIO_BENCHMARK_RETURN_SCHEMA_VERSION = "1.0"
PORTFOLIO_BENCHMARK_RETURN_CALCULATION_VERSION = (
    "matched-sp500-boundary-cash-flow-time-weighted-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
PORTFOLIO_BENCHMARK_RETURN_FORMULA = {
    "pre_flow_equity": (
        "base_benchmark_total_equity + cumulative_prior_external_cash_flows"
    ),
    "subperiod_return": (
        "benchmark_pre_flow_equity / previous_benchmark_post_flow_equity - 1"
    ),
    "post_flow_equity": (
        "benchmark_pre_flow_equity + boundary_external_cash_flow"
    ),
    "linked_return": "product(1 + benchmark_subperiod_return) - 1",
    "cash_flow_timing": "AFTER_MATCHED_ASSET_AND_BENCHMARK_VALUATION_BOUNDARY",
    "cash_flow_investment_policy": "EXTERNAL_FLOWS_HELD_AS_ZERO_RETURN_CASH",
    "midperiod_cash_flow_policy": "NOT_SUPPORTED_REQUIRES_EXACT_BOUNDARY_VALUATION",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _result_id(portfolio_version: str, horizon: str) -> str:
    material = [
        portfolio_version,
        horizon,
        PORTFOLIO_BENCHMARK_RETURN_CALCULATION_VERSION,
    ]
    return "PBRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    funding: Mapping[str, Any],
    valuations: Sequence[Mapping[str, Any]],
    flows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    initial_funding = _fraction(funding["exact_amount"], "initial funding")
    if initial_funding <= 0:
        raise ValueError("Initial funding must be positive")
    flow_by_asset_valuation = {item["valuation_id"]: item for item in flows}
    prior_post_flow_equity = initial_funding
    cumulative_prior_flow = Fraction(0)
    linked_growth = Fraction(1)
    subperiods = []
    for valuation in valuations:
        base_equity = _fraction(
            valuation["exact_fractions"]["benchmark_total_equity"],
            "base benchmark total equity",
        )
        pre_flow_equity = base_equity + cumulative_prior_flow
        if pre_flow_equity <= 0 or prior_post_flow_equity <= 0:
            raise ValueError("Benchmark subperiod equity must remain positive")
        subperiod_return = pre_flow_equity / prior_post_flow_equity - 1
        boundary_flow = flow_by_asset_valuation.get(
            valuation["asset_portfolio_valuation_id"]
        )
        signed_flow = (
            _fraction(boundary_flow["exact_signed_amount"], "boundary cash flow")
            if boundary_flow is not None
            else Fraction(0)
        )
        post_flow_equity = pre_flow_equity + signed_flow
        if post_flow_equity <= 0:
            raise ValueError("Benchmark post-flow equity must remain positive")
        linked_growth *= 1 + subperiod_return
        subperiods.append(
            {
                "horizon": valuation["horizon"],
                "horizon_label": valuation["horizon_label"],
                "benchmark_valuation_id": valuation["valuation_id"],
                "benchmark_valuation_record_hash": valuation["record_hash"],
                "asset_portfolio_valuation_id": valuation[
                    "asset_portfolio_valuation_id"
                ],
                "asset_portfolio_valuation_hash": valuation[
                    "asset_portfolio_valuation_hash"
                ],
                "effective_at": valuation["outcome_benchmark_price_effective_at"],
                "previous_benchmark_post_flow_equity": _decimal_string(
                    prior_post_flow_equity
                ),
                "base_benchmark_total_equity": _decimal_string(base_equity),
                "cumulative_prior_external_cash_flow": _decimal_string(
                    cumulative_prior_flow
                ),
                "benchmark_pre_flow_equity": _decimal_string(pre_flow_equity),
                "benchmark_subperiod_return": _decimal_string(subperiod_return),
                "boundary_cash_flow_id": (
                    boundary_flow["flow_id"] if boundary_flow is not None else None
                ),
                "boundary_cash_flow_record_hash": (
                    boundary_flow["record_hash"]
                    if boundary_flow is not None
                    else None
                ),
                "boundary_signed_cash_flow": _decimal_string(signed_flow),
                "benchmark_post_flow_equity": _decimal_string(post_flow_equity),
                "exact_fractions": {
                    "previous_benchmark_post_flow_equity": _fraction_material(
                        prior_post_flow_equity
                    ),
                    "base_benchmark_total_equity": _fraction_material(base_equity),
                    "cumulative_prior_external_cash_flow": _fraction_material(
                        cumulative_prior_flow
                    ),
                    "benchmark_pre_flow_equity": _fraction_material(pre_flow_equity),
                    "benchmark_subperiod_return": _fraction_material(
                        subperiod_return
                    ),
                    "boundary_signed_cash_flow": _fraction_material(signed_flow),
                    "benchmark_post_flow_equity": _fraction_material(post_flow_equity),
                },
            }
        )
        cumulative_prior_flow += signed_flow
        prior_post_flow_equity = post_flow_equity
    linked_return = linked_growth - 1
    return {
        "subperiods": subperiods,
        "subperiod_count": len(subperiods),
        "initial_funding": _decimal_string(initial_funding),
        "cumulative_external_cash_flow": _decimal_string(cumulative_prior_flow),
        "ending_benchmark_pre_flow_equity": subperiods[-1][
            "benchmark_pre_flow_equity"
        ],
        "ending_benchmark_post_flow_equity": subperiods[-1][
            "benchmark_post_flow_equity"
        ],
        "time_weighted_benchmark_portfolio_return": _decimal_string(linked_return),
        "exact_fractions": {
            "initial_funding": _fraction_material(initial_funding),
            "cumulative_external_cash_flow": _fraction_material(cumulative_prior_flow),
            "ending_benchmark_pre_flow_equity": subperiods[-1]["exact_fractions"][
                "benchmark_pre_flow_equity"
            ],
            "ending_benchmark_post_flow_equity": subperiods[-1]["exact_fractions"][
                "benchmark_post_flow_equity"
            ],
            "time_weighted_benchmark_portfolio_return": _fraction_material(
                linked_return
            ),
        },
    }


class TimeWeightedPortfolioBenchmarkReturnLedger(TimeWeightedPortfolioReturnLedger):
    """Append-only matched S&P TWR; not relative return, alpha or a track record."""

    def __init__(
        self,
        path: str | Path,
        benchmark_valuation_ledger: SimulatedPortfolioBenchmarkValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
    ) -> None:
        self.path = Path(path)
        self.benchmark_valuation_ledger = benchmark_valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger

    @staticmethod
    def not_calculable(
        portfolio_version: str, horizon: str, reasons: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "through_horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "benchmark_portfolio_return_calculated": False,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, through_horizon: str
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[str],
    ]:
        all_benchmarks = self.benchmark_valuation_ledger.verify()
        candidates = [
            item
            for item in all_benchmarks
            if item.get("portfolio_version") == portfolio_version
        ]
        through = next(
            (item for item in candidates if item.get("horizon") == through_horizon),
            None,
        )
        reasons = []
        if through_horizon == "ENTRY":
            reasons.append("ENTRY is the funding baseline, not a return horizon.")
        if through is None:
            reasons.append(
                "Verified through-horizon benchmark portfolio valuation is missing."
            )
            return None, [], [], reasons
        through_at = _as_datetime(through["outcome_benchmark_price_effective_at"])
        valuations = sorted(
            (
                item
                for item in candidates
                if _as_datetime(item["outcome_benchmark_price_effective_at"])
                <= through_at
            ),
            key=lambda item: _as_datetime(
                item["outcome_benchmark_price_effective_at"]
            ),
        )
        effective_times = [
            item["outcome_benchmark_price_effective_at"] for item in valuations
        ]
        if len(set(effective_times)) != len(effective_times):
            reasons.append("Benchmark valuations must have unique effective times.")
        if any(
            item.get("outcome_asset_price_effective_at")
            != item.get("outcome_benchmark_price_effective_at")
            for item in valuations
        ):
            reasons.append(
                "Asset and benchmark portfolio boundaries must share exact effective times."
            )
        asset_records = {
            item["valuation_id"]: item
            for item in self.benchmark_valuation_ledger.asset_valuation_ledger.verify()
            if item.get("portfolio_version") == portfolio_version
        }
        for benchmark in valuations:
            asset = asset_records.get(benchmark.get("asset_portfolio_valuation_id"))
            if asset is None or any(
                (
                    benchmark.get("asset_portfolio_valuation_hash")
                    != asset.get("record_hash"),
                    benchmark.get("horizon") != asset.get("horizon"),
                    benchmark.get("outcome_asset_price_effective_at")
                    != asset.get("outcome_asset_price_effective_at"),
                    benchmark.get("outcome_benchmark_price_effective_at")
                    != asset.get("outcome_benchmark_price_effective_at"),
                )
            ):
                reasons.append(
                    "Every benchmark valuation must retain its exact asset valuation boundary."
                )
                break
        funding = self.benchmark_valuation_ledger.funding_ledger.funding_for(
            portfolio_version
        )
        if funding is None:
            reasons.append("Verified initial portfolio funding is missing.")
        flows = sorted(
            (
                item
                for item in self.cash_flow_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and _as_datetime(item["effective_at"]) <= through_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        included_asset_ids = {
            item["asset_portfolio_valuation_id"] for item in valuations
        }
        if any(item["valuation_id"] not in included_asset_ids for item in flows):
            reasons.append(
                "Every included cash flow must match an included asset and benchmark boundary."
            )
        boundary_times = {
            item["asset_portfolio_valuation_id"]: item[
                "outcome_benchmark_price_effective_at"
            ]
            for item in valuations
        }
        if any(
            item.get("effective_at") != boundary_times.get(item.get("valuation_id"))
            for item in flows
        ):
            reasons.append("Cash flows must use the exact matched valuation time.")
        if valuations:
            identity_fields = ("strategy_version", "model_versions", "git_revision")
            reference = valuations[0]
            related_assets = [
                asset_records[item["asset_portfolio_valuation_id"]]
                for item in valuations
                if item["asset_portfolio_valuation_id"] in asset_records
            ]
            if any(
                any(item.get(field) != reference.get(field) for field in identity_fields)
                for item in [*valuations, *related_assets, *flows]
            ) or (
                funding is not None
                and any(
                    funding.get(field) != reference.get(field)
                    for field in identity_fields
                )
            ):
                reasons.append(
                    "Benchmark return evidence must share strategy, model and Git identity."
                )
        return funding, valuations, flows, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        through_horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        funding, valuations, flows, reasons = self._support(version, horizon)
        if reasons:
            return self.not_calculable(version, horizon, reasons)
        assert funding is not None and valuations
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(item["calculated_at"]) for item in valuations]
            + [_as_datetime(item["recorded_at"]) for item in flows]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot predate supporting evidence."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(funding, valuations, flows)
        except ValueError as error:
            return self.not_calculable(version, horizon, [str(error)])
        asset_ids = [item["asset_portfolio_valuation_id"] for item in valuations]
        asset_hashes = [item["asset_portfolio_valuation_hash"] for item in valuations]
        result = {
            "schema_version": PORTFOLIO_BENCHMARK_RETURN_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_BENCHMARK_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": (
                "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_SP500_"
                "PORTFOLIO_BENCHMARK_RETURN"
            ),
            "simulation_only": True,
            "currency": "USD",
            "benchmark_family": "S&P 500",
            "benchmark_ticker": "^GSPC",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_horizon_label": valuations[-1]["horizon_label"],
            "funding_id": funding["funding_id"],
            "funding_record_hash": funding["record_hash"],
            "supporting_benchmark_valuation_ids": [
                item["valuation_id"] for item in valuations
            ],
            "supporting_benchmark_valuation_hashes": [
                item["record_hash"] for item in valuations
            ],
            "supporting_asset_valuation_ids": asset_ids,
            "supporting_asset_valuation_hashes": asset_hashes,
            "supporting_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_cash_flow_hashes": [item["record_hash"] for item in flows],
            "benchmark_portfolio_return_calculated": True,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuations[0]["strategy_version"],
            "model_versions": valuations[0]["model_versions"],
            "git_revision": valuations[0]["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_BENCHMARK_RETURN_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-return record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("through_horizon") or "")
            funding, valuations, flows, reasons = self._support(version, horizon)
            if reasons or funding is None or not valuations:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-return record {index} lost supporting evidence."
                )
            try:
                economics = _economics(funding, valuations, flows)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(item["calculated_at"]) for item in valuations]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-return record {index} has invalid values."
                ) from error
            expected_id = _result_id(version, horizon)
            asset_ids = [item["asset_portfolio_valuation_id"] for item in valuations]
            asset_hashes = [item["asset_portfolio_valuation_hash"] for item in valuations]
            boundary = (
                record.get("schema_version")
                == PORTFOLIO_BENCHMARK_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_BENCHMARK_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == (
                    "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_SP500_"
                    "PORTFOLIO_BENCHMARK_RETURN"
                )
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("benchmark_family") == "S&P 500"
                and record.get("benchmark_ticker") == "^GSPC"
                and record.get("through_horizon_label") == valuations[-1]["horizon_label"]
                and record.get("funding_id") == funding["funding_id"]
                and record.get("funding_record_hash") == funding["record_hash"]
                and record.get("supporting_benchmark_valuation_ids")
                == [item["valuation_id"] for item in valuations]
                and record.get("supporting_benchmark_valuation_hashes")
                == [item["record_hash"] for item in valuations]
                and record.get("supporting_asset_valuation_ids") == asset_ids
                and record.get("supporting_asset_valuation_hashes") == asset_hashes
                and record.get("supporting_cash_flow_ids")
                == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes")
                == [item["record_hash"] for item in flows]
                and record.get("benchmark_portfolio_return_calculated") is True
                and record.get("relative_portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuations[0]["strategy_version"]
                and record.get("model_versions") == valuations[0]["model_versions"]
                and record.get("git_revision") == valuations[0]["git_revision"]
                and record.get("formula") == PORTFOLIO_BENCHMARK_RETURN_FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio benchmark-return record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records
