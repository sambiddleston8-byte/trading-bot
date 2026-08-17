from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal, getcontext
import hashlib,json

import pytest

from core.features.pit_feature_contract import PITFeatureRecord, _record, build_revision_matrix, build_technical_feature_matrices, campaign_observation_cutoff, revise_feature_record, validate_revision_chain
from core.guardrailed_backtest import MarketBar
from core.orchestration.stage2_qualification import _at,_bar_available,_bar_close,_sessions
from core.research.stage3_feature_strategy_evaluation import evaluate, evaluate_momentum_confirmed
from core.research.stage3_train_rolling_diagnostic import (
    _policy_divergence_observed,
    evaluate_train_market_breadth,
    evaluate_train_rolling,
)
from core.research import stage3_train_rolling_diagnostic as rolling_diagnostic
from scripts.run_stage3_train_market_breadth_evaluation import _summary
from core.research.stage4_train_volatility_evaluation import (
    evaluate_train_volatility_risk_off,
)
from core.research.pit_feature_signal_adapter import VolatilityRiskOffSignalAdapter
from scripts.run_stage4_train_volatility_evaluation import (
    _summary as stage4_summary,
)

def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
RETRIEVED_AT="2026-08-16T20:00:00+00:00"
def cutoffs():return {role:{day:campaign_observation_cutoff(day) for day in _sessions(start,end)} for role,start,end in (("TRAIN","2024-10-01","2025-02-28"),("VALIDATION","2025-03-01","2025-04-30"))}
def qualification_pin(root):return hashlib.sha256((root/"data/research/massive_campaign_v2_revision_2/stage2/qualification_report.json").read_bytes()).hexdigest()
def build(root):return build_technical_feature_matrices(root,retrieved_at=RETRIEVED_AT,observation_cutoffs=cutoffs(),qualification_report_artifact_sha256=qualification_pin(root))

def environment(
    tmp_path,drop=None,volatility_spike_days=(),train_corporate_actions=()
):
    root=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2"; store=root/"clean_feature_store";store.mkdir(parents=True)
    artifacts={}
    for role,start,end in (("TRAIN","2024-10-01","2025-02-28"),("VALIDATION","2025-03-01","2025-04-30")):
        bars=[]
        for index,day in enumerate(_sessions(start,end)):
            for offset,symbol in enumerate(("AAPL","MSFT","SPY")):
                if drop==(role,day,symbol):continue
                base=Decimal(f"{100+index+offset}.123456789123")
                high_spread=Decimal("30") if day in volatility_spike_days else Decimal("2")
                low_spread=Decimal("30") if day in volatility_spike_days else Decimal("1")
                bars.append({"symbol":symbol,"session_date":day,"open_at":_at(day,__import__("datetime").time(9,30)),"close_at":_at(day,_bar_close(day)),"available_at":_bar_available(day),"open":str(base),"high":str(base+high_spread),"low":str(base-low_spread),"close":str(base+1),"volume":"100000","source_payload_sha256":hashlib.sha256(f"{role}:{day}:{symbol}".encode()).hexdigest()})
        value={"schema_version":"1.0","role":role,"bars":bars,"corporate_actions":list(train_corporate_actions) if role=="TRAIN" else [],"quarantine_only":False,"clean_feature_store":True}
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
    bars=tuple(
        MarketBar(
            "AAPL",
            datetime.fromisoformat(row["close_at"])-timedelta(hours=6,minutes=30),
            datetime.fromisoformat(row["close_at"]),
            datetime.fromisoformat(row["available_at"]),
            Decimal(row["close"]),
            Decimal(row["high"]),
            Decimal(row["low"]),
            Decimal(row["close"]),
            Decimal("100000"),
        )
        for row in rows
    )
    assert VolatilityRiskOffSignalAdapter._atr_value(
        bars,window=14
    )==Decimal(record.values["atr_14"])

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


