from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PeriodicTurnoverLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def fill(identifier, side, gross, filled_at, fee=0, **overrides):
    result = {
        "fill_id": identifier,
        "record_hash": f"hash-{identifier}",
        "portfolio_version": "PORT-001",
        "filled_at": filled_at,
        "side": side,
        "gross_value": gross,
        "fees": fee,
        **IDENTITY,
    }
    result.update(overrides)
    return result


def valuation(identifier, effective, equity, fills, **overrides):
    result = {
        "valuation_id": identifier,
        "record_hash": f"hash-{identifier}",
        "portfolio_version": "PORT-001",
        "effective_at": effective,
        "calculated_at": effective,
        "supporting_fill_ids": [item["fill_id"] for item in fills],
        "supporting_fill_hashes": [item["record_hash"] for item in fills],
        "exact_fractions": {"total_equity": fraction(equity)},
        **IDENTITY,
    }
    result.update(overrides)
    return result


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


class ValuationStub(Stub):
    def __init__(self, values, executions):
        super().__init__(values)
        self.position_value_ledger = type("PositionValues", (), {})()
        self.position_value_ledger.observation_ledger = type("Observations", (), {})()
        self.position_value_ledger.observation_ledger.execution_ledger = executions


def setup_ledgers(tmp_path, *, interval=True):
    initial = fill("FILL-INITIAL", "BUY", 500, "2025-01-02T20:00:00+00:00", 1)
    interval_fills = []
    if interval:
        interval_fills = [
            fill("FILL-BUY", "BUY", 100, "2025-01-03T15:00:00+00:00", 0.5),
            fill("FILL-SELL", "SELL", 50, "2025-01-03T16:00:00+00:00", 0.25),
        ]
    executions = Stub([initial, *interval_fills])
    previous = valuation("VAL-1", "2025-01-02T21:00:00+00:00", 1000, [initial])
    current = valuation(
        "VAL-2", "2025-01-03T21:00:00+00:00", 1100, [initial, *interval_fills]
    )
    values = ValuationStub([previous, current], executions)
    ledger = PeriodicTurnoverLedger(tmp_path / "turnover.jsonl", values)
    return values, executions, ledger, current


def calculate(ledger, current, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "current_valuation_id": current["valuation_id"],
        "calculated_at": "2025-01-03T21:01:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import periodic_turnover as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_gross_two_way_turnover_excludes_initial_deployment(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path)
    result = calculate(ledger, current)
    assert result["scope"] == "SIMULATED_GROSS_TWO_WAY_PERIODIC_TURNOVER"
    assert result["supporting_fill_ids"] == ["FILL-BUY", "FILL-SELL"]
    assert "FILL-INITIAL" not in result["supporting_fill_ids"]
    assert result["exact_fractions"]["gross_buy_notional"] == fraction(100)
    assert result["exact_fractions"]["gross_sell_notional"] == fraction(50)
    assert result["exact_fractions"]["average_boundary_equity"] == fraction(1050)
    assert result["exact_fractions"]["gross_period_turnover"] == fraction(1, 7)
    assert result["exact_fractions"]["recorded_execution_fees"] == fraction(3, 4)
    assert result["execution_count"] == 2
    assert result["initial_deployment_excluded"] is True
    assert result["complete_round_trip_claim"] is False
    assert result["annualized"] is False
    assert result["order_submission_enabled"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


def test_zero_activity_interval_has_zero_turnover(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path, interval=False)
    result = calculate(ledger, current)
    assert result["status"] == "CALCULATED"
    assert result["supporting_fill_ids"] == []
    assert result["execution_count"] == 0
    assert result["exact_fractions"]["gross_period_turnover"] == fraction(0)


def test_first_valuation_has_no_turnover_predecessor(tmp_path):
    values, _, ledger, _ = setup_ledgers(tmp_path)
    result = ledger.calculate(portfolio_version="PORT-001", current_valuation_id="VAL-1")
    assert result["status"] == "NOT_CALCULABLE"
    assert "previous" in " ".join(result["reasons"]).lower()


def test_missing_or_extra_interval_fill_blocks_calculation(tmp_path):
    values, executions, ledger, current = setup_ledgers(tmp_path)
    executions.values.pop()
    result = calculate(ledger, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exactly reconcile" in " ".join(result["reasons"])

    extra = fill("FILL-EXTRA", "BUY", 10, "2025-01-03T17:00:00+00:00")
    executions.values.append(extra)
    result = calculate(ledger, current)
    assert result["status"] == "NOT_CALCULABLE"


def test_valuation_support_cannot_remove_prior_fill(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path)
    current["supporting_fill_ids"].remove("FILL-INITIAL")
    current["supporting_fill_hashes"].remove("hash-FILL-INITIAL")
    result = calculate(ledger, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exactly reconcile" in " ".join(result["reasons"])


def test_identity_mismatch_fails_closed(tmp_path):
    _, executions, ledger, current = setup_ledgers(tmp_path)
    executions.values[1]["git_revision"] = "different"
    result = calculate(ledger, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "identity" in " ".join(result["reasons"])


@pytest.mark.parametrize("field,value", [("gross_value", 0), ("gross_value", -1), ("fees", -1)])
def test_invalid_execution_economics_fail_closed(tmp_path, field, value):
    _, executions, ledger, current = setup_ledgers(tmp_path)
    executions.values[1][field] = value
    result = calculate(ledger, current)
    assert result["status"] == "NOT_CALCULABLE"


def test_calculation_cannot_predate_support_or_use_future_time(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path)
    early = calculate(ledger, current, calculated_at="2025-01-03T20:59:00+00:00")
    assert early["status"] == "NOT_CALCULABLE"
    future = calculate(ledger, current, calculated_at="2099-01-01T00:00:00+00:00")
    assert future["status"] == "NOT_CALCULABLE"


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(ledger, current), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"gross_period_turnover": "99"},
        {"supporting_fill_ids": []},
        {"initial_deployment_excluded": False},
        {"gross_two_way_convention": False},
        {"annualized": True},
        {"order_submission_enabled": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, ledger, current = setup_ledgers(tmp_path)
    calculate(ledger, current)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_changed_pinned_fill_or_valuation_blocks_verification(tmp_path):
    values, executions, ledger, current = setup_ledgers(tmp_path)
    calculate(ledger, current)
    executions.values[1]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="supporting evidence"):
        ledger.verify()

    executions.values[1]["record_hash"] = "hash-FILL-BUY"
    values.values[1]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="supporting evidence"):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, ledger, current = setup_ledgers(tmp_path)
    calculate(ledger, current)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
