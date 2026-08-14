from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_open_order_quantity_evidence import (
    ProviderPaperOpenOrderQuantityEvidenceLedger,
)
from core.broker.provider_paper_risk_snapshot import ProviderPaperRiskSnapshotLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
ACCOUNT_PAYLOAD = "b" * 64
POSITIONS_PAYLOAD = "c" * 64
ORDERS_PAYLOAD = "d" * 64
PRIOR_PAYLOAD = "e" * 64
BUY_ORDER = "1" * 64
SELL_ORDER = "2" * 64


def ledgers(tmp_path):
    account = PaperBrokerAccountSnapshotLedger(tmp_path / "account.jsonl")
    risk = ProviderPaperRiskSnapshotLedger(tmp_path / "risk.jsonl", account)
    quantities = ProviderPaperOpenOrderQuantityEvidenceLedger(
        tmp_path / "order-quantities.jsonl", risk
    )
    return account, risk, quantities


def make_risk(
    account_ledger,
    risk_ledger,
    *,
    account_observed="2026-01-01T12:00:00+00:00",
    risk_observed="2026-01-01T12:00:30+00:00",
    open_orders=None,
):
    if open_orders is None:
        open_orders = [
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_notional_usd": "250",
            },
            {
                "order_reference_sha256": BUY_ORDER,
                "ticker": "MSFT",
                "side": "BUY",
                "remaining_notional_usd": "300",
            },
        ]
    account = account_ledger.record(
        broker="Alpaca",
        account_reference_sha256=ACCOUNT,
        observed_at=account_observed,
        recorded_at=account_observed,
        cash="700",
        settled_cash="700",
        unsettled_cash="0",
        buying_power="700",
        equity="1500",
        source_payload_sha256=ACCOUNT_PAYLOAD,
        paper_account_confirmed=True,
    )
    return risk_ledger.record(
        account_snapshot_id=account["snapshot_id"],
        account_snapshot_record_hash=account["record_hash"],
        observed_at=risk_observed,
        recorded_at=risk_observed,
        previous_close_equity_usd="1500",
        previous_close_observed_at="2025-12-31T21:00:00+00:00",
        previous_close_equity_source_payload_sha256=PRIOR_PAYLOAD,
        positions=[{"ticker": "AAPL", "long_market_value_usd": "500"}],
        open_orders=open_orders,
        positions_source_payload_sha256=POSITIONS_PAYLOAD,
        open_orders_source_payload_sha256=ORDERS_PAYLOAD,
        paper_account_confirmed=True,
    )


def evidence(ledger, risk, *, open_orders=None, **changes):
    if open_orders is None:
        open_orders = [
            {
                "order_reference_sha256": BUY_ORDER,
                "ticker": "MSFT",
                "side": "BUY",
                "remaining_quantity": "3",
                "risk_mark_price_usd": "100",
            },
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_quantity": "1.25",
                "risk_mark_price_usd": "200",
            },
        ]
    values = {
        "risk_snapshot_id": risk["snapshot_id"],
        "risk_snapshot_record_hash": risk["record_hash"],
        "open_orders": open_orders,
        "recorded_at": "2026-01-01T12:01:00+00:00",
    }
    values.update(changes)
    return ledger.record(**values)


def rewrite(path, mutation):
    from core.broker import provider_paper_open_order_quantity_evidence as module

    value = json.loads(path.read_text())
    mutation(value)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_quantities_prices_and_totals_match_pinned_orders(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    result = evidence(quantities, risk)

    assert [item["order_reference_sha256"] for item in result["open_orders"]] == [
        SELL_ORDER,
        BUY_ORDER,
    ]
    assert result["pending_buy_remaining_notional_usd"] == "300"
    assert result["pending_sell_remaining_notional_usd"] == "250"
    assert result["open_orders_source_payload_sha256"] == ORDERS_PAYLOAD
    assert result["quantity_price_identity_validated"] is True
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "broker_reconciliation_complete",
        "risk_policy_assessed",
        "risk_limits_enforced",
        "order_route_exists",
        "paper_order_submission_allowed",
        "live_trading_enabled",
        "external_head_anchor_present",
        "cryptographic_authentication_present",
    ):
        assert result[field] is False


def test_empty_pinned_orders_accept_only_empty_evidence(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger, open_orders=[])
    result = evidence(quantities, risk, open_orders=[])
    assert result["open_orders"] == []
    assert result["pending_buy_remaining_notional_usd"] == "0"
    assert result["pending_sell_remaining_notional_usd"] == "0"


def test_sell_total_is_explicitly_derived_from_every_pinned_sell_order(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(
        account,
        risk_ledger,
        open_orders=[
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_notional_usd": "250",
            },
            {
                "order_reference_sha256": "3" * 64,
                "ticker": "MSFT",
                "side": "SELL",
                "remaining_notional_usd": "150",
            },
        ],
    )
    result = evidence(
        quantities,
        risk,
        open_orders=[
            {
                "order_reference_sha256": "3" * 64,
                "ticker": "MSFT",
                "side": "SELL",
                "remaining_quantity": "1.5",
                "risk_mark_price_usd": "100",
            },
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_quantity": "1.25",
                "risk_mark_price_usd": "200",
            },
        ],
    )
    assert result["pending_sell_remaining_notional_usd"] == "400"


