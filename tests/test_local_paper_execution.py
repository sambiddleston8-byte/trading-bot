import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.broker import (
    LiveTradingPromotionPreflight,
    LocalPaperExecutionLedger,
    PaperOrderProposalLedger,
    PaperSubmissionPreflight,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


def proposal_ledger(tmp_path, **overrides):
    ledger = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    values = {
        "decision_id": "DEC-001",
        "portfolio_version": "PORT-001",
        "ticker": "NVDA",
        "side": "BUY",
        "quantity": 2.0,
        "reference_price": 100.0,
        "target_weight": 0.10,
        "strategy_version": "strategy-v1",
        "model_versions": [{"component": "portfolio", "version": "1.0"}],
        "created_at": "2026-08-12T10:00:00+00:00",
        "git_revision": "abc123",
        "order_id": "PORD-001",
    }
    values.update(overrides)
    ledger.propose(**values)
    return ledger


def execution_ledger(tmp_path, proposal=None):
    return LocalPaperExecutionLedger(
        tmp_path / "simulated_fills.jsonl",
        proposal or proposal_ledger(tmp_path),
    )


def rewrite_with_valid_hash(path, **changes):
    from core.broker import local_paper_execution as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_full_fill_is_local_traceable_and_tamper_evident(tmp_path):
    ledger = execution_ledger(tmp_path)
    record = ledger.simulate_full_fill(
        order_id="PORD-001",
        fill_price=101.0,
        fees=0.25,
        filled_at="2026-08-12T10:01:00+00:00",
    )

    assert record["execution_mode"] == "LOCAL_SIMULATION_ONLY"
    assert record["source"] == "DETERMINISTIC_LOCAL_SIMULATION"
    assert record["status"] == "SIMULATED_FILLED"
    assert record["decision_id"] == "DEC-001"
    assert record["portfolio_version"] == "PORT-001"
    assert record["requested_quantity"] == record["filled_quantity"] == 2.0
    assert record["slippage_bps"] == pytest.approx(100.0)
    assert record["slippage_amount"] == pytest.approx(2.0)
    assert record["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [record]


def test_sell_slippage_uses_positive_for_adverse_convention(tmp_path):
    proposals = proposal_ledger(tmp_path, side="SELL")
    ledger = execution_ledger(tmp_path, proposals)

    adverse = ledger.simulate_full_fill(order_id="PORD-001", fill_price=99.0)
    assert adverse["slippage_bps"] == pytest.approx(100.0)
    assert adverse["slippage_amount"] == pytest.approx(2.0)


def test_fill_requires_verified_existing_proposal(tmp_path):
    ledger = execution_ledger(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        ledger.simulate_full_fill(order_id="PORD-MISSING", fill_price=100.0)


def test_tampered_proposal_blocks_fill_and_verification(tmp_path):
    proposals = proposal_ledger(tmp_path)
    proposal_record = json.loads(proposals.path.read_text())
    proposal_record["quantity"] = 99
    proposals.path.write_text(json.dumps(proposal_record) + "\n")
    ledger = execution_ledger(tmp_path, proposals)

    with pytest.raises(LedgerIntegrityError, match="modified"):
        ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)


def test_simulation_retry_is_idempotent_only_for_identical_content(tmp_path):
    ledger = execution_ledger(tmp_path)
    first = ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.5)
    recovered = ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.5)
    assert recovered == first
    assert len(ledger.verify()) == 1

    with pytest.raises(LedgerIntegrityError, match="already exists"):
        ledger.simulate_full_fill(
            order_id="PORD-001", fill_price=101.0, allow_existing=True
        )


def test_concurrent_identical_retries_create_one_fill(tmp_path):
    ledger = execution_ledger(tmp_path)

    def simulate():
        return ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: simulate(), range(2)))

    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("fill_price", 0),
        ("fill_price", float("nan")),
        ("fees", -1),
        ("fees", float("inf")),
    ],
)
def test_invalid_fill_values_fail_closed(tmp_path, field, value):
    ledger = execution_ledger(tmp_path)
    values = {"order_id": "PORD-001", "fill_price": 100.0, "fees": 0.0}
    values[field] = value
    with pytest.raises(ValueError):
        ledger.simulate_full_fill(**values)


