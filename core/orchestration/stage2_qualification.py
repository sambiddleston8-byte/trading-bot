"""Qualify captured TRAIN/VALIDATION bytes and rehearse the authoritative engine."""
from __future__ import annotations
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib, json, os, stat
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.guardrailed_backtest import (BacktestConfig, CorporateAction, ExchangeFeeSchedule,
    ExchangeFeeTier, GuardrailedBacktestEngine, MarketBar, ResearchExemptionDataAttestation,
    UniverseEvent)
from core.orchestration.massive_historical_adapter import parse_massive_unadjusted_daily_bars
from core.orchestration.stage2_bounded_capture import request_plan
from core.research.conservative_baseline_strategy import ConservativeBaselineStrategy, conservative_baseline_parameters
from core.research.conservative_baseline_campaign_v2_proposal import PARENT_RESEARCH_EXEMPTION_ID, PARENT_RESEARCH_EXEMPTION_RECORD_HASH

NY = ZoneInfo("America/New_York")
SYMBOLS = ("AAPL", "MSFT", "SPY")
HOLIDAYS = frozenset(map(date.fromisoformat, ("2024-11-28","2024-12-25","2025-01-01","2025-01-09","2025-01-20","2025-02-17","2025-04-18")))
EARLY_CLOSES = frozenset(map(date.fromisoformat, ("2024-11-29","2024-12-24")))
ROOT = Path("data/research/massive_campaign_v2_revision_2/stage2")
CLEAN = ROOT / "clean_feature_store"
DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256 = "f56d8d62b9180162861d2111fe0aac3865e986f26c87bd48d5149557cead6893"

def _canonical(v: Any) -> bytes: return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def _sessions(start: str, end: str) -> list[str]:
    cursor, finish = date.fromisoformat(start), date.fromisoformat(end); out=[]
    while cursor <= finish:
        if cursor.weekday() < 5 and cursor not in HOLIDAYS: out.append(cursor.isoformat())
        cursor = cursor.fromordinal(cursor.toordinal()+1)
    return out
def _at(day: str, value: time) -> str: return datetime.combine(date.fromisoformat(day), value, NY).astimezone(timezone.utc).isoformat()
def _bar_close(day: str) -> time: return time(13) if date.fromisoformat(day) in EARLY_CLOSES else time(16)
def _bar_available(day: str) -> str:
    closed=datetime.combine(date.fromisoformat(day),_bar_close(day),NY)
    return (closed.replace(second=0)+__import__("datetime").timedelta(minutes=1)).astimezone(timezone.utc).isoformat()
def _write_private(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700); path.parent.chmod(0o700)
    payload=_canonical(value)+b"\n"; digest=hashlib.sha256(payload).hexdigest()
    if path.exists():
        if path.read_bytes()!=payload: raise ValueError("clean feature artifact conflicts")
        return digest
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
    try:
        n=os.write(fd,payload)
        if n!=len(payload): raise OSError("short clean-store write")
        os.fsync(fd)
    finally: os.close(fd)
    return digest

