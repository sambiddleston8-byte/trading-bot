from datetime import datetime, timedelta, timezone
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import active_pipeline_image_approval as module
from core.orchestration.active_pipeline_image_approval import (
    REQUIRED_ENTRYPOINT,
    REQUIRED_ENVIRONMENT,
    ActivePipelineImageApprovalLedger,
)


IMAGE_DIGEST = "a" * 64
IMAGE = "registry.example/sam-pat/replay@sha256:" + IMAGE_DIGEST


def values(now=None):
    approved = now or datetime.now(timezone.utc)
    return {
        "image_reference": IMAGE,
        "git_revision": "1" * 40,
        "dependency_lock_sha256": "2" * 64,
        "runner_entrypoint_source_sha256": "3" * 64,
        "dockerfile_sha256": "4" * 64,
        "build_provenance_sha256": "5" * 64,
        "security_review_evidence_sha256": "6" * 64,
        "built_by": "CI_BUILD_IDENTITY",
        "built_at": approved - timedelta(minutes=2),
        "reviewed_by": "INDEPENDENT_SECURITY_REVIEWER",
        "reviewed_at": approved - timedelta(minutes=1),
        "approved_at": approved,
    }


def approve(tmp_path, **changes):
    ledger = ActivePipelineImageApprovalLedger(tmp_path / "image-approvals.jsonl")
    payload = values()
    payload.update(changes)
    return ledger, ledger.approve(**payload)


def rewrite(path, **changes):
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(module._canonical_json(record) + "\n", encoding="utf-8")


def test_records_one_pinned_reviewed_image_without_granting_authority(tmp_path):
    ledger, record = approve(tmp_path)

    assert record["record_type"] == "ACTIVE_PIPELINE_SIMULATION_IMAGE_APPROVAL"
    assert record["status"] == "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY"
    assert record["image_digest_sha256"] == IMAGE_DIGEST
    assert record["entrypoint"] == REQUIRED_ENTRYPOINT
    assert record["execution_environment"] == REQUIRED_ENVIRONMENT
    assert record["previous_hash"] == GENESIS_HASH
    assert record["simulation_only"] is True
    for field in module.FIXED_FALSE:
        assert record[field] is False
    assert ledger.verify() == [record]


@pytest.mark.parametrize(
    "image_reference",
    [
        "registry.example/sam-pat/replay:latest",
        "registry.example/sam-pat/replay@sha256:short",
        "https://registry.example/sam-pat/replay@sha256:" + IMAGE_DIGEST,
        "registry.example/replay@sha256:" + "A" * 64,
    ],
)
def test_tag_only_or_malformed_image_references_are_rejected(tmp_path, image_reference):
    with pytest.raises(ValueError, match="image_reference"):
        approve(tmp_path, image_reference=image_reference)


def test_builder_and_reviewer_must_be_independent(tmp_path):
    with pytest.raises(ValueError, match="builder cannot"):
        approve(tmp_path, reviewed_by="ci_build_identity")


def test_build_review_and_approval_timestamps_must_be_real_and_chronological(tmp_path):
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="chronological"):
        approve(
            tmp_path,
            built_at=now,
            reviewed_at=now - timedelta(minutes=1),
            approved_at=now,
        )
    with pytest.raises(ValueError, match="actual append time"):
        approve(tmp_path, approved_at=now + timedelta(minutes=6))


@pytest.mark.parametrize(
    "changes,fragment",
    [
        ({"entrypoint": "/bin/sh"}, "entrypoint"),
        ({"execution_environment": "CONTAINER_WITH_NETWORK"}, "execution_environment"),
        ({"git_revision": "short"}, "git_revision"),
        ({"dependency_lock_sha256": "bad"}, "SHA-256"),
    ],
)
def test_unpinned_or_unsafe_identity_fields_fail_closed(tmp_path, changes, fragment):
    with pytest.raises(ValueError, match=fragment):
        approve(tmp_path, **changes)


def test_rehashed_digest_or_authority_tampering_is_detected(tmp_path):
    ledger, _ = approve(tmp_path)
    rewrite(ledger.path, image_digest_sha256="b" * 64)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()

    ledger.path.unlink()
    _, _ = approve(tmp_path)
    rewrite(ledger.path, live_trading_enabled=True)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_one_image_cannot_receive_multiple_mutable_approvals(tmp_path):
    ledger = ActivePipelineImageApprovalLedger(tmp_path / "image-approvals.jsonl")
    payload = values()
    first = ledger.approve(**payload)
    assert ledger.approve(**payload) == first
    changed = dict(payload)
    changed["security_review_evidence_sha256"] = "7" * 64
    with pytest.raises(LedgerIntegrityError, match="single immutable approval"):
        ledger.approve(**changed)


def test_incomplete_or_modified_ledger_fails_closed(tmp_path):
    ledger, _ = approve(tmp_path)
    ledger.path.write_bytes(ledger.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
