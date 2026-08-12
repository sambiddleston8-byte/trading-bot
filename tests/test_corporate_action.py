from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import CorporateActionLedger


SOURCE_HASH = "a" * 64


def ledger_and_fill(tmp_path):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="NVDA",
        side="BUY",
        quantity=2,
        reference_price=100,
        target_weight=0.1,
        strategy_version="strategy-v1",
        model_versions=[{"component": "portfolio", "version": "1.0"}],
        created_at="2025-01-02T15:00:00+00:00",
        git_revision="abc123",
        order_id="PORD-001",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill = executions.simulate_full_fill(
        order_id="PORD-001",
        fill_price=101,
        fees=0.2,
        filled_at="2025-01-02T15:01:00+00:00",
    )
    return CorporateActionLedger(tmp_path / "actions.jsonl", executions), fill


def record(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "covers_from_at": "2025-01-02T15:01:00+00:00",
        "through_at": "2025-02-03T16:00:00+00:00",
        "retrieved_at": "2025-02-03T17:00:00+00:00",
        "data_source": "TEST_CORPORATE_ACTION_FIXTURE",
        "source_version": "fixture-v1",
        "source_input_sha256": SOURCE_HASH,
    }
    values.update(overrides)
    return ledger.record(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import corporate_action as module

    item = json.loads(path.read_text())
    item.update(changes)
    material = {key: value for key, value in item.items() if key != "record_hash"}
    item["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(item) + "\n")


def test_records_verified_no_event_coverage_without_performance_claim(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    item = record(ledger, fill)

    assert item["status"] == "NO_EVENTS"
    assert item["performance_claim"] is False
    assert item["total_return_calculated"] is False
    assert item["learning_eligible"] is False
    assert item["execution_record_hash"] == fill["record_hash"]
    assert item["previous_hash"] == GENESIS_HASH
    assert len(item["source_evidence_sha256"]) == 64
    assert ledger.verify() == [item]
    check = ledger.no_event_check(
        fill_id=fill["fill_id"], through_at="2025-02-03T16:00:00+00:00"
    )
    assert check["status"] == "NO_EVENTS"
    assert check["evidence_id"] == item["evidence_id"]
    assert check["evidence_record_hash"] == item["record_hash"]


def test_preserves_and_sorts_exact_dividend_and_split_terms(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    item = record(
        ledger,
        fill,
        events=[
            {
                "event_type": "STOCK_SPLIT",
                "source_event_id": "split-1",
                "effective_at": "2025-02-01T00:00:00+00:00",
                "numerator": 10,
                "denominator": 1,
            },
            {
                "event_type": "CASH_DIVIDEND",
                "source_event_id": "dividend-1",
                "ex_at": "2025-01-15T00:00:00+00:00",
                "payment_at": "2025-01-31T00:00:00+00:00",
                "amount_per_share": "0.0100",
                "currency": "usd",
            },
        ],
    )

    assert item["status"] == "SUPPORTED_EVENTS_PRESENT"
    assert [event["event_type"] for event in item["events"]] == [
        "CASH_DIVIDEND",
        "STOCK_SPLIT",
    ]
    assert item["events"][0]["amount_per_share"] == "0.01"
    assert item["events"][0]["currency"] == "USD"
    assert item["events"][1]["numerator"] == "10"
    assert item["events"][1]["denominator"] == "1"
    assert ledger.evidence_for(
        fill_id=fill["fill_id"], through_at="2025-02-01T00:00:00+00:00"
    ) == item
    with pytest.raises(ValueError, match="contains"):
        ledger.no_event_check(
            fill_id=fill["fill_id"], through_at="2025-02-01T00:00:00+00:00"
        )


def test_uncertain_and_unsupported_evidence_are_recorded_but_fail_closed(tmp_path):
    uncertain_ledger, fill = ledger_and_fill(tmp_path / "uncertain")
    uncertain = record(
        uncertain_ledger,
        fill,
        completeness_status="UNCERTAIN",
        uncertainty_reasons=["Provider response was incomplete"],
    )
    assert uncertain["status"] == "UNCERTAIN"
    with pytest.raises(ValueError, match="may contain"):
        uncertain_ledger.no_event_check(
            fill_id=fill["fill_id"], through_at="2025-02-01T00:00:00+00:00"
        )

    other_ledger, other_fill = ledger_and_fill(tmp_path / "other")
    unsupported = record(
        other_ledger,
        other_fill,
        events=[
            {
                "event_type": "OTHER",
                "source_event_id": "spinoff-1",
                "effective_at": "2025-01-20T00:00:00+00:00",
                "description": "Spin-off requiring a separate valuation policy",
            }
        ],
    )
    assert unsupported["status"] == "UNSUPPORTED_EVENTS_PRESENT"


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"covers_from_at": "2025-01-03T00:00:00+00:00"}, "start"),
        ({"through_at": "2025-01-01T00:00:00+00:00"}, "extend"),
        ({"retrieved_at": "2025-02-01T00:00:00+00:00"}, "predate"),
        ({"source_input_sha256": "not-a-hash"}, "SHA-256"),
        ({"completeness_status": "UNCERTAIN"}, "requires reasons"),
        (
            {
                "completeness_status": "COMPLETE",
                "uncertainty_reasons": ["should not be here"],
            },
            "forbids",
        ),
    ],
)
def test_invalid_coverage_or_provenance_fails_closed(
    tmp_path, overrides, fragment
):
    ledger, fill = ledger_and_fill(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        record(ledger, fill, **overrides)


@pytest.mark.parametrize(
    "event,fragment",
    [
        (
            {
                "event_type": "CASH_DIVIDEND",
                "source_event_id": "div-1",
                "ex_at": "2025-03-01T00:00:00+00:00",
                "amount_per_share": 1,
                "currency": "USD",
            },
            "outside",
        ),
        (
            {
                "event_type": "CASH_DIVIDEND",
                "source_event_id": "div-1",
                "ex_at": "2025-01-15T00:00:00+00:00",
                "payment_at": "2025-01-14T00:00:00+00:00",
                "amount_per_share": 1,
                "currency": "USD",
            },
            "predate",
        ),
        (
            {
                "event_type": "STOCK_SPLIT",
                "source_event_id": "split-1",
                "effective_at": "2025-02-01T00:00:00+00:00",
                "numerator": 0,
                "denominator": 1,
            },
            "positive",
        ),
    ],
)
def test_invalid_event_evidence_fails_closed(tmp_path, event, fragment):
    ledger, fill = ledger_and_fill(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        record(ledger, fill, events=[event])


def test_requires_verified_fill(tmp_path):
    ledger, _ = ledger_and_fill(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        ledger.record(
            fill_id="SFILL-MISSING",
            covers_from_at="2025-01-02T15:01:00+00:00",
            through_at="2025-02-03T16:00:00+00:00",
            retrieved_at="2025-02-03T17:00:00+00:00",
            data_source="TEST",
            source_version="v1",
            source_input_sha256=SOURCE_HASH,
        )


def test_identical_concurrent_retries_create_one_record(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: record(ledger, fill), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


def test_conflicting_evidence_for_same_coverage_fails_closed(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    record(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        record(ledger, fill, source_version="fixture-v2")


def test_complete_overlapping_coverage_must_agree_on_events(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    record(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        record(
            ledger,
            fill,
            through_at="2025-03-03T16:00:00+00:00",
            retrieved_at="2025-03-03T17:00:00+00:00",
            source_input_sha256="b" * 64,
            events=[
                {
                    "event_type": "CASH_DIVIDEND",
                    "source_event_id": "div-1",
                    "ex_at": "2025-01-15T00:00:00+00:00",
                    "amount_per_share": 1,
                    "currency": "USD",
                }
            ],
        )


def test_consistent_extended_coverage_is_allowed(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    dividend = {
        "event_type": "CASH_DIVIDEND",
        "source_event_id": "provider-div-1",
        "ex_at": "2025-01-15T00:00:00+00:00",
        "amount_per_share": 1,
        "currency": "USD",
    }
    first = record(ledger, fill, events=[dividend])
    second = record(
        ledger,
        fill,
        through_at="2025-03-03T16:00:00+00:00",
        retrieved_at="2025-03-03T17:00:00+00:00",
        source_input_sha256="b" * 64,
        events=[
            dividend,
            {
                "event_type": "STOCK_SPLIT",
                "source_event_id": "provider-split-1",
                "effective_at": "2025-02-15T00:00:00+00:00",
                "numerator": 2,
                "denominator": 1,
            },
        ],
    )
    assert ledger.verify() == [first, second]


@pytest.mark.parametrize(
    "field,value",
    [
        ("performance_claim", True),
        ("status", "NO_EVENTS"),
        ("ticker", "FORGED"),
        ("source_input_sha256", "b" * 64),
        ("events", []),
        ("learning_eligible", True),
    ],
)
def test_rehashed_record_cannot_change_boundary_or_evidence(
    tmp_path, field, value
):
    ledger, fill = ledger_and_fill(tmp_path)
    record(
        ledger,
        fill,
        events=[
            {
                "event_type": "CASH_DIVIDEND",
                "source_event_id": "div-1",
                "ex_at": "2025-01-15T00:00:00+00:00",
                "amount_per_share": 1,
                "currency": "USD",
            }
        ],
    )
    rewrite_with_valid_hash(ledger.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_evidence_lookup_requires_full_coverage(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    record(ledger, fill)
    assert (
        ledger.evidence_for(
            fill_id=fill["fill_id"], through_at="2025-03-01T00:00:00+00:00"
        )
        is None
    )


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger, fill = ledger_and_fill(tmp_path)
    first = record(ledger, fill)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]
