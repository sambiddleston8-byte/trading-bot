from __future__ import annotations

"""Authenticated, whole-dataset access boundary for one sealed replay engine.

This module resolves, for one already-admitted replay dataset and one sealed
research engine, the exact authenticated source bytes that engine's declared
dataset-artifact roles require.  It authenticates whole datasets: it does not
parse or slice a payload, does not validate any row as point-in-time correct,
and does not claim an engine is ready to consume what it returns.  It reads
nothing outside the caller-supplied ledgers, opens no clock of its own, and
wires into no pipeline, broker, execution or provider surface.

Without a sealed invocation, ``as_of`` is a caller assertion suitable only for
inert inspection.  Passing an invocation additionally binds that assertion to
the ledger-issued replay clock and authenticated-content paths.  Neither mode
makes the returned whole-file bytes row-valid or engine-ready.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import canonical_timestamp
from core.orchestration.replay_dataset_admission import ReplayDatasetAdmissionLedger
from core.orchestration.replay_input_coverage import verify_replay_input_coverage

if TYPE_CHECKING:
    from core.orchestration.sealed_replay_invocation import SealedReplayInvocation


_BOUNDARY_AUTHORITY = object()

FIXED_TRUE_FIELDS = ("source_bytes_authenticated",)
FIXED_FALSE_FIELDS = (
    "semantic_claim_validated",
    "point_in_time_rows_validated",
    "engine_input_ready",
    "replay_executed",
    "performance_calculated",
    "performance_claim_allowed",
    "paper_broker_submission_enabled",
    "broker_connection_allowed",
    "order_submitted",
    "live_trading_enabled",
)


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value))
    except ValueError as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _same_content_store(left: Any, right: Any) -> bool:
    """Compare the ledger and blob roots that define one authenticated store."""

    try:
        return (
            Path(left.path).resolve() == Path(right.path).resolve()
            and Path(left.blob_directory).resolve()
            == Path(right.blob_directory).resolve()
        )
    except (AttributeError, TypeError, OSError):
        return False


@dataclass(frozen=True, slots=True)
class PointInTimeArtifactBoundary:
    """One authenticated whole-dataset artifact; it proves nothing about rows.

    This object attests only that its ``payload`` is the exact, unmodified
    authenticated source bytes admitted for ``dataset_role`` under a verified
    admission.  It never asserts row-level point-in-time validity and never
    asserts that an engine is ready to consume it.
    """

    engine_key: str
    dataset_role: str
    content_evidence_id: str
    source_uri: str
    media_type: str
    publicly_available_at: str
    retrieved_at: str
    source_input_sha256: str
    payload: bytes
    source_bytes_authenticated: bool = True
    semantic_claim_validated: bool = False
    point_in_time_rows_validated: bool = False
    engine_input_ready: bool = False
    replay_executed: bool = False
    performance_calculated: bool = False
    performance_claim_allowed: bool = False
    paper_broker_submission_enabled: bool = False
    broker_connection_allowed: bool = False
    order_submitted: bool = False
    live_trading_enabled: bool = False
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _BOUNDARY_AUTHORITY:
            raise PermissionError(
                "PointInTimeArtifactBoundary must be issued by "
                "resolve_point_in_time_artifact_boundary"
            )
        # Spend the authority immediately so no copy of this object -- including
        # one made by dataclasses.replace -- can inherit a usable issuance right.
        object.__setattr__(self, "_authority", None)
        if any(getattr(self, name) is not True for name in FIXED_TRUE_FIELDS) or any(
            getattr(self, name) is not False for name in FIXED_FALSE_FIELDS
        ):
            raise ValueError(
                "point-in-time artifact boundary fixed safety flags were altered"
            )


def resolve_point_in_time_artifact_boundary(
    *,
    engine_key: str,
    admission_record: Mapping[str, Any],
    admission_ledger: ReplayDatasetAdmissionLedger,
    content_ledger: AuthenticatedSourceContentLedger,
    as_of: str,
    invocation: "SealedReplayInvocation | None" = None,
) -> Mapping[str, PointInTimeArtifactBoundary]:
    """Resolve every authenticated dataset artifact one engine's contract needs.

    ``admission_record`` must match, by both ``admission_id`` and
    ``record_hash``, exactly one record returned by ``admission_ledger.verify()``.
    ``engine_key`` must be a sealed engine the replay input contract covers
    completely as a dataset-artifact consumer; an unknown, derived-only or only
    partially covered engine is rejected outright rather than given a partial
    role set.  Each returned artifact's authenticated ``publicly_available_at``
    must not be after the explicit ``as_of`` boundary; there is no default
    clock and no substitute scan for a missing role.

    When ``invocation`` is omitted, ``as_of`` remains caller-asserted and the
    result is suitable only for inert inspection. It still cannot assert
    point-in-time row validity or engine readiness.
    """

    if not isinstance(admission_record, Mapping):
        raise ValueError("a supplied replay dataset admission record is required")
    resolved_engine = _required(engine_key, "engine_key")
    resolved_as_of = _timestamp(as_of, "as_of")

    admitted_content_ledger = getattr(admission_ledger, "content_ledger", None)
    if not _same_content_store(content_ledger, admitted_content_ledger):
        raise ValueError(
            "content ledger does not match the store verified by the admission ledger"
        )

    admission_id = _required(admission_record.get("admission_id"), "admission_id")
    record_hash = _required(admission_record.get("record_hash"), "record_hash")
    matches = [
        record
        for record in admission_ledger.verify()
        if record.get("admission_id") == admission_id
        and record.get("record_hash") == record_hash
    ]
    if len(matches) != 1:
        raise ValueError(
            "admission_record must match exactly one verified admission ledger record"
        )
    verified_admission = matches[0]

    coverage = verify_replay_input_coverage(verified_admission)
    known_engine_keys = {key for key, _, _ in coverage.contract_snapshot}
    if resolved_engine not in known_engine_keys:
        raise ValueError(f"engine_key is not a sealed replay engine: {resolved_engine}")
    if resolved_engine in coverage.derived_only_engine_keys:
        raise ValueError(
            f"engine_key is derived-only and consumes no dataset artifact: {resolved_engine}"
        )
    if resolved_engine not in coverage.dataset_artifact_engine_keys:
        raise ValueError(
            f"engine_key does not consume authenticated dataset artifacts: {resolved_engine}"
        )
    if resolved_engine in coverage.uncovered_engine_keys:
        raise ValueError(
            f"engine_key has incomplete replay input coverage: {resolved_engine}"
        )

    needed_roles = coverage.engine_input_roles()[resolved_engine]
    if not needed_roles:
        raise ValueError(f"engine_key declares no dataset artifact roles: {resolved_engine}")

    if invocation is not None:
        invocation.require_valid()
        if invocation.now() != as_of:
            raise ValueError(
                "sealed replay invocation clock does not match the explicit as_of value"
            )
        if Path(content_ledger.path).resolve() != Path(
            invocation.authenticated_source_ledger_path
        ).resolve():
            raise ValueError(
                "content ledger path does not match the sealed replay invocation"
            )
        if Path(content_ledger.blob_directory).resolve() != Path(
            invocation.authenticated_source_blobs_directory
        ).resolve():
            raise ValueError(
                "content ledger blob directory does not match the sealed replay invocation"
            )

    artifacts_by_role = {
        artifact["role"]: artifact for artifact in verified_admission["artifacts"]
    }
    resolved: dict[str, PointInTimeArtifactBoundary] = {}
    for role in needed_roles:
        artifact_reference = artifacts_by_role.get(role)
        if artifact_reference is None:
            raise ValueError(f"verified admission is missing required artifact role: {role}")
        content_evidence_id = _required(
            artifact_reference.get("content_evidence_id"), "content_evidence_id"
        )
        content_record, payload = content_ledger.read_verified(content_evidence_id)
        if content_record.get("content_evidence_id") != content_evidence_id:
            raise ValueError("authenticated content does not match the admitted artifact")
        public_at = _timestamp(
            content_record.get("publicly_available_at"), "publicly_available_at"
        )
        if public_at > resolved_as_of:
            raise ValueError(
                f"artifact for role {role} is not yet publicly available as of as_of"
            )
        resolved[role] = PointInTimeArtifactBoundary(
            engine_key=resolved_engine,
            dataset_role=role,
            content_evidence_id=content_evidence_id,
            source_uri=content_record["source_uri"],
            media_type=content_record["media_type"],
            publicly_available_at=content_record["publicly_available_at"],
            retrieved_at=content_record["retrieved_at"],
            source_input_sha256=content_record["source_input_sha256"],
            payload=payload,
            _authority=_BOUNDARY_AUTHORITY,
        )

    return MappingProxyType(resolved)
