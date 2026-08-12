from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import ShadowTestResultLedger

class Stub:
    def __init__(self, values): self.values=list(values)
    def verify(self): return self.values

PLAN={"shadow_plan_id":"SHADOW-1","record_hash":"plan-hash","strategy_registry_entry_id":"STRATEGY-1","candidate_strategy_version":"v2","baseline_strategy_version":"v1","shadow_not_after":"2022-10-01T00:00:00+00:00","minimum_complete_decisions":20,"primary_metric":"MEAN_ABSOLUTE_PREDICTION_ERROR","minimum_primary_improvement":"0.02","maximum_drawdown_degradation":"0.01","maximum_turnover_increase":"0.05"}
BASE={"shadow_plan_id":"SHADOW-1","evidence_bundle_sha256":"a"*64,"complete_decisions":25,"baseline_primary_metric":"0.10","candidate_primary_metric":"0.07","baseline_maximum_drawdown":"0.10","candidate_maximum_drawdown":"0.105","baseline_turnover":"0.20","candidate_turnover":"0.24","completed_by":"paper-evaluator","completed_at":"2022-10-02T00:00:00+00:00"}

def ledger(tmp_path, metric=None):
    plan=dict(PLAN)
    if metric: plan["primary_metric"]=metric
    return ShadowTestResultLedger(tmp_path/"results.jsonl",Stub([plan]))
def record(target,**changes): values=dict(BASE); values.update(changes); return target.record(**values)
def rewrite(path,**changes):
    from core.orchestration import shadow_test_result as module
    value=json.loads(path.read_text()); value.update(changes); material={k:v for k,v in value.items() if k!="record_hash"}; value["record_hash"]=module._record_hash(material); path.write_text(json.dumps(value)+"\n")

def test_records_complete_passing_shadow_evidence_without_promotion(tmp_path):
    target=ledger(tmp_path); result=record(target)
    assert result["status"]=="SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW" and result["all_shadow_criteria_met"] is True
    assert result["primary_improvement"]=="0.03" and result["previous_hash"]==GENESIS_HASH
    for field in ("promotion_approved","incumbent_changed","production_active","deployment_performed","order_submitted","live_trading_enabled"): assert result[field] is False
    assert target.verify()==[result]

def test_higher_is_better_and_risk_rejection(tmp_path):
    result=record(ledger(tmp_path,"HIT_RATE"),baseline_primary_metric="0.40",candidate_primary_metric="0.43")
    assert result["primary_improvement"]=="0.03"
    rejected=record(ledger(tmp_path/"rejected"),candidate_turnover="0.30")
    assert rejected["status"]=="SHADOW_CRITERIA_NOT_MET" and rejected["turnover_constraint_passed"] is False

@pytest.mark.parametrize("field,value,fragment",[("shadow_plan_id","unknown","verified"),("evidence_bundle_sha256","bad","SHA-256"),("complete_decisions",19,"below"),("completed_at","2022-09-01T00:00:00+00:00","window ends"),("candidate_primary_metric","nan","finite"),("candidate_maximum_drawdown",-1,"non-negative"),("candidate_turnover",-1,"non-negative")])
def test_invalid_result_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment): record(ledger(tmp_path),**{field:value})

def test_concurrent_retry_appends_once(tmp_path):
    target=ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool: first,second=list(pool.map(lambda _:record(target),range(2)))
    assert first==second and len(target.verify())==1

@pytest.mark.parametrize("changes",[{"status":"PROMOTED"},{"complete_decisions":20},{"candidate_primary_metric":"0.20"},{"primary_improvement":"99"},{"all_shadow_criteria_met":False},{"complete_fixed_window":False},{"retrospective_window_selection_used":True},{"window_extension_used":True},{"metric_substitution_used":True},{"promotion_approved":True},{"incumbent_changed":True},{"production_active":True},{"deployment_performed":True},{"order_submitted":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path); record(target); rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
