from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import PromotionReviewBundleLedger

class Stub:
    def __init__(self, values): self.values=list(values)
    def verify(self): return self.values

RESULT={"shadow_result_id":"SHADOW-RESULT-1","record_hash":"result-hash","status":"SHADOW_CRITERIA_MET_AWAITING_HUMAN_REVIEW","completed_at":"2022-10-02T00:00:00+00:00","candidate_strategy_version":"v2","baseline_strategy_version":"v1"}
BASE={"shadow_result_id":"SHADOW-RESULT-1","candidate_git_revision":"a"*40,"github_issue_url":"https://github.com/sam/bot/issues/1","github_pull_request_url":"https://github.com/sam/bot/pull/2","builder_identity":"Codex","independent_reviewer_identity":"Claude Code","deterministic_test_evidence_sha256":"b"*64,"independent_review_evidence_sha256":"c"*64,"rollback_plan_sha256":"d"*64,"implementation_manifest_sha256":"e"*64,"assembled_by":"Codex","assembled_at":"2022-10-03T00:00:00+00:00"}

def ledger(tmp_path, passing=True):
    result=dict(RESULT)
    if not passing: result["status"]="SHADOW_CRITERIA_NOT_MET"
    return PromotionReviewBundleLedger(tmp_path/"promotion.jsonl",Stub([result]))
def assemble(target,**changes): values=dict(BASE); values.update(changes); return target.assemble(**values)
def rewrite(path,**changes):
    from core.orchestration import promotion_review_bundle as module
    value=json.loads(path.read_text()); value.update(changes); material={k:v for k,v in value.items() if k!="record_hash"}; value["record_hash"]=module._record_hash(material); path.write_text(json.dumps(value)+"\n")

def test_assembles_complete_bundle_without_human_decision(tmp_path):
    target=ledger(tmp_path); result=assemble(target)
    assert result["status"]=="COMPLETE_AWAITING_EXPLICIT_HUMAN_DECISION" and result["previous_hash"]==GENESIS_HASH
    assert result["deterministic_tests_passed"] is True and result["independent_review_passed"] is True and result["rollback_plan_present"] is True
    for field in ("human_decision_recorded","promotion_approved","incumbent_changed","production_rule_changed","production_active","deployment_performed","order_submitted","live_trading_enabled"): assert result[field] is False
    assert target.verify()==[result]

@pytest.mark.parametrize("field,value,fragment",[("shadow_result_id","unknown","passing"),("candidate_git_revision","abc","full hexadecimal"),("github_issue_url","https://example.com/1","GitHub issues"),("github_pull_request_url","https://github.com/sam/bot/issues/2","GitHub pull"),("independent_reviewer_identity","Codex","differ"),("deterministic_test_evidence_sha256","bad","SHA-256"),("assembled_at","2022-01-01T00:00:00+00:00","predate")])
def test_invalid_bundle_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment): assemble(ledger(tmp_path),**{field:value})

def test_failed_shadow_result_cannot_be_bundled(tmp_path):
    with pytest.raises(ValueError,match="passing"): assemble(ledger(tmp_path,passing=False))

def test_concurrent_retry_appends_once(tmp_path):
    target=ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool: first,second=list(pool.map(lambda _:assemble(target),range(2)))
    assert first==second and len(target.verify())==1

@pytest.mark.parametrize("changes",[{"status":"APPROVED"},{"candidate_git_revision":"f"*40},{"independent_reviewer_identity":"Codex"},{"deterministic_tests_passed":False},{"independent_review_passed":False},{"rollback_plan_present":False},{"human_decision_recorded":True},{"promotion_approved":True},{"incumbent_changed":True},{"production_rule_changed":True},{"production_active":True},{"deployment_performed":True},{"order_submitted":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path); assemble(target); rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
