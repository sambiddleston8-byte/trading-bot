from __future__ import annotations

"""Immutable evaluation of every preregistered robustness slice."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.robustness_plan import DIMENSIONS, RobustnessTestPlanLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all

SCHEMA_VERSION="1.0";POLICY_VERSION="complete-preregistered-robustness-result-v1";MAX_CLOCK_SKEW=timedelta(minutes=5);_SHA256=re.compile(r"^[0-9a-f]{64}$")

def _required(value:Any,name:str,maximum:int=300)->str:
    result=str(value or "").strip()
    if not result:raise ValueError(f"{name} is required")
    if len(result)>maximum:raise ValueError(f"{name} exceeds {maximum} characters")
    return result
def _timestamp(value:str|datetime)->datetime:return datetime.fromisoformat(canonical_timestamp(value))
def _hash(value:Any,name:str)->str:
    result=str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result
def _slice_results(values:Sequence[Mapping[str,Any]],planned:list[dict[str,str]])->list[dict[str,Any]]:
    if not isinstance(values,Sequence) or isinstance(values,(str,bytes)):raise ValueError("slice_results must be a sequence")
    expected={(item["dimension"],item["label"]):item for item in planned};resolved=[];seen=set();seen_evidence=set()
    for value in values:
        if not isinstance(value,Mapping):raise ValueError("each slice result must be an object")
        dimension=_required(value.get("dimension"),"dimension",50).upper();label=_required(value.get("label"),"label",100);key=(dimension,label)
        if key not in expected:raise ValueError("slice result was not preregistered")
        if key in seen:raise ValueError("slice results must be unique")
        passed=value.get("passed")
        if not isinstance(passed,bool):raise ValueError("slice passed must be boolean")
        evidence_hash=_hash(value.get("evidence_sha256"),"evidence_sha256")
        if evidence_hash in seen_evidence:raise ValueError("each robustness slice requires distinct evidence")
        seen_evidence.add(evidence_hash)
        resolved.append({"dimension":dimension,"label":label,"evidence_sha256":evidence_hash,"passed":passed,"transaction_costs_included":value.get("transaction_costs_included") is True,"point_in_time_data_used":value.get("point_in_time_data_used") is True,"survivorship_safe_universe_used":value.get("survivorship_safe_universe_used") is True,"leakage_check_passed":value.get("leakage_check_passed") is True});seen.add(key)
    if seen!=set(expected):raise ValueError("every preregistered robustness slice requires a result")
    if any(not all(item[field] for field in ("transaction_costs_included","point_in_time_data_used","survivorship_safe_universe_used","leakage_check_passed")) for item in resolved):raise ValueError("every slice must satisfy costs, point-in-time, survivorship and leakage controls")
    return sorted(resolved,key=lambda item:(item["dimension"],item["label"].casefold()))
def _result_id(plan_id:str,completed_at:str,results:list[dict[str,Any]])->str:return "ROBUST-RESULT-"+hashlib.sha256(_canonical_json([plan_id,completed_at,results,POLICY_VERSION]).encode()).hexdigest()[:32].upper()

class RobustnessTestResultLedger:
    def __init__(self,path:str|Path,plan_ledger:RobustnessTestPlanLedger)->None:self.path=Path(path);self.plan_ledger=plan_ledger
    def records(self)->list[dict[str,Any]]:
        if not self.path.exists():return []
        raw=self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):raise LedgerIntegrityError("Robustness-result ledger has an incomplete final line.")
        records=[]
        with self.path.open("r",encoding="utf-8") as source:
            for number,line in enumerate(source,start=1):
                if not line.strip():raise LedgerIntegrityError(f"Blank robustness-result line at {number}.")
                try:record=json.loads(line)
                except json.JSONDecodeError as error:raise LedgerIntegrityError(f"Invalid JSON at robustness-result line {number}.") from error
                if not isinstance(record,dict):raise LedgerIntegrityError(f"Robustness-result line {number} is not an object.")
                records.append(record)
        return records
    def record(self,*,robustness_plan_id:str,slice_results:Sequence[Mapping[str,Any]],completed_by:str,completed_at:str|datetime|None=None,allow_existing:bool=True)->dict[str,Any]:
        plans=self.plan_ledger.verify();plan=next((item for item in plans if item.get("robustness_plan_id")==robustness_plan_id),None)
        if plan is None:raise ValueError("A verified robustness plan is required")
        completed=_timestamp(completed_at or datetime.now(timezone.utc))
        if completed<_timestamp(plan["planned_at"]):raise ValueError("completed_at cannot predate the robustness plan")
        if completed>datetime.now(timezone.utc)+MAX_CLOCK_SKEW:raise ValueError("completed_at cannot be in the future")
        results=_slice_results(slice_results,plan["robustness_slices"]);passed=sum(item["passed"] for item in results);fraction=Decimal(passed)/Decimal(len(results));dimension_pass={dimension:any(item["passed"] for item in results if item["dimension"]==dimension) for dimension in DIMENSIONS};accepted=fraction>=Decimal(plan["minimum_passing_slice_fraction"]) and all(dimension_pass.values())
        record={"schema_version":SCHEMA_VERSION,"policy_version":POLICY_VERSION,"robustness_result_id":_result_id(plan["robustness_plan_id"],completed.isoformat(),results),"record_type":"CONTROLLED_LEARNING_COMPLETE_ROBUSTNESS_RESULT","status":"ROBUSTNESS_CRITERIA_MET" if accepted else "ROBUSTNESS_CRITERIA_NOT_MET","simulation_only":True,"completed_at":completed.isoformat(),"completed_by":_required(completed_by,"completed_by",100),"robustness_plan_id":plan["robustness_plan_id"],"robustness_plan_record_hash":plan["record_hash"],"experiment_result_id":plan["experiment_result_id"],"experiment_id":plan["experiment_id"],"slice_results":results,"total_slices":len(results),"passing_slices":passed,"passing_slice_fraction":format(fraction.normalize(),"f"),"minimum_passing_slice_fraction":plan["minimum_passing_slice_fraction"],"dimension_has_passing_slice":dict(sorted(dimension_pass.items())),"all_robustness_criteria_met":accepted,"shadow_eligible":accepted,"shadow_test_started":False,"promotion_approved":False,"production_rule_changed":False,"deployment_performed":False,"order_submitted":False,"live_trading_enabled":False}
        return self._append(record,allow_existing=allow_existing)
    def verify(self)->list[dict[str,Any]]:
        previous=GENESIS_HASH;seen=set();records=self.records()
        for index,record in enumerate(records,start=1):
            material={k:v for k,v in record.items() if k!="record_hash"}
            if record.get("previous_hash")!=previous or record.get("record_hash")!=_record_hash(material):raise LedgerIntegrityError(f"Robustness-result record {index} has been modified.")
            plans,reasons=resolve_pinned_records(self.plan_ledger.verify(),[record.get("robustness_plan_id")],[record.get("robustness_plan_record_hash")],id_field="robustness_plan_id",label="robustness plan")
            if reasons or len(plans)!=1:raise LedgerIntegrityError(f"Robustness-result record {index} lost its plan.")
            plan=plans[0]
            try:completed=_timestamp(record.get("completed_at"));results=_slice_results(record.get("slice_results") or [],plan["robustness_slices"]);_required(record.get("completed_by"),"completed_by",100)
            except (TypeError,ValueError) as error:raise LedgerIntegrityError(f"Robustness-result record {index} has invalid values.") from error
            passed=sum(item["passed"] for item in results);fraction=Decimal(passed)/Decimal(len(results));dimension_pass={dimension:any(item["passed"] for item in results if item["dimension"]==dimension) for dimension in DIMENSIONS};accepted=fraction>=Decimal(plan["minimum_passing_slice_fraction"]) and all(dimension_pass.values());expected=_result_id(plan["robustness_plan_id"],completed.isoformat(),results)
            boundary=(record.get("schema_version")==SCHEMA_VERSION and record.get("policy_version")==POLICY_VERSION and record.get("robustness_result_id")==expected and expected not in seen and record.get("record_type")=="CONTROLLED_LEARNING_COMPLETE_ROBUSTNESS_RESULT" and record.get("status")== ("ROBUSTNESS_CRITERIA_MET" if accepted else "ROBUSTNESS_CRITERIA_NOT_MET") and record.get("simulation_only") is True and record.get("experiment_result_id")==plan["experiment_result_id"] and record.get("experiment_id")==plan["experiment_id"] and completed>=_timestamp(plan["planned_at"]) and completed<=datetime.now(timezone.utc)+MAX_CLOCK_SKEW and record.get("total_slices")==len(results) and record.get("passing_slices")==passed and record.get("passing_slice_fraction")==format(fraction.normalize(),"f") and record.get("minimum_passing_slice_fraction")==plan["minimum_passing_slice_fraction"] and record.get("dimension_has_passing_slice")==dict(sorted(dimension_pass.items())) and record.get("all_robustness_criteria_met") is accepted and record.get("shadow_eligible") is accepted and all(record.get(field) is False for field in ("shadow_test_started","promotion_approved","production_rule_changed","deployment_performed","order_submitted","live_trading_enabled")))
            if not boundary:raise LedgerIntegrityError(f"Robustness-result record {index} violates its boundary.")
            seen.add(expected);previous=record["record_hash"]
        return records
    def _append(self,result:dict[str,Any],*,allow_existing:bool):
        self.path.parent.mkdir(parents=True,exist_ok=True);descriptor=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX);records=self.verify();existing=next((item for item in records if item["robustness_result_id"]==result["robustness_result_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in result.items() if k not in ignored}:return existing
                raise LedgerIntegrityError("Robustness result already exists.")
            if any(item["robustness_plan_id"]==result["robustness_plan_id"] for item in records):raise LedgerIntegrityError("Robustness plan already has a result.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH};record={**material,"record_hash":_record_hash(material)};target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:_write_all(target,(_canonical_json(record)+"\n").encode());os.fsync(target)
            finally:os.close(target)
            return record
        finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
