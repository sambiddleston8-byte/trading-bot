from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal, getcontext
import hashlib,json

import pytest

from core.features.pit_feature_contract import PITFeatureRecord, _record, build_revision_matrix, build_technical_feature_matrices, campaign_observation_cutoff, revise_feature_record, validate_revision_chain
from core.orchestration.stage2_qualification import _at,_bar_available,_bar_close,_sessions
from core.research.stage3_feature_strategy_evaluation import evaluate

def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
RETRIEVED_AT="2026-08-16T20:00:00+00:00"
def cutoffs():return {role:{day:campaign_observation_cutoff(day) for day in _sessions(start,end)} for role,start,end in (("TRAIN","2024-10-01","2025-02-28"),("VALIDATION","2025-03-01","2025-04-30"))}
def qualification_pin(root):return hashlib.sha256((root/"data/research/massive_campaign_v2_revision_2/stage2/qualification_report.json").read_bytes()).hexdigest()
def build(root):return build_technical_feature_matrices(root,retrieved_at=RETRIEVED_AT,observation_cutoffs=cutoffs(),qualification_report_artifact_sha256=qualification_pin(root))

def environment(tmp_path,drop=None):
    root=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2"; store=root/"clean_feature_store";store.mkdir(parents=True)
    artifacts={}
    for role,start,end in (("TRAIN","2024-10-01","2025-02-28"),("VALIDATION","2025-03-01","2025-04-30")):
        bars=[]
        for index,day in enumerate(_sessions(start,end)):
            for offset,symbol in enumerate(("AAPL","MSFT","SPY")):
                if drop==(role,day,symbol):continue
                base=Decimal(f"{100+index+offset}.123456789123")
                bars.append({"symbol":symbol,"session_date":day,"open_at":_at(day,__import__("datetime").time(9,30)),"close_at":_at(day,_bar_close(day)),"available_at":_bar_available(day),"open":str(base),"high":str(base+2),"low":str(base-1),"close":str(base+1),"volume":"100000","source_payload_sha256":hashlib.sha256(f"{role}:{day}:{symbol}".encode()).hexdigest()})
        value={"schema_version":"1.0","role":role,"bars":bars,"corporate_actions":[],"quarantine_only":False,"clean_feature_store":True}
        payload=canonical(value)+b"\n";(store/f"{role.lower()}.json").write_bytes(payload);artifacts[role]=hashlib.sha256(payload).hexdigest()
    report={"artifacts":artifacts};report["qualification_sha256"]=hashlib.sha256(canonical(report)).hexdigest()
    (root/"qualification_report.json").write_bytes(canonical(report)+b"\n")
    return root

