from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import HermesEmergencyStopLedger

class Stub:
    def __init__(self, values): self.values=list(values)
    def verify(self): return self.values

POLICY={"policy_id":"HMPOL-1","record_hash":"policy-hash","recorded_at":"2026-01-01T00:00:00+00:00","emergency_stop_identifier":"stop-v1","status":"PREREGISTERED_INACTIVE"}
BASE={"policy_id":"HMPOL-1","emergency_stop_identifier":"stop-v1","trigger_source":"HUMAN","reason":"Operator requested immediate stop.","triggered_by":"Sam","triggered_at":"2026-01-01T01:00:00+00:00"}

def ledger(tmp_path): return HermesEmergencyStopLedger(tmp_path/"stop.jsonl",Stub([dict(POLICY)]))
def trigger(target,**changes): values=dict(BASE); values.update(changes); return target.trigger(**values)
def rewrite(path,**changes):
    from core.orchestration import hermes_emergency_stop as module
    value=json.loads(path.read_text()); value.update(changes); material={k:v for k,v in value.items() if k!="record_hash"}; value["record_hash"]=module._record_hash(material); path.write_text(json.dumps(value)+"\n")

def test_unknown_or_inactive_policy_is_stopped_by_default(tmp_path):
    target=ledger(tmp_path)
    assert target.runtime_state("unknown")=={"state":"STOPPED","work_allowed":False,"reason":"UNKNOWN_POLICY"}
    assert target.runtime_state("HMPOL-1")=={"state":"STOPPED","work_allowed":False,"reason":"POLICY_INACTIVE"}

def test_trigger_latches_all_work_off_and_preserves_evidence(tmp_path):
    target=ledger(tmp_path); result=trigger(target)
    assert result["status"]=="STOPPED_LATCHED" and result["running_jobs_must_terminate"] is True and result["previous_hash"]==GENESIS_HASH
    for field in ("new_jobs_allowed","evidence_deletion_allowed","automatic_resume_allowed","self_resume_allowed","scheduler_enabled","model_invocation_allowed","network_access_allowed","broker_access_allowed","aws_access_allowed","github_write_allowed","production_rule_write_allowed","promotion_allowed","order_submission_allowed","live_trading_enabled"): assert result[field] is False
    assert target.runtime_state("HMPOL-1")["state"]=="STOPPED_LATCHED" and target.verify()==[result]

@pytest.mark.parametrize("field,value,fragment",[("policy_id","unknown","verified"),("emergency_stop_identifier","wrong","match"),("trigger_source","AGENT","not permitted"),("reason","","required"),("triggered_at","2025-01-01T00:00:00+00:00","predate")])
def test_invalid_stop_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment): trigger(ledger(tmp_path),**{field:value})

def test_concurrent_retry_appends_once(tmp_path):
    target=ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool: first,second=list(pool.map(lambda _:trigger(target),range(2)))
    assert first==second and len(target.verify())==1

@pytest.mark.parametrize("changes",[{"status":"RUNNING"},{"running_jobs_must_terminate":False},{"new_jobs_allowed":True},{"evidence_deletion_allowed":True},{"automatic_resume_allowed":True},{"self_resume_allowed":True},{"scheduler_enabled":True},{"model_invocation_allowed":True},{"network_access_allowed":True},{"broker_access_allowed":True},{"aws_access_allowed":True},{"github_write_allowed":True},{"production_rule_write_allowed":True},{"promotion_allowed":True},{"order_submission_allowed":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path); trigger(target); rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
