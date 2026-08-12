from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import SimulatedRoundTripExecutionLedger


IDENTITY = {
    "decision_id": "DEC-001",
    "portfolio_version": "PORT-001",
    "ticker": "NVDA",
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "research", "version": "1.0"}],
    "git_revision": "abc123",
}


def fill(identifier, side, filled_at, gross, fee, slippage, quantity=2, **overrides):
    result = {
        "fill_id": identifier,
        "record_hash": f"hash-{identifier}",
        "side": side,
        "filled_at": filled_at,
        "filled_quantity": quantity,
        "gross_value": gross,
        "fees": fee,
        "slippage_amount": slippage,
        **IDENTITY,
    }
    result.update(overrides)
    return result


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


def ledgers(tmp_path):
    entry = fill("FILL-ENTRY", "BUY", "2025-01-02T15:00:00+00:00", 200, 2, 1)
    exit_fill = fill("FILL-EXIT", "SELL", "2025-02-03T15:00:00+00:00", 240, 3, 2)
    executions = Stub([entry, exit_fill])
    ledger = SimulatedRoundTripExecutionLedger(tmp_path / "round_trips.jsonl", executions)
    return executions, ledger, entry, exit_fill


def calculate(ledger, **overrides):
    values = {
        "entry_fill_id": "FILL-ENTRY",
        "exit_fill_id": "FILL-EXIT",
        "calculated_at": "2025-02-03T15:01:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def rewrite_with_valid_hash(path, **changes):
    from core.performance import round_trip_execution as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_complete_round_trip_after_both_fees(tmp_path):
    _, ledger, _, _ = ledgers(tmp_path)
    result = calculate(ledger)
    assert result["scope"] == "COMPLETE_LOCAL_SIMULATED_ENTRY_EXIT_ROUND_TRIP"
    assert result["recorded_entry_cost"] == "202"
    assert result["recorded_exit_proceeds"] == "237"
    assert result["net_profit_or_loss"] == "35"
    assert result["exact_fractions"]["net_round_trip_return"] == fraction(35, 202)
    assert result["recorded_round_trip_fees"] == "5"
    assert result["recorded_signed_round_trip_slippage"] == "3"
    assert result["pairing_policy"] == "FIFO_BY_FILLED_AT_THEN_FILL_ID"
    assert result["entry_and_exit_fees_included"] is True
    assert result["entry_and_exit_fill_slippage_embedded"] is True
    assert result["bid_ask_spread_separately_included"] is False
    assert result["market_impact_included"] is False
    assert result["tax_and_withholding_modelled"] is False
    assert result["recommendation_provided"] is False
    assert result["order_submission_enabled"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda entry, exit_fill: entry.update(side="SELL"), "BUY entry"),
        (lambda entry, exit_fill: exit_fill.update(side="BUY"), "SELL exit"),
        (lambda entry, exit_fill: exit_fill.update(ticker="AAPL"), "share decision"),
        (lambda entry, exit_fill: exit_fill.update(filled_quantity=1), "quantities"),
        (
            lambda entry, exit_fill: exit_fill.update(filled_at="2025-01-02T15:00:00+00:00"),
            "strictly after",
        ),
        (lambda entry, exit_fill: exit_fill.update(fees=999), "cannot exceed"),
    ],
)
def test_invalid_fill_pair_fails_closed(tmp_path, mutation, fragment):
    _, ledger, entry, exit_fill = ledgers(tmp_path)
    mutation(entry, exit_fill)
    result = calculate(ledger)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert ledger.records() == []


def test_missing_fill_and_invalid_calculation_time_fail_closed(tmp_path):
    executions, ledger, _, _ = ledgers(tmp_path)
    executions.values.pop()
    missing = calculate(ledger)
    assert missing["status"] == "NOT_CALCULABLE"
    assert "missing" in " ".join(missing["reasons"]).lower()

    _, ledger, _, _ = ledgers(tmp_path / "other")
    early = calculate(ledger, calculated_at="2025-02-03T14:59:00+00:00")
    assert early["status"] == "NOT_CALCULABLE"
    future = calculate(ledger, calculated_at="2099-01-01T00:00:00+00:00")
    assert future["status"] == "NOT_CALCULABLE"


def test_fill_cannot_be_reused_in_another_round_trip(tmp_path):
    executions, ledger, _, _ = ledgers(tmp_path)
    calculate(ledger)
    executions.values.append(
        fill("FILL-EXIT-2", "SELL", "2025-02-04T15:00:00+00:00", 250, 3, 1)
    )
    reused = calculate(
        ledger,
        exit_fill_id="FILL-EXIT-2",
        calculated_at="2025-02-04T15:01:00+00:00",
    )
    assert reused["status"] == "NOT_CALCULABLE"
    assert "FIFO" in " ".join(reused["reasons"])


def test_fifo_pairing_prevents_favourable_fill_selection(tmp_path):
    executions, ledger, _, _ = ledgers(tmp_path)
    older = fill(
        "FILL-OLDER", "BUY", "2025-01-01T15:00:00+00:00", 180, 2, 1
    )
    executions.values.insert(0, older)

    selected = calculate(ledger)
    assert selected["status"] == "NOT_CALCULABLE"
    assert "FIFO" in " ".join(selected["reasons"])

    canonical = calculate(ledger, entry_fill_id="FILL-OLDER")
    assert canonical["status"] == "CALCULATED"
    assert canonical["entry_fill_id"] == "FILL-OLDER"


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, ledger, _, _ = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"net_round_trip_return": "99"},
        {"entry_fill_record_hash": "forged"},
        {"complete_round_trip_calculated": False},
        {"pairing_policy": "USER_SELECTED"},
        {"entry_and_exit_fees_included": False},
        {"entry_and_exit_fill_slippage_embedded": False},
        {"market_impact_included": True},
        {"tax_and_withholding_modelled": True},
        {"recommendation_provided": True},
        {"order_submission_enabled": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, ledger, _, _ = ledgers(tmp_path)
    calculate(ledger)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_changed_pinned_fill_blocks_verification(tmp_path):
    executions, ledger, _, _ = ledgers(tmp_path)
    calculate(ledger)
    executions.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="supporting evidence"):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, ledger, _, _ = ledgers(tmp_path)
    calculate(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
