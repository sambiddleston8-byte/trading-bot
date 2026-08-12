from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.persistence.legacy_import_preview import (
    LEGACY_STATUS,
    build_legacy_import_preview,
    write_preview_manifest,
)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_is_read_only_and_never_promotes_legacy_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    snapshot = source / "research_portfolio_20260809T132405Z.json"
    snapshot.write_text(json.dumps({"version": "legacy-v1", "holdings": [{"ticker": "TEST"}]}))
    before = snapshot.read_bytes()

    preview = build_legacy_import_preview(source)

    assert snapshot.read_bytes() == before
    assert preview["read_only"] is True
    assert preview["database_writes"] is False
    assert preview["summary"]["promotion_eligible"] == 0
    entry = preview["entries"][0]
    assert entry["classification"] == LEGACY_STATUS
    assert entry["promotion_eligible"] is False
    assert entry["timestamp_source"] == "filename"
    assert entry["holdings_count"] == 1
    assert "missing_immutable_decision_ledger" in entry["promotion_blockers"]
    assert "missing_portfolio_id" in entry["promotion_blockers"]


def test_preview_detects_duplicate_content_and_portfolio_id_collisions(tmp_path):
    payload = json.dumps({"portfolio_id": "legacy-one", "holdings": []})
    for stamp in ("20260809T132405Z", "20260809T132406Z"):
        (tmp_path / f"research_portfolio_{stamp}.json").write_text(payload)

    preview = build_legacy_import_preview(tmp_path)

    assert preview["summary"]["duplicate_hash_groups"] == 1
    assert preview["summary"]["portfolio_id_collision_groups"] == 1
    assert len(next(iter(preview["duplicate_hashes"].values()))) == 2


def test_preview_records_malformed_source_without_exposing_error_text(tmp_path):
    source = tmp_path / "research_portfolio_20260809T132405Z.json"
    source.write_text("not-json")

    preview = build_legacy_import_preview(tmp_path)

    entry = preview["entries"][0]
    assert preview["summary"]["malformed_sources"] == 1
    assert entry["parse_status"] == "FAIL"
    assert entry["parse_error_type"] == "JSONDecodeError"
    assert "parse_error" not in entry
    assert _digest(source) == entry["source_sha256"]


def test_manifest_write_does_not_modify_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    snapshot = source / "research_portfolio_20260809T132405Z.json"
    snapshot.write_text(json.dumps({"holdings": []}))
    before = _digest(snapshot)

    preview = build_legacy_import_preview(source)
    destination = tmp_path / "output" / "manifest.json"
    write_preview_manifest(preview, destination)

    assert destination.exists()
    assert _digest(snapshot) == before
    assert json.loads(destination.read_text())["classification"] == LEGACY_STATUS


def test_unreadable_source_is_blocked_instead_of_aborting(tmp_path, monkeypatch):
    from core.persistence import legacy_import_preview as module

    source = tmp_path / "research_portfolio_20260809T132405Z.json"
    source.write_text(json.dumps({"holdings": []}))
    monkeypatch.setattr(module, "_sha256", lambda _path: (_ for _ in ()).throw(PermissionError()))

    preview = build_legacy_import_preview(tmp_path)

    assert preview["summary"]["malformed_sources"] == 1
    assert preview["entries"][0]["source_sha256"] is None
    assert "unreadable_source" in preview["entries"][0]["promotion_blockers"]


def test_repository_legacy_collection_has_expected_read_only_scope():
    root = Path(__file__).resolve().parents[1]
    source = root / "data" / "research" / "portfolios"
    paths = sorted(source.glob("research_portfolio_*.json"))
    before = {path: _digest(path) for path in paths}

    preview = build_legacy_import_preview(source)

    assert preview["summary"]["discovered"] == 14
    assert preview["summary"]["promotion_eligible"] == 0
    assert all(entry["classification"] == LEGACY_STATUS for entry in preview["entries"])
    assert {path: _digest(path) for path in paths} == before
