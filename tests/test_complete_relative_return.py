from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import CompleteFixedHorizonRelativeReturnLedger


IDENTITY = {
    "decision_id": "DEC-001",
    "portfolio_version": "PORT-001",
    "ticker": "NVDA",
    "horizon": "1_MONTH",
    "horizon_label": "1 month",
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "research", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


def ledgers(tmp_path):
    asset = {
        "result_id": "FHRT-1",
        "record_hash": "hash-asset",
        "paired_at": "2025-02-03T17:03:00+00:00",
        "entry_fill_id": "FILL-ENTRY",
        "outcome_observation_id": "OBS-OUTCOME",
        "outcome_observation_record_hash": "hash-observation",
        "exact_fractions": {
            "net_total_return_after_entry_and_exit_fees": fraction(45, 202)
        },
        **IDENTITY,
    }
    benchmark = {
        "result_id": "BTR-1",
        "record_hash": "hash-benchmark",
        "calculated_at": "2025-02-03T17:02:00+00:00",
        "fill_id": "FILL-ENTRY",
        "outcome_observation_id": "OBS-OUTCOME",
        "outcome_observation_hash": "hash-observation",
        "benchmark_family": "S&P 500",
        "benchmark_ticker": "^GSPC",
        "exact_fractions": {"benchmark_gross_cash_total_return": fraction(1, 10)},
        **IDENTITY,
    }
    assets = Stub([asset])
    benchmarks = Stub([benchmark])
    ledger = CompleteFixedHorizonRelativeReturnLedger(
        tmp_path / "complete_relative.jsonl", assets, benchmarks
    )
    return ledger, assets, benchmarks, asset, benchmark


def calculate(ledger, asset, benchmark, **overrides):
    values = {
        "asset_outcome_result_id": asset["result_id"],
        "benchmark_total_return_result_id": benchmark["result_id"],
        "calculated_at": "2025-02-03T17:04:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import complete_relative_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_complete_matched_benchmark_relative_return(tmp_path):
    ledger, _, _, asset, benchmark = ledgers(tmp_path)
    result = calculate(ledger, asset, benchmark)
    assert result["scope"] == "SIMULATED_COMPLETE_FIXED_HORIZON_BENCHMARK_RELATIVE_RETURN"
    assert result["comparison_method"] == "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
    assert result["exact_fractions"]["asset_net_total_return_after_entry_and_exit_fees"] == fraction(45, 202)
    assert result["exact_fractions"]["benchmark_gross_cash_total_return"] == fraction(1, 10)
    assert result["exact_fractions"]["complete_benchmark_relative_return"] == fraction(62, 505)
    assert result["asset_entry_and_exit_fees_included"] is True
    assert result["benchmark_execution_cost_deducted"] is False
    assert result["relative_total_return_calculated"] is True
    assert result["alpha_calculated"] is False
    assert result["risk_adjusted"] is False
    assert result["success_rule_applied"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda asset, benchmark: benchmark.update(ticker="AAPL"), "identity"),
        (lambda asset, benchmark: benchmark.update(fill_id="OTHER"), "entry fill"),
        (
            lambda asset, benchmark: benchmark.update(outcome_observation_hash="different"),
            "observation evidence",
        ),
    ],
)
def test_mismatched_benchmark_fails_closed(tmp_path, mutation, fragment):
    ledger, _, _, asset, benchmark = ledgers(tmp_path)
    mutation(asset, benchmark)
    result = calculate(ledger, asset, benchmark)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])


def test_missing_support_and_time_guards_fail_closed(tmp_path):
    ledger, assets, _, asset, benchmark = ledgers(tmp_path)
    assets.values.clear()
    missing = calculate(ledger, asset, benchmark)
    assert missing["status"] == "NOT_CALCULABLE"

    ledger, _, _, asset, benchmark = ledgers(tmp_path / "other")
    early = calculate(ledger, asset, benchmark, calculated_at="2025-02-03T17:02:59+00:00")
    future = calculate(ledger, asset, benchmark, calculated_at="2099-01-01T00:00:00+00:00")
    assert early["status"] == "NOT_CALCULABLE"
    assert future["status"] == "NOT_CALCULABLE"


def test_identical_concurrent_retries_append_once(tmp_path):
    ledger, _, _, asset, benchmark = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: calculate(ledger, asset, benchmark), range(2))
        )
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"complete_benchmark_relative_return": "99"},
        {"asset_outcome_record_hash": "forged"},
        {"comparison_method": "ALPHA"},
        {"asset_entry_and_exit_fees_included": False},
        {"benchmark_execution_cost_deducted": True},
        {"relative_total_return_calculated": False},
        {"alpha_calculated": True},
        {"risk_adjusted": True},
        {"success_rule_applied": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    ledger, _, _, asset, benchmark = ledgers(tmp_path)
    calculate(ledger, asset, benchmark)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_changed_pinned_support_blocks_verification(tmp_path):
    ledger, assets, _, asset, benchmark = ledgers(tmp_path)
    calculate(ledger, asset, benchmark)
    assets.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    ledger, _, _, asset, benchmark = ledgers(tmp_path)
    calculate(ledger, asset, benchmark)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
