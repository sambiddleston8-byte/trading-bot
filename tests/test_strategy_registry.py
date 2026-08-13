from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH,LedgerIntegrityError
from core.orchestration import CandidateStrategyRegistryLedger
class Stub:
    def __init__(self,values):self.values=list(values)
    def verify(self):return self.values
EXPERIMENT={"experiment_id":"EXP-1","record_hash":"exp-hash","candidate_strategy_version":"v2","baseline_strategy_version":"v1"}
RESULT={"result_id":"RESULT-1","record_hash":"result-hash","experiment_id":"EXP-1","experiment_record_hash":"exp-hash","status":"ACCEPTANCE_CRITERIA_MET","completed_at":"2022-07-01T00:00:00+00:00"}
ROBUST={"robustness_result_id":"ROBUST-RESULT-1","record_hash":"robust-hash","experiment_result_id":"RESULT-1","experiment_id":"EXP-1","status":"ROBUSTNESS_CRITERIA_MET","completed_at":"2022-07-02T00:00:00+00:00"}
def ledger(tmp_path,accepted=True):
    robust=dict(ROBUST)
    if not accepted:robust["status"]="ROBUSTNESS_CRITERIA_NOT_MET"
    robustness=Stub([robust]);plans=Stub([]);results=Stub([dict(RESULT)]);manifests=Stub([]);experiments=Stub([dict(EXPERIMENT)]);manifests.experiment_ledger=experiments;results.run_manifest_ledger=manifests;plans.result_ledger=results;robustness.plan_ledger=plans
    return CandidateStrategyRegistryLedger(tmp_path/"registry.jsonl",robustness)
def classify(target,**changes):values={"robustness_result_id":"ROBUST-RESULT-1","recorded_by":"Codex","recorded_at":"2022-07-03T00:00:00+00:00"};values.update(changes);return target.classify(**values)
def rewrite(path,**changes):
    from core.orchestration import strategy_registry as module
    value=json.loads(path.read_text());value.update(changes);material={k:v for k,v in value.items() if k!="record_hash"};value["record_hash"]=module._record_hash(material);path.write_text(json.dumps(value)+"\n")
def test_robust_candidate_is_only_shadow_eligible(tmp_path):
    target=ledger(tmp_path);result=classify(target)
    assert result["status"]=="ELIGIBLE_FOR_SHADOW_NOT_STARTED" and result["robustness_result_id"]=="ROBUST-RESULT-1" and result["shadow_eligible"] is True and result["shadow_test_started"] is False and result["previous_hash"]==GENESIS_HASH and target.verify()==[result]
def test_failed_robustness_is_rejected(tmp_path):
    result=classify(ledger(tmp_path,False));assert result["status"]=="REJECTED_EXPERIMENT" and result["shadow_eligible"] is False
def test_plain_or_unknown_result_cannot_bypass_robustness(tmp_path):
    with pytest.raises(ValueError,match="robustness"):classify(ledger(tmp_path),robustness_result_id="RESULT-1")
def test_concurrent_retry_appends_once(tmp_path):
    target=ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:first,second=list(pool.map(lambda _:classify(target),range(2)))
    assert first==second and len(target.verify())==1
@pytest.mark.parametrize("changes",[{"status":"INCUMBENT"},{"robustness_result_id":"other"},{"candidate_strategy_version":"v3"},{"shadow_eligible":False},{"shadow_test_started":True},{"incumbent":True},{"production_active":True},{"human_promotion_approved":True},{"deployment_performed":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path);classify(target);rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError):target.verify()
