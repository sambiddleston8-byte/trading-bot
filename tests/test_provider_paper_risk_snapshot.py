from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_risk_snapshot import ProviderPaperRiskSnapshotLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
CASH_PAYLOAD = "b" * 64
POSITIONS_PAYLOAD = "c" * 64
ORDERS_PAYLOAD = "d" * 64
PREVIOUS_CLOSE_PAYLOAD = "e" * 64
ORDER_1 = "1" * 64
ORDER_2 = "2" * 64


def account_ledger(tmp_path):
    return PaperBrokerAccountSnapshotLedger(tmp_path / "paper-account.jsonl")


def risk_ledger(tmp_path, account):
    return ProviderPaperRiskSnapshotLedger(tmp_path / "risk-snapshot.jsonl", account)


def pinned_account(account, **overrides):
    values = {
        "broker": "Alpaca",
        "account_reference_sha256": ACCOUNT,
        "observed_at": "2025-01-01T12:00:00+00:00",
        "recorded_at": "2025-01-01T12:01:00+00:00",
        "cash": "1000",
        "settled_cash": "900",
        "unsettled_cash": "100",
        "buying_power": "900",
        "equity": "1500",
        "source_payload_sha256": CASH_PAYLOAD,
        "paper_account_confirmed": True,
    }
    values.update(overrides)
    return account.record(**values)


def record(item, pinned, **overrides):
    values = {
        "account_snapshot_id": pinned["snapshot_id"],
        "account_snapshot_record_hash": pinned["record_hash"],
        "observed_at": "2025-01-01T12:00:30+00:00",
        "recorded_at": "2025-01-01T12:00:45+00:00",
        "previous_close_equity_usd": "1600",
        "previous_close_observed_at": "2024-12-31T21:00:00+00:00",
        "previous_close_equity_source_payload_sha256": PREVIOUS_CLOSE_PAYLOAD,
        "positions": [
            {"ticker": "AAPL", "long_market_value_usd": "500"},
            {"ticker": "MSFT", "long_market_value_usd": "300"},
        ],
        "open_orders": [
            {
                "order_reference_sha256": ORDER_1,
                "ticker": "AAPL",
                "side": "BUY",
                "remaining_notional_usd": "200",
            },
        ],
        "positions_source_payload_sha256": POSITIONS_PAYLOAD,
        "open_orders_source_payload_sha256": ORDERS_PAYLOAD,
        "paper_account_confirmed": True,
    }
    values.update(overrides)
    return item.record(**values)