def qualify(repository_root: Path) -> dict[str, Any]:
    root=repository_root/ROOT; q=root/"quarantine"
    manifest_bytes=(q/"captures.jsonl").read_bytes()
    captures=[json.loads(x) for x in manifest_bytes.splitlines()]
    if len(captures)!=27: raise ValueError("capture manifest is incomplete")
    identity_fields=("dataset","symbol","role","start","end","path")
    if {tuple(x[k] for k in identity_fields) for x in captures}!={tuple(x[k] for k in identity_fields) for x in request_plan()}: raise ValueError("capture manifest differs from the exact authorized request plan")
    bars={s:[] for s in SYMBOLS}; actions=[]
    for capture in captures:
        blob=q/f"{capture['payload_sha256']}.json"; raw=blob.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=capture["payload_sha256"]: raise ValueError("quarantine hash failed")
        if capture["dataset"]=="DAILY_BARS":
            for item in parse_massive_unadjusted_daily_bars(raw):
                payload=item["payload"]; day=item["window_start"].date().isoformat()
                if payload["ticker"] != capture["symbol"]: raise ValueError("bar payload ticker differs from capture manifest")
                bars[capture["symbol"]].append({"symbol":capture["symbol"],"session_date":day,
                    "open_at":_at(day,time(9,30)),"close_at":_at(day,_bar_close(day)),"available_at":_bar_available(day),
                    **{k:str(payload[k]) for k in ("open","high","low","close","volume")},
                    "source_payload_sha256":capture["payload_sha256"]})
        elif capture["dataset"]=="DIVIDENDS":
            for row in json.loads(raw)["results"]:
                announced=row.get("declaration_date")
                if not announced: raise ValueError("dividend lacks official declaration_date")
                if row.get("currency") != "USD": raise ValueError("dividend currency is not USD")
                if row["id"] in {x["source_id"] for x in actions}: raise ValueError("corporate action identity is duplicated")
                actions.append({"symbol":row["ticker"],"action_type":"CASH_DIVIDEND","source_id":row["id"],
                    "effective_date":row["ex_dividend_date"],"reported_at":_at(announced,time(16)),
                    "reported_at_rule":"OFFICIAL_DECLARATION_DATE_16_00_AMERICA_NEW_YORK",
                    "cash_per_share":str(row["cash_amount"]),"pay_date":row["pay_date"],
                    "source_payload_sha256":capture["payload_sha256"]})
        else:
            for row in json.loads(raw)["results"]:
                announced=row.get("announcement_date")
                if not announced: raise ValueError("split lacks a documented official announcement date")
                if row["id"] in {x["source_id"] for x in actions}: raise ValueError("corporate action identity is duplicated")
                actions.append({"symbol":row["ticker"],"action_type":"SPLIT","source_id":row["id"],
                    "effective_date":row["execution_date"],"reported_at":_at(announced,time(16)),
                    "reported_at_rule":"OFFICIAL_ANNOUNCEMENT_DATE_16_00_AMERICA_NEW_YORK",
                    "split_ratio":str(Decimal(str(row["split_to"]))/Decimal(str(row["split_from"]))),
                    "source_payload_sha256":capture["payload_sha256"]})
    expected={"TRAIN":_sessions("2024-10-01","2025-02-28"),"VALIDATION":_sessions("2025-03-01","2025-04-30")}
    checks={}; artifacts={}
    for symbol, rows in bars.items():
        rows.sort(key=lambda x:x["session_date"]); dates=[x["session_date"] for x in rows]
        if dates != expected["TRAIN"]+expected["VALIDATION"]: raise ValueError(f"{symbol} session calendar is incomplete")
        if any(min(Decimal(x[k]) for k in ("open","high","low","close"))<=0 or Decimal(x["low"])>min(Decimal(x["open"]),Decimal(x["close"])) or Decimal(x["high"])<max(Decimal(x["open"]),Decimal(x["close"])) or Decimal(x["volume"])<0 for x in rows): raise ValueError("OHLCV consistency failed")
        checks[symbol]={"train_sessions":len(expected["TRAIN"]),"validation_sessions":len(expected["VALIDATION"]),"ohlcv_consistent":True}
    if any([x["session_date"] for x in bars[s]] != [x["session_date"] for x in bars["SPY"]] for s in SYMBOLS): raise ValueError("cross-symbol synchronization failed")
    all_dates=set(expected["TRAIN"]+expected["VALIDATION"])
    if any(x["effective_date"] not in all_dates for x in actions): raise ValueError("corporate action effective date is outside an admitted session")
    split_captures=[x for x in captures if x["dataset"]=="STOCK_SPLITS"]
    if {(x["symbol"],x["start"],x["end"]) for x in split_captures}!={(s,"2024-10-01","2025-04-30") for s in SYMBOLS}: raise ValueError("split capture coverage is incomplete")
    documentation=repository_root/"data/research/massive_campaign_v2_revision_2/public_documentation/blobs/sha256"/DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256[:2]/f"{DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256}.blob"
    if not documentation.exists() or hashlib.sha256(documentation.read_bytes()).hexdigest()!=DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256: raise ValueError("registered dividend documentation bytes are unavailable")
    for role, dates in expected.items():
        content={"schema_version":"1.0","role":role,"bars":[x for s in SYMBOLS for x in bars[s] if x["session_date"] in dates],"corporate_actions":[x for x in actions if x["effective_date"] in dates],"quarantine_only":False,"clean_feature_store":True}
        artifacts[role]=_write_private(repository_root/CLEAN/f"{role.lower()}.json",content)
    report={"status":"QUALIFIED_AND_ADMITTED_TRAIN_VALIDATION","capture_manifest_sha256":hashlib.sha256(manifest_bytes).hexdigest(),"calendar":"XNYS_EXPLICIT_2024_10_TO_2025_04_WITH_2025_01_09_CLOSURE","checks":checks,"cross_symbol_synchronized":True,"corporate_action_count":len(actions),"split_count":sum(x["action_type"]=="SPLIT" for x in actions),"pit_rule":"16:00 America/New_York on documented official announcement date","dividend_announcement_semantics_documentation_sha256":DIVIDEND_DOCUMENTATION_PAYLOAD_SHA256,"split_announcement_semantics":"NO_CAPTURED_SPLITS_RULE_VACUOUS_FUTURE_MISSING_ANNOUNCEMENT_DATE_FAILS_CLOSED","artifacts":artifacts,"dataset_admitted":True,"untouched_test_admitted":False,"performance_claim_allowed":False}
    report["qualification_sha256"]=hashlib.sha256(_canonical(report)).hexdigest()
    _write_private(repository_root/ROOT/"qualification_report.json",report)
    return report