def test_momentum_confirmed_strategy_runs_over_bounded_partitions(tmp_path):
    environment(tmp_path);build(tmp_path)
    pins={role:json.loads((tmp_path/f"data/research/massive_campaign_v2_revision_2/stage3/technical_features/{role.lower()}_matrix.json").read_text())["matrix_sha256"] for role in ("TRAIN","VALIDATION")}
    baseline=evaluate(tmp_path,admitted_matrix_sha256=pins)
    confirmed=evaluate_momentum_confirmed(tmp_path,admitted_matrix_sha256=pins)
    assert confirmed["status"]=="TRAIN_VALIDATION_MOMENTUM_CONFIRMED_STRATEGY_EVALUATED"
    assert confirmed["evaluation_sha256"]!=baseline["evaluation_sha256"]
    assert confirmed["strategy_variant_lineage"]=={
        "policy_version":"admitted-pit-momentum-confirmed-signal-v2",
        "parent_policy_version":"admitted-pit-technical-signal-v1",
        "validation_evaluation_ordinal":2,
        "validation_reused":True,
        "parameter_selection_partition":"TRAIN_ONLY",
    }
    assert confirmed["partitions"]["TRAIN"]["scenarios"]["BASE"]["composite"]["evaluated_sessions"]==103
    assert confirmed["partitions"]["VALIDATION"]["scenarios"]["BASE"]["composite"]["evaluated_sessions"]==42
    assert confirmed["one_bar_train_purge"] is confirmed["one_bar_validation_embargo"] is True
    assert confirmed["untouched_test_included"] is False


def test_train_rolling_diagnostic_executes_three_folds_without_validation(tmp_path):
    environment(tmp_path);build(tmp_path)
    train_matrix=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    pin=json.loads(train_matrix.read_text())["matrix_sha256"]
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").unlink()
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/validation.json").unlink()
    for forbidden in (
        tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/test_matrix.json",
        tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/test.json",
    ):
        forbidden.parent.mkdir(parents=True,exist_ok=True)
        forbidden.write_text("must not be read")
        forbidden.unlink()
    with pytest.raises(ValueError,match="admitted pin"):
        evaluate_train_rolling(tmp_path,admitted_train_matrix_sha256="0"*64)
    report=evaluate_train_rolling(tmp_path,admitted_train_matrix_sha256=pin)
    assert report["source_partition"]=="TRAIN"
    assert report["validation_data_read"] is report["untouched_test_included"] is False
    assert report["parameter_search_allowed"] is report["promotion_allowed"] is False
    for policy in report["policies"].values():
        assert len(policy["folds"])==3
        assert all(fold["source_sessions"]==18 for fold in policy["folds"])
        assert all(all(value[:10]==fold["evaluation_start"][:10] for value in fold["embargoed_decision_ats"]) for fold in policy["folds"])
        assert all(all(value[:10]==fold["evaluation_end"][:10] for value in fold["purged_decision_ats"]) for fold in policy["folds"])
        assert all(left["evaluation_end"]<right["evaluation_start"] for left,right in zip(policy["folds"],policy["folds"][1:]))
        assert all(fold["scenarios"]["BASE"]["cost_model_bps"]["baseline_slippage"]=="10" for fold in policy["folds"])
        assert all(fold["scenarios"]["PESSIMISTIC"]["cost_model_bps"]["baseline_slippage"]=="20" for fold in policy["folds"])
        for scenario in ("BASE","PESSIMISTIC"):
            aggregate=policy["aggregate"][scenario]
            assert aggregate["pooled_evaluated_sessions"]==54
            assert aggregate["pooled_daily_observations"]==54
            compounded=Decimal("1")
            benchmark=Decimal("1")
            for fold in aggregate["fold_returns"]:
                compounded*=Decimal("1")+Decimal(fold["total_return"])
                benchmark*=Decimal("1")+Decimal(fold["spy_buy_hold_total_return"])
            assert compounded-1==Decimal(aggregate["fold_reset_chained_total_return"])
            assert benchmark-1==Decimal(aggregate["fold_reset_spy_buy_hold_total_return"])
            assert Decimal(aggregate["fold_reset_excess_return_vs_spy"])==compounded-benchmark
            assert aggregate["completed_trade_count"]==sum(fold["scenarios"][scenario]["composite"]["completed_trade_count"] for fold in policy["folds"])
            assert Decimal(aggregate["execution_cost_attribution"]["fees"])==sum(Decimal(fold["scenarios"][scenario]["composite"]["execution_cost_attribution"]["fees"]) for fold in policy["folds"])
            assert Decimal(aggregate["execution_cost_attribution"]["adverse_execution_cost"])==sum(Decimal(fold["scenarios"][scenario]["composite"]["execution_cost_attribution"]["adverse_execution_cost"]) for fold in policy["folds"])
            weighted=sum(Decimal(fold["scenarios"][scenario]["composite"]["annual_turnover"])*Decimal(fold["source_sessions"]) for fold in policy["folds"])/Decimal("54")
            assert weighted==Decimal(aggregate["session_weighted_annual_turnover"])
            assert Decimal("0")<=Decimal(aggregate["fold_reset_chained_maximum_drawdown"])<Decimal("1")
            assert aggregate["pooled_daily_sharpe_ratio"] is None or Decimal(aggregate["pooled_daily_sharpe_ratio"]).is_finite()
            assert all(trade["fold_id"].startswith("TRAIN-FOLD-") for trade in aggregate["trade_log"])
    repeated=evaluate_train_rolling(tmp_path,admitted_train_matrix_sha256=pin)
    assert repeated["evaluation_sha256"]==report["evaluation_sha256"]
    # Golden values reproduced directly from parent commit cc6b512aa70339a9975fcf1d310f50d2a7cd91c4.
    assert report["evaluation_sha256"]=="71d2b3513de382be9642ae0f653fc4280df50d5867f55d8a67be1b26d42eaff3"
    assert report["artifact_sha256"]=="10614257c087cf5eaf20c474d9d0e6fef51d9a2250938b9ddfe6dce81e33729f"