def rewrite(path, **changes):
    from core.broker import provider_paper_risk_snapshot as module

    value = json.loads(path.read_text())
    value.update(changes)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_records_valid_derivation_and_exposure_computation(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    result = record(item, pinned)
    assert result["previous_hash"] == GENESIS_HASH
    assert result["account_snapshot_id"] == pinned["snapshot_id"]
    assert result["account_snapshot_record_hash"] == pinned["record_hash"]
    assert result["current_account_equity_usd"] == "1500"
    assert result["current_position_gross_exposure_usd"] == "800"
    assert result["pending_buy_exposure_usd"] == "200"
    assert result["conservative_gross_exposure_usd"] == "1000"
    assert result["daily_loss_usd"] == "100"
    assert result["evidence_normalized"] is True
    assert result["broker_reconciliation_complete"] is False
    assert result["risk_limits_enforced"] is False
    assert result["paper_order_submission_allowed"] is False
    assert result["live_trading_enabled"] is False

    result_gain = record(
        item,
        pinned,
        observed_at="2025-01-01T12:00:50+00:00",
        recorded_at="2025-01-01T12:00:55+00:00",
        previous_close_equity_usd="1400",
    )
    assert result_gain["daily_loss_usd"] == "0"
    assert item.verify() == [result, result_gain]


def test_sell_orders_do_not_reduce_exposure(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    result = record(
        item,
        pinned,
        open_orders=[
            {
                "order_reference_sha256": ORDER_1,
                "ticker": "AAPL",
                "side": "BUY",
                "remaining_notional_usd": "200",
            },
            {
                "order_reference_sha256": ORDER_2,
                "ticker": "MSFT",
                "side": "SELL",
                "remaining_notional_usd": "9999",
            },
        ],
    )
    assert result["current_position_gross_exposure_usd"] == "800"
    assert result["pending_buy_exposure_usd"] == "200"
    assert result["conservative_gross_exposure_usd"] == "1000"


def test_positions_and_orders_are_canonically_sorted(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    result = record(
        item,
        pinned,
        positions=[
            {"ticker": "MSFT", "long_market_value_usd": "300"},
            {"ticker": "AAPL", "long_market_value_usd": "500"},
        ],
        open_orders=[
            {
                "order_reference_sha256": ORDER_2,
                "ticker": "MSFT",
                "side": "SELL",
                "remaining_notional_usd": "100",
            },
            {
                "order_reference_sha256": ORDER_1,
                "ticker": "AAPL",
                "side": "BUY",
                "remaining_notional_usd": "200",
            },
        ],
    )
    assert [entry["ticker"] for entry in result["positions"]] == ["AAPL", "MSFT"]
    assert [entry["order_reference_sha256"] for entry in result["open_orders"]] == [ORDER_1, ORDER_2]


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"previous_close_equity_usd": 1600.5}, "float"),
        ({"previous_close_equity_usd": "-1"}, "non-negative"),
        (
            {
                "positions": [
                    {"ticker": "AAPL", "long_market_value_usd": "500"},
                    {"ticker": "AAPL", "long_market_value_usd": "100"},
                ]
            },
            "unique tickers",
        ),
        ({"positions": [{"ticker": "aapl", "long_market_value_usd": "500"}]}, "ticker format"),
        (
            {
                "open_orders": [
                    {
                        "order_reference_sha256": ORDER_1,
                        "ticker": "AAPL",
                        "side": "BUY",
                        "remaining_notional_usd": "0",
                    }
                ]
            },
            "positive",
        ),
        (
            {
                "open_orders": [
                    {
                        "order_reference_sha256": ORDER_1,
                        "ticker": "AAPL",
                        "side": "BUY",
                        "remaining_notional_usd": "1",
                    },
                    {
                        "order_reference_sha256": ORDER_1,
                        "ticker": "MSFT",
                        "side": "BUY",
                        "remaining_notional_usd": "1",
                    },
                ]
            },
            "unique order_reference_sha256",
        ),
        (
            {
                "open_orders": [
                    {
                        "order_reference_sha256": "not-a-hash",
                        "ticker": "AAPL",
                        "side": "BUY",
                        "remaining_notional_usd": "1",
                    }
                ]
            },
            "SHA-256",
        ),
        (
            {
                "open_orders": [
                    {
                        "order_reference_sha256": ORDER_1,
                        "ticker": "AAPL",
                        "side": "HOLD",
                        "remaining_notional_usd": "1",
                    }
                ]
            },
            "BUY or SELL",
        ),
        ({"positions_source_payload_sha256": "not-a-hash"}, "SHA-256"),
        ({"previous_close_equity_source_payload_sha256": "not-a-hash"}, "SHA-256"),
        (
            {"previous_close_observed_at": "2025-01-01T12:00:00+00:00"},
            "earlier UTC-date close",
        ),
        (
            {"previous_close_observed_at": "2025-01-01T10:00:00+00:00"},
            "earlier UTC-date close",
        ),
        (
            {"previous_close_observed_at": "2024-12-20T21:00:00+00:00"},
            "within seven days",
        ),
        ({"paper_account_confirmed": False}, "confirmed"),
    ],
)
def test_invalid_inputs_are_rejected(tmp_path, overrides, fragment):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    with pytest.raises(ValueError, match=fragment):
        record(item, pinned, **overrides)
    assert item.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"observed_at": "2025-01-01T11:59:59+00:00"}, "collection bundle window"),
        (
            {
                "observed_at": "2025-01-01T12:01:01+00:00",
                "recorded_at": "2025-01-01T12:01:01+00:00",
            },
            "collection bundle window",
        ),
        (
            {"observed_at": "2025-01-01T12:00:50+00:00", "recorded_at": "2025-01-01T12:00:40+00:00"},
            "after recorded_at",
        ),
        ({"recorded_at": "2099-01-01T00:00:00+00:00"}, "future"),
        ({"account_snapshot_id": "PBAS-WRONG"}, "missing"),
        ({"account_snapshot_record_hash": "0" * 64}, "hash has changed"),
    ],
)
def test_timing_and_pin_validation_is_enforced(tmp_path, overrides, fragment):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    with pytest.raises(ValueError, match=fragment):
        record(item, pinned, **overrides)


