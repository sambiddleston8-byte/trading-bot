import hashlib
import os
from pathlib import Path
import pytest
from core.knowledge import ObsidianKnowledgeExporter

BASE={"category":"Companies","record_id":"NVDA","title":"NVIDIA","authoritative_source":"decision-ledger:DEC-1","authoritative_record_hash":"a"*64,"sections":{"Current view":["Research summary."],"Risks":"Valuation risk."},"generated_at":"2026-01-01T00:00:00+00:00"}

def export(target,**changes):values=dict(BASE);values.update(changes);return target.export(**values)

def test_writes_non_authoritative_generated_obsidian_page(tmp_path):
    target=ObsidianKnowledgeExporter(tmp_path);path=export(target);content=path.read_text()
    assert path==tmp_path/"Companies"/"NVDA.generated.md"
    assert "authoritative: false" in content and "database/immutable ledger and Git history remain authoritative" in content
    assert "# NVIDIA" in content and "## Risks" in content

def test_identical_export_is_idempotent_and_managed_update_is_allowed(tmp_path):
    target=ObsidianKnowledgeExporter(tmp_path);path=export(target);before=path.stat().st_ino
    assert export(target)==path and path.stat().st_ino==before
    export(target,sections={"Current view":"Updated evidence."},generated_at="2026-01-02T00:00:00+00:00")
    assert "Updated evidence." in path.read_text()

def test_manual_edit_is_never_overwritten(tmp_path):
    target=ObsidianKnowledgeExporter(tmp_path);path=export(target);path.write_text(path.read_text().replace("Research summary.","Manual note."))
    with pytest.raises(FileExistsError,match="manually edited"):export(target,sections={"Current view":"New generated view."})
    assert "Manual note." in path.read_text()

def test_unmanaged_page_and_symlink_are_never_overwritten(tmp_path):
    target=ObsidianKnowledgeExporter(tmp_path);folder=tmp_path/"Companies";folder.mkdir();path=folder/"NVDA.generated.md";path.write_text("manual")
    with pytest.raises(FileExistsError,match="not a managed"):export(target)
    path.unlink();outside=tmp_path/"outside.md";outside.write_text("safe");path.symlink_to(outside)
    with pytest.raises(FileExistsError,match="symlinked"):export(target)
    assert outside.read_text()=="safe"

@pytest.mark.parametrize("field,value,fragment",[("category","Unknown","not supported"),("record_id","../escape","unsafe"),("record_id","a/b","unsafe"),("authoritative_record_hash","bad","SHA-256"),("sections",{},"one section"),("sections",{"# bad":"content"},"heading")])
def test_invalid_export_fails_closed(tmp_path,field,value,fragment):
    with pytest.raises(ValueError,match=fragment):export(ObsidianKnowledgeExporter(tmp_path),**{field:value})

def test_all_roadmap_categories_are_supported(tmp_path):
    target=ObsidianKnowledgeExporter(tmp_path)
    for category in ("Companies","Investment-Decisions","Strategies","Research-Lessons","Experiments","Architecture","Investment-Committee","Post-Mortems"):
        assert export(target,category=category,record_id=category).parent.name==category
