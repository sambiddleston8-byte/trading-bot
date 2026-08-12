from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import FixedHorizonRoundTripOutcomeLedger


IDENTITY = {
    "decision_id": "DEC-001",
    "portfolio_version": "PORT-001",
    "ticker": "NVDA",
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
    observation = {
        "observation_id": "OBS-OUTCOME",
        "record_hash": "hash-observation",
        "asset_price": 120,
        "asset_price_effective_at": "2025-02-03T16:00:00+00:00",
    }
    total = {
        "result_id": "TRET-1",
        "record_hash": "hash-total",
        "outcome_observation_id": observation["observation_id"],
        "exact_fractions": {
            "recorded_entry_cost": fraction(202),
            "split_adjusted_quantity": fraction(2),
            "gross_dividend_cash": fraction(10),
        },
    }
    total_ledger = Stub([total])
    total_ledger.observation_ledger = Stub([observation])
    pair = {
        "pair_id": "POPAIR-1",
        "record_hash": "hash-pair",
        "paired_at": "2025-02-03T17:02:00+00:00",
        "total_return_result_id": total["result_id"],
        "fill_id": "FILL-ENTRY",
        "horizon": "1_MONTH",
        "horizon_label": "1 month",
        "exact_fractions": {"predicted_expected_return": fraction(1, 5)},
        **IDENTITY,
    }
    pair_ledger = Stub([pair])
    pair_ledger.total_return_ledger = total_ledger
    exit_fill = {
        "fill_id": "FILL-EXIT",
        "record_hash": "hash-exit",
        "filled_at": observation["asset_price_effective_at"],
    }
    trip = {
        "result_id": "RTRIP-1",
        "record_hash": "hash-trip",
        "calculated_at": "2025-02-03T17:01:00+00:00",
        "entry_fill_id": "FILL-ENTRY",
        "exit_fill_id": exit_fill["fill_id"],
        "exit_filled_at": exit_fill["filled_at"],
        "exact_fractions": {
            "filled_quantity": fraction(2),
            "recorded_entry_cost": fraction(202),
            "exit_fill_gross_value": fraction(240),
            "recorded_exit_proceeds": fraction(237),
        },
        **IDENTITY,
    }
    round_trips = Stub([trip])
    round_trips.execution_ledger = Stub([exit_fill])
    result_ledger = FixedHorizonRoundTripOutcomeLedger(
        tmp_path / "fixed_round_trips.jsonl", pair_ledger, round_trips
    )
    return result_ledger, pair_ledger, round_trips, pair, trip, total, observation, exit_fill


def pair_result(ledger, pair, trip, **overrides):
    values = {
        "prediction_pair_id": pair["pair_id"],
        "round_trip_result_id": trip["result_id"],
        "paired_at": "2025-02-03T17:03:00+00:00",
    }
    values.update(overrides)
    return ledger.pair(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import fixed_horizon_round_trip as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_pairs_complete_round_trip_at_exact_prediction_horizon(tmp_path):
    ledger, _, _, pair, trip, _, _, _ = ledgers(tmp_path)
    result = pair_result(ledger, pair, trip)
    assert result["record_type"] == "COMPLETE_FIXED_HORIZON_PREDICTION_ROUND_TRIP_OUTCOME"
    assert result["complete_round_trip"] is True
    assert result["entry_and_exit_fees_included"] is True
    assert result["gross_dividends_included_pre_tax"] is True
    assert result["recorded_entry_cost"] == "202"
    assert result["recorded_exit_proceeds"] == "237"
    assert result["gross_paid_dividend_cash"] == "10"
    assert result["net_outcome_value"] == "247"
    assert result["net_profit_or_loss"] == "45"
    assert result["exact_fractions"]["net_total_return_after_entry_and_exit_fees"] == fraction(45, 202)
    assert result["exact_fractions"]["prediction_error"] == fraction(23, 1010)
    assert result["success_rule_applied"] is False
    assert result["hit_rate_calculated"] is False
    assert result["calibration_calculated"] is False
    assert result["order_submission_enabled"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda pair, trip, total, observation, exit_fill: trip.update(ticker="AAPL"), "identity"),
        (lambda pair, trip, total, observation, exit_fill: trip.update(entry_fill_id="OTHER"), "entry fill"),
        (
            lambda pair, trip, total, observation, exit_fill: exit_fill.update(
                filled_at="2025-02-03T16:01:00+00:00"
            ),
            "time",
        ),
        (
            lambda pair, trip, total, observation, exit_fill: trip["exact_fractions"].update(
                recorded_entry_cost=fraction(203)
            ),
            "entry cost",
        ),
        (
            lambda pair, trip, total, observation, exit_fill: trip["exact_fractions"].update(
                filled_quantity=fraction(1)
            ),
            "quantity",
        ),
        (
            lambda pair, trip, total, observation, exit_fill: observation.update(asset_price=119),
            "price",
        ),
    ],
)
def test_mismatched_horizon_evidence_fails_closed(tmp_path, mutation, fragment):
    ledger, _, _, pair, trip, total, observation, exit_fill = ledgers(tmp_path)
    mutation(pair, trip, total, observation, exit_fill)
    result = pair_result(ledger, pair, trip)
    assert result["status"] == "NOT_PAIRABLE"
    assert fragment in " ".join(result["reasons"]).lower()


def test_missing_support_and_time_guards_fail_closed(tmp_path):
    ledger, pair_ledger, _, pair, trip, _, _, _ = ledgers(tmp_path)
    pair_ledger.values.clear()
    missing = pair_result(ledger, pair, trip)
    assert missing["status"] == "NOT_PAIRABLE"

    ledger, _, _, pair, trip, _, _, _ = ledgers(tmp_path / "other")
    early = pair_result(ledger, pair, trip, paired_at="2025-02-03T17:00:00+00:00")
    future = pair_result(ledger, pair, trip, paired_at="2099-01-01T00:00:00+00:00")
    assert early["status"] == "NOT_PAIRABLE"
    assert future["status"] == "NOT_PAIRABLE"


def test_identical_concurrent_retries_append_once(tmp_path):
    ledger, _, _, pair, trip, _, _, _ = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: pair_result(ledger, pair, trip), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"net_total_return_after_entry_and_exit_fees": "99"},
        {"prediction_error": "99"},
        {"complete_round_trip": False},
        {"entry_and_exit_fees_included": False},
        {"gross_dividends_included_pre_tax": False},
        {"success_rule_applied": True},
        {"hit_rate_calculated": True},
        {"calibration_calculated": True},
        {"order_submission_enabled": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    ledger, _, _, pair, trip, _, _, _ = ledgers(tmp_path)
    pair_result(ledger, pair, trip)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_changed_pinned_support_blocks_verification(tmp_path):
    ledger, pair_ledger, _, pair, trip, _, _, _ = ledgers(tmp_path)
    pair_result(ledger, pair, trip)
    pair_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    ledger, _, _, pair, trip, _, _, _ = ledgers(tmp_path)
    pair_result(ledger, pair, trip)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
