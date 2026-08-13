import json
import pytest
from core.decision_ledger import GENESIS_HASH,LedgerIntegrityError
from core.orchestration import RobustnessTestResultLedger

class Stub:
    def __init__(self,values):self.values=list(values)
    def verify(self):return self.values
SLICES=[{"dimension":"HISTORICAL_PERIOD","label":"early","evidence_rule":"fixed"},{"dimension":"HISTORICAL_PERIOD","label":"late","evidence_rule":"fixed"},{"dimension":"MARKET_REGIME","label":"high","evidence_rule":"fixed"},{"dimension":"MARKET_REGIME","label":"low","evidence_rule":"fixed"},{"dimension":"SECTOR","label":"tech","evidence_rule":"fixed"},{"dimension":"SECTOR","label":"industry","evidence_rule":"fixed"}]
PLAN={"robustness_plan_id":"ROBUST-1","record_hash":"plan-hash","planned_at":"2022-07-02T00:00:00+00:00","experiment_result_id":"RESULT-1","experiment_id":"EXP-1","robustness_slices":SLICES,"minimum_passing_slice_fraction":"0.8"}
RESULTS=[{"dimension":item["dimension"],"label":item["label"],"evidence_sha256":str(index+1)*64,"passed":index<5,"transaction_costs_included":True,"point_in_time_data_used":True,"survivorship_safe_universe_used":True,"leakage_check_passed":True} for index,item in enumerate(SLICES)]
BASE={"robustness_plan_id":"ROBUST-1","slice_results":RESULTS,"completed_by":"runner","completed_at":"2022-07-03T00:00:00+00:00"}
def ledger(tmp_path):return RobustnessTestResultLedger(tmp_path/"results.jsonl",Stub([dict(PLAN)]))
def record(target,**changes):values=dict(BASE);values.update(changes);return target.record(**values)
def rewrite(path,**changes):
    from core.orchestration import robustness_result as module
    value=json.loads(path.read_text());value.update(changes);material={k:v for k,v in value.items() if k!="record_hash"};value["record_hash"]=module._record_hash(material);path.write_text(json.dumps(value)+"\n")
def test_records_complete_passing_robustness_without_shadow_start(tmp_path):
    target=ledger(tmp_path);result=record(target)
    assert result["status"]=="ROBUSTNESS_CRITERIA_MET" and result["passing_slices"]==5 and result["passing_slice_fraction"]=="0.8333333333333333333333333333" and result["shadow_eligible"] is True and result["shadow_test_started"] is False and result["previous_hash"]==GENESIS_HASH
    assert target.verify()==[result]
def test_each_dimension_requires_a_passing_slice(tmp_path):
    values=[dict(item) for item in RESULTS]
    for item in values:
        if item["dimension"]=="SECTOR":item["passed"]=False
    result=record(ledger(tmp_path),slice_results=values)
    assert result["status"]=="ROBUSTNESS_CRITERIA_NOT_MET" and result["dimension_has_passing_slice"]["SECTOR"] is False
def test_one_artifact_cannot_masquerade_as_multiple_robustness_slices(tmp_path):
    values=[dict(item,evidence_sha256="a"*64) for item in RESULTS]
    with pytest.raises(ValueError,match="distinct evidence"):record(ledger(tmp_path),slice_results=values)
@pytest.mark.parametrize("change,fragment",[(RESULTS[:-1],"every preregistered"),([dict(RESULTS[0],transaction_costs_included=False)]+RESULTS[1:],"every slice"),([dict(RESULTS[0],label="unknown")]+RESULTS[1:],"not preregistered")])
def test_invalid_results_fail_closed(tmp_path,change,fragment):
    with pytest.raises(ValueError,match=fragment):record(ledger(tmp_path),slice_results=change)
@pytest.mark.parametrize("changes",[{"status":"PROMOTED"},{"passing_slices":6},{"passing_slice_fraction":"1"},{"dimension_has_passing_slice":{}},{"all_robustness_criteria_met":False},{"shadow_eligible":False},{"shadow_test_started":True},{"promotion_approved":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path);record(target);rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError):target.verify()
