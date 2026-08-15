from __future__ import annotations

"""Canonical, provider-neutral observation envelopes for sealed replay roles.

The envelope is deliberately narrower than a provider adapter.  It standardises
the evidence and bitemporal fields that every normalized observation must carry,
but leaves provider-specific payload semantics unresolved until representative
provider samples have been qualified.  Availability, not retrieval, determines
whether an observation was knowable at a decision cutoff.

This module performs no I/O and exposes no execution, broker, or provider path.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp
from core.orchestration.replay_input_coverage import (
    DATASET_ARTIFACTS,
    declared_artifact_roles,
    engine_input_kinds,
    engine_input_roles,
    verify_contract_integrity,
)

if TYPE_CHECKING:
    from core.orchestration.sealed_replay_invocation import SealedReplayInvocation


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "canonical-historical-role-cutoff-v1"
_BATCH_AUTHORITY = object()
_SHA256_LENGTH = 64
EFFECTIVE_BY_CUTOFF = "EFFECTIVE_BY_CUTOFF"
KNOWN_FUTURE_EFFECTIVE_ALLOWED = "KNOWN_FUTURE_EFFECTIVE_ALLOWED"

REQUIRED_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "role",
        "provider_id",
        "provider_dataset_id",
        "provider_record_id",
        "effective_at",
        "available_at",
        "retrieved_at",
        "observation_cutoff_at",
        "source_payload_sha256",
        "normalized_payload_sha256",
        "payload",
    }
)


@dataclass(frozen=True, slots=True)
class HistoricalRoleSchema:
    """Shared envelope contract for one active-pipeline dataset role."""

    role: str
    schema_version: str = SCHEMA_VERSION
    cutoff_field: str = "available_at"
    effective_at_policy: str = EFFECTIVE_BY_CUTOFF
    empty_observations_allowed: bool = False
    provider_payload_semantics_qualified: bool = False
    required_observation_fields: frozenset[str] = REQUIRED_OBSERVATION_FIELDS


_KNOWN_FUTURE_EFFECTIVE_ROLES = frozenset(
    {
        "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES",
        "MARKET_CALENDARS_AND_HALTS",
        "POINT_IN_TIME_CORPORATE_EVENTS",
        "UNIVERSE_MEMBERSHIP",
    }
)
_SPARSE_ROLES = frozenset(
    {
        "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES",
        "POINT_IN_TIME_CORPORATE_EVENTS",
        "POINT_IN_TIME_NEWS",
        "POINT_IN_TIME_SPECIALIST_RESEARCH",
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE",
    }
)

ROLE_SCHEMAS: Mapping[str, HistoricalRoleSchema] = MappingProxyType(
    {
        role: HistoricalRoleSchema(
            role=role,
            effective_at_policy=(
                KNOWN_FUTURE_EFFECTIVE_ALLOWED
                if role in _KNOWN_FUTURE_EFFECTIVE_ROLES
                else EFFECTIVE_BY_CUTOFF
            ),
            empty_observations_allowed=role in _SPARSE_ROLES,
        )
        for role in sorted(declared_artifact_roles())
    }
)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("payload must be finite canonical JSON data") from error


def normalized_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash one normalized payload using the platform's canonical JSON form."""

    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _text(value: Any, name: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = _text(value, name, maximum=_SHA256_LENGTH)
    if len(resolved) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in resolved
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def verify_role_schema_registry() -> None:
    """Fail if the schema registry drifts from the active replay role contract."""

    declared = declared_artifact_roles()
    if set(ROLE_SCHEMAS) != declared:
        raise ValueError("historical role schemas must cover the active replay roles exactly")
    for role, schema in ROLE_SCHEMAS.items():
        if (
            schema.role != role
            or schema.schema_version != SCHEMA_VERSION
            or schema.cutoff_field != "available_at"
            or schema.effective_at_policy
            not in {EFFECTIVE_BY_CUTOFF, KNOWN_FUTURE_EFFECTIVE_ALLOWED}
            or schema.empty_observations_allowed != (role in _SPARSE_ROLES)
            or (role in _KNOWN_FUTURE_EFFECTIVE_ROLES)
            != (schema.effective_at_policy == KNOWN_FUTURE_EFFECTIVE_ALLOWED)
            or schema.provider_payload_semantics_qualified is not False
            or schema.required_observation_fields != REQUIRED_OBSERVATION_FIELDS
        ):
            raise ValueError(f"historical role schema is invalid: {role}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _normalize_observation(
    value: Mapping[str, Any],
    *,
    expected_role: str,
    decision_at: datetime,
) -> Mapping[str, Any]:
    schema = ROLE_SCHEMAS[expected_role]
    if not isinstance(value, Mapping) or set(value) != schema.required_observation_fields:
        raise ValueError("historical observation has missing or unsupported fields")
    role = _text(value.get("role"), "role", maximum=100)
    if role != expected_role or role not in ROLE_SCHEMAS:
        raise ValueError("historical observation role does not match its role schema")
    observation_schema_version = _text(
        value.get("schema_version"), "schema_version", maximum=20
    )
    if observation_schema_version != schema.schema_version:
        raise ValueError("historical observation schema version is unsupported")

    effective_at = _timestamp(value.get("effective_at"), "effective_at")
    available_at = _timestamp(
        value.get(schema.cutoff_field), schema.cutoff_field
    )
    retrieved_at = _timestamp(value.get("retrieved_at"), "retrieved_at")
    cutoff_at = _timestamp(value.get("observation_cutoff_at"), "observation_cutoff_at")
    if cutoff_at != decision_at:
        raise ValueError("observation cutoff must equal the exact decision timestamp")
    if available_at > decision_at:
        raise ValueError("observation available_at is after the decision timestamp")
    if schema.effective_at_policy == EFFECTIVE_BY_CUTOFF and effective_at > decision_at:
        raise ValueError(
            f"{expected_role} effective_at is after the decision timestamp"
        )
    if retrieved_at < available_at:
        raise ValueError("observation retrieved_at cannot predate available_at")

    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    payload_json = _canonical_json(payload)
    canonical_payload = json.loads(payload_json)
    expected_payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    supplied_payload_hash = _sha256(
        value.get("normalized_payload_sha256"), "normalized_payload_sha256"
    )
    if supplied_payload_hash != expected_payload_hash:
        raise ValueError("normalized payload hash does not match the payload")

    return MappingProxyType(
        {
            "schema_version": observation_schema_version,
            "role": role,
            "provider_id": _text(value.get("provider_id"), "provider_id"),
            "provider_dataset_id": _text(
                value.get("provider_dataset_id"), "provider_dataset_id"
            ),
            "provider_record_id": _text(
                value.get("provider_record_id"), "provider_record_id", maximum=1000
            ),
            "effective_at": effective_at.isoformat(timespec="microseconds"),
            "available_at": available_at.isoformat(timespec="microseconds"),
            "retrieved_at": retrieved_at.isoformat(timespec="microseconds"),
            "observation_cutoff_at": cutoff_at.isoformat(timespec="microseconds"),
            "source_payload_sha256": _sha256(
                value.get("source_payload_sha256"), "source_payload_sha256"
            ),
            "normalized_payload_sha256": supplied_payload_hash,
            "payload": _freeze_json(canonical_payload),
        }
    )


@dataclass(frozen=True, slots=True)
class HistoricalRoleCutoffBatch:
    """Immutable cutoff-checked observations for one active replay engine."""

    schema_version: str
    policy_version: str
    engine_key: str
    decision_at: str
    observations_by_role: Mapping[str, tuple[Mapping[str, Any], ...]]
    batch_sha256: str
    observation_cutoffs_validated: bool = True
    normalized_payload_hashes_validated: bool = True
    provider_payload_semantics_qualified: bool = False
    source_bytes_authenticated: bool = False
    observation_selection_validated: bool = False
    role_coverage_validated: bool = False
    dataset_commitment_bound: bool = False
    engine_input_ready: bool = False
    replay_executed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _BATCH_AUTHORITY:
            raise PermissionError(
                "HistoricalRoleCutoffBatch must be issued by "
                "enforce_historical_role_cutoffs"
            )
        object.__setattr__(self, "_authority", None)
        if (
            self.observation_cutoffs_validated is not True
            or self.normalized_payload_hashes_validated is not True
            or any(
                getattr(self, name) is not False
                for name in (
                    "provider_payload_semantics_qualified",
                    "source_bytes_authenticated",
                    "observation_selection_validated",
                    "role_coverage_validated",
                    "dataset_commitment_bound",
                    "engine_input_ready",
                    "replay_executed",
                    "broker_connection_allowed",
                    "orders_submitted",
                    "live_trading_enabled",
                )
            )
        ):
            raise ValueError("historical cutoff batch safety flags were altered")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "engine_key": self.engine_key,
            "decision_at": self.decision_at,
            "observations_by_role": {
                role: [_thaw_json(item) for item in observations]
                for role, observations in self.observations_by_role.items()
            },
            "batch_sha256": self.batch_sha256,
            "observation_cutoffs_validated": self.observation_cutoffs_validated,
            "normalized_payload_hashes_validated": (
                self.normalized_payload_hashes_validated
            ),
            "provider_payload_semantics_qualified": (
                self.provider_payload_semantics_qualified
            ),
            "source_bytes_authenticated": self.source_bytes_authenticated,
            "observation_selection_validated": self.observation_selection_validated,
            "role_coverage_validated": self.role_coverage_validated,
            "dataset_commitment_bound": self.dataset_commitment_bound,
            "engine_input_ready": self.engine_input_ready,
            "replay_executed": self.replay_executed,
            "broker_connection_allowed": self.broker_connection_allowed,
            "orders_submitted": self.orders_submitted,
            "live_trading_enabled": self.live_trading_enabled,
        }


