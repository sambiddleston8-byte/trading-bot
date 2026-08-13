from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PaperPositionStateLedger


IDENTITY = {
    "portfolio_version": "PORT-001",
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def fill(identifier, side, timestamp, quantity, gross, fee=0, ticker="AAA", **overrides):
    result = {
        "fill_id": identifier,
        "record_hash": f"hash-{identifier}",
        "decision_id": f"DEC-{identifier}",
        "ticker": ticker,
        "side": side,
        "filled_at": timestamp,
        "filled_quantity": quantity,
        "gross_value": gross,
        "fees": fee,
        **IDENTITY,
    }
    result.update(overrides)
    return result


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


def ledger(tmp_path, fills):
    source = Stub(fills)
    return source, PaperPositionStateLedger(tmp_path / "positions.jsonl", source)


def calculate(target, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "as_of": "2025-02-01T21:00:00+00:00",
        "calculated_at": "2025-02-01T21:01:00+00:00",
    }
    values.update(overrides)
    return target.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import paper_position_state as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_exact_open_positions_and_trade_cash_only(tmp_path):
    fills = [
        fill("BUY-1", "BUY", "2025-01-02T15:00:00+00:00", "2.5", "250", "1.25"),
        fill("BUY-2", "BUY", "2025-01-03T15:00:00+00:00", "1", "120", "0.75"),
    ]
    _, target = ledger(tmp_path, fills)
    result = calculate(target)
    assert result["scope"] == "LOCAL_SIMULATED_PAPER_POSITION_STATE"
    assert result["open_positions"][0]["open_quantity"] == "3.5"
    assert result["open_positions"][0]["exact_open_quantity"] == fraction(7, 2)
    assert result["total_buy_gross"] == "370"
    assert result["total_sell_gross"] == "0"
    assert result["total_recorded_fees"] == "2"
    assert result["net_trade_cash_flow"] == "-372"
    assert result["source_fill_count"] == 2
    assert result["source_fill_tail_hash"] == "hash-BUY-2"
    assert result["broker_cash_reconciled"] is False
    assert result["performance_metric_calculated"] is False
    assert result["order_submission_enabled"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert target.verify() == [result]


def test_partial_sell_consumes_oldest_lots_across_different_decisions(tmp_path):
    fills = [
        fill("BUY-A", "BUY", "2025-01-02T15:00:00+00:00", 2, 200, 1),
        fill("BUY-B", "BUY", "2025-01-03T15:00:00+00:00", 3, 330, 2),
        fill("SELL-C", "SELL", "2025-01-04T15:00:00+00:00", 4, 480, 3),
    ]
    _, target = ledger(tmp_path, fills)
    result = calculate(target)
    position = result["open_positions"][0]
    assert position["open_quantity"] == "1"
    assert [lot["buy_fill_id"] for lot in position["supporting_open_lots"]] == ["BUY-B"]
    assert position["supporting_open_lots"][0]["remaining_quantity"] == "1"
    assert result["net_trade_cash_flow"] == "-56"
    assert result["lot_pooling_policy"] == "PORTFOLIO_VERSION_AND_TICKER_ONLY"


def test_complete_sale_removes_position(tmp_path):
    fills = [
        fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200),
        fill("SELL", "SELL", "2025-01-03T15:00:00+00:00", 2, 220),
    ]
    _, target = ledger(tmp_path, fills)
    result = calculate(target)
    assert result["open_positions"] == []
    assert result["open_position_count"] == 0
    assert result["net_trade_cash_flow"] == "20"
    assert result["realized_profit_calculated"] is False


def test_multi_ticker_and_same_time_buys_have_deterministic_fill_id_order(tmp_path):
    fills = [
        fill("BUY-Z", "BUY", "2025-01-02T15:00:00+00:00", 2, 200),
        fill("BUY-A", "BUY", "2025-01-02T15:00:00+00:00", 1, 90),
        fill("BUY-BBB", "BUY", "2025-01-02T15:00:00+00:00", 4, 200, ticker="BBB"),
        fill("SELL", "SELL", "2025-01-03T15:00:00+00:00", 2, 220),
    ]
    _, target = ledger(tmp_path, fills)
    result = calculate(target)
    assert [position["ticker"] for position in result["open_positions"]] == ["AAA", "BBB"]
    aaa = result["open_positions"][0]
    assert aaa["open_quantity"] == "1"
    assert [lot["buy_fill_id"] for lot in aaa["supporting_open_lots"]] == ["BUY-Z"]


def test_integrates_with_verified_local_execution_hash_chain(tmp_path):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    for order_id, decision_id, side, quantity, created_at in (
        ("PORD-BUY", "DEC-BUY", "BUY", 3, "2025-01-02T14:59:00+00:00"),
        ("PORD-SELL", "DEC-SELL", "SELL", 1, "2025-01-03T14:59:00+00:00"),
    ):
        proposals.propose(
            decision_id=decision_id,
            portfolio_version="PORT-001",
            ticker="AAA",
            side=side,
            quantity=quantity,
            reference_price=100,
            target_weight=0.3 if side == "BUY" else 0.2,
            created_at=created_at,
            order_id=order_id,
            strategy_version=IDENTITY["strategy_version"],
            model_versions=IDENTITY["model_versions"],
            git_revision=IDENTITY["git_revision"],
        )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    executions.simulate_full_fill(
        order_id="PORD-BUY",
        fill_price=101,
        fees=1,
        filled_at="2025-01-02T15:00:00+00:00",
    )
    executions.simulate_full_fill(
        order_id="PORD-SELL",
        fill_price=105,
        fees=0.5,
        filled_at="2025-01-03T15:00:00+00:00",
    )
    target = PaperPositionStateLedger(tmp_path / "positions.jsonl", executions)
    result = calculate(target)
    assert result["open_positions"][0]["open_quantity"] == "2"
    assert result["source_fill_tail_hash"] == executions.verify()[-1]["record_hash"]
    assert target.verify() == [result]


@pytest.mark.parametrize(
    "fills,fragment",
    [
        ([fill("SELL", "SELL", "2025-01-02T15:00:00+00:00", 1, 100)], "exceeds"),
        (
            [
                fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 1, 100),
                fill("SELL", "SELL", "2025-01-03T15:00:00+00:00", 2, 220),
            ],
            "exceeds",
        ),
        (
            [
                fill("BUY", "BUY", "2025-01-02T15:00:00Z", 1, 100),
                fill("SELL", "SELL", "2025-01-02T15:00:00+00:00", 1, 100),
            ],
            "ambiguous",
        ),
    ],
)
def test_invalid_short_or_ambiguous_history_fails_without_append(tmp_path, fills, fragment):
    _, target = ledger(tmp_path, fills)
    with pytest.raises(ValueError, match=fragment):
        calculate(target)
    assert target.records() == []