def test_train_market_breadth_evaluation_is_separate_and_train_only(tmp_path):
    environment(tmp_path);build(tmp_path)
    assert rolling_diagnostic.FOLD_COUNT==3
    matrix_path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    matrix=json.loads(matrix_path.read_text())
    identical={scenario:{"completed_trade_count":1} for scenario in ("BASE","PESSIMISTIC")}
    assert _policy_divergence_observed({"policies":{
        "BASELINE_REFERENCE":{"aggregate":identical},
        "MARKET_BREADTH":{"aggregate":identical},
    }}) is False
    zero_trade={
        "BASE":{"completed_trade_count":0,"result":"different"},
        "PESSIMISTIC":{"completed_trade_count":1,"result":"different"},
    }
    assert _policy_divergence_observed({"policies":{
        "BASELINE_REFERENCE":{"aggregate":identical},
        "MARKET_BREADTH":{"aggregate":zero_trade},
    }}) is False
    sessions=sorted({row["effective_at"] for row in matrix["rows"]})
    assert len(sessions)==54
    bearish_sessions=set(sessions[20:30])
    for row in matrix["rows"]:
        if row["entity_id"] in {"MSFT","SPY"} and row["effective_at"] in bearish_sessions:
            row["values"]={**row["values"],"sma_20":"90","sma_50":"100","momentum_20":"-0.1"}
            material={key:value for key,value in row.items() if key!="record_sha256"}
            row["record_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    material={key:value for key,value in matrix.items() if key!="matrix_sha256"}
    matrix["matrix_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    matrix_path.write_bytes(canonical(matrix)+b"\n")
    pin=matrix["matrix_sha256"]
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").unlink()
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/validation.json").unlink()
    test_matrix=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/test_matrix.json"
    test_store=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/test.json"
    test_matrix.write_text("sealed TEST matrix must not be read")
    test_store.write_text("sealed TEST store must not be read")
    report=evaluate_train_market_breadth(
        tmp_path,admitted_train_matrix_sha256=pin
    )
    assert report["status"]=="TRAIN_ONLY_MARKET_BREADTH_POLICY_EVALUATION_COMPLETE"
    assert report["source_partition"]=="TRAIN"
    assert report["validation_data_read"] is False
    assert report["untouched_test_included"] is False
    assert report["promotion_allowed"] is False
    assert report["policy_divergence_observed"] is True
    assert report["artifact_lineage"]["revision"]==1
    assert report["artifact_lineage"]["predecessor_paths"]==[]
    assert set(report["policies"])=={"BASELINE_REFERENCE","MARKET_BREADTH"}
    assert report["policies"]["MARKET_BREADTH"]["policy_version"]=="admitted-pit-majority-breadth-signal-v3"
    for policy in report["policies"].values():
        assert len(policy["folds"])==3
        for scenario in ("BASE","PESSIMISTIC"):
            aggregate=policy["aggregate"][scenario]
            assert aggregate["pooled_evaluated_sessions"]==54
            assert aggregate["pooled_daily_observations"]==54
            assert aggregate["completed_trade_count"]==len(aggregate["trade_log"])
            assert all(
                trade["fold_id"].startswith("TRAIN-FOLD-")
                for trade in aggregate["trade_log"]
            )
    for scenario in ("BASE","PESSIMISTIC"):
        baseline=report["policies"]["BASELINE_REFERENCE"]["aggregate"][scenario]
        breadth=report["policies"]["MARKET_BREADTH"]["aggregate"][scenario]
        assert breadth["completed_trade_count"]>0
        assert breadth["fold_reset_chained_total_return"]!=baseline["fold_reset_chained_total_return"]
    assert test_matrix.read_text()=="sealed TEST matrix must not be read"
    assert test_store.read_text()=="sealed TEST store must not be read"
    output=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/train_market_breadth_policy_evaluation_committed_v1.json"
    assert output.exists()
    repeated=evaluate_train_market_breadth(
        tmp_path,admitted_train_matrix_sha256=pin
    )
    assert repeated["evaluation_sha256"]==report["evaluation_sha256"]
    assert repeated["artifact_sha256"]==report["artifact_sha256"]
    public=_summary(report)
    assert set(public)=={
        "status","evaluation_sha256","artifact_sha256","artifact_path"
    }
    assert "policies" not in public


def test_stage4_volatility_risk_off_evaluation_is_train_only_and_deterministic(tmp_path):
    environment(tmp_path,volatility_spike_days={"2024-12-12"});build(tmp_path)
    matrix_path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    pin=json.loads(matrix_path.read_text())["matrix_sha256"]
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/validation_matrix.json").unlink()
    (tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/validation.json").unlink()
    test_matrix=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/test_matrix.json"
    test_store=tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/test.json"
    test_matrix.write_text("sealed TEST matrix must not be read")
    test_store.write_text("sealed TEST store must not be read")

    report=evaluate_train_volatility_risk_off(
        tmp_path,admitted_train_matrix_sha256=pin
    )
    assert report["status"]=="TRAIN_ONLY_VOLATILITY_RISK_OFF_EVALUATION_COMPLETE"
    assert report["source_partition"]=="TRAIN"
    assert report["validation_data_read"] is False
    assert report["untouched_test_included"] is False
    assert report["parameter_search_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["artifact_lineage"]["revision"]==2
    assert report["artifact_lineage"]["predecessor_paths"]==[
        "data/research/massive_campaign_v2_revision_2/stage4/"
        "train_volatility_risk_off_evaluation_committed_v1.json"
    ]
    warmup=report["evaluation_metadata"]["volatility_warmup_and_lineage"]
    assert warmup["minimum_history_bars"]==35
    assert warmup["insufficient_history_suppressions_expected"]==0
    assert len(warmup["available_history_bars_at_fold_start"])==3
    assert all(
        count>=35
        for symbols in warmup["available_history_bars_at_fold_start"].values()
        for count in symbols.values()
    )
    assert set(report["policies"])=={
        "PRIOR_MARKET_BREADTH","VOLATILITY_RISK_OFF"
    }
    assert report["policies"]["VOLATILITY_RISK_OFF"]["policy_version"]=="admitted-pit-volatility-risk-off-signal-v4"
    for policy in report["policies"].values():
        assert len(policy["folds"])==3
        for scenario in ("BASE","PESSIMISTIC"):
            aggregate=policy["aggregate"][scenario]
            assert aggregate["pooled_evaluated_sessions"]==54
            assert aggregate["pooled_daily_observations"]==54
            assert aggregate["completed_trade_count"]==len(aggregate["trade_log"])
            assert all(
                trade["fold_id"].startswith("TRAIN-FOLD-")
                for trade in aggregate["trade_log"]
            )
    for scenario in ("BASE","PESSIMISTIC"):
        prior=report["policies"]["PRIOR_MARKET_BREADTH"]["aggregate"][scenario]
        risk_off=report["policies"]["VOLATILITY_RISK_OFF"]["aggregate"][scenario]
        assert risk_off["fold_reset_chained_total_return"]!=prior["fold_reset_chained_total_return"]
    risk_folds=report["policies"]["VOLATILITY_RISK_OFF"]["folds"]
    diagnostics=[
        values
        for fold in risk_folds
        for values in fold["scenarios"]["BASE"]["strategy_diagnostics"].values()
    ]
    assert all(
        "strategy_diagnostics" not in fold["scenarios"]["BASE"]
        for fold in report["policies"]["PRIOR_MARKET_BREADTH"]["folds"]
    )
    assert sum(x["insufficient_history_suppressions"] for x in diagnostics)==0
    assert sum(x["percentile_risk_off_suppressions"] for x in diagnostics)>0
    assert sum(x["breadth_entry_candidates"] for x in diagnostics)==sum(
        x["percentile_risk_off_suppressions"]+x["entries_permitted"]
        for x in diagnostics
    )
    assert test_matrix.read_text()=="sealed TEST matrix must not be read"
    assert test_store.read_text()=="sealed TEST store must not be read"
    output=tmp_path/"data/research/massive_campaign_v2_revision_2/stage4/train_volatility_risk_off_evaluation_committed_v2.json"
    assert output.exists()
    repeated=evaluate_train_volatility_risk_off(
        tmp_path,admitted_train_matrix_sha256=pin
    )
    assert repeated["evaluation_sha256"]==report["evaluation_sha256"]
    assert repeated["artifact_sha256"]==report["artifact_sha256"]
    public=stage4_summary(report)
    assert set(public)=={
        "status","evaluation_sha256","artifact_sha256","artifact_path"
    }
    assert "policies" not in public


@pytest.mark.parametrize("action_type",("SPLIT","STOCK_SPLIT","SPIN_OFF","UNKNOWN"))
def test_stage4_volatility_evaluation_rejects_non_dividend_actions(
    tmp_path,action_type
):
    environment(
        tmp_path,
        train_corporate_actions=({"action_type":action_type},),
    );build(tmp_path)
    matrix_path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    pin=json.loads(matrix_path.read_text())["matrix_sha256"]
    with pytest.raises(ValueError,match="permits CASH_DIVIDEND only"):
        evaluate_train_volatility_risk_off(
            tmp_path,admitted_train_matrix_sha256=pin
        )


def test_stage4_volatility_evaluation_rejects_unqualified_train_change(tmp_path):
    root=environment(tmp_path);build(tmp_path)
    matrix_path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    pin=json.loads(matrix_path.read_text())["matrix_sha256"]
    train=root/"clean_feature_store/train.json"
    value=json.loads(train.read_text());value["bars"][0]["close"]="999"
    train.write_bytes(canonical(value)+b"\n")
    with pytest.raises(ValueError,match="differs from qualification"):
        evaluate_train_volatility_risk_off(
            tmp_path,admitted_train_matrix_sha256=pin
        )


def test_stage4_volatility_evaluation_rejects_incomplete_fold_warmup(tmp_path):
    root=environment(tmp_path);build(tmp_path)
    matrix_path=tmp_path/"data/research/massive_campaign_v2_revision_2/stage3/technical_features/train_matrix.json"
    pin=json.loads(matrix_path.read_text())["matrix_sha256"]
    train=root/"clean_feature_store/train.json"
    value=json.loads(train.read_text())
    aapl_days=sorted(
        row["session_date"] for row in value["bars"] if row["symbol"]=="AAPL"
    )[:16]
    value["bars"]=[
        row for row in value["bars"]
        if not (row["symbol"]=="AAPL" and row["session_date"] in aapl_days)
    ]
    payload=canonical(value)+b"\n";train.write_bytes(payload)
    qualification=root/"qualification_report.json"
    report=json.loads(qualification.read_text())
    report["artifacts"]["TRAIN"]=hashlib.sha256(payload).hexdigest()
    material={key:value for key,value in report.items() if key!="qualification_sha256"}
    report["qualification_sha256"]=hashlib.sha256(canonical(material)).hexdigest()
    qualification.write_bytes(canonical(report)+b"\n")
    with pytest.raises(ValueError,match="warm-up completes"):
        evaluate_train_volatility_risk_off(
            tmp_path,admitted_train_matrix_sha256=pin
        )