def test_admitted_technical_matrices_are_causal_and_cross_sectionally_aligned(tmp_path):
    environment(tmp_path);summary=build(tmp_path)
    assert summary["matrices"]["TRAIN"]["row_count"]==162
    assert summary["matrices"]["TRAIN"]["session_count"]==54
    assert summary["matrices"]["VALIDATION"]["row_count"]==126
    assert summary["matrices"]["VALIDATION"]["session_count"]==42
    validation=json.loads((tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").read_text())
    first=validation["rows"][0]; record=PITFeatureRecord(**first)
    assert set(record.values)=={"sma_20","sma_50","momentum_20","atr_14"}
    assert record.available_at==max(x["available_at"] for x in record.provenance["input_rows"])
    assert set(record.provenance["source_artifact_sha256"])=={"QUALIFICATION_REPORT","TRAIN","VALIDATION"}
    assert len(record.provenance["input_rows"])==50
    assert all(datetime.fromisoformat(x["available_at"])<=datetime.fromisoformat(record.observation_cutoff_at) for x in record.provenance["input_rows"])

def test_non_monotonic_technical_vector_matches_golden_values():
    rows=[]
    for index in range(50):
        day=(datetime(2025,1,1,tzinfo=timezone.utc)+timedelta(days=index)).date().isoformat();close=Decimal(100)+Decimal((index*7)%13)+Decimal(index)/10
        rows.append({"session_date":day,"close_at":f"{day}T21:00:00+00:00","available_at":f"{day}T21:01:00+00:00","close":str(close),"high":str(close+1+Decimal(index%3)/10),"low":str(close-1-Decimal(index%5)/10),"source_payload_sha256":hashlib.sha256(day.encode()).hexdigest()})
    record=_record(symbol="AAPL",role="TRAIN",rows=rows,retrieved_at=RETRIEVED_AT,observation_cutoff_at="2025-02-19T21:05:00+00:00",artifact_hashes={"TRAIN":"1"*64})
    assert record.values=={"sma_20":"110.05","sma_50":"108.33","momentum_20":"-0.0090171325518485121731289449954914","atr_14":"7.578571428571428571428571428571429"}

def test_hash_valid_leakage_tamper_and_revision_forgery_fail_closed(tmp_path):
    environment(tmp_path);build(tmp_path)
    path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json"
    row=json.loads(path.read_text())["rows"][0]
    row["provenance"]["input_rows"][0]["available_at"]="2099-01-01T00:00:00+00:00"
    material={k:v for k,v in row.items() if k!="record_sha256"};row["record_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    with pytest.raises(ValueError,match="leaks beyond observation cutoff"):PITFeatureRecord(**row)
    clean=json.loads(path.read_text())["rows"][0];clean["revision"]=2
    material={k:v for k,v in clean.items() if k!="record_sha256"};clean["record_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    with pytest.raises(ValueError,match="revision"):PITFeatureRecord(**clean)
    original=PITFeatureRecord(**json.loads(path.read_text())["rows"][0])
    revised=revise_feature_record(original,retrieved_at="2026-08-17T20:00:00+00:00",values=original.values,provenance=original.provenance)
    assert revised.revision==2 and revised.prior_revision_sha256==original.record_sha256
    validate_revision_chain((original,revised))
    forged=dict(revised.as_dict());forged["prior_revision_sha256"]="0"*64
    material={k:v for k,v in forged.items() if k!="record_sha256"};forged["record_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    with pytest.raises(ValueError,match="parent is not authentic"):validate_revision_chain((original,PITFeatureRecord(**forged)))
    changed=dict(revised.as_dict());changed["entity_id"]="MSFT"
    material={k:v for k,v in changed.items() if k!="record_sha256"};changed["record_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    with pytest.raises(ValueError,match="immutable feature identity"):validate_revision_chain((original,PITFeatureRecord(**changed)))

def test_independent_observation_cutoff_rejects_unavailable_input(tmp_path):
    environment(tmp_path);schedule=cutoffs();schedule["VALIDATION"]["2025-03-03"]=_at("2025-03-03",_bar_close("2025-03-03"))
    assert campaign_observation_cutoff("2025-03-03")=="2025-03-03T21:05:00+00:00"
    with pytest.raises(ValueError,match="campaign calendar contract"):build_technical_feature_matrices(tmp_path,retrieved_at=RETRIEVED_AT,observation_cutoffs=schedule,qualification_report_artifact_sha256=qualification_pin(tmp_path))

def test_qualification_trust_anchor_and_revision_matrix_fail_closed(tmp_path):
    environment(tmp_path)
    with pytest.raises(ValueError,match="admitted pin"):build_technical_feature_matrices(tmp_path,retrieved_at=RETRIEVED_AT,observation_cutoffs=cutoffs(),qualification_report_artifact_sha256="0"*64)
    build(tmp_path);matrix=json.loads((tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json").read_text());first=matrix["rows"][0]
    revised=build_revision_matrix(matrix,retrieved_at="2026-08-17T20:00:00+00:00",value_overrides={first["feature_id"]:first["values"]},provenance_overrides={first["feature_id"]:first["provenance"]})
    assert all(row["revision"]==2 for row in revised["rows"])
    first_revised=revised["rows"][0];third=build_revision_matrix(revised,retrieved_at="2026-08-18T20:00:00+00:00",value_overrides={first_revised["feature_id"]:first_revised["values"]},provenance_overrides={first_revised["feature_id"]:first_revised["provenance"]})
    assert all(row["revision"]==3 for row in third["rows"])
    forged=dict(matrix);forged["admitted"]=False
    with pytest.raises(ValueError,match="matrix hash"):build_revision_matrix(forged,retrieved_at="2026-08-17T20:00:00+00:00",value_overrides={},provenance_overrides={})

def test_missing_cross_sectional_source_row_blocks_entire_matrix(tmp_path):
    environment(tmp_path,drop=("VALIDATION","2025-03-03","MSFT"))
    with pytest.raises(ValueError,match="cross-sectionally aligned"):build(tmp_path)

def test_partition_overlap_and_global_decimal_context_fail_closed_or_stay_deterministic(tmp_path):
    root=environment(tmp_path);build(tmp_path)
    admitted=(tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").read_bytes()
    original_precision=getcontext().prec
    try:
        getcontext().prec=6
        build(tmp_path)
    finally:getcontext().prec=original_precision
    assert (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").read_bytes()==admitted
    validation=root/"clean_feature_store/validation.json";value=json.loads(validation.read_text())
    for row in value["bars"]:
        if row["session_date"]=="2025-03-03":row["session_date"]="2025-02-28"
    payload=canonical(value)+b"\n";validation.write_bytes(payload)
    qualification=root/"qualification_report.json";q=json.loads(qualification.read_text());q["artifacts"]["VALIDATION"]=hashlib.sha256(payload).hexdigest();q["qualification_sha256"]=hashlib.sha256(canonical({k:v for k,v in q.items() if k!="qualification_sha256"})).hexdigest();qualification.write_bytes(canonical(q)+b"\n")
    with pytest.raises(ValueError,match="strictly precede"):build(tmp_path)

def test_future_input_changes_no_earlier_feature_values(tmp_path):
    environment(tmp_path);build(tmp_path)
    path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json"
    before=json.loads(path.read_text()); first=dict(before["rows"][0]["values"])
    # A later source row cannot be in the first validation feature's trailing window.
    store=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/validation.json"
    value=json.loads(store.read_text());later=next(x for x in value["bars"] if x["symbol"]=="AAPL" and x["session_date"]=="2025-03-05");later["close"]="9999";payload=canonical(value)+b"\n";store.write_bytes(payload)
    qualification=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/qualification_report.json";q=json.loads(qualification.read_text());q["artifacts"]["VALIDATION"]=hashlib.sha256(payload).hexdigest();q["qualification_sha256"]=hashlib.sha256(canonical({k:v for k,v in q.items() if k!="qualification_sha256"})).hexdigest();qualification.write_bytes(canonical(q)+b"\n")
    # Derive in a separate output root to avoid overwriting the admitted matrix.
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").unlink()
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json").unlink()
    build(tmp_path);after=json.loads(path.read_text())
    assert after["rows"][0]["values"]==first
    before_later=next(x for x in before["rows"] if x["entity_id"]=="AAPL" and x["effective_at"][:10]=="2025-03-05")
    after_later=next(x for x in after["rows"] if x["entity_id"]=="AAPL" and x["effective_at"][:10]=="2025-03-05")
    assert after_later["values"]!=before_later["values"]

def test_stage3_feature_strategy_evaluation_uses_bounded_partitions(tmp_path):
    environment(tmp_path);build(tmp_path)
    pins={role:json.loads((tmp_path/f"data/research/massive_campaign_v2_revision_2/stage3/technical_features/{role.lower()}_matrix.json").read_text())["matrix_sha256"] for role in ("TRAIN","VALIDATION")}
    with pytest.raises(ValueError,match="admitted matrix pin"):evaluate(tmp_path)
    report=evaluate(tmp_path,admitted_matrix_sha256=pins)
    assert report["partitions"]["TRAIN"]["source_sessions"]==103
    assert report["partitions"]["TRAIN"]["scenarios"]["BASE"]["composite"]["evaluated_sessions"]==103
    assert report["partitions"]["VALIDATION"]["source_sessions"]==42
    assert report["partitions"]["VALIDATION"]["scenarios"]["PESSIMISTIC"]["composite"]["evaluated_sessions"]==42
    assert report["one_bar_train_purge"] is report["one_bar_validation_embargo"] is True
    assert report["train_purged_decision_at"]==report["partitions"]["TRAIN"]["evaluation_end"]
    assert report["validation_embargoed_decision_at"][:10]==report["partitions"]["VALIDATION"]["evaluation_start"][:10]
    assert report["partitions"]["TRAIN"]["scenarios"]["BASE"]["composite"]["completed_trade_count"]>0
    composite=report["partitions"]["TRAIN"]["scenarios"]["BASE"]["composite"]
    assert len(composite["trade_log"])==composite["completed_trade_count"]
    assert composite["execution_cost_attribution"]["filled_execution_count"]==composite["filled_order_count"]
    assert len({row["trade_id"] for row in composite["trade_log"]})==len(composite["trade_log"])
    attribution=composite["execution_cost_attribution"]
    assert abs(Decimal(attribution["cash_pnl_reconciliation_residual"]))<=Decimal(attribution["cash_reconciliation_tolerance"])
    assert report["untouched_test_included"] is False
