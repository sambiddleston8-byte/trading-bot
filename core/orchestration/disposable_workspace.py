from __future__ import annotations

"""Local disposable experiment workspaces with enforced expiry and containment."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from core.decision_ledger import canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json, _write_all

ROOT_MARKER="SAM_PAT_EXPERIMENT_SANDBOX_V1\n";SCHEMA_VERSION="1.0";POLICY_VERSION="local-disposable-experiment-workspace-v1";_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$");_HASH=re.compile(r"^[0-9a-f]{64}$");MAX_RETENTION_HOURS=720

def _required(value:Any,name:str,maximum:int=200)->str:
    result=str(value or "").strip()
    if not result:raise ValueError(f"{name} is required")
    if len(result)>maximum:raise ValueError(f"{name} exceeds {maximum} characters")
    return result
def _timestamp(value:str|datetime)->datetime:return datetime.fromisoformat(canonical_timestamp(value))
def _positive_int(value:Any,name:str,maximum:int)->int:
    if isinstance(value,bool):raise ValueError(f"{name} must be an integer")
    try:result=int(value)
    except (TypeError,ValueError) as error:raise ValueError(f"{name} must be an integer") from error
    if result<1 or result>maximum or str(result)!=str(value).strip():raise ValueError(f"{name} must be between 1 and {maximum}")
    return result

class DisposableExperimentWorkspace:
    """Creates and expires only marker-authorized, directly-contained workspaces."""
    def __init__(self,sandbox_root:str|Path)->None:
        self.root=Path(sandbox_root)
    def _verified_root(self)->Path:
        if self.root.is_symlink() or not self.root.is_dir():raise ValueError("sandbox_root must be an existing non-symlink directory")
        root=self.root.resolve();marker=root/".experiment-sandbox-root"
        if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8")!=ROOT_MARKER:raise ValueError("sandbox_root is missing the exact safety marker")
        return root
    def create(self,*,experiment_id:str,dataset_manifest_sha256:str,retention_hours:int,created_at:str|datetime|None=None)->dict[str,Any]:
        root=self._verified_root();experiment=_required(experiment_id,"experiment_id")
        if not _ID.fullmatch(experiment):raise ValueError("experiment_id contains unsafe path characters")
        dataset_hash=str(dataset_manifest_sha256 or "").strip().lower()
        if not _HASH.fullmatch(dataset_hash):raise ValueError("dataset_manifest_sha256 must be a lowercase SHA-256 digest")
        retention=_positive_int(retention_hours,"retention_hours",MAX_RETENTION_HOURS);created=_timestamp(created_at or datetime.now(timezone.utc));expires=created+timedelta(hours=retention)
        workspace_id="EWS-"+hashlib.sha256(_canonical_json([experiment,dataset_hash,created.isoformat(),uuid4().hex,POLICY_VERSION]).encode()).hexdigest()[:32].upper();directory=root/workspace_id
        directory.mkdir(mode=0o700)
        manifest={"schema_version":SCHEMA_VERSION,"policy_version":POLICY_VERSION,"workspace_id":workspace_id,"experiment_id":experiment,"dataset_manifest_sha256":dataset_hash,"created_at":created.isoformat(),"expires_at":expires.isoformat(),"retention_hours":retention,"status":"DISPOSABLE_LOCAL_SANDBOX","database_file":"experiment.sqlite3","network_allowed":False,"authoritative_data_write_allowed":False,"broker_access_allowed":False,"aws_access_allowed":False,"promotion_allowed":False,"live_trading_enabled":False}
        manifest["manifest_sha256"]=hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
        descriptor=os.open(directory/"manifest.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:_write_all(descriptor,(_canonical_json(manifest)+"\n").encode());os.fsync(descriptor)
        finally:os.close(descriptor)
        database=directory/"experiment.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("CREATE TABLE workspace_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.executemany("INSERT INTO workspace_metadata(key,value) VALUES (?,?)",[("workspace_id",workspace_id),("experiment_id",experiment),("dataset_manifest_sha256",dataset_hash),("expires_at",expires.isoformat())])
        os.chmod(database,0o600)
        return manifest
    def inspect(self,workspace_id:str)->dict[str,Any]:
        root=self._verified_root();resolved=_required(workspace_id,"workspace_id",100)
        if not re.fullmatch(r"EWS-[0-9A-F]{32}",resolved):raise ValueError("workspace_id format is invalid")
        directory=root/resolved
        if directory.is_symlink() or not directory.is_dir() or directory.parent.resolve()!=root:raise ValueError("workspace is not a direct contained directory")
        manifest_path=directory/"manifest.json";database=directory/"experiment.sqlite3"
        if manifest_path.is_symlink() or database.is_symlink():raise ValueError("workspace files cannot be symlinks")
        try:manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as error:raise ValueError("workspace manifest is unreadable") from error
        supplied=manifest.pop("manifest_sha256",None);expected=hashlib.sha256(_canonical_json(manifest).encode()).hexdigest();manifest["manifest_sha256"]=supplied
        if supplied!=expected or manifest.get("workspace_id")!=resolved or manifest.get("policy_version")!=POLICY_VERSION:raise ValueError("workspace manifest integrity check failed")
        if not database.is_file():raise ValueError("workspace database is missing")
        return manifest
    def purge_expired(self,workspace_id:str,*,now:str|datetime|None=None)->dict[str,Any]:
        root=self._verified_root();manifest=self.inspect(workspace_id);current=_timestamp(now or datetime.now(timezone.utc));expires=_timestamp(manifest["expires_at"])
        if current<expires:raise ValueError("workspace retention has not expired")
        directory=root/workspace_id;allowed={"manifest.json","experiment.sqlite3"};names={item.name for item in directory.iterdir()}
        if names!=allowed:raise ValueError("workspace contains unexpected files; refusing purge")
        for name in sorted(allowed):
            target=directory/name
            if target.is_symlink() or not target.is_file():raise ValueError("workspace file safety check failed")
            target.unlink()
        directory.rmdir()
        return {"workspace_id":workspace_id,"status":"PURGED_AFTER_RETENTION","purged_at":current.isoformat(),"recoverable":False}
