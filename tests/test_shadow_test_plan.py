from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import ShadowTestPlanLedger

class Stub:
    def __init__(self, values): self.values = list(values)
    def verify(self): return self.values

EXPERIMENT = {"experiment_id":"EXP-1","record_hash":"exp-hash","shadow_not_before":"2022-07-01T00:00:00+00:00","primary_metric":"MEAN_ABSOLUTE_PREDICTION_ERROR","minimum_primary_improvement":"0.02","maximum_drawdown_degradation":"0.01","maximum_turnover_increase":"0.05"}
ENTRY = {"strategy_registry_entry_id":"STRATEGY-1","record_hash":"entry-hash","status":"ELIGIBLE_FOR_SHADOW_NOT_STARTED","experiment_id":"EXP-1","experiment_record_hash":"exp-hash","candidate_strategy_version":"v2","baseline_strategy_version":"v1"}
BASE = {"strategy_registry_entry_id":"STRATEGY-1","shadow_not_before":"2022-08-01T00:00:00+00:00","shadow_not_after":"2022-10-01T00:00:00+00:00","minimum_complete_decisions":20,"observation_cadence":"DAILY_MARKET_CLOSE","planned_by":"Codex","planned_at":"2022-07-02T00:00:00+00:00"}

def ledger(tmp_path, eligible=True):
    entry=dict(ENTRY)
    if not eligible: entry["status"]="REJECTED_EXPERIMENT"
    registry=Stub([entry]); results=Stub([]); manifests=Stub([]); manifests.experiment_ledger=Stub([dict(EXPERIMENT)]); results.run_manifest_ledger=manifests; registry.result_ledger=results
    return ShadowTestPlanLedger(tmp_path/"shadow.jsonl", registry)

def plan(target, **changes): values=dict(BASE); values.update(changes); return target.preregister(**values)
def rewrite(path, **changes):
    from core.orchestration import shadow_test_plan as module
    value=json.loads(path.read_text()); value.update(changes); material={k:v for k,v in value.items() if k!="record_hash"}; value["record_hash"]=module._record_hash(material); path.write_text(json.dumps(value)+"\n")

def test_preregisters_inert_future_shadow_plan(tmp_path):
    target=ledger(tmp_path); result=plan(target)
    assert result["status"]=="PREREGISTERED_NOT_STARTED" and result["paper_only"] is True
    assert result["previous_hash"]==GENESIS_HASH
    for field in ("retrospective_window_selection_allowed","window_extension_after_start_allowed","metric_substitution_allowed","broker_connection_allowed","shadow_test_started","shadow_result_recorded","promotion_approved","incumbent_changed","production_active","deployment_performed","order_submitted","live_trading_enabled"): assert result[field] is False
    assert target.verify()==[result]

@pytest.mark.parametrize("field,value,fragment",[("strategy_registry_entry_id","unknown","eligible"),("shadow_not_before","2022-07-01T00:00:00+00:00","after preregistration"),("shadow_not_before","2022-06-01T00:00:00+00:00","after preregistration"),("shadow_not_after","2022-08-20T00:00:00+00:00","30 days"),("minimum_complete_decisions",0,"between"),("observation_cadence","HOURLY","not supported")])
def test_invalid_plan_fails_closed(tmp_path, field, value, fragment):
    with pytest.raises(ValueError, match=fragment): plan(ledger(tmp_path), **{field:value})

def test_rejected_candidate_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="eligible"): plan(ledger(tmp_path, eligible=False))

def test_concurrent_retry_appends_once(tmp_path):
    target=ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool: first,second=list(pool.map(lambda _:plan(target),range(2)))
    assert first==second and len(target.verify())==1

@pytest.mark.parametrize("changes",[{"status":"STARTED"},{"shadow_not_after":"2023-01-01T00:00:00+00:00"},{"minimum_complete_decisions":1},{"primary_metric":"HIT_RATE"},{"retrospective_window_selection_allowed":True},{"window_extension_after_start_allowed":True},{"broker_connection_allowed":True},{"shadow_test_started":True},{"promotion_approved":True},{"incumbent_changed":True},{"production_active":True},{"deployment_performed":True},{"order_submitted":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target=ledger(tmp_path); plan(target); rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
