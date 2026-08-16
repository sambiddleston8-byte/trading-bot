from datetime import datetime, timezone
import json

import pytest
import hashlib
import core.orchestration.stage2_qualification as qualification_module

from core.orchestration.stage2_bounded_capture import execute_capture
from core.orchestration.stage2_qualification import HOLIDAYS, _at, _sessions, qualify, rehearse


class Response:
    status_code=200; headers={"content-type":"application/json"}
    def __init__(self,payload): self.content=payload

class Session:
    def get(self,url,**kwargs):
        params=kwargs["params"]
        if "/v2/aggs/" in url:
            symbol=url.split("/ticker/")[1].split("/")[0]; start,end=url.split("/day/")[1].split("/")[:2]
            results=[]
            for index,day in enumerate(_sessions(start,end)):
                stamp=int(datetime.fromisoformat(day+"T00:00:00+00:00").timestamp()*1000)
                base=100+index
                results.append({"o":base,"h":base+2,"l":base-1,"c":base+1,"v":100000,"t":stamp})
            return Response(json.dumps({"ticker":symbol,"adjusted":False,"status":"OK","results":results}).encode())
        symbol=params["ticker"]
        if url.endswith("/dividends"):
            rows=[{"id":"d-"+symbol,"ticker":symbol,"cash_amount":0.25,"currency":"USD","declaration_date":"2025-03-10","ex_dividend_date":"2025-03-20","pay_date":"2025-03-25"}]
        else: rows=[]
        return Response(json.dumps({"status":"OK","results":rows}).encode())


def test_calendar_contract_includes_special_closure_and_exact_counts():
    assert "2025-01-09" not in _sessions("2025-01-01","2025-01-31")
    assert len(_sessions("2024-10-01","2025-02-28"))==103
    assert len(_sessions("2025-03-01","2025-04-30"))==42
    assert _at("2025-01-10", __import__("datetime").time(16))=="2025-01-10T21:00:00+00:00"
    assert _at("2025-03-10", __import__("datetime").time(16))=="2025-03-10T20:00:00+00:00"


def test_end_to_end_qualification_admission_and_rehearsal_are_bounded(tmp_path,monkeypatch):
    documentation=b"synthetic official declaration_date semantics"
    digest=hashlib.sha256(documentation).hexdigest()
    monkeypatch.setattr(qualification_module,"DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256",digest)
    doc=tmp_path/"data/research/massive_campaign_v2_revision_2/public_documentation/blobs/sha256"/digest[:2]/f"{digest}.blob"
    doc.parent.mkdir(parents=True); doc.write_bytes(documentation)
    execute_capture(repository_root=tmp_path,api_key="synthetic-key",session=Session(),sleeper=lambda _:None)
    qualification=qualify(tmp_path)
    assert qualification["status"]=="QUALIFIED_AND_ADMITTED_TRAIN_VALIDATION"
    assert qualification["cross_symbol_synchronized"] is True
    assert qualification["corporate_action_count"]==3
    assert qualification["untouched_test_admitted"] is False
    validation=json.loads((tmp_path/"data/research/massive_campaign_v2_revision_2/stage2/clean_feature_store/validation.json").read_text())
    assert all(item["reported_at_rule"].startswith("OFFICIAL_DECLARATION_DATE") for item in validation["corporate_actions"])
    report=rehearse(tmp_path,qualification)
    assert report["authoritative_engine"].endswith("GuardrailedBacktestEngine")
    assert report["purge_observations"]==report["embargo_observations"]==1
    assert report["scenarios"]["BASE"]["baseline_slippage_bps_per_side"]=="10"
    assert report["scenarios"]["PESSIMISTIC"]["baseline_slippage_bps_per_side"]=="20"
    assert "spy_buy_hold_total_return" in report["scenarios"]["BASE"]
    assert report["performance_claim_allowed"] is False
