import json
import pytest
from core.decision_ledger import GENESIS_HASH,LedgerIntegrityError
from core.orchestration import RobustnessTestPlanLedger

class Stub:
    def __init__(self,values):self.values=list(values)
    def verify(self):return self.values
RESULT={"result_id":"RESULT-1","record_hash":"result-hash","status":"ACCEPTANCE_CRITERIA_MET","completed_at":"2022-07-01T00:00:00+00:00","experiment_id":"EXP-1"}
SLICES=[{"dimension":"HISTORICAL_PERIOD","label":"early","evidence_rule":"2010-2015 fixed window"},{"dimension":"HISTORICAL_PERIOD","label":"late","evidence_rule":"2016-2021 fixed window"},{"dimension":"SECTOR","label":"technology","evidence_rule":"GICS technology"},{"dimension":"SECTOR","label":"industrials","evidence_rule":"GICS industrials"},{"dimension":"MARKET_REGIME","label":"high volatility","evidence_rule":"VIX threshold fixed before run"},{"dimension":"MARKET_REGIME","label":"low volatility","evidence_rule":"VIX threshold fixed before run"}]
BASE={"experiment_result_id":"RESULT-1","robustness_slices":SLICES,"minimum_passing_slice_fraction":"0.8","planned_by":"Codex","planned_at":"2022-07-02T00:00:00+00:00"}
def ledger(tmp_path,passing=True):
    result=dict(RESULT)
    if not passing:result["status"]="REJECTION_CRITERIA_MET"
    return RobustnessTestPlanLedger(tmp_path/"robust.jsonl",Stub([result]))
def plan(target,**changes):values=dict(BASE);values.update(changes);return target.preregister(**values)
def rewrite(path,**changes):
    from core.orchestration import robustness_plan as module
    value=json.loads(path.read_text());value.update(changes);material={k:v for k,v in value.items() if k!="record_hash"};value["record_hash"]=module._record_hash(material);path.write_text(json.dumps(value)+"\n")

def test_preregisters_all_robustness_dimensions_without_execution(tmp_path):
    target=ledger(tmp_path);result=plan(target)
    assert result["status"]=="PREREGISTERED_NOT_EXECUTED" and len(result["robustness_slices"])==6 and result["previous_hash"]==GENESIS_HASH
    assert all(result[field] is True for field in ("transaction_costs_required","point_in_time_data_required","survivorship_safe_universe_required","no_leakage_required"))
    for field in ("robustness_executed","result_recorded","shadow_eligible","promotion_approved","production_rule_changed","deployment_performed","order_submitted","live_trading_enabled"):assert result[field] is False
    assert target.verify()==[result]

def test_failed_oos_result_cannot_progress(tmp_path):
    with pytest.raises(ValueError,match="passing"):plan(ledger(tmp_path,False))

@pytest.mark.parametrize("field,value,fragment",[("experiment_result_id","unknown","passing"),("minimum_passing_slice_fraction",0,"greater"),("minimum_passing_slice_fraction",1.1,"at most"),("planned_at","2022-06-01T00:00:00+00:00","predate"),("robustness_slices",SLICES[:-1],"two slices")])
def test_invalid_plan_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment):plan(ledger(tmp_path),**{field:value})

@pytest.mark.parametrize("changes",[{"status":"EXECUTED"},{"robustness_slices":[]},{"minimum_passing_slice_fraction":"0.1"},{"transaction_costs_required":False},{"point_in_time_data_required":False},{"survivorship_safe_universe_required":False},{"no_leakage_required":False},{"robustness_executed":True},{"shadow_eligible":True},{"promotion_approved":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path);plan(target);rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError):target.verify()
