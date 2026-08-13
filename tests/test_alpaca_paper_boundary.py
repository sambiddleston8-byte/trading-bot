import json
import os

import pytest

from core.broker import AlpacaPaperConfiguration, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


def _proposal(ledger, **overrides):
    values = {
        "decision_id": "DEC-001",
        "portfolio_version": "PORT-001",
        "ticker": "NVDA",
        "side": "BUY",
        "quantity": 2.5,
        "reference_price": 100.0,
        "target_weight": 0.10,
        "strategy_version": "paper-proposal-v1",
        "model_versions": [{"component": "portfolio", "version": "1.0"}],
        "created_at": "2026-08-12T10:00:00+00:00",
        "git_revision": "abc123",
        "order_id": "PORD-001",
    }
    values.update(overrides)
    return ledger.propose(**values)


def test_proposal_is_complete_paper_only_and_tamper_evident(tmp_path):
    ledger = PaperOrderProposalLedger(tmp_path / "paper_orders.jsonl")
    record = _proposal(ledger)

    assert record["execution_mode"] == "PAPER_ONLY"
    assert record["status"] == "PROPOSED"
    assert record["order_type"] == "MARKET"
    assert record["time_in_force"] == "DAY"
    assert record["previous_hash"] == GENESIS_HASH
    assert record["client_order_id"].startswith("SP-")
    assert ledger.verify() == [record]


def test_same_order_can_only_be_recovered_with_identical_content(tmp_path):
    ledger = PaperOrderProposalLedger(tmp_path / "paper_orders.jsonl")
    first = _proposal(ledger)
    recovered = _proposal(ledger, allow_existing=True)
    assert recovered == first

    with pytest.raises(LedgerIntegrityError, match="already exists"):
        _proposal(ledger, quantity=3, allow_existing=True)


def test_default_order_and_client_ids_are_stable_across_retry(tmp_path):
    ledger = PaperOrderProposalLedger(tmp_path / "paper_orders.jsonl")
    first = _proposal(ledger, order_id=None, created_at=None)
    recovered = _proposal(ledger, order_id=None, created_at=None, allow_existing=True)

    assert recovered == first
    assert len(ledger.verify()) == 1
    assert first["order_id"].startswith("PORD-")


def test_tampering_is_detected_before_another_proposal(tmp_path):
    path = tmp_path / "paper_orders.jsonl"
    ledger = PaperOrderProposalLedger(path)
    _proposal(ledger)
    record = json.loads(path.read_text())
    record["quantity"] = 999
    path.write_text(json.dumps(record) + "\n")

    with pytest.raises(LedgerIntegrityError, match="modified"):
        ledger.verify()
    with pytest.raises(LedgerIntegrityError):
        _proposal(ledger, order_id="PORD-002")


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "0.1"),
        ("execution_mode", "LIVE"),
        ("status", "SUBMITTED"),
        ("order_type", "LIMIT"),
        ("time_in_force", "GTC"),
        ("ticker", "../NVDA"),
        ("order_id", "unsafe"),
        ("model_versions", ["not-an-object"]),
    ],
)
def test_rehashed_tampering_cannot_weaken_paper_only_boundary(tmp_path, field, value):
    from core.broker import alpaca_paper as module

    path = tmp_path / "paper_orders.jsonl"
    ledger = PaperOrderProposalLedger(path)
    record = _proposal(ledger)
    record[field] = value
    material = {key: item for key, item in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")

    with pytest.raises(LedgerIntegrityError, match="paper-only boundary"):
        ledger.verify()


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    path = tmp_path / "paper_orders.jsonl"
    ledger = PaperOrderProposalLedger(path)
    first = _proposal(ledger)
    with path.open("ab") as target:
        target.write(b'{"partial":')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()

    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]


def test_complete_record_without_newline_requires_and_accepts_explicit_repair(tmp_path):
    path = tmp_path / "paper_orders.jsonl"
    ledger = PaperOrderProposalLedger(path)
    first = _proposal(ledger)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    assert ledger.repair_incomplete_tail() is None
    assert path.read_bytes().endswith(b"\n")
    assert ledger.verify() == [first]


def test_short_disk_writes_are_completed(tmp_path, monkeypatch):
    from core.broker import alpaca_paper as module

    original_write = os.write

    def short_write(descriptor, payload):
        return original_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(module.os, "write", short_write)
    ledger = PaperOrderProposalLedger(tmp_path / "paper_orders.jsonl")
    record = _proposal(ledger)
    assert ledger.verify() == [record]


def test_git_revision_is_resolved_from_project_not_process_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record = _proposal(
        PaperOrderProposalLedger(tmp_path / "paper_orders.jsonl"),
        git_revision=None,
    )
    assert record["git_revision"] != "UNKNOWN"


@pytest.mark.parametrize("mode", ["RECORD_ONLY", "LIVE", "REAL_MONEY", ""])
def test_non_paper_execution_modes_are_rejected(tmp_path, mode):
    with pytest.raises(ValueError, match="PAPER_ONLY"):
        _proposal(PaperOrderProposalLedger(tmp_path / "orders.jsonl"), execution_mode=mode)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://example.com",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets/v2/orders",
    ],
)
def test_live_or_arbitrary_endpoints_are_rejected(endpoint):
    with pytest.raises(ValueError, match="official paper"):
        AlpacaPaperConfiguration(endpoint)


def test_environment_ignores_generic_live_endpoint_variable(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.delenv("ALPACA_PAPER_API_BASE_URL", raising=False)
    assert AlpacaPaperConfiguration.from_environment().endpoint == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", 0),
        ("quantity", float("nan")),
        ("reference_price", -1),
        ("target_weight", 1.1),
        ("side", "HOLD"),
        ("ticker", "../NVDA"),
    ],
)
def test_invalid_order_values_are_rejected(tmp_path, field, value):
    with pytest.raises(ValueError):
        _proposal(PaperOrderProposalLedger(tmp_path / "orders.jsonl"), **{field: value})


@pytest.mark.parametrize("order_id", ["unsafe", "PORD-../BAD", "PORD-", "PORD-lower"])
def test_unsafe_order_ids_are_rejected(tmp_path, order_id):
    with pytest.raises(ValueError, match="safe identifier"):
        _proposal(PaperOrderProposalLedger(tmp_path / "orders.jsonl"), order_id=order_id)


@pytest.mark.parametrize(
    "versions",
    [[], [{"component": "", "version": "1"}], [{"component": "portfolio", "version": ""}]],
)
def test_missing_model_identity_is_rejected(tmp_path, versions):
    with pytest.raises(ValueError, match="model_versions"):
        _proposal(PaperOrderProposalLedger(tmp_path / "orders.jsonl"), model_versions=versions)
