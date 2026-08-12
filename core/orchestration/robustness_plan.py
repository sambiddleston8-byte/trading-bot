from __future__ import annotations

"""Immutable robustness checks preregistered after OOS and before shadow."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.experiment_result import SandboxExperimentResultLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all

SCHEMA_VERSION = "1.0"
POLICY_VERSION = "preregistered-multidimensional-robustness-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
DIMENSIONS = {"HISTORICAL_PERIOD", "SECTOR", "MARKET_REGIME"}


def _required(value: Any, name: str, maximum: int = 300) -> str:
    result = str(value or "").strip()
    if not result: raise ValueError(f"{name} is required")
    if len(result) > maximum: raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _fraction(value: Any, name: str) -> str:
    try: result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error: raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite() or result <= 0 or result > 1: raise ValueError(f"{name} must be greater than 0 and at most 1")
    return format(result.normalize(), "f")


def _slices(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)): raise ValueError("robustness_slices must be a sequence")
    resolved=[]; seen=set()
    for value in values:
        if not isinstance(value, Mapping): raise ValueError("each robustness slice must be an object")
        dimension=_required(value.get("dimension"),"dimension",50).upper(); label=_required(value.get("label"),"label",100); evidence_rule=_required(value.get("evidence_rule"),"evidence_rule",1000)
        if dimension not in DIMENSIONS: raise ValueError("dimension is not supported")
        key=(dimension,label.casefold())
        if key in seen: raise ValueError("robustness slices must be unique")
        seen.add(key); resolved.append({"dimension":dimension,"label":label,"evidence_rule":evidence_rule})
    counts={dimension:sum(item["dimension"]==dimension for item in resolved) for dimension in DIMENSIONS}
    if any(counts[dimension] < 2 for dimension in DIMENSIONS): raise ValueError("at least two slices are required for every robustness dimension")
    if len(resolved)>100: raise ValueError("at most 100 robustness slices are supported")
    return sorted(resolved,key=lambda item:(item["dimension"],item["label"].casefold()))


def _plan_id(result_id: str, planned_at: str, slices: list[dict[str,str]], fraction: str) -> str:
    return "ROBUST-"+hashlib.sha256(_canonical_json([result_id,planned_at,slices,fraction,POLICY_VERSION]).encode()).hexdigest()[:32].upper()


class RobustnessTestPlanLedger:
    def __init__(self,path:str|Path,result_ledger:SandboxExperimentResultLedger)->None:
        self.path=Path(path);self.result_ledger=result_ledger

    def records(self)->list[dict[str,Any]]:
        if not self.path.exists():return []
        raw=self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):raise LedgerIntegrityError("Robustness-plan ledger has an incomplete final line.")
        records=[]
        with self.path.open("r",encoding="utf-8") as source:
            for number,line in enumerate(source,start=1):
                if not line.strip():raise LedgerIntegrityError(f"Blank robustness-plan line at {number}.")
                try:record=json.loads(line)
                except json.JSONDecodeError as error:raise LedgerIntegrityError(f"Invalid JSON at robustness-plan line {number}.") from error
                if not isinstance(record,dict):raise LedgerIntegrityError(f"Robustness-plan line {number} is not an object.")
                records.append(record)
        return records

    def preregister(self,*,experiment_result_id:str,robustness_slices:Sequence[Mapping[str,Any]],minimum_passing_slice_fraction:Any,planned_by:str,planned_at:str|datetime|None=None,allow_existing:bool=True)->dict[str,Any]:
        results=self.result_ledger.verify();result=next((item for item in results if item.get("result_id")==experiment_result_id),None)
        if result is None or result.get("status")!="ACCEPTANCE_CRITERIA_MET":raise ValueError("A verified passing out-of-sample experiment result is required")
        planned=_timestamp(planned_at or datetime.now(timezone.utc))
        if planned<_timestamp(result["completed_at"]):raise ValueError("planned_at cannot predate the experiment result")
        if planned>datetime.now(timezone.utc)+MAX_CLOCK_SKEW:raise ValueError("planned_at cannot be in the future")
        slices=_slices(robustness_slices);fraction=_fraction(minimum_passing_slice_fraction,"minimum_passing_slice_fraction")
        record={"schema_version":SCHEMA_VERSION,"policy_version":POLICY_VERSION,"robustness_plan_id":_plan_id(result["result_id"],planned.isoformat(),slices,fraction),"record_type":"CONTROLLED_LEARNING_ROBUSTNESS_TEST_PLAN","status":"PREREGISTERED_NOT_EXECUTED","simulation_only":True,"planned_at":planned.isoformat(),"planned_by":_required(planned_by,"planned_by",100),"experiment_result_id":result["result_id"],"experiment_result_record_hash":result["record_hash"],"experiment_id":result["experiment_id"],"robustness_slices":slices,"minimum_passing_slice_fraction":fraction,"transaction_costs_required":True,"point_in_time_data_required":True,"survivorship_safe_universe_required":True,"no_leakage_required":True,"robustness_executed":False,"result_recorded":False,"shadow_eligible":False,"promotion_approved":False,"production_rule_changed":False,"deployment_performed":False,"order_submitted":False,"live_trading_enabled":False}
        return self._append(record,allow_existing=allow_existing)

    def verify(self)->list[dict[str,Any]]:
        previous=GENESIS_HASH;seen=set();records=self.records()
        for index,record in enumerate(records,start=1):
            material={k:v for k,v in record.items() if k!="record_hash"}
            if record.get("previous_hash")!=previous or record.get("record_hash")!=_record_hash(material):raise LedgerIntegrityError(f"Robustness-plan record {index} has been modified.")
            results,reasons=resolve_pinned_records(self.result_ledger.verify(),[record.get("experiment_result_id")],[record.get("experiment_result_record_hash")],id_field="result_id",label="experiment result")
            if reasons or len(results)!=1:raise LedgerIntegrityError(f"Robustness-plan record {index} lost its result.")
            result=results[0]
            try:planned=_timestamp(record.get("planned_at"));slices=_slices(record.get("robustness_slices") or []);fraction=_fraction(record.get("minimum_passing_slice_fraction"),"fraction");_required(record.get("planned_by"),"planned_by",100)
            except (TypeError,ValueError) as error:raise LedgerIntegrityError(f"Robustness-plan record {index} has invalid values.") from error
            expected=_plan_id(result["result_id"],planned.isoformat(),slices,fraction);fixed_false=("robustness_executed","result_recorded","shadow_eligible","promotion_approved","production_rule_changed","deployment_performed","order_submitted","live_trading_enabled")
            boundary=(record.get("schema_version")==SCHEMA_VERSION and record.get("policy_version")==POLICY_VERSION and record.get("robustness_plan_id")==expected and expected not in seen and record.get("record_type")=="CONTROLLED_LEARNING_ROBUSTNESS_TEST_PLAN" and record.get("status")=="PREREGISTERED_NOT_EXECUTED" and record.get("simulation_only") is True and result.get("status")=="ACCEPTANCE_CRITERIA_MET" and record.get("experiment_result_id")==result["result_id"] and record.get("experiment_result_record_hash")==result["record_hash"] and record.get("experiment_id")==result["experiment_id"] and planned>=_timestamp(result["completed_at"]) and planned<=datetime.now(timezone.utc)+MAX_CLOCK_SKEW and all(record.get(field) is True for field in ("transaction_costs_required","point_in_time_data_required","survivorship_safe_universe_required","no_leakage_required")) and all(record.get(field) is False for field in fixed_false))
            if not boundary:raise LedgerIntegrityError(f"Robustness-plan record {index} violates its boundary.")
            seen.add(expected);previous=record["record_hash"]
        return records

    def _append(self,result:dict[str,Any],*,allow_existing:bool):
        self.path.parent.mkdir(parents=True,exist_ok=True);descriptor=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX);records=self.verify();existing=next((item for item in records if item["robustness_plan_id"]==result["robustness_plan_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in result.items() if k not in ignored}:return existing
                raise LedgerIntegrityError("Robustness plan already exists.")
            if any(item["experiment_result_id"]==result["experiment_result_id"] for item in records):raise LedgerIntegrityError("Experiment result already has a robustness plan.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH};record={**material,"record_hash":_record_hash(material)};target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:_write_all(target,(_canonical_json(record)+"\n").encode());os.fsync(target)
            finally:os.close(target)
            return record
        finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
