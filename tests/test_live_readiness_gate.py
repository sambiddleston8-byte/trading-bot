from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.broker import LiveReadinessEvidenceGateLedger
from core.broker.live_readiness_gate import AUTHORITY_FIELDS
from core.broker.local_paper_execution import LIVE_PROMOTION_GATE_LABELS
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


def gate_evidence(*, failed=(), observed_at=None):
    observed = observed_at or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {
        key: {
            "status": "FAILED" if key in failed else "PASSED",
            "observed_at": observed,
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.live_readiness.{key}",
            "source_uri": f"https://evidence.example/{key}",
            "evidence_summary": f"Independent evidence for {key}",
            "independent_reviewer_identity": f"Reviewer {index}",
        }
        for index, key in enumerate(LIVE_PROMOTION_GATE_LABELS, start=1)
    }


def ledger(tmp_path):
    return LiveReadinessEvidenceGateLedger(tmp_path / "live_readiness.jsonl")


def assess(book, **overrides):
    now = datetime.now(timezone.utc)
    values = {
        "strategy_version": "strategy-v1",
        "git_revision": "a" * 40,
        "evidence_window_start": (now - timedelta(days=365)).isoformat(),
        "evidence_window_end": (now - timedelta(days=1)).isoformat(),
        "gates": gate_evidence(),
        "builder_identity": "Codex",
        "strategy_owner_identity": "Sam",
        "assessed_by": "Independent Assessor",
        "assessed_at": now.isoformat(),
    }
    values.update(overrides)
    return book.assess(**values)


def rewrite(path, **changes):
    from core.broker import live_readiness_gate as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_complete_evidence_permits_human_review_only(tmp_path):
    book = ledger(tmp_path)
    record = assess(book)
    assert record["status"] == "HUMAN_REVIEW_ELIGIBLE_ONLY"
    assert record["all_evidence_passed"] is True
    assert record["open_gates"] == []
    assert all(record[field] is False for field in AUTHORITY_FIELDS)
    assert record["next_authority"] == (
        "SEPARATE_EXPLICIT_HUMAN_DECISION_AND_IMPLEMENTATION_REQUIRED"
    )
    assert record["previous_hash"] == GENESIS_HASH
    assert book.verify() == [record]


def test_failed_evidence_blocks_and_names_exact_gates(tmp_path):
    failed = {"broker_state_reconciliation", "operational_resilience"}
    record = assess(ledger(tmp_path), gates=gate_evidence(failed=failed))
    assert record["status"] == "BLOCKED"
    assert set(record["open_gates"]) == failed
    assert record["all_evidence_passed"] is False
    assert record["next_authority"] == "RESOLVE_LIVE_READINESS_GATES"


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda gates, now: gates.pop("operational_resilience"), "exactly"),
        (lambda gates, now: gates["operational_resilience"].update(status=True), "PASSED or FAILED"),
        (lambda gates, now: gates["operational_resilience"].update(source_input_sha256="bad"), "SHA-256"),
        (lambda gates, now: gates["operational_resilience"].update(source_uri="http://unsafe.example"), "HTTPS"),
        (lambda gates, now: gates["operational_resilience"].update(observed_at=(now + timedelta(minutes=1)).isoformat()), "postdate"),
        (lambda gates, now: gates["operational_resilience"].update(evidence_summary=""), "required"),
        (lambda gates, now: gates["operational_resilience"].update(source_locator=""), "required"),
        (lambda gates, now: gates["operational_resilience"].update(
            source_input_sha256=gates["broker_state_reconciliation"]["source_input_sha256"],
            source_locator=gates["broker_state_reconciliation"]["source_locator"],
        ), "distinct"),
    ],
)
def test_invalid_gate_evidence_fails_before_append(tmp_path, mutation, fragment):
    book = ledger(tmp_path)
    now = datetime.now(timezone.utc)
    gates = gate_evidence()
    mutation(gates, now)
    with pytest.raises(ValueError, match=fragment):
        assess(book, gates=gates, assessed_at=now.isoformat())
    assert book.records() == []


def test_invalid_revision_or_window_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="full hexadecimal"):
        assess(ledger(tmp_path / "revision"), git_revision="abc")
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="later"):
        assess(
            ledger(tmp_path / "order"),
            evidence_window_start=now.isoformat(),
            evidence_window_end=(now - timedelta(days=1)).isoformat(),
        )
    with pytest.raises(ValueError, match="postdate"):
        assess(
            ledger(tmp_path / "future"),
            evidence_window_end=(now + timedelta(days=1)).isoformat(),
        )