def rehearse(repository_root: Path, qualification: dict[str,Any]) -> dict[str,Any]:
    train_path,validation_path=repository_root/CLEAN/"train.json",repository_root/CLEAN/"validation.json"
    if hashlib.sha256(train_path.read_bytes()).hexdigest()!=qualification["artifacts"]["TRAIN"] or hashlib.sha256(validation_path.read_bytes()).hexdigest()!=qualification["artifacts"]["VALIDATION"]: raise ValueError("clean feature artifacts do not match qualification")
    train=json.loads(train_path.read_text()); validation=json.loads(validation_path.read_text())
    results={}
    for scenario in ("BASE","PESSIMISTIC"):
        returns={}
        for symbol in SYMBOLS:
            warm=[x for x in train["bars"] if x["symbol"]==symbol][:-1][-50:]
            val=[x for x in validation["bars"] if x["symbol"]==symbol][1:]
            source=warm+val
            market=[MarketBar(symbol=x["symbol"],open_at=datetime.fromisoformat(x["open_at"]),close_at=datetime.fromisoformat(x["close_at"]),available_at=datetime.fromisoformat(x["available_at"]),open=Decimal(x["open"]),high=Decimal(x["high"]),low=Decimal(x["low"]),close=Decimal(x["close"]),volume=Decimal(x["volume"])) for x in source]
            corporate=[]
            for action in train["corporate_actions"]+validation["corporate_actions"]:
                if action["symbol"] != symbol: continue
                effective=datetime.combine(date.fromisoformat(action["effective_date"]),time(9,30),NY).astimezone(timezone.utc)
                if action["action_type"]=="CASH_DIVIDEND":
                    corporate.append(CorporateAction(symbol,"CASH_DIVIDEND",effective,datetime.fromisoformat(action["reported_at"]),action["source_id"],cash_per_share=Decimal(action["cash_per_share"]),cash_paid_at=datetime.combine(date.fromisoformat(action["pay_date"]),time(16),NY).astimezone(timezone.utc)))
                else:
                    corporate.append(CorporateAction(symbol,"SPLIT",effective,datetime.fromisoformat(action["reported_at"]),action["source_id"],split_ratio=Decimal(action["split_ratio"])))
            digest=hashlib.sha256(_canonical(source)).hexdigest()
            att=ResearchExemptionDataAttestation._from_explicit_research_exemption(source_id=f"RESEARCH_EXEMPTION:{symbol}:VALIDATION_REHEARSAL",source_content_sha256=digest,validation_receipt_sha256=qualification["qualification_sha256"],derivation_policy_version="stage3-validation-conformance-rehearsal-v1",evidence_role_hashes=(("ASSUMED_CAPTURE_MANIFEST",qualification["capture_manifest_sha256"]),("ASSUMED_CORPORATE_ACTION_PIT",qualification["artifacts"]["VALIDATION"]),("ASSUMED_TRAIN_WARMUP",qualification["artifacts"]["TRAIN"]),("ASSUMED_VALIDATION_BARS",digest)),exemption_id=PARENT_RESEARCH_EXEMPTION_ID,exemption_record_sha256=PARENT_RESEARCH_EXEMPTION_RECORD_HASH)
            config=BacktestConfig(initial_cash=Decimal("100000"),execution_scenario=scenario,baseline_slippage_bps=Decimal("10") if scenario=="BASE" else Decimal("20"),bid_ask_half_spread_bps=Decimal("5") if scenario=="BASE" else Decimal("10"),liquidity_impact_bps_at_max_participation=Decimal("10") if scenario=="BASE" else Decimal("20"),stop_pierce_fill_fraction=Decimal("0.5") if scenario=="BASE" else Decimal("1"))
            engine=GuardrailedBacktestEngine(config=config,fee_schedule=ExchangeFeeSchedule("REHEARSAL-FEES-v1",(ExchangeFeeTier(None,Decimal("1"),Decimal("0.01")),)),data_attestation=att)
            result=engine.run(bars=market,universe_events=[UniverseEvent(symbol,"ADD",market[0].open_at,market[0].open_at, "fixed-campaign-basket")],terminal_outcomes=[],corporate_actions=corporate,prices_are_unadjusted=True,strategy=ConservativeBaselineStrategy(),parameters=conservative_baseline_parameters(),evaluation_start=market[50].open_at,evaluation_end=market[-1].close_at)
            returns[symbol]=str(result.total_return)
        spy=[x for x in validation["bars"] if x["symbol"]=="SPY"][1:]
        spy_dividends=sum((Decimal(x["cash_per_share"]) for x in validation["corporate_actions"] if x["symbol"]=="SPY" and x["action_type"]=="CASH_DIVIDEND"),Decimal("0"))
        spy_market_return=(Decimal(spy[-1]["close"])+spy_dividends)/Decimal(spy[0]["open"])-Decimal("1")
        results[scenario]={"strategy_returns":returns,"spy_buy_hold_total_return":str(spy_market_return),"strategy_excess_return_vs_spy":{s:str(Decimal(returns[s])-spy_market_return) for s in ("AAPL","MSFT")},"baseline_slippage_bps_per_side":str(config.baseline_slippage_bps),"half_spread_bps_per_side":str(config.bid_ask_half_spread_bps),"liquidity_impact_bps_at_max_participation":str(config.liquidity_impact_bps_at_max_participation)}
    purge=1; embargo=1; warmup=len(warm); scored=len(val)
    report={"status":"VALIDATION_CONFORMANCE_REHEARSAL_COMPLETE","purge_observations":purge,"embargo_observations":embargo,"warmup_observations":warmup,"validation_scored_sessions":scored,"authoritative_engine":"core.guardrailed_backtest:GuardrailedBacktestEngine","scenarios":results,"performance_claim_allowed":False,"promotion_allowed":False}
    _write_private(repository_root/ROOT/"stage3_validation_rehearsal.json",report); return report
