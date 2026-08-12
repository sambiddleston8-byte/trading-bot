from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import SandboxLessonProposalLedger


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


def outcome(identifier="FHRT-1", paired_at="2025-02-03T17:03:00+00:00"):
    return {
        "result_id": identifier,
        "record_hash": f"hash-{identifier}",
        "paired_at": paired_at,
        "complete_round_trip": True,
    }


BASE = {
    "proposer_type": "CODEX",
    "proposer_id": "codex-sandbox-v1",
    "supporting_outcome_ids": ["FHRT-1"],
    "observation_summary": "The fixed-horizon return missed the preregistered expectation.",
    "suspected_cause": "The valuation assumption may have been too optimistic.",
    "uncertainty_statement": "One outcome cannot establish causality.",
    "falsifiable_hypothesis": "Reducing the growth assumption would improve out-of-sample error.",
    "proposed_experiment": "Run a preregistered walk-forward comparison using the lower assumption.",
    "expected_disconfirming_result": "The lower assumption fails to reduce out-of-sample error.",
    "proposed_at": "2025-02-03T17:04:00+00:00",
}


def propose(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.propose(**values)


def rewrite(path, **changes):
    from core.orchestration import lesson_proposal as module
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def ledgers(tmp_path):
    outcomes = Stub([outcome()])
    ledger = SandboxLessonProposalLedger(tmp_path / "lessons.jsonl", outcomes)
    return outcomes, ledger


def test_records_evidence_backed_nonoperative_lesson(tmp_path):
    _, ledger = ledgers(tmp_path)
    result = propose(ledger)
    assert result["status"] == "SANDBOX_PROPOSAL_UNAPPROVED"
    assert result["supporting_outcome_ids"] == ["FHRT-1"]
    assert result["supporting_outcome_hashes"] == ["hash-FHRT-1"]
    assert result["cause_established"] is False
    for field in (
        "human_approval_recorded", "experiment_executed", "lesson_validated",
        "production_rule_changed", "model_weight_changed", "code_changed",
        "permission_changed", "promotion_performed", "deployment_performed",
        "order_submitted", "learning_applied", "live_trading_enabled",
    ):
        assert result[field] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize("proposer", ["HUMAN", "CODEX", "CLAUDE_CODE"])
def test_approved_proposer_types_are_traceable(tmp_path, proposer):
    _, ledger = ledgers(tmp_path)
    assert propose(ledger, proposer_type=proposer)["proposer_type"] == proposer


def test_hermes_provenance_is_blocked_while_hermes_is_inactive(tmp_path):
    _, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError, match="activation"):
        propose(ledger, proposer_type="HERMES", proposer_id="hermes-sandbox-v1")


def test_missing_or_changed_evidence_fails_closed(tmp_path):
    outcomes, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError, match="verified complete outcomes"):
        propose(ledger, supporting_outcome_ids=["UNKNOWN"])
    result = propose(ledger)
    outcomes.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost evidence"):
        ledger.verify()
    assert result["supporting_outcome_hashes"] == ["hash-FHRT-1"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposer_type", "UNCONTROLLED_BOT"),
        ("proposer_id", "unsafe id"),
        ("supporting_outcome_ids", []),
        ("supporting_outcome_ids", ["FHRT-1", "FHRT-1"]),
        ("observation_summary", ""),
        ("suspected_cause", ""),
        ("uncertainty_statement", ""),
        ("falsifiable_hypothesis", ""),
        ("proposed_experiment", ""),
        ("expected_disconfirming_result", ""),
    ],
)
def test_required_bounded_fields_fail_closed(tmp_path, field, value):
    _, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError):
        propose(ledger, **{field: value})


def test_proposal_cannot_predate_evidence_or_be_future(tmp_path):
    _, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError, match="predate"):
        propose(ledger, proposed_at="2025-02-03T17:02:59+00:00")
    with pytest.raises(ValueError, match="future"):
        propose(ledger, proposed_at="2099-01-01T00:00:00+00:00")


def test_identical_concurrent_retries_append_once(tmp_path):
    _, ledger = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: propose(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"cause_established": True},
        {"human_approval_recorded": True},
        {"experiment_executed": True},
        {"lesson_validated": True},
        {"production_rule_changed": True},
        {"model_weight_changed": True},
        {"code_changed": True},
        {"permission_changed": True},
        {"promotion_performed": True},
        {"deployment_performed": True},
        {"order_submitted": True},
        {"learning_applied": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, ledger = ledgers(tmp_path)
    propose(ledger)
    rewrite(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, ledger = ledgers(tmp_path)
    propose(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
