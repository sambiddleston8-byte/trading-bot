from datetime import datetime, timedelta, timezone

from core.performance import PerformanceMetricReadinessGate, SharpeMetricReadinessGate


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


class Stub:
    def __init__(self, values=()):
        self.values = list(values)

    def verify(self):
        return self.values


class FundingStub:
    def funding_for(self, version):
        if version != "PORT-001":
            return None
        return {
            "funding_id": "FUND-1",
            "record_hash": "funding-hash",
            "effective_at": "2024-01-01T21:00:00+00:00",
        }


class MilestoneStub(Stub):
    funding_ledger = FundingStub()


def evidence():
    target = {
        "valuation_id": "PVAL-12M",
        "record_hash": "milestone-hash",
        "portfolio_version": "PORT-001",
        "horizon": "12_MONTHS",
        "outcome_asset_price_effective_at": "2025-01-02T23:00:00+00:00",
        **IDENTITY,
    }
    values = []
    current = datetime(2024, 1, 2, 21, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
    while current <= end:
        if current.weekday() < 5:
            index = len(values) + 1
            values.append(
                {
                    "valuation_id": f"DVAL-{index:03d}",
                    "record_hash": f"dval-hash-{index:03d}",
                    "portfolio_version": "PORT-001",
                    "effective_at": current.isoformat(),
                    **IDENTITY,
                }
            )
        current += timedelta(days=1)
    daily_returns = []
    risk_free_returns = []
    for index, (previous, current_value) in enumerate(zip(values, values[1:]), start=1):
        previous_date = previous["effective_at"][:10]
        current_date = current_value["effective_at"][:10]
        daily = {
            "result_id": f"DRET-{index:03d}",
            "record_hash": f"dret-hash-{index:03d}",
            "portfolio_version": "PORT-001",
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current_value["valuation_id"],
            "current_valuation_record_hash": current_value["record_hash"],
            "previous_market_session_date": previous_date,
            "current_market_session_date": current_date,
            "current_effective_at": current_value["effective_at"],
            "daily_return_calculated": True,
            **IDENTITY,
        }
        daily_returns.append(daily)
        risk_free_returns.append(
            {
                "result_id": f"DRF-{index:03d}",
                "record_hash": f"drf-hash-{index:03d}",
                "portfolio_version": "PORT-001",
                "daily_portfolio_return_id": daily["result_id"],
                "daily_portfolio_return_record_hash": daily["record_hash"],
                "previous_market_session_date": previous_date,
                "current_market_session_date": current_date,
                "daily_risk_free_return_calculated": True,
                **IDENTITY,
            }
        )
    return target, values, daily_returns, risk_free_returns


def gate(*, mutate=None):
    target, values, daily_returns, risk_free_returns = evidence()
    if mutate is not None:
        mutate(values, daily_returns, risk_free_returns)
    base = PerformanceMetricReadinessGate(
        MilestoneStub([target]), Stub([]), Stub(values), Stub(daily_returns)
    )
    return SharpeMetricReadinessGate(base, Stub(risk_free_returns))


def assess(item):
    return item.assess(portfolio_version="PORT-001", through_horizon="12_MONTHS")


def test_complete_exact_pairing_is_evidence_ready_without_calculating_sharpe():
    result = assess(gate())
    assert result["status"] == "EVIDENCE_READY"
    assert result["complete_pairing"] is True
    assert result["daily_return_observation_count"] >= 252
    assert result["risk_free_return_observation_count"] == result[
        "daily_return_observation_count"
    ]
    assert result["matched_pair_count"] == result["daily_return_observation_count"]
    assert len(result["evidence_snapshot_sha256"]) == 64
    assert result["sharpe_calculated"] is False
    assert result["risk_adjusted_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False


def test_missing_pair_blocks_readiness():
    result = assess(gate(mutate=lambda _v, _d, risk_free: risk_free.pop()))
    assert result["status"] == "BLOCKED"
    assert result["complete_pairing"] is False
    assert "match" in " ".join(result["reasons"])


def test_extra_pair_blocks_readiness():
    def mutate(_values, _daily, risk_free):
        risk_free.append(
            {
                **risk_free[-1],
                "result_id": "EXTRA",
                "record_hash": "extra-hash",
                "daily_portfolio_return_id": "UNKNOWN",
            }
        )

    result = assess(gate(mutate=mutate))
    assert result["status"] == "BLOCKED"
    assert "exactly" in " ".join(result["reasons"])


def test_duplicate_pair_blocks_readiness():
    def mutate(_values, _daily, risk_free):
        risk_free.append(
            {**risk_free[-1], "result_id": "DUP", "record_hash": "dup-hash"}
        )

    result = assess(gate(mutate=mutate))
    assert result["status"] == "BLOCKED"
    assert "duplicate" in " ".join(result["reasons"])


def test_later_risk_free_history_does_not_invalidate_earlier_horizon():
    def mutate(_values, _daily, risk_free):
        risk_free.append(
            {
                **risk_free[-1],
                "result_id": "FUTURE",
                "record_hash": "future-hash",
                "daily_portfolio_return_id": "FUTURE-DAILY",
                "previous_market_session_date": "2025-01-02",
                "current_market_session_date": "2025-01-03",
            }
        )

    result = assess(gate(mutate=mutate))
    assert result["status"] == "EVIDENCE_READY"


def test_changed_daily_hash_dates_or_identity_blocks_readiness():
    for field, value in (
        ("daily_portfolio_return_record_hash", "wrong"),
        ("previous_market_session_date", "2024-01-01"),
        ("git_revision", "wrong"),
    ):
        result = assess(
            gate(
                mutate=lambda _v, _d, risk_free, f=field, x=value: risk_free[0].update(
                    {f: x}
                )
            )
        )
        assert result["status"] == "BLOCKED"
        assert "same daily return" in " ".join(result["reasons"])


def test_incomplete_base_daily_series_blocks_even_if_available_pairs_match():
    def mutate(values, daily, risk_free):
        del values[-10:]
        del daily[-10:]
        del risk_free[-10:]

    result = assess(gate(mutate=mutate))
    assert result["status"] == "BLOCKED"
    assert "at least" in " ".join(result["reasons"])


def test_evidence_snapshot_changes_when_paired_evidence_changes():
    first = assess(gate())

    def mutate(_values, _daily, risk_free):
        risk_free[0]["record_hash"] = "legitimate-new-hash"

    second = assess(gate(mutate=mutate))
    assert second["status"] == "EVIDENCE_READY"
    assert second["evidence_snapshot_sha256"] != first["evidence_snapshot_sha256"]
