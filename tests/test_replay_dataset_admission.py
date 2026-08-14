import json
from datetime import datetime, timezone

import pytest

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.replay_dataset_admission import (
    OPTIONAL_ROLES,
    POLICY_VERSION,
    POLICY_VERSION_V1,
    POLICY_VERSION_V2,
    REQUIRED_ROLES,
    ReplayDatasetAdmissionLedger,
    _hash,
    admittable_roles_for,
    _manifest,
)


# The six point-in-time roles v2 adds on top of the v1 alphabet.
V2_ONLY_ROLES = frozenset({
    "POINT_IN_TIME_ANALYST_AND_EARNINGS_ESTIMATES",
    "POINT_IN_TIME_CORPORATE_EVENTS",
    "POINT_IN_TIME_MACRO_SERIES",
    "POINT_IN_TIME_NEWS",
    "POINT_IN_TIME_SPECIALIST_RESEARCH",
    "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE",
})


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


def inputs(
    tmp_path, *, gap=False, plan_access="2022-03-01T00:00:00+00:00", extra_roles=()
):
    contents = source_ledger(tmp_path)
    terms = ingest(contents, "https://provider.example/terms", b"approved terms", "full document")
    artifacts = []
    artifact_records = []
    for index, role in enumerate(sorted(REQUIRED_ROLES) + list(extra_roles), start=1):
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
    assert record["policy_version"] == POLICY_VERSION_V2
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
        _manifest(json.dumps(parsed).encode(), POLICY_VERSION_V1)


def test_manifest_accepts_optional_raw_daily_bars_without_changing_required_roles(tmp_path):
    values = inputs(tmp_path)
    _, payload = values[2].read_verified(values[5]["content_evidence_id"])
    parsed = json.loads(payload)
    parsed["artifacts"].append({
        "role": "RAW_DAILY_SESSION_BARS", "content_evidence_id": "CONTENT-BARS",
    })
    artifacts, _ = _manifest(json.dumps(parsed).encode(), POLICY_VERSION_V1)
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


