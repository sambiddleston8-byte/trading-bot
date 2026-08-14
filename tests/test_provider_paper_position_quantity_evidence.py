from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_position_quantity_evidence import (
    ProviderPaperPositionQuantityEvidenceLedger,
)
from core.broker.provider_paper_risk_snapshot import ProviderPaperRiskSnapshotLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
ACCOUNT_PAYLOAD = "b" * 64
POSITIONS_PAYLOAD = "c" * 64
ORDERS_PAYLOAD = "d" * 64
PRIOR_PAYLOAD = "e" * 64


def ledgers(tmp_path):
    account = PaperBrokerAccountSnapshotLedger(tmp_path / "account.jsonl")
    risk = ProviderPaperRiskSnapshotLedger(tmp_path / "risk.jsonl", account)
    quantity = ProviderPaperPositionQuantityEvidenceLedger(
        tmp_path / "quantity.jsonl", risk
    )
    return account, risk, quantity


def make_risk(
    account_ledger,
    risk_ledger,
    *,
    account_observed="2026-01-01T12:00:00+00:00",
    risk_observed="2026-01-01T12:00:30+00:00",
    prior_observed="2025-12-31T21:00:00+00:00",
    cash="700",
    equity="1500",
    positions=None,
    payload=POSITIONS_PAYLOAD,
):
    if positions is None:
        positions = [
            {"ticker": "AAPL", "long_market_value_usd": "500"},
            {"ticker": "MSFT", "long_market_value_usd": "300"},
        ]
    account = account_ledger.record(
        broker="Alpaca",
        account_reference_sha256=ACCOUNT,
        observed_at=account_observed,
        recorded_at=account_observed,
        cash=cash,
        settled_cash=cash,
        unsettled_cash="0",
        buying_power=cash,
        equity=equity,
        source_payload_sha256=ACCOUNT_PAYLOAD,
        paper_account_confirmed=True,
    )
    return risk_ledger.record(
        account_snapshot_id=account["snapshot_id"],
        account_snapshot_record_hash=account["record_hash"],
        observed_at=risk_observed,
        recorded_at=risk_observed,
        previous_close_equity_usd=equity,
        previous_close_observed_at=prior_observed,
        previous_close_equity_source_payload_sha256=PRIOR_PAYLOAD,
        positions=positions,
        open_orders=[],
        positions_source_payload_sha256=payload,
        open_orders_source_payload_sha256=ORDERS_PAYLOAD,
        paper_account_confirmed=True,
    )