@pytest.mark.parametrize("identity", ["codex", "SAM"])
def test_gate_reviewer_must_differ_from_builder_and_owner(tmp_path, identity):
    gates = gate_evidence()
    gates["operational_resilience"]["independent_reviewer_identity"] = identity
    with pytest.raises(ValueError, match="reviewer must differ"):
        assess(ledger(tmp_path), gates=gates)


def test_gate_reviewer_must_differ_from_assessor(tmp_path):
    gates = gate_evidence()
    gates["operational_resilience"]["independent_reviewer_identity"] = (
        "Independent Assessor"
    )
    with pytest.raises(ValueError, match="reviewer must differ"):
        assess(ledger(tmp_path), gates=gates)


@pytest.mark.parametrize("identity", ["codex", "SAM"])
def test_assessor_must_differ_from_builder_and_owner(tmp_path, identity):
    with pytest.raises(ValueError, match="assessor must differ"):
        assess(ledger(tmp_path), assessed_by=identity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "LIVE_READY"),
        ("all_evidence_passed", False),
        ("credentials_permitted", True),
        ("network_request_enabled", True),
        ("broker_connection_allowed", True),
        ("order_submission_enabled", True),
        ("live_trading_enabled", True),
        ("deployment_performed", True),
        ("automatic_promotion_enabled", True),
        ("builder_identity", "Independent Assessor"),
    ],
)
def test_rehashed_tampering_cannot_grant_authority_or_weaken_identity(tmp_path, field, value):
    book = ledger(tmp_path)
    assess(book)
    rewrite(book.path, **{field: value})
    with pytest.raises(LedgerIntegrityError):
        book.verify()


def test_forward_only_supersession_and_latest_head(tmp_path):
    book = ledger(tmp_path)
    first = assess(book, gates=gate_evidence(failed={"operational_resilience"}))
    with pytest.raises(LedgerIntegrityError, match="supersede"):
        assess(book, git_revision="b" * 40)
    second = assess(
        book,
        git_revision="b" * 40,
        assessed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        supersedes_assessment_id=first["assessment_id"],
    )
    assert book.latest(strategy_version="strategy-v1") == second
    with pytest.raises(LedgerIntegrityError, match="First strategy"):
        assess(
            book,
            strategy_version="strategy-v2",
            git_revision="c" * 40,
            assessed_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            supersedes_assessment_id=second["assessment_id"],
        )


def test_verify_rejects_rehashed_forked_history(tmp_path):
    from core.broker import live_readiness_gate as module

    book = ledger(tmp_path)
    first = assess(book, gates=gate_evidence(failed={"operational_resilience"}))
    second_time = datetime.now(timezone.utc) + timedelta(seconds=1)
    second = assess(
        book,
        git_revision="b" * 40,
        assessed_at=second_time.isoformat(),
        supersedes_assessment_id=first["assessment_id"],
    )
    fork_time = second_time + timedelta(seconds=1)
    fork = {
        **second,
        "git_revision": "c" * 40,
        "assessed_at": fork_time.isoformat(),
        "supersedes_assessment_id": first["assessment_id"],
        "previous_hash": second["record_hash"],
    }
    fork["assessment_id"] = module._assessment_id(
        fork["strategy_version"],
        fork["git_revision"],
        fork["evidence_window_start"],
        fork["evidence_window_end"],
        fork["assessed_at"],
        fork["gates"],
    )
    material = {key: value for key, value in fork.items() if key != "record_hash"}
    fork["record_hash"] = module._record_hash(material)
    with book.path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(fork) + "\n")
    with pytest.raises(LedgerIntegrityError, match="competing heads"):
        book.verify()


def test_concurrent_identical_assessment_is_idempotent(tmp_path):
    book = ledger(tmp_path)
    moment = datetime.now(timezone.utc)
    now = moment.isoformat()
    gates = gate_evidence(observed_at=(moment - timedelta(days=1)).isoformat())
    window_start = (moment - timedelta(days=365)).isoformat()
    window_end = (moment - timedelta(days=1)).isoformat()
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(
            lambda _: assess(
                book,
                assessed_at=now,
                gates=gates,
                evidence_window_start=window_start,
                evidence_window_end=window_end,
            ),
            range(2),
        ))
    assert records[0]["assessment_id"] == records[1]["assessment_id"]
    assert len(book.verify()) == 1


def test_incomplete_or_invalid_lines_fail_closed(tmp_path):
    book = ledger(tmp_path)
    assess(book)
    book.path.write_bytes(book.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete"):
        book.verify()
