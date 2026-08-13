from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import PaperBrokerAccountSnapshotLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
PAYLOAD = "b" * 64


def ledger(tmp_path):
    return PaperBrokerAccountSnapshotLedger(tmp_path / "paper-account.jsonl")


def record(item, **overrides):
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
        "source_payload_sha256": PAYLOAD,
        "paper_account_confirmed": True,
    }
    values.update(overrides)
    return item.record(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.broker import paper_account_snapshot as module

    value = json.loads(path.read_text())
    value.update(changes)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_records_only_paper_cash_evidence_without_relabelling_performance(tmp_path):
    item = ledger(tmp_path)
    result = record(item)
    assert result["previous_hash"] == GENESIS_HASH
    assert result["broker_environment"] == "PAPER"
    assert result["cash"] == "1000"
    assert result["settled_cash"] == "900"
    assert result["unsettled_cash"] == "100"
    assert result["source_payload_stored"] is False
    assert result["gross_pre_tax_performance_relabelled"] is False
    assert result["tax_liability_estimated"] is False
    assert result["broker_reconciliation_complete"] is False
    assert result["order_submitted"] is False
    assert result["live_trading_enabled"] is False
    assert item.verify() == [result]


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"paper_account_confirmed": False}, "paper account"),
        ({"cash": "999"}, "must equal"),
        ({"equity": "999"}, "equity cannot"),
        ({"settled_cash": "-1", "cash": "99"}, "non-negative"),
        ({"account_reference_sha256": "secret-account-number"}, "SHA-256"),
        ({"source_payload_sha256": "not-a-hash"}, "SHA-256"),
        ({"recorded_at": "2025-01-01T11:59:00+00:00"}, "reverse ordered"),
        ({"recorded_at": "2099-01-01T00:00:00+00:00"}, "future-dated"),
    ],
)
def test_invalid_or_non_paper_snapshot_is_rejected(tmp_path, overrides, fragment):
    item = ledger(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        record(item, **overrides)
    assert item.records() == []


def test_snapshots_for_one_account_must_move_forward(tmp_path):
    item = ledger(tmp_path)
    record(item)
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        record(item, cash="1100", settled_cash="1000")
    second = record(
        item,
        observed_at="2025-01-02T12:00:00+00:00",
        recorded_at="2025-01-02T12:01:00+00:00",
    )
    assert item.verify()[-1] == second


def test_identical_concurrent_retry_appends_once(tmp_path):
    item = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: record(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"broker_environment": "LIVE"},
        {"cash": "999"},
        {"source_payload_stored": True},
        {"paper_account_confirmed": False},
        {"gross_pre_tax_performance_relabelled": True},
        {"tax_liability_estimated": True},
        {"broker_reconciliation_complete": True},
        {"order_submitted": True},
        {"performance_metric_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item = ledger(tmp_path)
    record(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()