def test_concurrent_retry_is_idempotent(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: record(item, pinned), range(2)))
    assert first == second
    assert len(item.verify()) == 1


def test_multi_record_chain_verifies_across_pinned_snapshots(tmp_path):
    account = account_ledger(tmp_path)
    pinned_first = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    first = record(item, pinned_first)
    pinned_second = pinned_account(
        account,
        observed_at="2025-01-02T12:00:00+00:00",
        recorded_at="2025-01-02T12:01:00+00:00",
        cash="1100",
        settled_cash="1000",
        equity="1600",
    )
    second = record(
        item,
        pinned_second,
        account_snapshot_id=pinned_second["snapshot_id"],
        account_snapshot_record_hash=pinned_second["record_hash"],
        observed_at="2025-01-02T12:00:20+00:00",
        recorded_at="2025-01-02T12:00:30+00:00",
    )
    assert second["previous_hash"] == first["record_hash"]
    assert item.verify() == [first, second]


def test_monotonic_observed_at_per_account_is_enforced(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    record(item, pinned, observed_at="2025-01-01T12:00:40+00:00", recorded_at="2025-01-01T12:00:45+00:00")
    with pytest.raises(ValueError, match="move forward"):
        record(
            item,
            pinned,
            observed_at="2025-01-01T12:00:10+00:00",
            recorded_at="2025-01-01T12:00:45+00:00",
        )
    assert len(item.verify()) == 1


def test_equal_time_with_changed_evidence_is_rejected_before_append(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    record(item, pinned)
    with pytest.raises(ValueError, match="move forward"):
        record(item, pinned, previous_close_equity_usd="1700")
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"current_position_gross_exposure_usd": "999999"},
        {"pending_buy_exposure_usd": "999999"},
        {"conservative_gross_exposure_usd": "999999"},
        {"daily_loss_usd": "999999"},
        {"current_account_equity_usd": "999999"},
        {"account_snapshot_record_hash": "0" * 64},
        {"broker_reconciliation_complete": True},
        {"risk_limits_enforced": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"source_payloads_stored": True},
        {"external_head_anchor_present": True},
        {"cryptographic_authentication_present": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    record(item, pinned)
    rewrite(item.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_rehashed_unexpected_field_is_rejected(tmp_path):
    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    record(item, pinned)
    rewrite(item.path, injected_field="unexpected")
    with pytest.raises(LedgerIntegrityError):
        item.verify()


@pytest.mark.parametrize("mutation", ["extra", "non_mapping", "noncanonical_fraction"])
def test_rehashed_nested_shape_tampering_is_rejected(tmp_path, mutation):
    from core.broker import provider_paper_risk_snapshot as module

    account = account_ledger(tmp_path)
    pinned = pinned_account(account)
    item = risk_ledger(tmp_path, account)
    record(item, pinned)
    value = json.loads(item.path.read_text())
    if mutation == "extra":
        value["positions"][0]["short_market_value_usd"] = "10"
    elif mutation == "non_mapping":
        value["positions"][0] = "AAPL"
    else:
        value["exact_fractions"]["positions"][0]["long_market_value_usd"] = {
            "numerator": "1000",
            "denominator": "2",
        }
    material = {key: entry for key, entry in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    item.path.write_text(json.dumps(value) + "\n")
    with pytest.raises(LedgerIntegrityError):
        item.verify()
