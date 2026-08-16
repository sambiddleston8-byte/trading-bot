"""Five-timestamp PIT feature contract and first causal technical vertical slice."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, localcontext
import hashlib, json, os
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION="1.0"; FAMILY="CAUSAL_DAILY_TECHNICALS_V1"
FEATURE_NAMES=("atr_14","momentum_20","sma_20","sma_50")
DEFINITION={"sma_20":"mean close over current and prior 19 bars","sma_50":"mean close over current and prior 49 bars","momentum_20":"current close / close 20 sessions earlier - 1","atr_14":"mean true range over current and prior 13 sessions using 15 bars","decimal_context":{"precision":34,"rounding":"ROUND_HALF_EVEN"}}
DEFINITION_SHA256=hashlib.sha256(json.dumps(DEFINITION,sort_keys=True,separators=(",",":")).encode()).hexdigest()
ROOT=Path("data/research/massive_campaign_v2_revision_2/stage3/technical_features")
DECIMAL_CONTEXT=Context(prec=34,rounding=ROUND_HALF_EVEN,Emin=-999999,Emax=999999,traps=[DivisionByZero,InvalidOperation,Overflow])

def _canonical(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def _dt(v:str,name:str)->datetime:
    try: x=datetime.fromisoformat(v)
    except (TypeError,ValueError) as e: raise ValueError(f"{name} must be a timestamp") from e
    if x.tzinfo is None: raise ValueError(f"{name} must be timezone-aware")
    return x.astimezone(timezone.utc)
def _decimal(v:Decimal)->str:
    text=format(v,"f"); return text.rstrip("0").rstrip(".") if "." in text else text
def _write_all(fd:int,payload:bytes)->None:
    offset=0
    while offset<len(payload):
        count=os.write(fd,payload[offset:])
        if count<=0: raise OSError("feature-store write made no progress")
        offset+=count

@dataclass(frozen=True)
class PITFeatureRecord:
    feature_id:str; feature_family:str; feature_definition_sha256:str; entity_id:str; partition_role:str
    effective_at:str; reported_at:str; available_at:str; retrieved_at:str; observation_cutoff_at:str
    revision:int; prior_revision_sha256:str|None; values:Mapping[str,str]; provenance:Mapping[str,Any]
    record_sha256:str
    def __post_init__(self)->None:
        effective,reported,available,retrieved,cutoff=(_dt(getattr(self,n),n) for n in ("effective_at","reported_at","available_at","retrieved_at","observation_cutoff_at"))
        if not effective<=reported<=available or cutoff>retrieved: raise ValueError("feature timestamps violate PIT ordering")
        if retrieved < available: raise ValueError("retrieval cannot predate historical availability")
        if self.revision<1 or (self.revision==1)!=(self.prior_revision_sha256 is None): raise ValueError("feature revision chain is invalid")
        if self.prior_revision_sha256 is not None and (len(self.prior_revision_sha256)!=64 or any(x not in "0123456789abcdef" for x in self.prior_revision_sha256)): raise ValueError("prior revision hash is invalid")
        if self.feature_family!=FAMILY or self.feature_definition_sha256!=DEFINITION_SHA256: raise ValueError("feature definition identity changed")
        if set(self.values)!=set(FEATURE_NAMES) or any(not Decimal(value).is_finite() for value in self.values.values()): raise ValueError("feature value vector is invalid")
        material={k:v for k,v in self.as_dict().items() if k!="record_sha256"}
        if hashlib.sha256(_canonical(material)).hexdigest()!=self.record_sha256: raise ValueError("feature record hash is invalid")
        inputs=self.provenance.get("input_rows")
        if not isinstance(inputs,list) or not inputs: raise ValueError("feature provenance requires input rows")
        if any(_dt(x["available_at"],"input available_at")>cutoff for x in inputs): raise ValueError("feature input leaks beyond observation cutoff")
        if max(_dt(x["available_at"],"input available_at") for x in inputs)!=available: raise ValueError("derived available_at must equal maximum input availability")
    def as_dict(self)->dict[str,Any]: return {name:getattr(self,name) for name in self.__dataclass_fields__}

def _record(*,symbol:str,role:str,rows:Sequence[Mapping[str,Any]],retrieved_at:str,observation_cutoff_at:str,artifact_hashes:Mapping[str,str])->PITFeatureRecord:
    current=rows[-1]; closes=[Decimal(x["close"]) for x in rows]
    if any(value<=0 or not value.is_finite() for value in closes): raise ValueError("technical feature closes must be finite and positive")
    with localcontext(DECIMAL_CONTEXT):
        true_ranges=[]
        for prior,item in zip(rows[-15:-1],rows[-14:]):
            high,low,previous=Decimal(item["high"]),Decimal(item["low"]),Decimal(prior["close"])
            true_ranges.append(max(high-low,abs(high-previous),abs(low-previous)))
        values={"sma_20":_decimal(sum(closes[-20:],Decimal(0))/Decimal(20)),"sma_50":_decimal(sum(closes[-50:],Decimal(0))/Decimal(50)),"momentum_20":_decimal(closes[-1]/closes[-21]-Decimal(1)),"atr_14":_decimal(sum(true_ranges,Decimal(0))/Decimal(14))}
    inputs=[]
    for row in rows[-50:]:
        material={k:row[k] for k in sorted(row)}
        inputs.append({"row_id":f"{symbol}:{row['session_date']}","session_date":row["session_date"],"available_at":row["available_at"],"row_sha256":hashlib.sha256(_canonical(material)).hexdigest(),"source_payload_sha256":row["source_payload_sha256"]})
    feature_id="PITF-"+hashlib.sha256(f"{FAMILY}:{symbol}:{current['close_at']}".encode()).hexdigest()[:32].upper()
    latest=max(inputs,key=lambda x:_dt(x["available_at"],"input available_at"))["available_at"]
    material={"feature_id":feature_id,"feature_family":FAMILY,"feature_definition_sha256":DEFINITION_SHA256,"entity_id":symbol,"partition_role":role,"effective_at":current["close_at"],"reported_at":current["close_at"],"available_at":latest,"retrieved_at":retrieved_at,"observation_cutoff_at":observation_cutoff_at,"revision":1,"prior_revision_sha256":None,"values":values,"provenance":{"source_artifact_sha256":dict(sorted(artifact_hashes.items())),"input_rows":inputs,"derivation":"TRAILING_WINDOWS_INCLUDING_CURRENT_COMPLETED_BAR_ONLY"}}
    return PITFeatureRecord(**material,record_sha256=hashlib.sha256(_canonical(material)).hexdigest())

def revise_feature_record(previous:PITFeatureRecord,*,retrieved_at:str,values:Mapping[str,str],provenance:Mapping[str,Any])->PITFeatureRecord:
    if _dt(retrieved_at,"retrieved_at")<=_dt(previous.retrieved_at,"previous retrieved_at"): raise ValueError("revision retrieval must strictly follow its parent")
    if dict(values)!=dict(previous.values): raise ValueError("technical values require fresh source derivation, not metadata revision")
    material={k:v for k,v in previous.as_dict().items() if k!="record_sha256"}
    material.update(retrieved_at=_dt(retrieved_at,"retrieved_at").isoformat(),revision=previous.revision+1,prior_revision_sha256=previous.record_sha256,values=dict(values),provenance=dict(provenance))
    return PITFeatureRecord(**material,record_sha256=hashlib.sha256(_canonical(material)).hexdigest())

def _validate_revision_link(parent:PITFeatureRecord,child:PITFeatureRecord)->None:
    if child.feature_id!=parent.feature_id or child.revision!=parent.revision+1 or child.prior_revision_sha256!=parent.record_sha256: raise ValueError("revision chain parent is not authentic")
    immutable=("feature_family","feature_definition_sha256","entity_id","partition_role","effective_at","reported_at","available_at","observation_cutoff_at","values")
    if any(getattr(child,name)!=getattr(parent,name) for name in immutable): raise ValueError("revision changed immutable feature identity, PIT coordinates, or derived values")
    if _dt(child.retrieved_at,"retrieved_at")<=_dt(parent.retrieved_at,"parent retrieved_at"): raise ValueError("revision retrieval order is invalid")

def validate_revision_chain(records:Sequence[PITFeatureRecord])->None:
    if not records or records[0].revision!=1 or records[0].prior_revision_sha256 is not None: raise ValueError("revision chain must begin at revision 1")
    for parent,child in zip(records,records[1:]): _validate_revision_link(parent,child)

def build_revision_matrix(previous_matrix:Mapping[str,Any],*,retrieved_at:str,value_overrides:Mapping[str,Mapping[str,str]],provenance_overrides:Mapping[str,Mapping[str,Any]])->dict[str,Any]:
    prior_material={k:v for k,v in previous_matrix.items() if k!="matrix_sha256"}
    if previous_matrix.get("matrix_sha256")!=hashlib.sha256(_canonical(prior_material)).hexdigest(): raise ValueError("parent feature matrix hash is invalid")
    previous={row["feature_id"]:PITFeatureRecord(**row) for row in previous_matrix["rows"]}
    if set(value_overrides)!=set(provenance_overrides) or not set(value_overrides)<=set(previous): raise ValueError("revision overrides do not identify admitted features")
    rows=[]
    for feature_id,parent in sorted(previous.items()):
        child=revise_feature_record(parent,retrieved_at=retrieved_at,values=value_overrides.get(feature_id,parent.values),provenance=provenance_overrides.get(feature_id,parent.provenance))
        _validate_revision_link(parent,child);rows.append(child.as_dict())
    matrix={k:v for k,v in previous_matrix.items() if k not in ("rows","matrix_sha256")};matrix["rows"]=rows
    matrix["matrix_sha256"]=hashlib.sha256(_canonical(matrix)).hexdigest();return matrix

def campaign_observation_cutoff(day:str)->str:
    from core.orchestration.stage2_qualification import _at, _bar_close
    return (datetime.fromisoformat(_at(day,_bar_close(day)))+timedelta(minutes=5)).isoformat()

def build_technical_feature_matrices(repository_root:Path,*,retrieved_at:str,observation_cutoffs:Mapping[str,Mapping[str,str]],qualification_report_artifact_sha256:str)->dict[str,Any]:
    stage2=repository_root/"data/research/massive_campaign_v2_revision_2/stage2"; qualification_bytes=(stage2/"qualification_report.json").read_bytes(); qualification=json.loads(qualification_bytes)
    qualification_material={k:v for k,v in qualification.items() if k!="qualification_sha256"}
    if qualification.get("qualification_sha256")!=hashlib.sha256(_canonical(qualification_material)).hexdigest(): raise ValueError("qualification report integrity failed")
    qualification_artifact_sha256=hashlib.sha256(qualification_bytes).hexdigest()
    if qualification_artifact_sha256!=qualification_report_artifact_sha256: raise ValueError("qualification report differs from the admitted pin")
    paths={"TRAIN":stage2/"clean_feature_store/train.json","VALIDATION":stage2/"clean_feature_store/validation.json"}
    artifacts={role:path.read_bytes() for role,path in paths.items()}
    for role,payload in artifacts.items():
        if hashlib.sha256(payload).hexdigest()!=qualification["artifacts"][role]: raise ValueError("admitted source artifact hash changed")
    retrieved=_dt(retrieved_at,"retrieved_at").isoformat()
    source={role:json.loads(payload) for role,payload in artifacts.items()}; symbols=("AAPL","MSFT","SPY")
    train_sessions={x["session_date"] for x in source["TRAIN"]["bars"]}; validation_sessions={x["session_date"] for x in source["VALIDATION"]["bars"]}
    if not train_sessions or not validation_sessions or train_sessions&validation_sessions or max(train_sessions)>=min(validation_sessions): raise ValueError("TRAIN must strictly precede non-overlapping VALIDATION")
    history={symbol:[] for symbol in symbols}; matrices={}
    for role in ("TRAIN","VALIDATION"):
        rows=[]; by={(x["session_date"],x["symbol"]):x for x in source[role]["bars"]}; sessions=sorted({x["session_date"] for x in source[role]["bars"]})
        if set(observation_cutoffs.get(role,{}))!=set(sessions): raise ValueError("independent observation cutoff schedule is incomplete")
        if any(observation_cutoffs[role][day]!=campaign_observation_cutoff(day) for day in sessions): raise ValueError("observation cutoff differs from the campaign calendar contract")
        if set(by)!={(day,symbol) for day in sessions for symbol in symbols}: raise ValueError("feature source is not cross-sectionally aligned")
        for day in sessions:
            for symbol in symbols:
                history[symbol].append(by[(day,symbol)])
                if len(history[symbol])>=50:
                    used={"QUALIFICATION_REPORT":qualification_artifact_sha256,"TRAIN":qualification["artifacts"]["TRAIN"]}
                    if role=="VALIDATION": used["VALIDATION"]=qualification["artifacts"]["VALIDATION"]
                    rows.append(_record(symbol=symbol,role=role,rows=history[symbol],retrieved_at=retrieved,observation_cutoff_at=observation_cutoffs[role][day],artifact_hashes=used).as_dict())
        expected={day for day in sessions if role=="VALIDATION" or len([x for x in source[role]["bars"] if x["symbol"]=="AAPL" and x["session_date"]<=day])>=50}
        if {(x["effective_at"][:10],x["entity_id"]) for x in rows}!={(day,symbol) for day in expected for symbol in symbols}: raise ValueError("derived feature matrix lost cross-sectional alignment")
        matrix={"schema_version":SCHEMA_VERSION,"feature_family":FAMILY,"feature_definition_sha256":DEFINITION_SHA256,"partition_role":role,"qualification_report_artifact_sha256":qualification_artifact_sha256,"source_artifact_sha256":qualification["artifacts"][role],"rows":rows,"admitted":True,"untouched_test_included":False}
        matrix["matrix_sha256"]=hashlib.sha256(_canonical(matrix)).hexdigest(); matrices[role]=matrix
    output=repository_root/ROOT; output.mkdir(parents=True,exist_ok=True,mode=0o700); output.chmod(0o700)
    summary={"feature_family":FAMILY,"features":list(FEATURE_NAMES),"matrices":{},"untouched_test_included":False,"performance_claim_allowed":False}
    for role,matrix in matrices.items():
        path=output/f"{role.lower()}_matrix.json"; payload=_canonical(matrix)+b"\n"
        if path.exists() and path.read_bytes()!=payload: raise ValueError("admitted feature matrix conflicts")
        if not path.exists():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
            try: _write_all(fd,payload); os.fsync(fd)
            finally: os.close(fd)
        summary["matrices"][role]={"row_count":len(matrix["rows"]),"session_count":len({x["effective_at"] for x in matrix["rows"]}),"matrix_sha256":matrix["matrix_sha256"],"artifact_sha256":hashlib.sha256(payload).hexdigest()}
    summary["admission_sha256"]=hashlib.sha256(_canonical(summary)).hexdigest()
    return summary
