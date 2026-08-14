from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import core.orchestration as orchestration
import core.orchestration.point_in_time_artifact_boundary as boundary_module
from core.orchestration.point_in_time_artifact_boundary import (
    PointInTimeArtifactBoundary,
    resolve_point_in_time_artifact_boundary,
)
from core.orchestration.replay_dataset_admission import REQUIRED_ROLES
from core.orchestration.replay_input_coverage import (
    ADMISSION_FIXED_FALSE,
    ADMISSION_FIXED_TRUE,
    ADMISSION_IDENTITY_FIELDS,
    ADMISSION_POLICY_VERSION,
    ADMISSION_RECORD_TYPE,
    ADMISSION_SCHEMA_VERSION,
    ADMISSION_STATUS,
)


AS_OF = "2021-01-01T00:00:00+00:00"


class Admission:
    def __init__(self, records, content_ledger):
        self.records = records
        self.content_ledger = content_ledger

    def verify(self):
        return list(self.records)


class Contents:
    def __init__(self, values, *, path=None, blob_directory=None):
        self.values = values
        self.path = path if path is not None else Path("/tmp/pit-boundary-ledger.jsonl")
        self.blob_directory = (
            blob_directory if blob_directory is not None else Path("/tmp/pit-boundary-blobs")
        )

    def read_verified(self, identifier):
        return self.values[identifier]


class FakeInvocation:
    def __init__(self, *, now_value, ledger_path, blob_directory):
        self._now_value = now_value
        self.authenticated_source_ledger_path = ledger_path
        self.authenticated_source_blobs_directory = blob_directory
        self.require_valid_called = False

    def require_valid(self):
        self.require_valid_called = True
        return self

    def now(self):
        return self._now_value


def admission_record(*, admission_id="RDA-1", record_hash="a" * 64, roles=None, content_ids=None):
    resolved_roles = sorted(REQUIRED_ROLES) if roles is None else list(roles)
    ids = content_ids or {role: f"SRC-{role}" for role in resolved_roles}
    record = {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "policy_version": ADMISSION_POLICY_VERSION,
        "record_type": ADMISSION_RECORD_TYPE,
        "status": ADMISSION_STATUS,
        **{name: f"VALUE-{name}" for name in ADMISSION_IDENTITY_FIELDS},
        **{name: True for name in ADMISSION_FIXED_TRUE},
        **{name: False for name in ADMISSION_FIXED_FALSE},
        "artifacts": [{"role": role, "content_evidence_id": ids[role]} for role in resolved_roles],
    }
    record["admission_id"] = admission_id
    record["record_hash"] = record_hash
    return record


def content_record(
    content_evidence_id,
    *,
    public="2020-01-01T00:00:00+00:00",
    retrieved="2020-01-02T00:00:00+00:00",
    source_uri="https://example.com/data",
    media_type="application/octet-stream",
    source_hash="b" * 64,
):
    return {
        "content_evidence_id": content_evidence_id,
        "source_uri": source_uri,
        "media_type": media_type,
        "publicly_available_at": public,
        "retrieved_at": retrieved,
        "source_input_sha256": source_hash,
    }


def environment(
    *, roles=None, admission_id="RDA-1", record_hash="a" * 64, content_ids=None, public_overrides=None,
):
    resolved_roles = sorted(REQUIRED_ROLES) if roles is None else list(roles)
    ids = content_ids or {role: f"SRC-{role}" for role in resolved_roles}
    record = admission_record(
        admission_id=admission_id, record_hash=record_hash, roles=resolved_roles, content_ids=ids
    )
    values = {}
    for role in resolved_roles:
        overrides = (public_overrides or {}).get(role, {})
        values[ids[role]] = (content_record(ids[role], **overrides), f"payload-for-{role}".encode())
    contents = Contents(values)
    return record, Admission([record], contents), contents


def test_resolves_deterministic_multi_role_artifacts_for_a_covered_engine():
    record, admission_ledger, content_ledger = environment()

    result = resolve_point_in_time_artifact_boundary(
        engine_key="market_regime",
        admission_record=record,
        admission_ledger=admission_ledger,
        content_ledger=content_ledger,
        as_of=AS_OF,
    )

    assert set(result) == {"MARKET_CALENDARS_AND_HALTS", "TOTAL_RETURN_PRICES"}
    assert list(result) == sorted(result)
    for role, artifact in result.items():
        assert artifact.engine_key == "market_regime"
        assert artifact.dataset_role == role
        assert artifact.payload == f"payload-for-{role}".encode()
        assert artifact.source_bytes_authenticated is True
        assert artifact.semantic_claim_validated is False
        assert artifact.point_in_time_rows_validated is False
        assert artifact.engine_input_ready is False
        assert artifact.replay_executed is False
        assert artifact.performance_calculated is False
        assert artifact.performance_claim_allowed is False
        assert artifact.paper_broker_submission_enabled is False
        assert artifact.broker_connection_allowed is False
        assert artifact.order_submitted is False
        assert artifact.live_trading_enabled is False


def test_rejects_artifact_not_yet_publicly_available_as_of_the_explicit_boundary():
    record, admission_ledger, content_ledger = environment(
        public_overrides={"MARKET_CALENDARS_AND_HALTS": {"public": "2099-01-01T00:00:00+00:00"}}
    )

    with pytest.raises(ValueError, match="publicly available"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )


def test_admission_record_must_match_exactly_one_verified_ledger_record():
    record, admission_ledger, content_ledger = environment()

    with pytest.raises(ValueError, match="exactly one"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record={**record, "record_hash": "z" * 64},
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )
    with pytest.raises(ValueError, match="exactly one"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record={**record, "admission_id": "RDA-OTHER"},
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )


