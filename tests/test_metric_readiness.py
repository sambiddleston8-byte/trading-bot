from datetime import datetime, timedelta, timezone

from core.performance import PerformanceMetricReadinessGate
from core.performance.metric_readiness import (
    MAX_DAILY_CALENDAR_GAP_SECONDS,
    METRIC_READINESS_POLICY_VERSION,
    MIN_CAGR_ELAPSED_SECONDS,
    MIN_DAILY_RETURNS,
    MIN_DAILY_SERIES_SPAN_SECONDS,
    MIN_DAILY_VALUATIONS,
    SECONDS_PER_DAY,
)


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


class FundingLedgerStub:
    def __init__(self, record=True):
        self.value = (
            {
                "funding_id": "PFUND-001",
                "record_hash": "funding-hash",
                "portfolio_version": "PORT-001",
                "effective_at": "2024-01-01T16:00:00+00:00",
            }
            if record
            else None
        )

    def funding_for(self, portfolio_version):
        return self.value if portfolio_version == "PORT-001" else None


class ValuationLedgerStub:
    def __init__(self, values, funding=True):
        self.values = values
        self.funding_ledger = FundingLedgerStub(funding)

    def verify(self):
        return self.values


class ReturnLedgerStub:
    def __init__(self, values=()):
        self.values = list(values)

    def verify(self):
        return self.values


def valuation(index, effective_at, horizon):
    return {
        "valuation_id": f"PVAL-{index:03d}",
        "record_hash": f"valuation-hash-{index:03d}",
        "portfolio_version": "PORT-001",
        "horizon": horizon,
        "outcome_asset_price_effective_at": effective_at.isoformat(),
        **IDENTITY,
    }


def business_day_valuations():
    start = datetime(2024, 1, 2, 16, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, 16, tzinfo=timezone.utc)
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return [
        valuation(
            index,
            value,
            "12_MONTHS" if index == len(dates) else f"DAILY_{index:03d}",
        )
        for index, value in enumerate(dates, start=1)
    ]


def verified_return(valuations, *, change_support=False):
    ids = [item["valuation_id"] for item in valuations]
    hashes = [item["record_hash"] for item in valuations]
    if change_support:
        hashes[-1] = "wrong-hash"
    return {
        "result_id": "PRET-12M",
        "record_hash": "return-hash",
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "portfolio_return_calculated": True,
        "supporting_valuation_ids": ids,
        "supporting_valuation_hashes": hashes,
    }


