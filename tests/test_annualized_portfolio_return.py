from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import AnnualizedPortfolioReturnLedger, PerformanceMetricReadinessGate
from core.performance.annualized_portfolio_return import TROPICAL_YEAR_SECONDS


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(value):
    value = Fraction(value)
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


class Stub:
    def __init__(self, values=()):
        self.values = list(values)

    def verify(self):
        return self.values


class FundingStub:
    def __init__(self, effective_at):
        self.value = {
            "funding_id": "FUND-1",
            "record_hash": "funding-hash",
            "portfolio_version": "PORT-001",
            "effective_at": effective_at,
            **IDENTITY,
        }

    def funding_for(self, version):
        return self.value if version == "PORT-001" else None


class ValuationStub(Stub):
    def __init__(self, values, effective_at):
        super().__init__(values)
        self.funding_ledger = FundingStub(effective_at)


def evidence(*, elapsed_seconds=TROPICAL_YEAR_SECONDS, twr=Fraction(21, 100)):
    started = datetime(2024, 1, 1, 16, tzinfo=timezone.utc)
    ended = started + timedelta(seconds=elapsed_seconds)
    target = {
        "valuation_id": "PVAL-12M",
        "record_hash": "valuation-hash",
        "portfolio_version": "PORT-001",
        "horizon": "12_MONTHS",
        "horizon_label": "12 months",
        "outcome_asset_price_effective_at": ended.isoformat(),
        "calculated_at": ended.isoformat(),
        **IDENTITY,
    }
    portfolio_return = {
        "result_id": "PRET-12M",
        "record_hash": "return-hash",
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "calculated_at": ended.isoformat(),
        "portfolio_return_calculated": True,
        "time_weighted_portfolio_return": str(Decimal(twr.numerator) / Decimal(twr.denominator)),
        "exact_fractions": {"time_weighted_portfolio_return": fraction(twr)},
        "supporting_valuation_ids": [target["valuation_id"]],
        "supporting_valuation_hashes": [target["record_hash"]],
        **IDENTITY,
    }
    gate = PerformanceMetricReadinessGate(
        ValuationStub([target], started.isoformat()), Stub([portfolio_return])
    )
    return gate, target, portfolio_return


def ledger(tmp_path, **evidence_overrides):
    gate, target, portfolio_return = evidence(**evidence_overrides)
    return (
        AnnualizedPortfolioReturnLedger(tmp_path / "cagr.jsonl", gate),
        gate,
        target,
        portfolio_return,
    )


def calculate(item, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "calculated_at": "2026-01-03T00:00:00+00:00",
    }
    values.update(overrides)
    return item.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import annualized_portfolio_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_exact_tropical_year_cagr_equals_verified_return(tmp_path):
    item, _, _, portfolio_return = ledger(tmp_path)
    result = calculate(item)
    assert result["scope"] == "SIMULATED_GROSS_PRE_TAX_COMPOUND_ANNUAL_GROWTH_RATE"
    assert result["elapsed_seconds"] == TROPICAL_YEAR_SECONDS
    assert result["annualization_exponent"] == "1"
    assert result["compound_annual_growth_rate"] == "0.21"
    assert result["portfolio_return_id"] == portfolio_return["result_id"]
    assert result["cagr_calculated"] is True
    assert result["annualized"] is True
    assert result["risk_adjusted"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


def test_positive_return_over_two_years_has_lower_cagr(tmp_path):
    item, _, _, _ = ledger(
        tmp_path, elapsed_seconds=2 * TROPICAL_YEAR_SECONDS, twr=Fraction(21, 100)
    )
    result = calculate(item)
    assert result["annualization_exponent"] == "0.5"
    assert Decimal("0") < Decimal(result["compound_annual_growth_rate"]) < Decimal("0.21")


def test_negative_return_is_annualized_without_changing_its_sign(tmp_path):
    item, _, _, _ = ledger(tmp_path, twr=Fraction(-1, 5))
    result = calculate(item)
    assert result["compound_annual_growth_rate"] == "-0.2"


def test_less_than_365_days_fails_closed(tmp_path):
    item, _, _, _ = ledger(tmp_path, elapsed_seconds=364 * 86_400)
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["cagr_calculated"] is False
    assert "at least 365" in " ".join(result["reasons"])
    assert item.records() == []


def test_return_identity_must_match_valuation(tmp_path):
    item, gate, _, _ = ledger(tmp_path)
    gate.portfolio_return_ledger.values[0]["git_revision"] = "different"
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert "share strategy" in " ".join(result["reasons"])


def test_funding_identity_is_required_and_must_match(tmp_path):
    item, gate, _, _ = ledger(tmp_path)
    gate.valuation_ledger.funding_ledger.value.pop("git_revision")
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert "Funding" in " ".join(result["reasons"])


def test_calculation_time_cannot_predate_support_or_be_future(tmp_path):
    item, _, _, _ = ledger(tmp_path)
    before_support = calculate(item, calculated_at="2024-01-02T00:00:00+00:00")
    assert before_support["status"] == "NOT_CALCULABLE"
    assert "predate" in " ".join(before_support["reasons"])

    future = calculate(item, calculated_at="2099-01-01T00:00:00+00:00")
    assert future["status"] == "NOT_CALCULABLE"
    assert "future" in " ".join(future["reasons"])


def test_subsecond_elapsed_evidence_fails_closed(tmp_path):
    item, gate, target, portfolio_return = ledger(tmp_path)
    shifted = (
        datetime.fromisoformat(target["outcome_asset_price_effective_at"])
        + timedelta(microseconds=1)
    ).isoformat()
    target["outcome_asset_price_effective_at"] = shifted
    target["calculated_at"] = shifted
    portfolio_return["calculated_at"] = shifted
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert "whole-second" in " ".join(result["reasons"])


def test_total_loss_is_recorded_but_impossible_negative_growth_is_rejected(tmp_path):
    total_loss, _, _, _ = ledger(tmp_path / "total", twr=Fraction(-1))
    result = calculate(total_loss)
    assert result["compound_annual_growth_rate"] == "-1"

    impossible, _, _, _ = ledger(tmp_path / "impossible", twr=Fraction(-11, 10))
    rejected = calculate(impossible)
    assert rejected["status"] == "NOT_CALCULABLE"
    assert "negative gross growth" in " ".join(rejected["reasons"])


def test_identical_concurrent_retries_append_once(tmp_path):
    item, _, _, _ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"compound_annual_growth_rate": "9"},
        {"annualization_exponent": "9"},
        {"cagr_calculated": False},
        {"risk_adjusted": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rejects_rehashed_semantic_tampering(tmp_path, changes):
    item, _, _, _ = ledger(tmp_path)
    calculate(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_rejects_changed_pinned_return_hash(tmp_path):
    item, gate, _, _ = ledger(tmp_path)
    calculate(item)
    gate.portfolio_return_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost readiness support"):
        item.verify()


def test_repair_quarantines_incomplete_tail(tmp_path):
    item, _, _, _ = ledger(tmp_path)
    result = calculate(item)
    with item.path.open("ab") as target:
        target.write(b'{"partial":')
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial":'
    assert item.verify() == [result]
