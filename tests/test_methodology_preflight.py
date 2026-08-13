from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import EvidenceBackedPaperSubmissionPreflightLedger
from core.broker.methodology_preflight import METHODOLOGY_GATE_LABELS
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


def gates(*, failed=()):
    return {
        key: {
            "status": "FAILED" if key in failed else "PASSED",
            "evidence_summary": f"Test evidence for {key}",
            "observed_at": "2026-08-12T09:00:00+00:00",
            "source_uri": f"https://evidence.example/{key}",
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.gates.{key}",
        }
        for index, key in enumerate(METHODOLOGY_GATE_LABELS, start=1)
    }


def ledger(tmp_path):
    return EvidenceBackedPaperSubmissionPreflightLedger(
        tmp_path / "methodology_preflight.jsonl"
    )


def assess(book, **overrides):
    values = {
        "strategy_version": "strategy-v1",
        "git_revision": "abc123",
        "assessed_by": "INDEPENDENT_REVIEWER",
        "assessed_at": "2026-08-12T10:00:00+00:00",
        "gates": gates(),
    }
    values.update(overrides)
    return book.assess(**values)


def rewrite(path, **changes):
    from core.broker import methodology_preflight as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_complete_evidence_still_cannot_authorize_or_connect_broker(tmp_path):
    book = ledger(tmp_path)
    record = assess(book)

    assert record["status"] == "EVIDENCE_COMPLETE_AWAITING_HUMAN_DECISION"
    assert record["all_evidence_passed"] is True
    assert record["open_gates"] == []
    assert record["broker_submission_enabled"] is False
    assert record["paper_account_connected"] is False
    assert record["credentials_permitted"] is False
    assert record["network_request_enabled"] is False
    assert record["order_submission_enabled"] is False
    assert record["live_trading_enabled"] is False
    assert record["next_authority"] == (
        "SEPARATE_EXPLICIT_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
    )
    assert record["previous_hash"] == GENESIS_HASH
    assert book.verify() == [record]
    assert book.latest(strategy_version="strategy-v1") == record


def test_failed_evidence_blocks_and_names_exact_open_gates(tmp_path):
    book = ledger(tmp_path)
    failed = {"historical_universe_membership", "active_pipeline_out_of_sample"}
    record = assess(book, gates=gates(failed=failed))

    assert record["status"] == "BLOCKED"
    assert set(record["open_gates"]) == failed
    assert record["all_evidence_passed"] is False
    assert record["next_authority"] == "RESOLVE_METHODOLOGY_GATES"


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda value: value.pop("point_in_time_availability"), "exactly"),
        (
            lambda value: value["point_in_time_availability"].update(status=True),
            "PASSED or FAILED",
        ),
        (
            lambda value: value["point_in_time_availability"].update(
                source_input_sha256="bad"
            ),
            "SHA-256",
        ),
        (
            lambda value: value["point_in_time_availability"].update(
                source_uri="http://evidence.example/a"
            ),
            "HTTPS",
        ),
        (
            lambda value: value["point_in_time_availability"].update(
                observed_at="2026-08-12T10:01:00+00:00"
            ),
            "postdate",
        ),
        (
            lambda value: value["forecast_confidence_accuracy"].update(
                source_input_sha256=value["point_in_time_availability"][
                    "source_input_sha256"
                ],
                source_locator=value["point_in_time_availability"]["source_locator"],
            ),
            "distinct evidence location",
        ),
    ],
)
def test_boolean_missing_invalid_or_reused_evidence_cannot_clear_gate(
    tmp_path, mutate, fragment
):
    book = ledger(tmp_path)
    values = gates()
    mutate(values)
    with pytest.raises(ValueError, match=fragment):
        assess(book, gates=values)
    assert book.records() == []


def test_one_source_may_support_distinct_explicit_locations(tmp_path):
    book = ledger(tmp_path)
    values = gates()
    values["forecast_confidence_accuracy"]["source_input_sha256"] = values[
        "point_in_time_availability"
    ]["source_input_sha256"]
    record = assess(book, gates=values)
    assert record["gates"]["forecast_confidence_accuracy"]["source_locator"] != record[
        "gates"
    ]["point_in_time_availability"]["source_locator"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "CLEARED"),
        ("all_evidence_passed", False),
        ("broker_submission_enabled", True),
        ("paper_account_connected", True),
        ("credentials_permitted", True),
        ("network_request_enabled", True),
        ("order_submission_enabled", True),
        ("live_trading_enabled", True),
    ],
)
def test_rehashed_tampering_cannot_weaken_boundary(tmp_path, field, value):
    book = ledger(tmp_path)
    assess(book)
    rewrite(book.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        book.verify()


def test_reassessment_must_explicitly_supersede_current_head(tmp_path):
    book = ledger(tmp_path)
    first = assess(book, gates=gates(failed={"point_in_time_availability"}))
    with pytest.raises(LedgerIntegrityError, match="supersede"):
        assess(
            book,
            assessed_at="2026-08-12T11:00:00+00:00",
            git_revision="def456",
        )
    second = assess(
        book,
        assessed_at="2026-08-12T11:00:00+00:00",
        git_revision="def456",
        supersedes_assessment_id=first["assessment_id"],
    )
    assert len(book.verify()) == 2
    assert book.latest(strategy_version="strategy-v1") == second


def test_supersession_cannot_cross_strategy_or_move_backwards(tmp_path):
    book = ledger(tmp_path)
    first = assess(book)
    with pytest.raises(LedgerIntegrityError, match="First strategy"):
        assess(
            book,
            strategy_version="strategy-v2",
            assessed_at="2026-08-12T11:00:00+00:00",
            supersedes_assessment_id=first["assessment_id"],
        )
    with pytest.raises((ValueError, LedgerIntegrityError)):
        assess(
            book,
            assessed_at="2026-08-12T09:30:00+00:00",
            supersedes_assessment_id=first["assessment_id"],
        )


def test_concurrent_identical_assessment_is_idempotent(tmp_path):
    book = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: assess(book), range(2)))
    assert records[0]["assessment_id"] == records[1]["assessment_id"]
    assert len(book.verify()) == 1
