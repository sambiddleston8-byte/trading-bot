import json
from datetime import datetime, timezone

import pytest

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.replay_dataset_admission import (
    REQUIRED_ROLES,
    ReplayDatasetAdmissionLedger,
    _manifest,
)


class VerifiedLedger:
    def __init__(self, records):
        self._records = records

    def verify(self):
        return self._records


def source_ledger(tmp_path):
    return AuthenticatedSourceContentLedger(tmp_path / "sources.jsonl", tmp_path / "blobs")


def ingest(
    contents, uri, payload, locator, *,
    public="2022-02-27T00:00:00+00:00",
    retrieved="2022-02-28T00:00:00+00:00",
    recorded="2022-02-28T01:00:00+00:00",
):
    return contents.ingest(
        source_uri=uri, payload=payload, media_type="application/json",
        publicly_available_at=public, retrieved_at=retrieved,
        recorded_at=recorded, source_locator=locator,
    )


def inputs(tmp_path, *, gap=False, plan_access="2022-03-01T00:00:00+00:00"):
    contents = source_ledger(tmp_path)
    terms = ingest(contents, "https://provider.example/terms", b"approved terms", "full document")
    artifacts = []
    artifact_records = []
    for index, role in enumerate(sorted(REQUIRED_ROLES), start=1):
        uri = (
            "https://provider.example/historical-members"
            if role == "UNIVERSE_MEMBERSHIP"
            else f"https://provider.example/artifact/{index}"
        )
        record = ingest(
            contents, uri, f'{role}-sealed'.encode(), role,
            public="2022-03-01T00:00:00+00:00",
            retrieved="2022-03-02T00:00:00+00:00",
            recorded="2022-03-02T01:00:00+00:00",
        )
        artifact_records.append(record)
        artifacts.append({"role": role, "content_evidence_id": record["content_evidence_id"]})
    membership = next(
        record for record, ref in zip(artifact_records, artifacts)
        if ref["role"] == "UNIVERSE_MEMBERSHIP"
    )
    coverage = []
    refs = []
    for universe, marker in (("SP500", "c"), ("NASDAQ100", "d")):
        record = {
            "coverage_id": f"UCOV-{universe}", "record_hash": marker * 64,
            "universe": universe,
            "covers_from_at": (
                "2022-05-01T00:00:00+00:00" if gap else "2022-01-01T00:00:00+00:00"
            ),
            "through_at": "2023-01-01T00:00:00+00:00",
        "retrieved_at": "2022-03-02T00:00:00+00:00",
            "source_uri": membership["source_uri"],
            "source_input_sha256": membership["source_input_sha256"],
        }
        coverage.append(record)
        refs.append({"coverage_id": record["coverage_id"], "coverage_record_hash": record["record_hash"]})
    manifest_payload = json.dumps({
        "schema_version": "1.0",
        "manifest_type": "ACTIVE_PIPELINE_REPLAY_DATASET",
        "artifacts": artifacts,
        "coverage_references": refs,
    }, sort_keys=True, separators=(",", ":")).encode()
    manifest = ingest(
        contents, "https://provider.example/sealed-replay-manifest", manifest_payload, "root",
        public="2022-03-01T00:00:00+00:00",
        retrieved="2022-03-02T00:00:00+00:00",
        recorded="2022-03-02T01:00:00+00:00",
    )
    plan = {
        "replay_plan_id": "REPLAY-1", "record_hash": "e" * 64,
        "universe": "SP500_AND_NASDAQ100",
        "evaluation_not_before": "2022-04-01T00:00:00+00:00",
        "evaluation_not_after": "2022-12-31T00:00:00+00:00",
        "evaluation_data_access_not_before": plan_access,
        "sealed_evaluation_dataset_commitment_sha256": manifest["source_input_sha256"],
    }
    approval = {
        "approval_id": "DSA-1", "record_hash": "f" * 64,
        "approved_at": "2022-02-28T12:00:00+00:00",
        "terms_url": terms["source_uri"],
        "terms_content_sha256": terms["source_input_sha256"],
        "approved_universes": ["NASDAQ100", "SP500"],
        "permitted_uses": ["HISTORICAL_REPLAY", "LOCAL_RESEARCH"],
        "approved_data_hosts": ["provider.example"],
        "coverage_not_before": "2000-01-01", "coverage_not_after": "2025-12-31",
    }
    return plan, approval, contents, coverage, terms, manifest


def target(tmp_path, values=None):
    plan, approval, contents, coverage, terms, manifest = values or inputs(tmp_path)
    ledger = ReplayDatasetAdmissionLedger(
        tmp_path / "admission.jsonl", plan_ledger=VerifiedLedger([plan]),
        approval_ledger=VerifiedLedger([approval]), content_ledger=contents,
        coverage_ledger=VerifiedLedger(coverage),
    )
    return ledger, terms, manifest


