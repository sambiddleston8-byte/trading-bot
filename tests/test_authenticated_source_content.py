import json

import pytest

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


def ledger(tmp_path):
    return AuthenticatedSourceContentLedger(
        tmp_path / "sources.jsonl", tmp_path / "source_blobs"
    )


def ingest(target, payload=b'{"rate":"0.04"}', **overrides):
    values = {
        "source_uri": "https://evidence.example/risk-free",
        "payload": payload,
        "media_type": "application/json",
        "publicly_available_at": "2025-01-02T00:00:00+00:00",
        "retrieved_at": "2025-01-03T00:00:00+00:00",
        "recorded_at": "2025-01-04T00:00:00+00:00",
        "source_locator": "$.rate",
    }
    values.update(overrides)
    return target.ingest(**values)


def test_ingests_content_addressed_bytes_and_rehashes_on_verify(tmp_path):
    target = ledger(tmp_path)
    record = ingest(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["source_bytes_authenticated"] is True
    assert record["semantic_claim_validated"] is False
    blob = target.blob_directory / record["blob_relative_path"]
    assert blob.read_bytes() == b'{"rate":"0.04"}'
    assert target.verify() == [record]


def test_identical_ingest_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    first = ingest(target)
    assert ingest(target) == first
    assert len(target.verify()) == 1


def test_changed_blob_or_missing_blob_fails_authentication(tmp_path):
    target = ledger(tmp_path)
    record = ingest(target)
    blob = target.blob_directory / record["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"changed")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()
    blob.write_bytes(b'{"rate":"0.04"}')
    blob.unlink()
    with pytest.raises(LedgerIntegrityError, match="invalid content"):
        target.verify()


def test_symlink_and_path_escape_fail_closed(tmp_path):
    target = ledger(tmp_path)
    record = ingest(target)
    blob = target.blob_directory / record["blob_relative_path"]
    blob.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b'{"rate":"0.04"}')
    blob.symlink_to(outside)
    with pytest.raises(LedgerIntegrityError, match="invalid content"):
        target.verify()


@pytest.mark.parametrize("change", [
    {"source_bytes_authenticated": False},
    {"semantic_claim_validated": True},
    {"broker_submission_enabled": True},
    {"live_trading_enabled": True},
])
def test_rehashed_metadata_tampering_cannot_expand_authority(tmp_path, change):
    target = ledger(tmp_path)
    record = ingest(target)
    record.update(change)
    from core.data_quality import authenticated_source_content as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()


def test_invalid_payload_and_source_fail_before_write(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match="between 1 byte"):
        ingest(target, payload=b"")
    with pytest.raises(ValueError, match="HTTPS"):
        ingest(target, source_uri="http://evidence.example/risk-free")
    assert target.records() == []
