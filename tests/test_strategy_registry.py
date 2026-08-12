from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import CandidateStrategyRegistryLedger


class Stub:
    def __init__(self, values): self.values = list(values)
    def verify(self): return self.values


EXPERIMENT = {"experiment_id": "EXP-1", "record_hash": "exp-hash", "candidate_strategy_version": "v2", "baseline_strategy_version": "v1"}
RESULT = {"result_id": "RESULT-1", "record_hash": "result-hash", "experiment_id": "EXP-1", "experiment_record_hash": "exp-hash", "status": "ACCEPTANCE_CRITERIA_MET", "completed_at": "2022-07-01T00:00:00+00:00"}


def ledger(tmp_path, accepted=True):
    result = dict(RESULT)
    if not accepted: result["status"] = "REJECTION_CRITERIA_MET"
    results = Stub([result]); manifests = Stub([]); manifests.experiment_ledger = Stub([dict(EXPERIMENT)])
    results.run_manifest_ledger = manifests
    return CandidateStrategyRegistryLedger(tmp_path / "registry.jsonl", results)


def classify(target, **changes):
    values = {"result_id": "RESULT-1", "recorded_by": "Codex", "recorded_at": "2022-07-02T00:00:00+00:00"}
    values.update(changes); return target.classify(**values)


def rewrite(path, **changes):
    from core.orchestration import strategy_registry as module
    value = json.loads(path.read_text()); value.update(changes)
    material = {k: v for k, v in value.items() if k != "record_hash"}
    value["record_hash"] = module._record_hash(material); path.write_text(json.dumps(value) + "\n")


def test_accepted_result_is_only_shadow_eligible(tmp_path):
    target = ledger(tmp_path); result = classify(target)
    assert result["status"] == "ELIGIBLE_FOR_SHADOW_NOT_STARTED"
    assert result["shadow_eligible"] is True and result["shadow_test_started"] is False
    assert result["incumbent"] is False and result["production_active"] is False
    assert result["previous_hash"] == GENESIS_HASH and target.verify() == [result]


def test_failed_result_is_rejected(tmp_path):
    result = classify(ledger(tmp_path, accepted=False))
    assert result["status"] == "REJECTED_EXPERIMENT" and result["shadow_eligible"] is False


def test_unknown_or_predated_result_fails(tmp_path):
    with pytest.raises(ValueError, match="verified"): classify(ledger(tmp_path), result_id="unknown")
    with pytest.raises(ValueError, match="predate"): classify(ledger(tmp_path), recorded_at="2022-06-01T00:00:00+00:00")


def test_parent_change_fails_closed(tmp_path):
    target = ledger(tmp_path); classify(target); target.result_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost its result"): target.verify()


def test_concurrent_retry_appends_once(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool: first, second = list(pool.map(lambda _: classify(target), range(2)))
    assert first == second and len(target.verify()) == 1


@pytest.mark.parametrize("changes", [
    {"status": "INCUMBENT"}, {"candidate_strategy_version": "v3"},
    {"shadow_eligible": False}, {"shadow_test_started": True}, {"incumbent": True},
    {"production_active": True}, {"human_promotion_approved": True},
    {"code_changed": True}, {"deployment_performed": True},
    {"order_submitted": True}, {"live_trading_enabled": True},
])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target = ledger(tmp_path); classify(target); rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