def authoritative_daily_series():
    values = []
    start = datetime(2024, 1, 2, 21, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
    current = start
    while current <= end:
        if current.weekday() < 5:
            index = len(values) + 1
            values.append(
                {
                    "valuation_id": f"DPFVAL-{index:03d}",
                    "record_hash": f"daily-valuation-hash-{index:03d}",
                    "portfolio_version": "PORT-001",
                    "market_session_date": current.date().isoformat(),
                    "effective_at": current.isoformat(),
                    **IDENTITY,
                }
            )
        current += timedelta(days=1)
    returns = [
        {
            "result_id": f"DPRET-{index:03d}",
            "record_hash": f"daily-return-hash-{index:03d}",
            "portfolio_version": "PORT-001",
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current["valuation_id"],
            "current_valuation_record_hash": current["record_hash"],
            "current_effective_at": current["effective_at"],
            "daily_return_calculated": True,
            **IDENTITY,
        }
        for index, (previous, current) in enumerate(zip(values, values[1:]), start=1)
    ]
    return values, returns


def assess(valuations, returns=(), *, daily_values=None, daily_returns=None, **overrides):
    gate = PerformanceMetricReadinessGate(
        ValuationLedgerStub(valuations, funding=overrides.pop("funding", True)),
        ReturnLedgerStub(returns),
        ReturnLedgerStub(daily_values) if daily_values is not None else None,
        ReturnLedgerStub(daily_returns) if daily_returns is not None else None,
    )
    values = {"portfolio_version": "PORT-001", "through_horizon": "12_MONTHS"}
    values.update(overrides)
    return gate.assess(**values)


def test_sparse_milestones_block_statistical_and_annualized_metrics():
    values = [
        valuation(1, datetime(2024, 2, 1, 16, tzinfo=timezone.utc), "1_MONTH"),
        valuation(2, datetime(2025, 1, 2, 16, tzinfo=timezone.utc), "12_MONTHS"),
    ]
    result = assess(values, [verified_return(values)])
    assert result["status"] == "ASSESSED"
    assert result["milestone_valuation_observation_count"] == 2
    assert result["daily_valuation_observation_count"] == 0
    assert result["daily_return_observation_count"] == 0
    assert result["daily_cadence_ready"] is False
    assert result["metrics"]["CAGR"]["status"] == "EVIDENCE_READY"
    assert result["metrics"]["VOLATILITY"]["status"] == "BLOCKED"
    assert result["metrics"]["MAXIMUM_DRAWDOWN"]["status"] == "BLOCKED"
    assert result["metrics"]["SHARPE_RATIO"]["status"] == "BLOCKED"
    assert result["metrics"]["SORTINO_RATIO"]["status"] == "BLOCKED"


def test_complete_daily_history_readies_only_supported_metrics():
    values = business_day_valuations()
    daily_values, daily_returns = authoritative_daily_series()
    result = assess(
        values,
        [verified_return(values)],
        daily_values=daily_values,
        daily_returns=daily_returns,
    )
    assert len(daily_values) >= 253
    assert result["daily_cadence_ready"] is True
    assert result["maximum_observation_gap_seconds"] <= 4 * 86_400
    assert result["metrics"]["CAGR"]["status"] == "EVIDENCE_READY"
    assert result["metrics"]["VOLATILITY"]["status"] == "EVIDENCE_READY"
    assert result["metrics"]["MAXIMUM_DRAWDOWN"]["status"] == "EVIDENCE_READY"
    assert result["metrics"]["SHARPE_RATIO"]["status"] == "BLOCKED"
    assert "risk-free" in " ".join(result["metrics"]["SHARPE_RATIO"]["reasons"])
    assert result["metrics"]["SORTINO_RATIO"]["status"] == "BLOCKED"
    assert result["metrics"]["HIT_RATE"]["status"] == "BLOCKED"
    assert result["metrics"]["TURNOVER"]["status"] == "BLOCKED"
    assert result["metrics"]["PREDICTION_CALIBRATION"]["status"] == "BLOCKED"


def test_cagr_requires_return_pinned_to_exact_same_valuations():
    values = business_day_valuations()
    result = assess(values, [verified_return(values, change_support=True)])
    assert result["metrics"]["CAGR"]["status"] == "BLOCKED"
    assert result["verified_time_weighted_return_id"] is None
    assert "pinned to the same valuations" in " ".join(
        result["metrics"]["CAGR"]["reasons"]
    )


def test_duplicate_times_and_long_gap_fail_daily_cadence():
    values = business_day_valuations()
    daily_values, daily_returns = authoritative_daily_series()
    daily_values[1] = {**daily_values[1], "effective_at": daily_values[0]["effective_at"]}
    duplicate = assess(
        values,
        [verified_return(values)],
        daily_values=daily_values,
        daily_returns=daily_returns,
    )
    assert duplicate["daily_cadence_ready"] is False
    assert duplicate["unique_effective_times"] is False
    assert "duplicate" in " ".join(duplicate["metrics"]["VOLATILITY"]["reasons"])

    shifted = [
        {
            **item,
            "effective_at": (
                datetime.fromisoformat(item["effective_at"])
                + (timedelta(days=8) if index >= 100 else timedelta(0))
            ).isoformat(),
        }
        for index, item in enumerate(authoritative_daily_series()[0])
    ]
    _, shifted_returns = authoritative_daily_series()
    long_gap = assess(
        values,
        [verified_return(values)],
        daily_values=shifted,
        daily_returns=shifted_returns,
    )
    assert long_gap["daily_cadence_ready"] is False
    assert "more than four calendar days" in " ".join(
        long_gap["metrics"]["VOLATILITY"]["reasons"]
    )


def test_missing_funding_target_and_entry_are_not_assessable():
    values = business_day_valuations()
    no_funding = assess(values, funding=False)
    missing_target = assess(values, through_horizon="24_MONTHS")
    entry = assess(values, through_horizon="ENTRY")
    assert no_funding["status"] == "NOT_ASSESSABLE"
    assert "funding" in " ".join(no_funding["general_reasons"])
    assert missing_target["status"] == "NOT_ASSESSABLE"
    assert "through-horizon valuation" in " ".join(missing_target["general_reasons"])
    assert entry["status"] == "NOT_ASSESSABLE"
    assert "funding baseline" in " ".join(entry["general_reasons"])


def test_snapshot_is_content_addressed_and_no_metric_claim_is_made():
    values = business_day_valuations()
    first = assess(values, [verified_return(values)])
    changed = [{**item} for item in values]
    changed[-1]["record_hash"] = "changed-valuation-hash"
    second = assess(changed, [verified_return(changed)])
    assert first["evidence_snapshot_sha256"] != second["evidence_snapshot_sha256"]
    assert first["performance_metric_calculated"] is False
    assert first["annualized_result_calculated"] is False
    assert first["risk_adjusted_result_calculated"] is False
    assert first["recommendation_provided"] is False
    assert first["learning_eligible"] is False
    assert first["track_record_claim"] is False
    assert first["live_trading_enabled"] is False


def test_v1_policy_thresholds_are_pinned_against_silent_drift():
    assert METRIC_READINESS_POLICY_VERSION == "portfolio-metric-readiness-v2-authoritative-daily"
    assert SECONDS_PER_DAY == 86_400
    assert MIN_CAGR_ELAPSED_SECONDS == 365 * SECONDS_PER_DAY
    assert MIN_DAILY_VALUATIONS == 253
    assert MIN_DAILY_RETURNS == 252
    assert MIN_DAILY_SERIES_SPAN_SECONDS == 365 * SECONDS_PER_DAY
    assert MAX_DAILY_CALENDAR_GAP_SECONDS == 4 * SECONDS_PER_DAY


def test_identity_mismatch_blocks_every_potentially_ready_metric():
    values = business_day_valuations()
    values[100] = {**values[100], "git_revision": "different-revision"}
    result = assess(values, [verified_return(values)])
    assert result["status"] == "NOT_ASSESSABLE"
    assert "strategy, model and Git identity" in " ".join(result["general_reasons"])
    for name in ("CAGR", "VOLATILITY", "MAXIMUM_DRAWDOWN"):
        assert result["metrics"][name]["status"] == "BLOCKED"


def test_valuation_before_funding_blocks_metrics():
    values = business_day_valuations()
    values[0] = {
        **values[0],
        "outcome_asset_price_effective_at": "2023-12-29T16:00:00+00:00",
    }
    result = assess(values, [verified_return(values)])
    assert result["status"] == "NOT_ASSESSABLE"
    assert "predates initial funding" in " ".join(result["general_reasons"])
    assert result["metrics"]["CAGR"]["status"] == "BLOCKED"
    assert result["metrics"]["VOLATILITY"]["status"] == "BLOCKED"


def test_ambiguous_duplicate_returns_block_cagr():
    values = business_day_valuations()
    first = verified_return(values)
    second = {**first, "result_id": "PRET-12M-DUPLICATE", "record_hash": "other-return-hash"}
    result = assess(values, [first, second])
    assert result["verified_time_weighted_return_id"] is None
    assert result["metrics"]["CAGR"]["status"] == "BLOCKED"
    assert "exactly one verified time-weighted return" in " ".join(
        result["metrics"]["CAGR"]["reasons"]
    )


def test_daily_readiness_requires_exact_return_chain_not_just_many_values():
    values = business_day_valuations()
    daily_values, daily_returns = authoritative_daily_series()
    broken = [{**item} for item in daily_returns]
    broken[100]["previous_valuation_record_hash"] = "wrong-hash"
    result = assess(
        values,
        [verified_return(values)],
        daily_values=daily_values,
        daily_returns=broken,
    )
    assert result["daily_cadence_ready"] is False
    assert "exactly pinned daily return" in " ".join(
        result["metrics"]["VOLATILITY"]["reasons"]
    )


def test_generic_milestone_density_can_never_substitute_for_daily_ledgers():
    values = business_day_valuations()
    result = assess(values, [verified_return(values)])
    assert len(values) >= 253
    assert result["daily_cadence_ready"] is False
    assert "Authoritative daily valuation and return ledgers" in " ".join(
        result["metrics"]["VOLATILITY"]["reasons"]
    )


def test_daily_series_before_funding_cannot_ready_risk_metrics():
    values = business_day_valuations()
    daily_values, daily_returns = authoritative_daily_series()
    daily_values[0] = {
        **daily_values[0],
        "effective_at": "2023-12-29T21:00:00+00:00",
        "market_session_date": "2023-12-29",
    }
    daily_returns[0] = {
        **daily_returns[0],
        "previous_valuation_record_hash": daily_values[0]["record_hash"],
    }
    result = assess(
        values,
        [verified_return(values)],
        daily_values=daily_values,
        daily_returns=daily_returns,
    )
    assert result["metrics"]["VOLATILITY"]["status"] == "BLOCKED"
    assert result["metrics"]["MAXIMUM_DRAWDOWN"]["status"] == "BLOCKED"
    assert "daily valuation predates initial funding" in " ".join(
        result["metrics"]["VOLATILITY"]["reasons"]
    )
