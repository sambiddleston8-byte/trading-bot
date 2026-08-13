from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.political import CongressionalTradeDisclosureLedger

BASE={"chamber":"HOUSE","politician_name":"Example Member","source":"OFFICIAL_HOUSE","source_record_id":"PTR-1-LINE-1","source_url":"https://disclosures-clerk.house.gov/example.pdf","source_access_basis":"OFFICIAL_USE_LEGAL_REVIEWED","source_terms_review_reference":"LEGAL-REVIEW-1","raw_document_sha256":"a"*64,"availability_evidence_sha256":"b"*64,"transaction_date":"2026-01-01","notification_date":"2026-01-02","filed_at":"2026-01-20T12:00:00+00:00","source_first_publicly_available_at":"2026-01-21T09:00:00+00:00","system_observed_at":"2026-01-21T10:00:00+00:00","owner":"SPOUSE","asset_name":"Example Corporation Common Stock","ticker":"EXM","transaction_type":"PURCHASE","amount_min_usd":"1001","amount_max_usd":"15000","recorded_by":"synthetic-test","recorded_at":"2026-01-21T11:00:00+00:00"}

def append(target,**changes): values=dict(BASE); values.update(changes); return target.append(**values)
def rewrite(path,**changes):
    from core.political import disclosure_ledger as module
    value=json.loads(path.read_text()); value.update(changes); material={k:v for k,v in value.items() if k!="record_hash"}; value["record_hash"]=module._record_hash(material); path.write_text(json.dumps(value)+"\n")

def test_records_point_in_time_evidence_not_copy_trade(tmp_path):
    target=CongressionalTradeDisclosureLedger(tmp_path/"trades.jsonl"); result=append(target)
    assert result["historical_point_in_time_signal_at"]=="2026-01-21T09:00:00+00:00" and result["live_system_signal_at"]=="2026-01-21T10:00:00+00:00"
    assert result["transaction_to_public_delay_days"]==20 and result["exact_trade_amount_known"] is False and result["previous_hash"]==GENESIS_HASH
    for field in ("automatic_trade_signal","copy_trade_allowed","broker_connection_allowed","order_submitted","live_trading_enabled"): assert result[field] is False
    assert target.verify()==[result]

def test_capitol_trades_requires_licensed_feed(tmp_path):
    target=CongressionalTradeDisclosureLedger(tmp_path/"trades.jsonl")
    with pytest.raises(ValueError,match="licensed feed"): append(target,source="CAPITOL_TRADES",source_access_basis="MANUAL_RESEARCH_REVIEWED")
    result=append(target,source="CAPITOL_TRADES",source_access_basis="LICENSED_FEED")
    assert result["source_access_basis"]=="LICENSED_FEED"

@pytest.mark.parametrize("field,value,fragment",[("source_access_basis","UNKNOWN","not supported"),("source_url","http://example.com","HTTPS"),("filed_at","2025-12-01T00:00:00+00:00","predate"),("notification_date","2025-12-01","predate"),("notification_date","2026-01-21","predate notification"),("source_first_publicly_available_at","2026-01-19T00:00:00+00:00","predate filing"),("system_observed_at","2026-01-21T08:00:00+00:00","predate public"),("amount_min_usd","nan","finite"),("amount_max_usd","1000","below"),("ticker","BAD TICKER","invalid")])
def test_invalid_disclosure_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment): append(CongressionalTradeDisclosureLedger(tmp_path/"x.jsonl"),**{field:value})

def test_concurrent_retry_appends_once(tmp_path):
    target=CongressionalTradeDisclosureLedger(tmp_path/"x.jsonl")
    with ThreadPoolExecutor(max_workers=2) as pool: first,second=list(pool.map(lambda _:append(target),range(2)))
    assert first==second and len(target.verify())==1

def test_changed_source_record_requires_explicit_linear_supersession(tmp_path):
    target=CongressionalTradeDisclosureLedger(tmp_path/"x.jsonl"); first=append(target)
    with pytest.raises(LedgerIntegrityError,match="explicitly supersede"):
        append(target,amount_max_usd="50000")
    second=append(target,amount_max_usd="50000",supersedes_disclosure_id=first["disclosure_id"])
    assert second["supersedes_disclosure_id"]==first["disclosure_id"]
    with pytest.raises(LedgerIntegrityError,match="current version"):
        append(target,amount_max_usd="100000",supersedes_disclosure_id=first["disclosure_id"])
    assert target.verify()==[first,second]

def test_explicit_cross_source_link_can_replace_same_transaction(tmp_path):
    target=CongressionalTradeDisclosureLedger(tmp_path/"x.jsonl"); first=append(target)
    second=append(target,source="LICENSED_PROVIDER",source_record_id="LP-99",source_access_basis="LICENSED_FEED",amount_max_usd="50000",supersedes_disclosure_id=first["disclosure_id"])
    assert second["source"]=="LICENSED_PROVIDER"

@pytest.mark.parametrize("changes",[{"historical_point_in_time_signal_at":"2026-01-01T00:00:00+00:00"},{"transaction_to_public_delay_days":0},{"amount_max_usd":"1000000"},{"exact_trade_amount_known":True},{"automatic_trade_signal":True},{"copy_trade_allowed":True},{"broker_connection_allowed":True},{"order_submitted":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=CongressionalTradeDisclosureLedger(tmp_path/"x.jsonl"); append(target); rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