def test_fill_cannot_predate_proposal(tmp_path):
    ledger = execution_ledger(tmp_path)
    with pytest.raises(ValueError, match="earlier"):
        ledger.simulate_full_fill(
            order_id="PORD-001",
            fill_price=100.0,
            filled_at="2026-08-12T09:59:59+00:00",
        )


def test_execution_tampering_is_detected(tmp_path):
    ledger = execution_ledger(tmp_path)
    ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)
    record = json.loads(ledger.path.read_text())
    record["fill_price"] = 1.0
    ledger.path.write_text(json.dumps(record) + "\n")

    with pytest.raises(LedgerIntegrityError, match="modified"):
        ledger.verify()


def test_semantically_inconsistent_rehashed_record_is_rejected(tmp_path):
    ledger = execution_ledger(tmp_path)
    ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)
    rewrite_with_valid_hash(ledger.path, gross_value=1.0)

    with pytest.raises(LedgerIntegrityError, match="Inconsistent"):
        ledger.verify()


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_mode", "PAPER_ONLY"),
        ("status", "FILLED"),
        ("source", "ALPACA"),
    ],
)
def test_rehashed_record_cannot_masquerade_as_real_execution(tmp_path, field, value):
    ledger = execution_ledger(tmp_path)
    ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)
    rewrite_with_valid_hash(ledger.path, **{field: value})

    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("proposal_record_hash", "forged", "does not match"),
        ("decision_id", "DEC-FORGED", "proposal identity"),
        ("reference_price", 1.0, "proposal identity"),
    ],
)
def test_rehashed_record_cannot_break_proposal_linkage(
    tmp_path, field, value, match
):
    ledger = execution_ledger(tmp_path)
    ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)
    rewrite_with_valid_hash(ledger.path, **{field: value})

    with pytest.raises(LedgerIntegrityError, match=match):
        ledger.verify()


def test_implausible_unit_error_price_and_fees_are_rejected(tmp_path):
    ledger = execution_ledger(tmp_path)
    with pytest.raises(ValueError, match="50%"):
        ledger.simulate_full_fill(order_id="PORD-001", fill_price=1_000.0)
    with pytest.raises(ValueError, match="gross value"):
        ledger.simulate_full_fill(
            order_id="PORD-001", fill_price=100.0, fees=1_000.0
        )


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger = execution_ledger(tmp_path)
    first = ledger.simulate_full_fill(order_id="PORD-001", fill_price=100.0)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]


def test_preflight_fails_closed_when_gates_are_missing_or_open():
    missing = PaperSubmissionPreflight.evaluate()
    assert missing["status"] == "BLOCKED"
    assert missing["broker_submission_enabled"] is False
    assert len(missing["open_gates"]) == 7

    partial = PaperSubmissionPreflight.evaluate({"point_in_time_availability": True})
    assert partial["status"] == "BLOCKED"
    assert "point_in_time_availability" not in partial["open_gates"]


def test_even_cleared_preflight_cannot_enable_submission():
    result = PaperSubmissionPreflight.evaluate(
        {key: True for key in PaperSubmissionPreflight.evaluate()["gates"]}
    )
    assert result["status"] == "CLEARED"
    assert result["broker_submission_enabled"] is False
    assert result["next_authority"] == "SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED"


def test_live_promotion_remains_disabled_even_when_evidence_is_complete():
    missing = LiveTradingPromotionPreflight.evaluate()
    assert missing["status"] == "BLOCKED"
    assert len(missing["open_gates"]) == 10
    assert missing["live_submission_enabled"] is False
    assert missing["live_endpoint_supported"] is False

    supplied = {key: True for key in missing["gates"]}
    complete = LiveTradingPromotionPreflight.evaluate(supplied)
    assert complete["status"] == "REVIEW_ELIGIBLE_ONLY"
    assert complete["live_submission_enabled"] is False
    assert complete["live_endpoint_supported"] is False
    assert complete["next_authority"] == (
        "SEPARATE_FUTURE_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
    )