class _FixedClock(datetime):
    """Freezes admit()'s wall-clock skew check so a v1 ledger line is fully
    reproducible; every other input below is already a literal fixture."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2022, 3, 2, 0, 5, 0, tzinfo=timezone.utc)


def test_golden_v1_admission_record_shape_and_fixed_fields_are_literal(tmp_path, monkeypatch):
    # v1 remains admissible so pre-v2 ledgers keep verifying; this test locks
    # its literal field set and fixed values so the v1/v2 split cannot
    # silently change admission_id/record_hash identity material.
    from core.orchestration import replay_dataset_admission as module
    monkeypatch.setattr(module, "datetime", _FixedClock)

    ledger, terms, manifest = target(tmp_path)
    expected_admitted_at = module._timestamp("2022-03-02T00:05:00+00:00").isoformat()
    record = admit(
        ledger, terms, manifest,
        admitted_at="2022-03-02T00:05:00+00:00",
        compatibility_policy_version=POLICY_VERSION_V1,
    )

    assert set(record) == {
        "schema_version", "policy_version", "admission_id", "record_type", "status",
        "admitted_at", "admitted_by", "replay_plan_id", "replay_plan_record_hash",
        "source_approval_id", "source_approval_record_hash",
        "terms_content_evidence_id", "terms_content_record_hash",
        "dataset_manifest_content_evidence_id", "dataset_manifest_content_record_hash",
        "dataset_commitment_sha256", "artifacts", "coverage_references",
        "required_artifact_roles_present", "authenticated_source_bytes_verified",
        "approved_source_and_terms_linked", "bounded_universe_intervals_reconciled",
        "source_attestation_only", "ground_truth_independently_proven",
        "evaluation_observations_interpreted", "model_trained", "replay_executed",
        "performance_calculated", "performance_claim_allowed",
        "paper_broker_submission_enabled", "broker_connection_allowed",
        "live_trading_enabled", "previous_hash", "record_hash",
    }
    assert record["schema_version"] == "1.0"
    assert record["policy_version"] == POLICY_VERSION_V1
    assert record["record_type"] == "SEALED_REPLAY_DATASET_METADATA_ADMISSION"
    assert record["status"] == "PREREQUISITES_LINKED_AWAITING_CONTROLLED_REPLAY"
    assert record["admitted_at"] == expected_admitted_at
    assert record["previous_hash"] == GENESIS_HASH
    for name in (
        "required_artifact_roles_present", "authenticated_source_bytes_verified",
        "approved_source_and_terms_linked", "bounded_universe_intervals_reconciled",
        "source_attestation_only",
    ):
        assert record[name] is True
    for name in (
        "ground_truth_independently_proven", "evaluation_observations_interpreted",
        "model_trained", "replay_executed", "performance_calculated",
        "performance_claim_allowed", "paper_broker_submission_enabled",
        "broker_connection_allowed", "live_trading_enabled",
    ):
        assert record[name] is False

    assert [item["role"] for item in record["artifacts"]] == [
        "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES",
        "MARKET_CALENDARS_AND_HALTS",
        "POINT_IN_TIME_FUNDAMENTALS",
        "TOTAL_RETURN_PRICES",
        "UNIVERSE_MEMBERSHIP",
    ]
    assert all(
        set(item) == {"role", "content_evidence_id"} for item in record["artifacts"]
    )
    assert [item["coverage_id"] for item in record["coverage_references"]] == [
        "UCOV-NASDAQ100", "UCOV-SP500",
    ]

    material = {key: value for key, value in record.items() if key != "record_hash"}
    assert record["record_hash"] == _hash(material)

    line = ledger.path.read_text()
    assert line == json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    # The v2 alphabet already exists here, and the v1 line still verifies
    # unchanged -- byte for byte, with no rewrite or migration.
    assert ledger.verify() == [record]
    assert ledger.path.read_text() == line


def test_policy_version_is_not_admission_identity_material(tmp_path):
    """The same prerequisites keep one identity hash under either policy."""

    values = inputs(tmp_path)
    plan, approval, contents, coverage, terms, manifest = values
    legacy, _, _ = target(tmp_path, values)
    current = ReplayDatasetAdmissionLedger(
        tmp_path / "current.jsonl", plan_ledger=VerifiedLedger([plan]),
        approval_ledger=VerifiedLedger([approval]), content_ledger=contents,
        coverage_ledger=VerifiedLedger(coverage),
    )

    first = admit(
        legacy, terms, manifest, compatibility_policy_version=POLICY_VERSION_V1
    )
    second = admit(current, terms, manifest)

    assert first["policy_version"] == POLICY_VERSION_V1
    assert second["policy_version"] == POLICY_VERSION_V2
    assert first["admission_id"] == second["admission_id"]
    assert first["dataset_commitment_sha256"] == second["dataset_commitment_sha256"]
    assert first["artifacts"] == second["artifacts"]
    assert first["coverage_references"] == second["coverage_references"]
    assert legacy.verify() == [first]
    assert current.verify() == [second]


def test_policy_alphabets_are_v1_plus_exactly_six_point_in_time_roles():
    v1 = admittable_roles_for(POLICY_VERSION_V1)
    v2 = admittable_roles_for(POLICY_VERSION_V2)

    assert v1 == REQUIRED_ROLES | {"RAW_DAILY_SESSION_BARS"}
    assert v1 == REQUIRED_ROLES | OPTIONAL_ROLES
    assert len(v1) == 7
    assert v2 - v1 == V2_ONLY_ROLES
    assert len(v2) == 13
    assert POLICY_VERSION == POLICY_VERSION_V2


def test_v1_rejects_and_v2_accepts_a_new_point_in_time_role(tmp_path):
    values = inputs(tmp_path, extra_roles=("POINT_IN_TIME_NEWS",))
    ledger, terms, manifest = target(tmp_path, values)

    with pytest.raises(ValueError, match="unsupported role reference"):
        admit(ledger, terms, manifest, compatibility_policy_version=POLICY_VERSION_V1)
    assert ledger.records() == []

    record = admit(ledger, terms, manifest)
    assert record["policy_version"] == POLICY_VERSION_V2
    assert {item["role"] for item in record["artifacts"]} == REQUIRED_ROLES | {
        "POINT_IN_TIME_NEWS"
    }
    assert ledger.verify() == [record]


@pytest.mark.parametrize("version", [
    "sealed-replay-dataset-admission-v0", "SEALED-REPLAY-DATASET-ADMISSION-V2",
    "", None, 2, ["sealed-replay-dataset-admission-v1"],
])
def test_admittable_roles_for_rejects_an_unknown_policy_version(version):
    with pytest.raises(ValueError, match="unsupported replay-dataset admission policy version"):
        admittable_roles_for(version)


def test_admit_fails_closed_for_an_unknown_policy_version(tmp_path):
    ledger, terms, manifest = target(tmp_path)
    with pytest.raises(ValueError, match="unsupported replay-dataset admission policy version"):
        admit(
            ledger, terms, manifest,
            compatibility_policy_version="sealed-replay-dataset-admission-v0",
        )
    assert ledger.records() == []


def test_the_compatibility_override_can_only_ever_re_stamp_v1(tmp_path):
    ledger, terms, manifest = target(tmp_path)

    # It is not a general version selector: even the current policy is refused,
    # so nothing but a superseded v1 record can travel through it.
    with pytest.raises(ValueError, match="only the superseded v1 policy"):
        admit(ledger, terms, manifest, compatibility_policy_version=POLICY_VERSION_V2)
    assert ledger.records() == []

    # Left alone, a new admission stamps v2.
    assert admit(ledger, terms, manifest)["policy_version"] == POLICY_VERSION_V2


def test_recorded_unknown_policy_version_fails_verification(tmp_path):
    ledger, terms, manifest = target(tmp_path)
    record = admit(ledger, terms, manifest)
    record["policy_version"] = "sealed-replay-dataset-admission-v3"
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = _hash(material)
    ledger.path.write_text(json.dumps(record) + "\n")

    with pytest.raises(LedgerIntegrityError, match="is invalid"):
        ledger.verify()


def test_rehashed_policy_downgrade_cannot_launder_a_v2_only_role(tmp_path):
    values = inputs(tmp_path, extra_roles=("POINT_IN_TIME_NEWS",))
    ledger, terms, manifest = target(tmp_path, values)
    record = admit(ledger, terms, manifest)
    record["policy_version"] = POLICY_VERSION_V1
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = _hash(material)
    ledger.path.write_text(json.dumps(record) + "\n")

    # Roles are validated against the alphabet the record itself claims, so a
    # rehashed downgrade invalidates the very artifacts the record carries.
    with pytest.raises(LedgerIntegrityError, match="is invalid"):
        ledger.verify()


def test_manifest_shape_is_identical_under_both_policy_versions(tmp_path):
    values = inputs(tmp_path)
    _, payload = values[2].read_verified(values[5]["content_evidence_id"])
    parsed = json.loads(payload)

    assert set(parsed) == {
        "schema_version", "manifest_type", "artifacts", "coverage_references",
    }
    for version in (POLICY_VERSION_V1, POLICY_VERSION_V2):
        artifacts, refs = _manifest(payload, version)
        assert {item["role"] for item in artifacts} == REQUIRED_ROLES
        assert all(set(item) == {"role", "content_evidence_id"} for item in artifacts)
        assert all(set(item) == {"coverage_id", "coverage_record_hash"} for item in refs)

    with pytest.raises(ValueError, match="unsupported fields"):
        _manifest(
            json.dumps({**parsed, "policy_version": POLICY_VERSION_V2}).encode(),
            POLICY_VERSION_V2,
        )


def test_manifest_role_alphabet_follows_the_policy_version_it_is_read_under(tmp_path):
    values = inputs(tmp_path, extra_roles=("POINT_IN_TIME_NEWS",))
    _, payload = values[2].read_verified(values[5]["content_evidence_id"])

    artifacts, _ = _manifest(payload, POLICY_VERSION_V2)
    assert {item["role"] for item in artifacts} == REQUIRED_ROLES | {"POINT_IN_TIME_NEWS"}
    with pytest.raises(ValueError, match="unsupported role reference"):
        _manifest(payload, POLICY_VERSION_V1)
    with pytest.raises(ValueError, match="unsupported replay-dataset admission policy version"):
        _manifest(payload, "sealed-replay-dataset-admission-v0")


def test_manifest_rejects_a_role_no_policy_version_admits(tmp_path):
    values = inputs(tmp_path)
    contents = values[2]
    manifest = values[5]
    _, payload = contents.read_verified(manifest["content_evidence_id"])
    parsed = json.loads(payload)
    parsed["artifacts"][-1] = {**parsed["artifacts"][-1], "role": "NOT_A_REAL_ROLE"}
    with pytest.raises(ValueError, match="unsupported role reference"):
        _manifest(json.dumps(parsed).encode(), POLICY_VERSION_V2)


def test_manifest_rejects_a_missing_required_role(tmp_path):
    values = inputs(tmp_path)
    contents = values[2]
    manifest = values[5]
    _, payload = contents.read_verified(manifest["content_evidence_id"])
    parsed = json.loads(payload)
    parsed["artifacts"] = parsed["artifacts"][1:]
    with pytest.raises(ValueError, match="must cover every required replay-data role"):
        _manifest(json.dumps(parsed).encode(), POLICY_VERSION_V2)
