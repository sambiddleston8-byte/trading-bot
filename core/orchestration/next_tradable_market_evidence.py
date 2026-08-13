from __future__ import annotations

"""Immutable causal market evidence for a future replay fill engine."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, InvestmentDecisionLedger, LedgerIntegrityError, canonical_timestamp
from core.orchestration.authenticated_replay_artifact import load_authenticated_replay_artifact_bundle
from core.orchestration.replay_dataset_admission import ReplayDatasetAdmissionLedger
from core.orchestration.replay_execution_policy_ledger import ReplayExecutionPolicyLedger


SCHEMA_VERSION="1.0"; POLICY_VERSION="causal-next-tradable-market-evidence-v1"
MAX_CLOCK_SKEW=timedelta(minutes=5)
FIXED_FALSE=("costs_calculated","participation_applied","fills_generated","position_mutated","cash_mutated","performance_calculated","performance_claim_allowed","model_trained","learning_applied","network_allowed","paper_broker_submission_enabled","broker_connection_allowed","orders_submitted","live_trading_enabled")


def _json(value:Any)->str:return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def _hash(value:Mapping[str,Any])->str:return hashlib.sha256(_json(value).encode()).hexdigest()
def _text(value:Any,name:str)->str:
    if not isinstance(value,str) or not value.strip():raise ValueError(f"{name} is required")
    return value.strip()
def _time(value:Any)->datetime:return datetime.fromisoformat(canonical_timestamp(value))
def _write(fd:int,payload:bytes)->None:
    offset=0
    while offset<len(payload):
        count=os.write(fd,payload[offset:])
        if count<=0:raise OSError("Unable to append next-tradable evidence")
        offset+=count


def _duration(minutes:Any)->timedelta:
    try:value=Decimal(str(minutes))*Decimal(60_000_000)
    except (InvalidOperation,TypeError,ValueError) as error:raise ValueError("order age must be an exact duration") from error
    if not value.is_finite() or value<=0 or value!=value.to_integral_value():raise ValueError("order age must resolve to positive whole microseconds")
    return timedelta(microseconds=int(value))


def _inside(moment:datetime,interval:Mapping[str,Any])->bool:
    return _time(interval["starts_at"])<=moment<_time(interval["ends_at"])


def _final_versions(rows:list[dict[str,Any]],ticker:str)->list[dict[str,Any]]:
    chains={}
    for row in rows:
        if row["ticker"]==ticker:chains.setdefault(row["event_id"],[]).append(row)
    return [max(chain,key=lambda item:item["version"]) for chain in chains.values()]


def _active_versions_during(
    rows:list[dict[str,Any]],ticker:str,start:datetime,end:datetime
)->list[dict[str,Any]]:
    chains={}
    for row in rows:
        if row["ticker"]==ticker:chains.setdefault(row["event_id"],[]).append(row)
    active=[]
    for chain in chains.values():
        ordered=sorted(chain,key=lambda item:item["version"])
        for index,row in enumerate(ordered):
            if row["record_status"]!="ACTIVE":continue
            known_from=_time(row["available_at"])
            known_until=(
                _time(ordered[index+1]["available_at"])
                if index+1<len(ordered) else datetime.max.replace(tzinfo=timezone.utc)
            )
            if known_from<=end and known_until>start:active.append(row)
    return active


def _result(bundle:Mapping[str,Any],ticker:str,decision_at:datetime,through:datetime)->dict[str,Any]:
    if ticker not in bundle["tickers"]:raise ValueError("decision ticker is absent from the replay bundle")
    coverage_start=_time(bundle["covers_from_at"]);coverage_end=_time(bundle["through_at"])
    if not coverage_start<=decision_at<coverage_end:raise ValueError("decision instant must fit inside bundle coverage")
    if through>=coverage_end:
        return {"terminal_status":"WINDOW_INCOMPLETE_CANNOT_DETERMINE","reason_code":"EVIDENCE_WINDOW_EXCEEDS_BUNDLE_COVERAGE","matching_continuity_events":[],"selected_observation":None}
    prices=bundle["artifacts"]["TOTAL_RETURN_PRICES"]["rows"]
    calendars=[row for row in bundle["artifacts"]["MARKET_CALENDARS_AND_HALTS"]["rows"] if row["ticker"]==ticker]
    corporate=_active_versions_during(
        bundle["artifacts"]["CORPORATE_ACTIONS"]["rows"],ticker,decision_at,through
    )
    delisting=bundle["artifacts"]["DELISTING_OUTCOMES"]
    state=delisting["resolved_ticker_states"][ticker]
    if state["actual_state_ambiguous"]:raise ValueError("ambiguous terminal state cannot support execution evidence")
    initial=state["initial_state"]
    terminal=state["canonical_terminal_outcome"]
    if initial["state"]=="TERMINAL" or (terminal and _time(terminal["effective_at"])<=decision_at):raise ValueError("instrument is terminal at the decision instant")
    known_actions=[
        row for row in corporate
        if (
            decision_at<=_time(row["effective_at"])<=through
            or (_time(row["effective_at"])<decision_at<_time(row["available_at"])<=through)
        )
    ]
    physical_actions=[
        row for row in _final_versions(
            bundle["artifacts"]["CORPORATE_ACTIONS"]["rows"],ticker
        )
        if row["record_status"]=="ACTIVE"
        and decision_at<=_time(row["effective_at"])<=through
    ]
    known_terminals=[
        row for row in _active_versions_during(delisting["rows"],ticker,decision_at,through)
        if decision_at<_time(row["effective_at"])<=through
    ]
    physical_terminals=[
        row for row in _final_versions(delisting["rows"],ticker)
        if row["record_status"]=="ACTIVE"
        and decision_at<_time(row["effective_at"])<=through
    ]
    tagged_actions=[{**row,"artifact_role":"CORPORATE_ACTIONS"} for row in known_actions+physical_actions]
    tagged_terminals=[{**row,"artifact_role":"DELISTING_OUTCOMES"} for row in known_terminals+physical_terminals]
    def unique_events(values:list[dict[str,Any]])->list[dict[str,Any]]:
        unique={
            (row["artifact_role"],row["ticker"],row["event_id"],row["version"]):row
            for row in values
        }
        return list(unique.values())
    events=sorted(
        unique_events(tagged_actions+tagged_terminals),
        key=lambda item:(item["effective_at"],item["available_at"],item["artifact_role"],item["event_id"],item["version"]),
    )
    if events:
        kinds={"corporate" if row["artifact_role"]=="CORPORATE_ACTIONS" else "terminal" for row in events}
        reason="CONTINUITY_AND_TERMINAL_EVENTS_IN_WINDOW" if len(kinds)==2 else ("CORPORATE_ACTION_IN_WINDOW" if "corporate" in kinds else "TERMINAL_OUTCOME_IN_WINDOW")
        return {"terminal_status":"WINDOW_INCOMPLETE_CANNOT_DETERMINE","reason_code":reason,"matching_continuity_events":events,"selected_observation":None}
    relevant_calendar=[
        row for row in calendars
        if _time(row["ends_at"])>decision_at and _time(row["starts_at"])<=through
    ]
    if any(_time(row["available_at"])>through for row in relevant_calendar):
        return {"terminal_status":"WINDOW_INCOMPLETE_CANNOT_DETERMINE","reason_code":"CALENDAR_STATE_NOT_KNOWN_WITHIN_WINDOW","matching_continuity_events":[],"selected_observation":None}
    open_intervals=[row for row in relevant_calendar if row["status"]=="OPEN"]
    eligible=[]
    for row in prices:
        if row["ticker"]!=ticker:continue
        quote=_time(row["quote_observed_at"]);trade=_time(row["trade_observed_at"]);available=_time(row["available_at"])
        if not (decision_at<quote<=through and decision_at<trade<=through and decision_at<available<=through):continue
        if quote!=trade:continue
        if not any(
            _inside(quote,interval) and _inside(trade,interval)
            and _time(interval["available_at"])<=available
            for interval in open_intervals
        ):continue
        if not (Decimal(row["bid"])>0 and Decimal(row["bid"])<=Decimal(row["last"])<=Decimal(row["ask"]) and Decimal(row["volume"])>0):continue
        eligible.append(row)
    if eligible:
        selected=min(eligible,key=lambda item:(item["trade_observed_at"],item["quote_observed_at"],item["available_at"],item["source_row_locator"]))
        return {"terminal_status":"ELIGIBLE_OBSERVATION_FOUND","reason_code":"FIRST_ELIGIBLE_NEXT_TRADABLE_OBSERVATION","matching_continuity_events":[],"selected_observation":selected}
    if not open_intervals:return {"terminal_status":"NO_ELIGIBLE_OBSERVATION_WITHIN_MAX_ORDER_AGE","reason_code":"MARKET_NEVER_OPEN","matching_continuity_events":[],"selected_observation":None}
    return {"terminal_status":"WINDOW_INCOMPLETE_CANNOT_DETERMINE","reason_code":"NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET","matching_continuity_events":[],"selected_observation":None}


class NextTradableMarketEvidenceLedger:
    def __init__(self,path:str|Path,*,admission_ledger:ReplayDatasetAdmissionLedger,policy_ledger:ReplayExecutionPolicyLedger,decision_ledger:InvestmentDecisionLedger,content_ledger:AuthenticatedSourceContentLedger)->None:
        self.path=Path(path);self.admission_ledger=admission_ledger;self.policy_ledger=policy_ledger;self.decision_ledger=decision_ledger;self.content_ledger=content_ledger

    def records(self)->list[dict[str,Any]]:
        if not self.path.exists():return []
        raw=self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):raise LedgerIntegrityError("Next-tradable evidence ledger has an incomplete final line.")
        records=[]
        with self.path.open(encoding="utf-8") as source:
            for number,line in enumerate(source,start=1):
                if not line.strip():raise LedgerIntegrityError(f"Blank evidence line at {number}.")
                try:value=json.loads(line)
                except json.JSONDecodeError as error:raise LedgerIntegrityError(f"Invalid evidence JSON at {number}.") from error
                if not isinstance(value,dict):raise LedgerIntegrityError(f"Evidence line {number} is not an object.")
                records.append(value)
        return records

    def _derive(self,*,admission_id:Any,policy_record_id:Any,decision_id:Any)->dict[str,Any]:
        admission=next((item for item in self.admission_ledger.verify() if item.get("admission_id")==_text(admission_id,"admission_id")),None)
        policy=next((item for item in self.policy_ledger.verify() if item.get("replay_execution_policy_record_id")==_text(policy_record_id,"policy_record_id")),None)
        decision=next((item for item in self.decision_ledger.verify() if item.get("decision_id")==_text(decision_id,"decision_id")),None)
        if not admission or not policy or not decision:raise ValueError("verified admission, policy, and decision are required")
        if admission["replay_plan_id"]!=policy["replay_plan_id"]:raise ValueError("admission and execution policy must bind the same plan")
        plan=next((item for item in self.policy_ledger.plan_ledger.verify() if item.get("replay_plan_id")==policy["replay_plan_id"]),None)
        if plan is None:raise ValueError("verified replay plan is required")
        side=decision.get("decision")
        if side not in {"BUY","SELL"}:raise ValueError("only BUY or SELL decisions can create execution evidence")
        decided=_time(decision["decided_at"])
        data_as_of=_time(decision["data_as_of"])
        if data_as_of>decided:raise ValueError("decision data_as_of cannot be later than decided_at")
        if not _time(plan["evaluation_not_before"])<=decided<_time(plan["evaluation_not_after"]):raise ValueError("decision lies outside preregistered evaluation")
        bundle=load_authenticated_replay_artifact_bundle(admission_ledger=self.admission_ledger,content_ledger=self.content_ledger,admission_id=admission["admission_id"])
        scenario_results={}
        for scenario in ("BASE","PESSIMISTIC"):
            through=decided+_duration(policy["scenarios"][scenario]["maximum_order_age_minutes"])
            scenario_results[scenario]={"through_at":through.isoformat(),**_result(bundle,decision["ticker"],decided,through)}
        artifact_pins={role:{field:item[field] for field in ("content_evidence_id","content_record_hash","source_input_sha256")} for role,item in bundle["artifacts"].items()}
        identity={"admission_id":admission["admission_id"],"admission_record_hash":admission["record_hash"],"replay_plan_id":plan["replay_plan_id"],"replay_plan_record_hash":plan["record_hash"],"dataset_commitment_sha256":admission["dataset_commitment_sha256"],"replay_execution_policy_record_id":policy["replay_execution_policy_record_id"],"replay_execution_policy_record_hash":policy["record_hash"],"execution_policy_id":policy["execution_policy_id"],"decision_id":decision["decision_id"],"decision_record_hash":decision["record_hash"],"ticker":decision["ticker"],"side":side,"decided_at":decided.isoformat(),"data_as_of":data_as_of.isoformat(),"scenario_results":scenario_results,"artifact_pins":artifact_pins,"provider_completeness_attested_not_independently_proven":bundle["provider_completeness_attested_not_independently_proven"],"plan_coverage_adequacy_proven":bundle["plan_coverage_adequacy_proven"]}
        return {**identity,"market_evidence_id":"NTE-"+hashlib.sha256(_json([identity,POLICY_VERSION]).encode()).hexdigest()[:32].upper()}

    def append(self,*,admission_id:str,replay_execution_policy_record_id:str,decision_id:str,assessed_by:str,assessed_at:str|datetime|None=None,allow_existing:bool=True)->dict[str,Any]:
        assessed=_time(assessed_at or datetime.now(timezone.utc));now=datetime.now(timezone.utc)
        if not now-MAX_CLOCK_SKEW<=assessed<=now+MAX_CLOCK_SKEW:raise ValueError("assessed_at must match actual append time")
        derived=self._derive(admission_id=admission_id,policy_record_id=replay_execution_policy_record_id,decision_id=decision_id)
        record={"schema_version":SCHEMA_VERSION,"policy_version":POLICY_VERSION,"record_type":"IMMUTABLE_NEXT_TRADABLE_MARKET_EVIDENCE","status":"EVIDENCE_ONLY_NOT_EXECUTED","assessed_at":assessed.isoformat(),"assessed_by":_text(assessed_by,"assessed_by"),**derived,"point_in_time_evidence_required":True,**{field:False for field in FIXED_FALSE}}
        return self._append(record,allow_existing=allow_existing)

    def verify(self)->list[dict[str,Any]]:
        previous=GENESIS_HASH;seen=set();keys=set();records=self.records()
        for index,record in enumerate(records,start=1):
            material={k:v for k,v in record.items() if k!="record_hash"}
            if record.get("previous_hash")!=previous or record.get("record_hash")!=_hash(material):raise LedgerIntegrityError(f"Next-tradable evidence {index} was modified.")
            try:derived=self._derive(admission_id=record.get("admission_id"),policy_record_id=record.get("replay_execution_policy_record_id"),decision_id=record.get("decision_id"));assessed=_time(record.get("assessed_at"));_text(record.get("assessed_by"),"assessed_by")
            except (KeyError,OverflowError,TypeError,ValueError) as error:raise LedgerIntegrityError(f"Next-tradable evidence {index} is invalid.") from error
            key=(derived["admission_id"],derived["replay_execution_policy_record_id"],derived["decision_id"])
            boundary=(record.get("schema_version")==SCHEMA_VERSION and record.get("policy_version")==POLICY_VERSION and record.get("record_type")=="IMMUTABLE_NEXT_TRADABLE_MARKET_EVIDENCE" and record.get("status")=="EVIDENCE_ONLY_NOT_EXECUTED" and all(record.get(k)==v for k,v in derived.items()) and derived["market_evidence_id"] not in seen and key not in keys and assessed<=datetime.now(timezone.utc)+MAX_CLOCK_SKEW and record.get("point_in_time_evidence_required") is True and all(record.get(field) is False for field in FIXED_FALSE))
            if not boundary:raise LedgerIntegrityError(f"Next-tradable evidence {index} violates its boundary.")
            seen.add(derived["market_evidence_id"]);keys.add(key);previous=record["record_hash"]
        return records

    def _append(self,record:dict[str,Any],*,allow_existing:bool)->dict[str,Any]:
        self.path.parent.mkdir(parents=True,exist_ok=True);lock=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX);records=self.verify();existing=next((item for item in records if item["market_evidence_id"]==record["market_evidence_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash","assessed_at"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in record.items() if k not in ignored}:return existing
                raise LedgerIntegrityError("Next-tradable evidence already exists with conflicting content.")
            material={**record,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH};result={**material,"record_hash":_hash(material)};target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:_write(target,(_json(result)+"\n").encode());os.fsync(target)
            finally:os.close(target)
            return result
        finally:fcntl.flock(lock,fcntl.LOCK_UN);os.close(lock)