def test_rejects_engine_with_incomplete_replay_input_coverage():
    record, admission_ledger, content_ledger = environment(roles=sorted(REQUIRED_ROLES))

    with pytest.raises(ValueError, match="incomplete"):
        resolve_point_in_time_artifact_boundary(
            engine_key="fundamental",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )


def test_rejects_unknown_and_derived_only_engines():
    record, admission_ledger, content_ledger = environment()

    with pytest.raises(ValueError, match="not a sealed replay engine"):
        resolve_point_in_time_artifact_boundary(
            engine_key="not_a_real_engine",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )
    with pytest.raises(ValueError, match="derived-only"):
        resolve_point_in_time_artifact_boundary(
            engine_key="decision",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
        )


def test_reads_only_the_exact_admitted_content_id_with_no_fallback_scan():
    roles = sorted(REQUIRED_ROLES)
    ids = {role: f"SRC-{role}" for role in roles}
    ids["TOTAL_RETURN_PRICES"] = "EVIDENCE-PRICE-1"
    ids["MARKET_CALENDARS_AND_HALTS"] = "EVIDENCE-CAL-1"
    record, admission_ledger, content_ledger = environment(
        roles=roles, content_ids=ids,
    )

    result = resolve_point_in_time_artifact_boundary(
        engine_key="market_regime",
        admission_record=record,
        admission_ledger=admission_ledger,
        content_ledger=content_ledger,
        as_of=AS_OF,
    )

    assert result["TOTAL_RETURN_PRICES"].content_evidence_id == "EVIDENCE-PRICE-1"
    assert result["MARKET_CALENDARS_AND_HALTS"].content_evidence_id == "EVIDENCE-CAL-1"


def test_invocation_binds_the_explicit_clock_and_content_ledger_paths(monkeypatch):
    record, admission_ledger, content_ledger = environment()
    invocation = FakeInvocation(
        now_value=AS_OF,
        ledger_path=content_ledger.path,
        blob_directory=content_ledger.blob_directory,
    )

    result = resolve_point_in_time_artifact_boundary(
        engine_key="market_regime",
        admission_record=record,
        admission_ledger=admission_ledger,
        content_ledger=content_ledger,
        as_of=AS_OF,
        invocation=invocation,
    )
    assert invocation.require_valid_called is True
    assert set(result) == {"MARKET_CALENDARS_AND_HALTS", "TOTAL_RETURN_PRICES"}

    different_store = Contents(
        content_ledger.values,
        path=Path("/tmp/different-admission-store.jsonl"),
        blob_directory=content_ledger.blob_directory,
    )
    with pytest.raises(ValueError, match="store verified by the admission ledger"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=different_store,
            as_of=AS_OF,
        )

    monkeypatch.setattr(
        invocation, "authenticated_source_ledger_path", Path("/tmp/some-other-ledger.jsonl")
    )
    with pytest.raises(ValueError, match="content ledger path"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
            invocation=invocation,
        )

    monkeypatch.setattr(invocation, "authenticated_source_ledger_path", content_ledger.path)
    monkeypatch.setattr(invocation, "_now_value", "2022-06-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="clock"):
        resolve_point_in_time_artifact_boundary(
            engine_key="market_regime",
            admission_record=record,
            admission_ledger=admission_ledger,
            content_ledger=content_ledger,
            as_of=AS_OF,
            invocation=invocation,
        )


def test_construction_and_replace_are_forbidden_off_the_issuing_resolver():
    record, admission_ledger, content_ledger = environment()
    result = resolve_point_in_time_artifact_boundary(
        engine_key="market_regime",
        admission_record=record,
        admission_ledger=admission_ledger,
        content_ledger=content_ledger,
        as_of=AS_OF,
    )
    issued = result["TOTAL_RETURN_PRICES"]

    with pytest.raises(PermissionError, match="resolve_point_in_time_artifact_boundary"):
        PointInTimeArtifactBoundary(
            engine_key="market_regime",
            dataset_role="TOTAL_RETURN_PRICES",
            content_evidence_id="SRC-TOTAL_RETURN_PRICES",
            source_uri="https://example.com/data",
            media_type="application/octet-stream",
            publicly_available_at="2020-01-01T00:00:00+00:00",
            retrieved_at="2020-01-02T00:00:00+00:00",
            source_input_sha256="b" * 64,
            payload=b"payload-for-TOTAL_RETURN_PRICES",
        )
    with pytest.raises(PermissionError, match="resolve_point_in_time_artifact_boundary"):
        replace(issued, dataset_role="MARKET_CALENDARS_AND_HALTS")
    with pytest.raises(PermissionError, match="resolve_point_in_time_artifact_boundary"):
        replace(issued, semantic_claim_validated=True)
    with pytest.raises(ValueError, match="fixed safety flags"):
        replace(
            issued,
            semantic_claim_validated=True,
            _authority=boundary_module._BOUNDARY_AUTHORITY,
        )


def test_module_imports_nothing_provider_or_pipeline_shaped_and_is_not_wired_in():
    tree = ast.parse(inspect.getsource(boundary_module))
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden = ("broker", "provider", "aws", "network", "pipeline")
    assert not [
        module for module in imported_modules
        if any(keyword in module.lower() for keyword in forbidden)
    ]

    init_path = Path(orchestration.__file__).parent / "__init__.py"
    assert "point_in_time_artifact_boundary" not in init_path.read_text(encoding="utf-8")
