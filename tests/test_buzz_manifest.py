from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from core.collaboration import BuzzWorkspaceManifestLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError

BASE={"workspace_name":"Sam and Pat Investment Platform","git_revision":"a"*40,"prepared_by":"Codex","prepared_at":"2026-01-01T00:00:00+00:00"}
def prepare(target,**changes):values=dict(BASE);values.update(changes);return target.prepare(**values)
def rewrite(path,**changes):
    from core.collaboration import buzz_manifest as module
    value=json.loads(path.read_text());value.update(changes);material={k:v for k,v in value.items() if k!="record_hash"};value["record_hash"]=module._record_hash(material);path.write_text(json.dumps(value)+"\n")

def test_prepares_local_inactive_buzz_topology(tmp_path):
    target=BuzzWorkspaceManifestLedger(tmp_path/"buzz.jsonl");result=prepare(target)
    assert result["status"]=="PREPARED_NOT_INSTALLED_OR_DEPLOYED" and result["relay_url"]=="ws://localhost:3000" and len(result["channels"])==10 and result["previous_hash"]==GENESIS_HASH
    assert [item["agent_id"] for item in result["agent_identities"]]==["CODEX","CLAUDE_CODE","HERMES"]
    assert all(item["active"] is False and item["identity_key_configured"] is False and "ENABLE_LIVE_TRADING" in item["denied_actions"] for item in result["agent_identities"])
    for field in ("buzz_installed","relay_started","workflow_scheduling_enabled","agent_keys_created","agents_connected","github_write_enabled","aws_access_enabled","broker_access_enabled","automatic_merge_enabled","automatic_promotion_enabled","live_trading_enabled"):assert result[field] is False
    assert target.verify()==[result]

def test_concurrent_retry_appends_once(tmp_path):
    target=BuzzWorkspaceManifestLedger(tmp_path/"buzz.jsonl")
    with ThreadPoolExecutor(max_workers=2) as pool:first,second=list(pool.map(lambda _:prepare(target),range(2)))
    assert first==second and len(target.verify())==1

def test_invalid_revision_fails_closed(tmp_path):
    with pytest.raises(ValueError,match="full hexadecimal"):prepare(BuzzWorkspaceManifestLedger(tmp_path/"x"),git_revision="abc")

@pytest.mark.parametrize("changes",[{"status":"RUNNING"},{"relay_url":"wss://remote.example"},{"channels":[]},{"agent_identities":[]},{"github_remains_code_authority":False},{"buzz_installed":True},{"relay_started":True},{"workflow_scheduling_enabled":True},{"agent_keys_created":True},{"agents_connected":True},{"repository_write_enabled":True},{"github_write_enabled":True},{"aws_access_enabled":True},{"broker_access_enabled":True},{"production_rule_write_enabled":True},{"automatic_merge_enabled":True},{"automatic_promotion_enabled":True},{"live_trading_enabled":True}])
def test_rehashed_semantic_tampering_is_detected(tmp_path,changes):
    target=BuzzWorkspaceManifestLedger(tmp_path/"buzz.jsonl");prepare(target);rewrite(target.path,**changes)
    with pytest.raises(LedgerIntegrityError):target.verify()