def admit(ledger, terms, manifest, **overrides):
    values = {
        "replay_plan_id": "REPLAY-1", "source_approval_id": "DSA-1",
        "terms_content_evidence_id": terms["content_evidence_id"],
        "dataset_manifest_content_evidence_id": manifest["content_evidence_id"],
        "admitted_by": "Codex", "admitted_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(overrides)
    return ledger.admit(**values)


def test_links_exact_sealed_manifest_without_interpreting_observations_or_running(tmp_path):
    ledger, terms, manifest = target(tmp_path)
    record = admit(ledger, terms, manifest)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["dataset_commitment_sha256"] == manifest["source_input_sha256"]
    assert record["authenticated_source_bytes_verified"] is True
    assert record["bounded_universe_intervals_reconciled"] is True
    for field in (
        "ground_truth_independently_proven", "evaluation_observations_interpreted",
        "model_trained", "replay_executed", "performance_calculated",
        "performance_claim_allowed", "paper_broker_submission_enabled",
        "broker_connection_allowed", "live_trading_enabled",
    ):
        assert record[field] is False
    assert ledger.verify() == [record]


def test_manifest_rejects_two_artifacts_claiming_the_same_role(tmp_path):
    values = inputs(tmp_path)
    contents = values[2]
    manifest = values[5]
    _, payload = contents.read_verified(manifest["content_evidence_id"])
    parsed = json.loads(payload)
    duplicate = dict(parsed["artifacts"][0])
    duplicate["content_evidence_id"] = "CONTENT-DIFFERENT"
    parsed["artifacts"].append(duplicate)
    with pytest.raises(ValueError, match="duplicate role"):
        _manifest(json.dumps(parsed).encode())


def test_manifest_accepts_optional_raw_daily_bars_without_changing_required_roles(tmp_path):
    values = inputs(tmp_path)
    _, payload = values[2].read_verified(values[5]["content_evidence_id"])
    parsed = json.loads(payload)
    parsed["artifacts"].append({
        "role": "RAW_DAILY_SESSION_BARS", "content_evidence_id": "CONTENT-BARS",
    })
    artifacts, _ = _manifest(json.dumps(parsed).encode())
    assert {item["role"] for item in artifacts} == REQUIRED_ROLES | {"RAW_DAILY_SESSION_BARS"}


def test_manifest_cannot_be_opened_before_preregistered_access_time(tmp_path):
    values = inputs(tmp_path, plan_access="2099-04-01T00:00:00+00:00")
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="access time"):
        admit(ledger, terms, manifest)


def test_admission_cannot_be_backdated(tmp_path):
    ledger, terms, manifest = target(tmp_path)
    with pytest.raises(ValueError, match="actual append time"):
        admit(ledger, terms, manifest, admitted_at="2022-03-03T00:00:00+00:00")


def test_source_approval_must_exist_before_dataset_access(tmp_path):
    values = inputs(tmp_path)
    values[1]["approved_at"] = "2022-03-02T00:00:00+00:00"
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="predate"):
        admit(ledger, terms, manifest)


def test_exact_terms_bytes_must_exist_before_human_approval(tmp_path):
    values = inputs(tmp_path)
    values[1]["approved_at"] = "2022-02-27T00:00:00+00:00"
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="terms bytes"):
        admit(ledger, terms, manifest)


def test_manifest_bytes_must_match_preregistered_commitment(tmp_path):
    values = inputs(tmp_path)
    values[0]["sealed_evaluation_dataset_commitment_sha256"] = "0" * 64
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="commitment"):
        admit(ledger, terms, manifest)


def test_authenticated_terms_must_exactly_match_human_approval(tmp_path):
    values = inputs(tmp_path)
    values[1]["terms_content_sha256"] = "0" * 64
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="exact authenticated terms"):
        admit(ledger, terms, manifest)


def test_every_planned_universe_must_have_gapless_approved_coverage(tmp_path):
    values = inputs(tmp_path, gap=True)
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="gap"):
        admit(ledger, terms, manifest)
    values = inputs(tmp_path / "unapproved")
    values[1]["approved_universes"] = ["SP500"]
    ledger, terms, manifest = target(tmp_path / "unapproved", values)
    with pytest.raises(ValueError, match="every planned universe"):
        admit(ledger, terms, manifest)


def test_coverage_must_use_authenticated_membership_artifact(tmp_path):
    values = inputs(tmp_path)
    contents = values[2].verify()
    wrong = next(item for item in contents if item["source_uri"].endswith("/artifact/1"))
    values[3][0]["source_uri"] = wrong["source_uri"]
    values[3][0]["source_input_sha256"] = wrong["source_input_sha256"]
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="membership artifact"):
        admit(ledger, terms, manifest)


def test_dataset_sources_must_use_an_explicitly_approved_host(tmp_path):
    values = inputs(tmp_path)
    values[1]["approved_data_hosts"] = ["different.example"]
    ledger, terms, manifest = target(tmp_path, values)
    with pytest.raises(ValueError, match="host is not explicitly approved"):
        admit(ledger, terms, manifest)


def test_identical_retry_is_idempotent_and_plan_gets_one_admission(tmp_path):
    ledger, terms, manifest = target(tmp_path)
    first = admit(ledger, terms, manifest)
    assert admit(ledger, terms, manifest, admitted_at=first["admitted_at"]) == first
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        admit(ledger, terms, manifest, admitted_by="Different operator")


@pytest.mark.parametrize("change", [
    {"evaluation_observations_interpreted": True}, {"model_trained": True},
    {"replay_executed": True}, {"performance_claim_allowed": True},
    {"paper_broker_submission_enabled": True}, {"broker_connection_allowed": True},
    {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_execute_or_grant_authority(tmp_path, change):
    ledger, terms, manifest = target(tmp_path)
    record = admit(ledger, terms, manifest)
    record.update(change)
    from core.orchestration import replay_dataset_admission as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._hash(material)
    ledger.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()