@pytest.mark.parametrize(
    "orders,fragment",
    [
        (
            [
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "MSFT",
                    "side": "BUY",
                    "remaining_quantity": "2",
                    "risk_mark_price_usd": "100",
                },
                {
                    "order_reference_sha256": SELL_ORDER,
                    "ticker": "AAPL",
                    "side": "SELL",
                    "remaining_quantity": "1.25",
                    "risk_mark_price_usd": "200",
                },
            ],
            "must equal pinned notional",
        ),
        (
            [
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "AAPL",
                    "side": "BUY",
                    "remaining_quantity": "3",
                    "risk_mark_price_usd": "100",
                },
                {
                    "order_reference_sha256": SELL_ORDER,
                    "ticker": "AAPL",
                    "side": "SELL",
                    "remaining_quantity": "1.25",
                    "risk_mark_price_usd": "200",
                },
            ],
            "ticker and side",
        ),
        (
            [
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "MSFT",
                    "side": "BUY",
                    "remaining_quantity": 3.0,
                    "risk_mark_price_usd": "100",
                },
                {
                    "order_reference_sha256": SELL_ORDER,
                    "ticker": "AAPL",
                    "side": "SELL",
                    "remaining_quantity": "1.25",
                    "risk_mark_price_usd": "200",
                },
            ],
            "not a float",
        ),
        (
            [
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "MSFT",
                    "side": "BUY",
                    "remaining_quantity": "3",
                    "risk_mark_price_usd": "100",
                }
            ],
            "exactly cover",
        ),
        (
            [
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "MSFT",
                    "side": "BUY",
                    "remaining_quantity": "3",
                    "risk_mark_price_usd": "100",
                },
                {
                    "order_reference_sha256": BUY_ORDER,
                    "ticker": "MSFT",
                    "side": "BUY",
                    "remaining_quantity": "3",
                    "risk_mark_price_usd": "100",
                },
            ],
            "unique order references",
        ),
    ],
)
def test_inexact_incomplete_or_mismatched_order_evidence_is_rejected(
    tmp_path, orders, fragment
):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with pytest.raises(ValueError, match=fragment):
        evidence(quantities, risk, open_orders=orders)
    assert quantities.records() == []


def test_extra_input_field_and_wrong_pinned_hash_are_rejected(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    orders = [
        {
            "order_reference_sha256": BUY_ORDER,
            "ticker": "MSFT",
            "side": "BUY",
            "remaining_quantity": "3",
            "risk_mark_price_usd": "100",
            "extra": True,
        },
        {
            "order_reference_sha256": SELL_ORDER,
            "ticker": "AAPL",
            "side": "SELL",
            "remaining_quantity": "1.25",
            "risk_mark_price_usd": "200",
        },
    ]
    with pytest.raises(ValueError, match="exactly five fields"):
        evidence(quantities, risk, open_orders=orders)
    with pytest.raises(ValueError, match="hash"):
        evidence(quantities, risk, risk_snapshot_record_hash="0" * 64)


def test_concurrent_retry_is_idempotent(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: evidence(quantities, risk), range(2)))
    assert first == second
    assert len(quantities.verify()) == 1


def test_equal_observation_with_different_factorization_is_rejected(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    evidence(quantities, risk)
    changed = [
        {
            "order_reference_sha256": BUY_ORDER,
            "ticker": "MSFT",
            "side": "BUY",
            "remaining_quantity": "6",
            "risk_mark_price_usd": "50",
        },
        {
            "order_reference_sha256": SELL_ORDER,
            "ticker": "AAPL",
            "side": "SELL",
            "remaining_quantity": "2.5",
            "risk_mark_price_usd": "100",
        },
    ]
    with pytest.raises(ValueError, match="move forward"):
        evidence(quantities, risk, open_orders=changed)


def test_multi_record_chain_moves_forward_with_new_risk_snapshot(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    first_risk = make_risk(account, risk_ledger)
    first = evidence(quantities, first_risk)
    second_risk = make_risk(
        account,
        risk_ledger,
        account_observed="2026-01-01T12:02:00+00:00",
        risk_observed="2026-01-01T12:02:30+00:00",
    )
    second = evidence(
        quantities, second_risk, recorded_at="2026-01-01T12:03:00+00:00"
    )
    assert second["previous_hash"] == first["record_hash"]
    assert len(quantities.verify()) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["open_orders"][0].update({"remaining_quantity": "999"}),
        lambda value: value["exact_fractions"]["open_orders"][0].update(
            {"remaining_quantity": {"numerator": "999", "denominator": "1"}}
        ),
        lambda value: value.update({"pending_sell_remaining_notional_usd": "0"}),
        lambda value: value.update({"paper_order_submission_allowed": True}),
        lambda value: value["exact_fractions"].update({"extra": {}}),
    ],
)
def test_rehashed_semantic_or_boundary_tampering_is_detected(tmp_path, mutation):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    evidence(quantities, risk)
    rewrite(quantities.path, mutation)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        quantities.verify()


def test_recorded_time_cannot_predate_observation_or_be_future(tmp_path):
    account, risk_ledger, quantities = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with pytest.raises(ValueError, match="before"):
        evidence(quantities, risk, recorded_at="2026-01-01T12:00:00+00:00")
    with pytest.raises(ValueError, match="future"):
        evidence(quantities, risk, recorded_at="2099-01-01T00:00:00+00:00")