def test_future_and_other_portfolio_fills_are_excluded(tmp_path):
    fills = [
        fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200),
        fill("FUTURE", "BUY", "2025-02-02T15:00:00+00:00", 10, 1000),
        fill(
            "OTHER",
            "BUY",
            "2025-01-02T15:00:00+00:00",
            50,
            5000,
            portfolio_version="PORT-OTHER",
        ),
    ]
    _, target = ledger(tmp_path, fills)
    result = calculate(target)
    assert result["supporting_fill_ids"] == ["BUY"]
    assert result["open_positions"][0]["open_quantity"] == "2"
    assert result["source_fill_count"] == 3


def test_later_backdated_fill_does_not_rewrite_historical_snapshot(tmp_path):
    source, target = ledger(
        tmp_path,
        [fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200)],
    )
    original = calculate(target)
    source.values.append(
        fill("BACKDATED", "BUY", "2025-01-03T15:00:00+00:00", 5, 500)
    )
    assert target.verify() == [original]
    revised = calculate(
        target,
        calculated_at="2025-02-01T21:02:00+00:00",
    )
    assert revised["snapshot_id"] != original["snapshot_id"]
    assert revised["open_positions"][0]["open_quantity"] == "7"
    assert target.verify() == [original, revised]


def test_mixed_strategy_identity_is_rejected(tmp_path):
    fills = [
        fill("BUY-A", "BUY", "2025-01-02T15:00:00+00:00", 1, 100),
        fill(
            "BUY-B",
            "BUY",
            "2025-01-03T15:00:00+00:00",
            1,
            100,
            strategy_version="strategy-v2",
        ),
    ]
    _, target = ledger(tmp_path, fills)
    with pytest.raises(ValueError, match="share strategy"):
        calculate(target)


@pytest.mark.parametrize(
    "changes",
    [
        {"open_position_count": 99},
        {"source_fill_count": 99},
        {"fifo_policy": "USER_SELECTED"},
        {"broker_cash_reconciled": True},
        {"realized_profit_calculated": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"order_submission_enabled": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, target = ledger(
        tmp_path,
        [fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200)],
    )
    calculate(target)
    rewrite_with_valid_hash(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_changed_pinned_fill_blocks_verification(tmp_path):
    source, target = ledger(
        tmp_path,
        [fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200)],
    )
    calculate(target)
    source.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="prefix"):
        target.verify()


def test_identical_concurrent_retries_create_one_snapshot(tmp_path):
    _, target = ledger(
        tmp_path,
        [fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200)],
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(target), range(2)))
    assert first == second
    assert len(target.verify()) == 1


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, target = ledger(
        tmp_path,
        [fill("BUY", "BUY", "2025-01-02T15:00:00+00:00", 2, 200)],
    )
    calculate(target)
    with target.path.open("ab") as output:
        output.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.verify()
    backup = target.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(target.verify()) == 1