def enforce_historical_role_cutoffs(
    *,
    engine_key: str,
    decision_at: str | datetime,
    observations_by_role: Mapping[str, Sequence[Mapping[str, Any]]],
    invocation: "SealedReplayInvocation | None" = None,
) -> HistoricalRoleCutoffBatch:
    """Validate every normalized observation before any engine adapter sees it.

    The function rejects the whole batch if even one observation was unavailable
    at the decision timestamp. Availability exactly at that timestamp is
    included. ``retrieved_at`` may be later than the decision:
    it records archival acquisition and is never substituted for availability.
    Provider-specific payload semantics and source authentication remain explicit
    prerequisites, so a successful batch is still not engine-ready.
    """

    verify_role_schema_registry()
    resolved_engine = _text(engine_key, "engine_key", maximum=100)
    verify_contract_integrity()
    kinds_by_engine = engine_input_kinds()
    roles_by_engine = engine_input_roles()
    if resolved_engine not in kinds_by_engine:
        raise ValueError("engine_key is not an active replay engine")
    kinds = kinds_by_engine[resolved_engine]
    roles = roles_by_engine[resolved_engine]
    if DATASET_ARTIFACTS not in kinds or not roles:
        raise ValueError("engine_key does not consume historical dataset roles")
    if not isinstance(observations_by_role, Mapping) or set(observations_by_role) != set(
        roles
    ):
        raise ValueError("observations must cover exactly the engine's historical roles")

    cutoff = _timestamp(decision_at, "decision_at")
    canonical_cutoff = cutoff.isoformat(timespec="microseconds")
    if invocation is not None:
        invocation.require_valid()
        if _timestamp(invocation.now(), "sealed invocation clock") != cutoff:
            raise ValueError("sealed replay invocation clock does not match decision_at")

    normalized: dict[str, tuple[Mapping[str, Any], ...]] = {}
    seen_provider_records: set[tuple[str, str, str]] = set()
    for role in sorted(roles):
        values = observations_by_role[role]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError(f"observations for {role} must be an ordered sequence")
        if not values and not ROLE_SCHEMAS[role].empty_observations_allowed:
            raise ValueError(f"observations for dense role {role} must be nonempty")
        records = tuple(
            _normalize_observation(value, expected_role=role, decision_at=cutoff)
            for value in values
        )
        identities = [
            (
                item["provider_id"],
                item["provider_dataset_id"],
                item["provider_record_id"],
            )
            for item in records
        ]
        if identities != sorted(identities):
            raise ValueError(f"observations for {role} must be canonically ordered")
        if len(identities) != len(set(identities)):
            raise ValueError(f"observations for {role} repeat a provider record identity")
        if any(identity in seen_provider_records for identity in identities):
            raise ValueError("provider record identities must be unique across roles")
        seen_provider_records.update(identities)
        normalized[role] = records

    hash_material = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "engine_key": resolved_engine,
        "decision_at": canonical_cutoff,
        "observations_by_role": {
            role: [_thaw_json(item) for item in values]
            for role, values in normalized.items()
        },
        "safety_flags": {
            "observation_cutoffs_validated": True,
            "normalized_payload_hashes_validated": True,
            "provider_payload_semantics_qualified": False,
            "source_bytes_authenticated": False,
            "observation_selection_validated": False,
            "role_coverage_validated": False,
            "dataset_commitment_bound": False,
            "engine_input_ready": False,
            "replay_executed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "live_trading_enabled": False,
        },
    }
    batch_hash = hashlib.sha256(
        _canonical_json(hash_material).encode("utf-8")
    ).hexdigest()
    return HistoricalRoleCutoffBatch(
        schema_version=SCHEMA_VERSION,
        policy_version=POLICY_VERSION,
        engine_key=resolved_engine,
        decision_at=canonical_cutoff,
        observations_by_role=MappingProxyType(normalized),
        batch_sha256=batch_hash,
        _authority=_BATCH_AUTHORITY,
    )
