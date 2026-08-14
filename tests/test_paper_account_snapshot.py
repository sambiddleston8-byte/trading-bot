from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
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
        ({"cash": 1000.0}, "exact decimal"),
        ({"cash": True}, "exact decimal"),
        ({"cash": " 1000 "}, "exact decimal"),
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


def test_backward_snapshot_is_rejected_before_append(tmp_path):
    item = ledger(tmp_path)
    first = record(item)
    with pytest.raises(ValueError, match="move forward"):
        record(
            item,
            observed_at="2025-01-01T11:00:00+00:00",
            recorded_at="2025-01-01T12:01:00+00:00",
        )
    assert item.verify() == [first]


def test_exact_decimal_instance_is_supported(tmp_path):
    item = ledger(tmp_path)
    result = record(
        item,
        cash=Decimal("1000"),
        settled_cash=Decimal("900"),
        unsettled_cash=Decimal("100"),
    )
    assert result["cash"] == "1000"


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
        {"unexpected": "secret-or-untrusted-field"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item = ledger(tmp_path)
    record(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_incomplete_or_oversized_snapshot_ledger_is_rejected(tmp_path):
    from core.broker.paper_broker_capture import MAX_LEDGER_BYTES

    incomplete = ledger(tmp_path / "incomplete")
    record(incomplete)
    incomplete.path.write_bytes(incomplete.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        incomplete.records()

    oversized = ledger(tmp_path / "oversized")
    oversized.path.parent.mkdir(mode=0o700)
    with oversized.path.open("wb") as destination:
        destination.truncate(MAX_LEDGER_BYTES + 1)
    oversized.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="safe size limit"):
        oversized.records()


def test_snapshot_ledger_and_lock_reject_links_and_weak_modes(tmp_path):
    for case in ("ledger-symlink", "ledger-hardlink", "ledger-mode", "lock-symlink"):
        case_path = tmp_path / case
        case_path.mkdir(mode=0o700)
        item = ledger(case_path)
        outside = case_path / "outside"
        outside.write_bytes(b"")
        outside.chmod(0o600)
        if case == "ledger-symlink":
            item.path.symlink_to(outside)
        elif case == "ledger-hardlink":
            item.path.hardlink_to(outside)
        elif case == "ledger-mode":
            item.path.write_bytes(b"")
            item.path.chmod(0o644)
        else:
            item.path.with_suffix(".jsonl.lock").symlink_to(outside)
        with pytest.raises(LedgerIntegrityError):
            record(item)
