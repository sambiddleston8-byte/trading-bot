from __future__ import annotations

"""Immutable local Buzz workspace plan; neither Buzz nor any agent is started."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


BUZZ_SCHEMA_VERSION = "1.0"
BUZZ_POLICY_VERSION = "local-fail-closed-block-buzz-workspace-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
REQUIRED_CHANNELS = [
    "investment-platform", "architecture", "research-engine", "valuation",
    "portfolio-construction", "bugs", "investment-decisions", "model-learning",
    "experiments", "codex-vs-claude",
]
COMMON_DENIED_ACTIONS = [
    "MERGE_PULL_REQUEST", "DEPLOY_SOFTWARE", "MODIFY_PRODUCTION_INVESTMENT_RULES",
    "PROMOTE_EXPERIMENT", "ACCESS_BROKER_CREDENTIALS", "SUBMIT_BROKER_ORDER",
    "ACCESS_AWS", "DELETE_OR_REWRITE_EVIDENCE", "ENABLE_LIVE_TRADING",
    "CHANGE_OWN_IDENTITY_OR_PERMISSIONS",
]
AGENT_ALLOWED_ACTIONS = {
    "CODEX": ["READ_SCOPED_PROJECT_CONTEXT", "POST_STATUS", "PROPOSE_GITHUB_CHANGE"],
    "CLAUDE_CODE": ["READ_SCOPED_PROJECT_CONTEXT", "POST_REVIEW", "CHALLENGE_PROPOSED_CHANGE"],
    "HERMES": ["READ_VERIFIED_LOCAL_EVIDENCE", "POST_SANDBOX_LESSON_PROPOSAL", "POST_SANDBOX_EXPERIMENT_PROPOSAL"],
}
_GIT_REVISION = re.compile(r"^[0-9a-f]{40,64}$")


def _required(value: Any, name: str, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _manifest_id(workspace: str, revision: str, prepared_at: str) -> str:
    return "BUZZ-" + hashlib.sha256(
        _canonical_json([workspace, revision, prepared_at, REQUIRED_CHANNELS, AGENT_ALLOWED_ACTIONS, COMMON_DENIED_ACTIONS, BUZZ_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class BuzzWorkspaceManifestLedger:
    """Records intended local coordination topology without deploying it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists(): return []
        raw=self.path.read_bytes()
        if raw and not raw.endswith(b"\n"): raise LedgerIntegrityError("Buzz-manifest ledger has an incomplete final line.")
        records=[]
        with self.path.open("r",encoding="utf-8") as source:
            for line_number,line in enumerate(source,start=1):
                if not line.strip(): raise LedgerIntegrityError(f"Blank Buzz-manifest line at {line_number}.")
                try: record=json.loads(line)
                except json.JSONDecodeError as error: raise LedgerIntegrityError(f"Invalid JSON at Buzz-manifest line {line_number}.") from error
                if not isinstance(record,dict): raise LedgerIntegrityError(f"Buzz-manifest line {line_number} is not an object.")
                records.append(record)
        return records

    def prepare(
        self, *, workspace_name: str, git_revision: str, prepared_by: str,
        prepared_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        workspace=_required(workspace_name,"workspace_name",100); revision=str(git_revision or "").strip().lower()
        if not _GIT_REVISION.fullmatch(revision): raise ValueError("git_revision must be a full hexadecimal commit identifier")
        prepared=_timestamp(prepared_at or datetime.now(timezone.utc))
        if prepared>datetime.now(timezone.utc)+MAX_CLOCK_SKEW: raise ValueError("prepared_at cannot be in the future")
        identities=[
            {"agent_id":agent,"identity_type":"DEDICATED_BUZZ_KEYPAIR_NOT_CONFIGURED","allowed_actions":list(actions),"denied_actions":list(COMMON_DENIED_ACTIONS),"active":False,"identity_key_configured":False}
            for agent,actions in AGENT_ALLOWED_ACTIONS.items()
        ]
        result={
            "schema_version":BUZZ_SCHEMA_VERSION,"policy_version":BUZZ_POLICY_VERSION,
            "buzz_manifest_id":_manifest_id(workspace,revision,prepared.isoformat()),
            "record_type":"BLOCK_BUZZ_LOCAL_WORKSPACE_MANIFEST","status":"PREPARED_NOT_INSTALLED_OR_DEPLOYED",
            "workspace_name":workspace,"prepared_at":prepared.isoformat(),"prepared_by":_required(prepared_by,"prepared_by",100),"git_revision":revision,
            "implementation":"BLOCK_OPEN_SOURCE_BUZZ","upstream_repository":"https://github.com/block/buzz","license":"Apache-2.0",
            "deployment_mode":"LOCAL_SELF_HOSTED_PLANNED","relay_url":"ws://localhost:3000","channels":list(REQUIRED_CHANNELS),"agent_identities":identities,
            "github_remains_code_authority":True,"database_and_ledgers_remain_state_authority":True,"buzz_is_coordination_only":True,"human_approval_gates_remain_external":True,
            "buzz_installed":False,"relay_started":False,"database_started":False,"redis_started":False,"object_storage_started":False,"workflow_scheduling_enabled":False,"webhooks_enabled":False,"agent_keys_created":False,"agents_connected":False,"repository_write_enabled":False,"github_write_enabled":False,"aws_access_enabled":False,"broker_access_enabled":False,"production_rule_write_enabled":False,"automatic_merge_enabled":False,"automatic_promotion_enabled":False,"live_trading_enabled":False,
        }
        return self._append(result,allow_existing=allow_existing)

    def verify(self)->list[dict[str,Any]]:
        previous_hash=GENESIS_HASH;seen=set();records=self.records()
        for index,record in enumerate(records,start=1):
            material={k:v for k,v in record.items() if k!="record_hash"}
            if record.get("previous_hash")!=previous_hash or record.get("record_hash")!=_record_hash(material): raise LedgerIntegrityError(f"Buzz-manifest record {index} has been modified.")
            try:
                workspace=_required(record.get("workspace_name"),"workspace",100);prepared=_timestamp(record.get("prepared_at"));revision=str(record.get("git_revision") or "").strip().lower();_required(record.get("prepared_by"),"prepared_by",100)
                if not _GIT_REVISION.fullmatch(revision): raise ValueError("revision")
            except (TypeError,ValueError) as error: raise LedgerIntegrityError(f"Buzz-manifest record {index} has invalid values.") from error
            expected_id=_manifest_id(workspace,revision,prepared.isoformat());identities=[{"agent_id":agent,"identity_type":"DEDICATED_BUZZ_KEYPAIR_NOT_CONFIGURED","allowed_actions":list(actions),"denied_actions":list(COMMON_DENIED_ACTIONS),"active":False,"identity_key_configured":False} for agent,actions in AGENT_ALLOWED_ACTIONS.items()]
            fixed_false=("buzz_installed","relay_started","database_started","redis_started","object_storage_started","workflow_scheduling_enabled","webhooks_enabled","agent_keys_created","agents_connected","repository_write_enabled","github_write_enabled","aws_access_enabled","broker_access_enabled","production_rule_write_enabled","automatic_merge_enabled","automatic_promotion_enabled","live_trading_enabled")
            boundary=(
                record.get("schema_version")==BUZZ_SCHEMA_VERSION and record.get("policy_version")==BUZZ_POLICY_VERSION and record.get("buzz_manifest_id")==expected_id and expected_id not in seen
                and record.get("record_type")=="BLOCK_BUZZ_LOCAL_WORKSPACE_MANIFEST" and record.get("status")=="PREPARED_NOT_INSTALLED_OR_DEPLOYED"
                and record.get("implementation")=="BLOCK_OPEN_SOURCE_BUZZ" and record.get("upstream_repository")=="https://github.com/block/buzz" and record.get("license")=="Apache-2.0"
                and record.get("deployment_mode")=="LOCAL_SELF_HOSTED_PLANNED" and record.get("relay_url")=="ws://localhost:3000" and record.get("channels")==REQUIRED_CHANNELS and record.get("agent_identities")==identities
                and prepared<=datetime.now(timezone.utc)+MAX_CLOCK_SKEW and all(record.get(field) is True for field in ("github_remains_code_authority","database_and_ledgers_remain_state_authority","buzz_is_coordination_only","human_approval_gates_remain_external"))
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary: raise LedgerIntegrityError(f"Buzz-manifest record {index} violates its boundary.")
            seen.add(expected_id);previous_hash=record["record_hash"]
        return records

    def _append(self,result:dict[str,Any],*,allow_existing:bool):
        self.path.parent.mkdir(parents=True,exist_ok=True);descriptor=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX);records=self.verify();existing=next((item for item in records if item["buzz_manifest_id"]==result["buzz_manifest_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in result.items() if k not in ignored}:return existing
                raise LedgerIntegrityError("Buzz workspace manifest already exists.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH};record={**material,"record_hash":_record_hash(material)};target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:_write_all(target,(_canonical_json(record)+"\n").encode());os.fsync(target)
            finally:os.close(target)
            return record
        finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
