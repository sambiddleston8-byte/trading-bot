import json
from pathlib import Path
import sqlite3
import pytest
from core.orchestration import DisposableExperimentWorkspace
from core.orchestration.disposable_workspace import ROOT_MARKER

def manager(tmp_path):
    root=tmp_path/"sandboxes";root.mkdir();(root/".experiment-sandbox-root").write_text(ROOT_MARKER);return DisposableExperimentWorkspace(root)
BASE={"experiment_id":"EXP-1","dataset_manifest_sha256":"a"*64,"retention_hours":24,"created_at":"2026-01-01T00:00:00+00:00"}
def create(target,**changes):values=dict(BASE);values.update(changes);return target.create(**values)
def test_creates_contained_private_sqlite_workspace(tmp_path):
    target=manager(tmp_path);manifest=create(target);directory=target.root/manifest["workspace_id"]
    assert manifest["status"]=="DISPOSABLE_LOCAL_SANDBOX" and manifest["network_allowed"] is False and manifest["authoritative_data_write_allowed"] is False
    assert (directory/"manifest.json").is_file() and (directory/"experiment.sqlite3").is_file()
    with sqlite3.connect(directory/"experiment.sqlite3") as connection:assert connection.execute("SELECT value FROM workspace_metadata WHERE key='experiment_id'").fetchone()[0]=="EXP-1"
    assert target.inspect(manifest["workspace_id"])["manifest_sha256"]==manifest["manifest_sha256"]
def test_cannot_purge_before_retention_and_purges_exact_files_after(tmp_path):
    target=manager(tmp_path);manifest=create(target)
    with pytest.raises(ValueError,match="not expired"):target.purge_expired(manifest["workspace_id"],now="2026-01-01T23:59:59+00:00")
    result=target.purge_expired(manifest["workspace_id"],now="2026-01-02T00:00:00+00:00")
    assert result["status"]=="PURGED_AFTER_RETENTION" and not (target.root/manifest["workspace_id"]).exists()
def test_unexpected_file_blocks_purge(tmp_path):
    target=manager(tmp_path);manifest=create(target);directory=target.root/manifest["workspace_id"];(directory/"evidence.txt").write_text("preserve")
    with pytest.raises(ValueError,match="unexpected files"):target.purge_expired(manifest["workspace_id"],now="2026-02-01T00:00:00+00:00")
    assert (directory/"evidence.txt").read_text()=="preserve"
def test_tampered_manifest_and_symlink_root_fail_closed(tmp_path):
    target=manager(tmp_path);manifest=create(target);path=target.root/manifest["workspace_id"]/"manifest.json";value=json.loads(path.read_text());value["expires_at"]="2025-01-01T00:00:00+00:00";path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="integrity"):target.inspect(manifest["workspace_id"])
    outside=tmp_path/"outside";outside.mkdir();(outside/".experiment-sandbox-root").write_text(ROOT_MARKER);link=tmp_path/"link";link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match="non-symlink"):DisposableExperimentWorkspace(link).create(**BASE)
@pytest.mark.parametrize("field,value,fragment",[("experiment_id","../escape","unsafe"),("dataset_manifest_sha256","bad","SHA-256"),("retention_hours",0,"between"),("retention_hours",721,"between")])
def test_invalid_creation_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment):create(manager(tmp_path),**{field:value})
def test_exact_root_marker_is_required(tmp_path):
    root=tmp_path/"root";root.mkdir()
    with pytest.raises(ValueError,match="safety marker"):DisposableExperimentWorkspace(root).create(**BASE)