def record(quantity_ledger, risk, *, positions=None, **changes):
    if positions is None:
        positions = [
            {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
            {"ticker": "AAPL", "long_quantity": "2.5", "mark_price_usd": "200"},
        ]
    values = {
        "risk_snapshot_id": risk["snapshot_id"],
        "risk_snapshot_record_hash": risk["record_hash"],
        "positions": positions,
        "recorded_at": "2026-01-01T12:01:00+00:00",
    }
    values.update(changes)
    return quantity_ledger.record(**values)


def rewrite(path, mutation):
    from core.broker import provider_paper_position_quantity_evidence as module

    value = json.loads(path.read_text())
    mutation(value)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_exact_quantity_times_price_matches_pinned_market_value(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    result = record(quantity, risk)
    assert result["positions"] == [
        {
            "ticker": "AAPL",
            "long_quantity": "2.5",
            "mark_price_usd": "200",
            "long_market_value_usd": "500",
        },
        {
            "ticker": "MSFT",
            "long_quantity": "3",
            "mark_price_usd": "100",
            "long_market_value_usd": "300",
        },
    ]
    assert result["current_position_gross_exposure_usd"] == "800"
    assert result["positions_source_payload_sha256"] == POSITIONS_PAYLOAD
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


def test_empty_pinned_portfolio_accepts_only_empty_quantity_list(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(
        account, risk_ledger, cash="1000", equity="1000", positions=[]
    )
    result = record(quantity, risk, positions=[])
    assert result["positions"] == []
    assert result["current_position_gross_exposure_usd"] == "0"


@pytest.mark.parametrize(
    "positions,fragment",
    [
        (
            [
                {"ticker": "AAPL", "long_quantity": "2", "mark_price_usd": "200"},
                {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
            ],
            "must equal pinned market value",
        ),
        (
            [{"ticker": "AAPL", "long_quantity": "2.5", "mark_price_usd": "200"}],
            "exactly cover",
        ),
        (
            [
                {"ticker": "AAPL", "long_quantity": "2.5", "mark_price_usd": "200"},
                {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
                {"ticker": "NVDA", "long_quantity": "1", "mark_price_usd": "10"},
            ],
            "exactly cover",
        ),
        (
            [
                {"ticker": "AAPL", "long_quantity": "2.5", "mark_price_usd": "200"},
                {"ticker": "AAPL", "long_quantity": "2.5", "mark_price_usd": "200"},
            ],
            "unique tickers",
        ),
        (
            [
                {"ticker": "AAPL", "long_quantity": 2.5, "mark_price_usd": "200"},
                {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
            ],
            "not a float",
        ),
        (
            [
                {
                    "ticker": "AAPL",
                    "long_quantity": "2.5",
                    "mark_price_usd": "200",
                    "extra": True,
                },
                {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
            ],
            "exactly three fields",
        ),
    ],
)
def test_incomplete_inexact_or_mismatched_positions_are_rejected(
    tmp_path, positions, fragment
):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with pytest.raises(ValueError, match=fragment):
        record(quantity, risk, positions=positions)
    assert quantity.records() == []


def test_wrong_pinned_hash_is_rejected(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with pytest.raises(ValueError, match="hash"):
        record(quantity, risk, risk_snapshot_record_hash="0" * 64)


def test_concurrent_retry_is_idempotent(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: record(quantity, risk), range(2)))
    assert first == second
    assert len(quantity.verify()) == 1


def test_equal_observation_with_different_factorization_is_rejected(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    record(quantity, risk)
    changed = [
        {"ticker": "AAPL", "long_quantity": "5", "mark_price_usd": "100"},
        {"ticker": "MSFT", "long_quantity": "6", "mark_price_usd": "50"},
    ]
    with pytest.raises(ValueError, match="move forward"):
        record(quantity, risk, positions=changed)
    assert len(quantity.verify()) == 1


def test_multi_record_chain_moves_forward_with_new_risk_snapshot(tmp_path):
    account, risk_ledger, quantity = ledgers(tmp_path)
    first_risk = make_risk(account, risk_ledger)
    first = record(quantity, first_risk)
    second_risk = make_risk(
        account,
        risk_ledger,
        account_observed="2026-01-02T12:00:00+00:00",
        risk_observed="2026-01-02T12:00:30+00:00",
        prior_observed="2026-01-01T21:00:00+00:00",
        payload="f" * 64,
    )
    second = record(
        quantity, second_risk, recorded_at="2026-01-02T12:01:00+00:00"
    )
    assert second["previous_hash"] == first["record_hash"]
    assert quantity.verify() == [first, second]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"current_position_gross_exposure_usd": "1"}),
        lambda value: value["positions"][0].update({"long_quantity": "999"}),
        lambda value: value["exact_fractions"]["positions"][0].update(
            {"extra": True}
        ),
        lambda value: value.update({"risk_limits_enforced": True}),
        lambda value: value.update({"paper_order_submission_allowed": True}),
        lambda value: value.update({"unexpected_permission": True}),
    ],
)
def test_rehashed_semantic_or_shape_tampering_is_rejected(tmp_path, mutation):
    account, risk_ledger, quantity = ledgers(tmp_path)
    risk = make_risk(account, risk_ledger)
    record(quantity, risk)
    rewrite(quantity.path, mutation)
    with pytest.raises(LedgerIntegrityError):
        quantity.verify()
