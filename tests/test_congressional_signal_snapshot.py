import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.political import CongressionalActivitySignalLedger

class Stub:
    def __init__(self,values):self.values=list(values)
    def verify(self):return self.values

def disclosure(identifier,politician,tx,low,high,public,observed,delay=20):
    return {"disclosure_id":identifier,"record_hash":"hash-"+identifier,"ticker":"EXM","politician_name":politician,"transaction_type":tx,"amount_min_usd":str(low),"amount_max_usd":str(high),"source":"OFFICIAL_HOUSE","historical_point_in_time_signal_at":public,"live_system_signal_at":observed,"transaction_to_public_delay_days":delay}
VALUES=[disclosure("D1","A","PURCHASE",1001,15000,"2026-01-21T09:00:00+00:00","2026-01-21T10:00:00+00:00"),disclosure("D2","A","PURCHASE",15001,50000,"2026-02-01T09:00:00+00:00","2026-02-01T10:00:00+00:00",30),disclosure("D3","B","SALE",1001,15000,"2026-02-02T09:00:00+00:00","2026-02-02T10:00:00+00:00",10),disclosure("FUTURE","C","SALE",50000,100000,"2026-04-01T09:00:00+00:00","2026-04-01T10:00:00+00:00")]
BASE={"ticker":"EXM","as_of":"2026-03-01T00:00:00+00:00","availability_mode":"HISTORICAL_PUBLIC_EVIDENCED","generated_by":"test","generated_at":"2026-03-02T00:00:00+00:00"}

def ledger(tmp_path):return CongressionalActivitySignalLedger(tmp_path/"signals.jsonl",Stub(VALUES))
def aggregate(target,**changes):values=dict(BASE);values.update(changes);return target.aggregate(**values)
def rewrite(path,**changes):
    from core.political import signal_snapshot as module
    value=json.loads(path.read_text());value.update(changes);material={k:v for k,v in value.items() if k!="record_hash"};value["record_hash"]=module._record_hash(material);path.write_text(json.dumps(value)+"\n")

def test_aggregates_only_point_in_time_evidence_conservatively(tmp_path):
    target=ledger(tmp_path);result=aggregate(target)
    assert result["disclosure_ids"]==["D1","D2","D3"] and result["purchase_count"]==2 and result["sale_count"]==1 and result["repeat_purchasers"]==1
    assert result["net_activity_lower_bound_usd"]=="1002" and result["net_activity_upper_bound_usd"]=="63999" and result["activity_direction"]=="POSITIVE_RANGE"
    assert result["mean_disclosure_delay_days"]=="20" and result["previous_hash"]==GENESIS_HASH
    for field in ("automatic_recommendation","standalone_investment_decision_allowed","copy_trade_allowed","broker_connection_allowed","order_submitted","live_trading_enabled"):assert result[field] is False
    assert target.verify()==[result]

def test_live_mode_uses_actual_system_observation(tmp_path):
    result=aggregate(ledger(tmp_path),availability_mode="LIVE_SYSTEM_OBSERVED",as_of="2026-02-02T09:30:00+00:00",generated_at="2026-02-03T00:00:00+00:00")
    assert result["disclosure_ids"]==["D1","D2"] and result["availability_field"]=="live_system_signal_at"

@pytest.mark.parametrize("field,value,fragment",[("ticker","UNKNOWN","No verified"),("availability_mode","TRANSACTION_DATE","not supported"),("as_of","2026-04-01T00:00:00+00:00","postdate")])
def test_invalid_snapshot_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment):aggregate(ledger(tmp_path),**{field:value})

@pytest.mark.parametrize("changes",[{"disclosure_ids":["D1","FUTURE"]},{"purchase_count":99},{"net_activity_lower_bound_usd":"999"},{"activity_direction":"NEGATIVE_RANGE"},{"mean_disclosure_delay_days":"0"},{"committee_relevance_available":True},{"automatic_recommendation":True},{"standalone_investment_decision_allowed":True},{"copy_trade_allowed":True},{"order_submitted":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=ledger(tmp_path);aggregate(target);rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError):target.verify()
