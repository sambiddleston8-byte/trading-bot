from __future__ import annotations

"""Immutable, inert prerequisites for one sealed active-pipeline replay.

This module deliberately does not execute the research pipeline.  It resolves
the verified replay plan, approved no-network image, every pipeline dependency,
and every ambient point-in-time input into one canonical context.  A future
runner must require this context before it is allowed to execute.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    current_git_revision,
)
from core.orchestration.active_pipeline_image_approval import (
    FIXED_FALSE as IMAGE_FIXED_FALSE,
    REQUIRED_ENTRYPOINT,
    REQUIRED_ENVIRONMENT,
)
from core.orchestration.active_pipeline_replay_plan import REQUIRED_COMPONENTS

if TYPE_CHECKING:
    from core.orchestration.sealed_replay_invocation import SealedReplayInvocation


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "sealed-active-pipeline-replay-context-v1"
RECORD_TYPE = "SEALED_ACTIVE_PIPELINE_REPLAY_CONTEXT"
REGISTRATION_RECORD_TYPE = "PREREGISTERED_ACTIVE_PIPELINE_REPLAY_CONTEXT"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")

REQUIRED_ENGINE_KEYS = frozenset(
    {
        "fundamental",
        "valuation",
        "decision",
        "catalyst",
        "news",
        "catalyst_bridge",
        "catalyst_validation",
        "catalyst_probability",
        "thesis",
        "synthesis",
        "audit",
        "market_signals",
        "sentiment",
        "market_regime",
        "macro_environment",
        "specialist_research",
        "master_decision",
        "outcome_learning",
        "valuation_quality",
        "authenticated_sources",
        "diagnostics",
        "supplemental_evidence",
    }
)

FIXED_FALSE_FIELDS = (
    "network_allowed",
    "provider_api_allowed",
    "filesystem_write_allowed",
    "mutable_learning_allowed",
    "provider_fallback_allowed",
    "degraded_stage_completion_allowed",
    "broker_connection_allowed",
    "paper_broker_submission_enabled",
    "promotion_approved",
    "deployment_performed",
    "order_submitted",
    "live_trading_enabled",
    "performance_claim_allowed",
)
CONTEXT_FIELDS = {
    "schema_version",
    "policy_version",
    "record_type",
    "status",
    "replay_plan_id",
    "replay_plan_record_hash",
    "image_approval_id",
    "image_approval_record_hash",
    "git_revision",
    "registered_at",
    "registered_by",
    "as_of_schedule",
    "as_of_schedule_sha256",
    "evaluation_not_before",
    "evaluation_not_after",
    "evaluation_data_access_not_before",
    "sealed_evaluation_dataset_commitment_sha256",
    "dependency_lock_sha256",
    "approved_image_digest_sha256",
    "pipeline_components",
    "engine_dependencies",
    "factor_lineage_policy",
    "learning_state_sha256",
    "authenticated_source_ledger_sha256",
    "authenticated_source_blobs_manifest_sha256",
    "strict_stage_errors",
    "point_in_time_inputs_required",
    "simulation_only",
    *FIXED_FALSE_FIELDS,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Replay-context append made no progress")
        offset += count


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _git_revision(value: Any) -> str:
    resolved = str(value or "").strip().lower()
    if not GIT_REVISION_PATTERN.fullmatch(resolved):
        raise ValueError("git_revision must be a full hexadecimal commit identifier")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _verified_record(
    ledger: Any,
    supplied: Mapping[str, Any],
    *,
    identifier_field: str,
    record_name: str,
) -> dict[str, Any]:
    verifier = getattr(ledger, "verify", None)
    if not callable(verifier):
        raise ValueError(f"{record_name} ledger must expose verify()")
    if not isinstance(supplied, Mapping):
        raise ValueError(f"a verified {record_name} record is required")
    identifier = _required(supplied.get(identifier_field), identifier_field, 100)
    supplied_hash = _sha256(supplied.get("record_hash"), f"{record_name}.record_hash")
    matches = [
        item
        for item in verifier()
        if isinstance(item, Mapping) and item.get(identifier_field) == identifier
    ]
    if len(matches) != 1:
        raise ValueError(f"exactly one verified {record_name} record is required")
    verified = dict(matches[0])
    if _sha256(verified.get("record_hash"), f"verified {record_name}.record_hash") != supplied_hash:
        raise ValueError(f"supplied {record_name} does not match the verified record")
    return verified


def _component(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"version", "source_sha256"}:
        raise ValueError(f"{name} must contain exactly version and source_sha256")
    return {
        "version": _required(value.get("version"), f"{name}.version", 120),
        "source_sha256": _sha256(
            value.get("source_sha256"), f"{name}.source_sha256"
        ),
    }


def _components(
    values: Any,
    *,
    required: frozenset[str] | set[str],
    name: str,
) -> dict[str, dict[str, str]]:
    if not isinstance(values, Mapping) or set(values) != set(required):
        raise ValueError(f"{name} must pin exactly the required components")
    return {
        key: _component(values[key], f"{name}.{key}")
        for key in sorted(required)
    }


def _as_of_schedule(
    values: Sequence[str | datetime],
    *,
    start: datetime,
    end: datetime,
) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("as_of_schedule must be an ordered sequence")
    if not values or len(values) > 10_000:
        raise ValueError("as_of_schedule must contain between 1 and 10000 instants")
    resolved = [_timestamp(value, "as_of_schedule item") for value in values]
    if any(not start <= value < end for value in resolved):
        raise ValueError("every as-of instant must fall inside the evaluation window")
    if any(left >= right for left, right in zip(resolved, resolved[1:])):
        raise ValueError("as_of_schedule must be strictly increasing and unique")
    return [value.isoformat(timespec="microseconds") for value in resolved]


def _validate_context_payload(
    payload: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    image: Mapping[str, Any],
) -> None:
    if set(payload) != CONTEXT_FIELDS:
        raise ValueError("context has missing or unsupported fields")
    plan_components = _components(
        plan.get("components"),
        required=REQUIRED_COMPONENTS,
        name="verified replay_plan.components",
    )
    payload_components = _components(
        payload.get("pipeline_components"),
        required=REQUIRED_COMPONENTS,
        name="context.pipeline_components",
    )
    dependencies = _components(
        payload.get("engine_dependencies"),
        required=REQUIRED_ENGINE_KEYS,
        name="context.engine_dependencies",
    )
    lineage = _component(payload.get("factor_lineage_policy"), "factor_lineage_policy")
    start = _timestamp(plan.get("evaluation_not_before"), "plan evaluation start")
    end = _timestamp(plan.get("evaluation_not_after"), "plan evaluation end")
    access = _timestamp(
        plan.get("evaluation_data_access_not_before"),
        "plan evaluation_data_access_not_before",
    )
    registered = _timestamp(payload.get("registered_at"), "context registered_at")
    schedule = _as_of_schedule(
        payload.get("as_of_schedule"),
        start=start,
        end=end,
    )
    revision = _git_revision(plan.get("git_revision"))
    boundary = (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("policy_version") == POLICY_VERSION
        and payload.get("record_type") == RECORD_TYPE
        and payload.get("status") == "SEALED_PREREQUISITES_ONLY_NOT_EXECUTED"
        and payload.get("replay_plan_id") == plan.get("replay_plan_id")
        and _sha256(payload.get("replay_plan_record_hash"), "context plan hash")
        == _sha256(plan.get("record_hash"), "verified plan hash")
        and payload.get("image_approval_id") == image.get("image_approval_id")
        and _sha256(payload.get("image_approval_record_hash"), "context image hash")
        == _sha256(image.get("record_hash"), "verified image hash")
        and _git_revision(payload.get("git_revision")) == revision
        and _git_revision(image.get("git_revision")) == revision
        and registered < access
        and _sha256(payload.get("as_of_schedule_sha256"), "as_of schedule digest")
        == hashlib.sha256(_canonical_json(schedule).encode("utf-8")).hexdigest()
        and _timestamp(payload.get("evaluation_not_before"), "context evaluation start")
        == start
        and _timestamp(payload.get("evaluation_not_after"), "context evaluation end")
        == end
        and _timestamp(
            payload.get("evaluation_data_access_not_before"),
            "context evaluation_data_access_not_before",
        )
        == access
        and _sha256(
            payload.get("sealed_evaluation_dataset_commitment_sha256"),
            "context dataset commitment",
        )
        == _sha256(
            plan.get("sealed_evaluation_dataset_commitment_sha256"),
            "plan dataset commitment",
        )
        and _sha256(payload.get("dependency_lock_sha256"), "context dependency lock")
        == _sha256(plan.get("dependency_lock_sha256"), "plan dependency lock")
        == _sha256(image.get("dependency_lock_sha256"), "image dependency lock")
        and _sha256(
            payload.get("approved_image_digest_sha256"),
            "context approved image digest",
        )
        == _sha256(plan.get("runner_sha256"), "plan runner")
        == _sha256(image.get("image_digest_sha256"), "image digest")
        and payload_components == plan_components
        and dependencies["master_decision"]
        == plan_components["master_portfolio_decision_engine"]
        and lineage == plan_components["factor_lineage_policy"]
        and all(
            _sha256(payload.get(field), field)
            for field in (
                "learning_state_sha256",
                "authenticated_source_ledger_sha256",
                "authenticated_source_blobs_manifest_sha256",
            )
        )
        and _required(payload.get("registered_by"), "registered_by", 100)
        and payload.get("strict_stage_errors") is True
        and payload.get("point_in_time_inputs_required") is True
        and payload.get("simulation_only") is True
        and all(payload.get(field) is False for field in FIXED_FALSE_FIELDS)
    )
    if not boundary:
        raise ValueError("context violates its sealed boundary")


@dataclass(frozen=True, slots=True)
class ActivePipelineReplayContext:
    """Canonical inert context; it has no run, broker, save or promotion method."""

    canonical_json: str
    context_sha256: str
    released_from_registration_id: str | None = None

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_json.encode("utf-8")

    def as_dict(self) -> dict[str, Any]:
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise RuntimeError("canonical replay context is not an object")
        return value


def _resolve_active_pipeline_replay_context(
    *,
    replay_plan_ledger: Any,
    replay_plan: Mapping[str, Any],
    image_approval_ledger: Any,
    image_approval: Mapping[str, Any],
    engine_dependencies: Mapping[str, Mapping[str, Any]],
    factor_lineage_policy: Mapping[str, Any],
    as_of_schedule: Sequence[str | datetime],
    learning_state_sha256: str,
    authenticated_source_ledger_sha256: str,
    authenticated_source_blobs_manifest_sha256: str,
    registered_by: str,
    registered_at: str | datetime | None = None,
) -> ActivePipelineReplayContext:
    """Resolve a fail-closed, fully pinned context without executing anything."""

    plan = _verified_record(
        replay_plan_ledger,
        replay_plan,
        identifier_field="replay_plan_id",
        record_name="replay plan",
    )
    image = _verified_record(
        image_approval_ledger,
        image_approval,
        identifier_field="image_approval_id",
        record_name="image approval",
    )

    revision = _git_revision(plan.get("git_revision"))
    plan_components = _components(
        plan.get("components"),
        required=REQUIRED_COMPONENTS,
        name="replay_plan.components",
    )
    dependencies = _components(
        engine_dependencies,
        required=REQUIRED_ENGINE_KEYS,
        name="engine_dependencies",
    )
    lineage = _component(factor_lineage_policy, "factor_lineage_policy")

    if dependencies["master_decision"] != plan_components[
        "master_portfolio_decision_engine"
    ]:
        raise ValueError("master decision dependency does not match the replay plan")
    if lineage != plan_components["factor_lineage_policy"]:
        raise ValueError("factor-lineage dependency does not match the replay plan")

    evaluation_start = _timestamp(plan.get("evaluation_not_before"), "evaluation start")
    evaluation_end = _timestamp(plan.get("evaluation_not_after"), "evaluation end")
    schedule = _as_of_schedule(
        as_of_schedule,
        start=evaluation_start,
        end=evaluation_end,
    )
    data_access_not_before = _timestamp(
        plan.get("evaluation_data_access_not_before"),
        "evaluation_data_access_not_before",
    )
    registered = _timestamp(
        registered_at or datetime.now(timezone.utc),
        "registered_at",
    )
    now = datetime.now(timezone.utc)
    if not now - MAX_CLOCK_SKEW <= registered <= now + MAX_CLOCK_SKEW:
        raise ValueError("registered_at must match the actual context registration time")
    if registered >= data_access_not_before:
        raise ValueError("replay context must be preregistered before evaluation data access")

    if plan.get("simulation_only") is not True or plan.get("active_route_only") is not True:
        raise ValueError("replay plan must remain simulation-only and active-route-only")
    if plan.get("point_in_time_inputs_required") is not True:
        raise ValueError("replay plan must require point-in-time inputs")
    for forbidden in (
        "evaluation_dataset_opened",
        "replay_executed",
        "performance_claim_allowed",
        "paper_broker_submission_enabled",
        "broker_connection_allowed",
        "live_trading_enabled",
    ):
        if plan.get(forbidden) is not False:
            raise ValueError(f"replay plan has forbidden authority: {forbidden}")

    if image.get("status") != "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY":
        raise ValueError("image is not approved for isolated simulation")
    if image.get("simulation_only") is not True:
        raise ValueError("image approval must remain simulation-only")
    if image.get("execution_environment") != REQUIRED_ENVIRONMENT:
        raise ValueError("image approval must require the no-network environment")
    if image.get("entrypoint") != REQUIRED_ENTRYPOINT:
        raise ValueError("image approval does not pin the required entrypoint")
    if any(image.get(field) is not False for field in IMAGE_FIXED_FALSE):
        raise ValueError("image approval contains forbidden authority")
    if _git_revision(image.get("git_revision")) != revision:
        raise ValueError("approved image Git revision does not match the replay plan")
    if _sha256(image.get("dependency_lock_sha256"), "image dependency lock") != _sha256(
        plan.get("dependency_lock_sha256"), "plan dependency lock"
    ):
        raise ValueError("approved image dependency lock does not match the replay plan")
    if _sha256(image.get("image_digest_sha256"), "approved image digest") != _sha256(
        plan.get("runner_sha256"), "plan runner"
    ):
        raise ValueError("approved image digest does not match the replay runner")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "record_type": RECORD_TYPE,
        "status": "SEALED_PREREQUISITES_ONLY_NOT_EXECUTED",
        "replay_plan_id": _required(plan.get("replay_plan_id"), "replay_plan_id", 100),
        "replay_plan_record_hash": _sha256(
            plan.get("record_hash"), "replay_plan_record_hash"
        ),
        "image_approval_id": _required(
            image.get("image_approval_id"), "image_approval_id", 100
        ),
        "image_approval_record_hash": _sha256(
            image.get("record_hash"), "image_approval_record_hash"
        ),
        "git_revision": revision,
        "registered_at": registered.isoformat(timespec="microseconds"),
        "registered_by": _required(registered_by, "registered_by", 100),
        "as_of_schedule": schedule,
        "as_of_schedule_sha256": hashlib.sha256(
            _canonical_json(schedule).encode("utf-8")
        ).hexdigest(),
        "evaluation_not_before": evaluation_start.isoformat(timespec="microseconds"),
        "evaluation_not_after": evaluation_end.isoformat(timespec="microseconds"),
        "evaluation_data_access_not_before": data_access_not_before.isoformat(
            timespec="microseconds"
        ),
        "sealed_evaluation_dataset_commitment_sha256": _sha256(
            plan.get("sealed_evaluation_dataset_commitment_sha256"),
            "sealed evaluation dataset commitment",
        ),
        "dependency_lock_sha256": _sha256(
            plan.get("dependency_lock_sha256"), "dependency lock"
        ),
        "approved_image_digest_sha256": _sha256(
            image.get("image_digest_sha256"), "approved image digest"
        ),
        "pipeline_components": plan_components,
        "engine_dependencies": dependencies,
        "factor_lineage_policy": lineage,
        "learning_state_sha256": _sha256(
            learning_state_sha256, "learning_state_sha256"
        ),
        "authenticated_source_ledger_sha256": _sha256(
            authenticated_source_ledger_sha256,
            "authenticated_source_ledger_sha256",
        ),
        "authenticated_source_blobs_manifest_sha256": _sha256(
            authenticated_source_blobs_manifest_sha256,
            "authenticated_source_blobs_manifest_sha256",
        ),
        "strict_stage_errors": True,
        "point_in_time_inputs_required": True,
        "simulation_only": True,
        **{field: False for field in FIXED_FALSE_FIELDS},
    }
    _validate_context_payload(payload, plan=plan, image=image)
    canonical = _canonical_json(payload)
    return ActivePipelineReplayContext(
        canonical_json=canonical,
        context_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


REGISTRATION_FIELDS = {
    "schema_version",
    "policy_version",
    "context_registration_id",
    "record_type",
    "status",
    "registered_at",
    "registered_by",
    "replay_plan_id",
    "replay_plan_record_hash",
    "image_approval_id",
    "image_approval_record_hash",
    "evaluation_data_access_not_before",
    "context_canonical_json",
    "context_sha256",
    "previous_hash",
    "record_hash",
}


def _registration_id(context_sha256: str) -> str:
    return "RCTX-" + hashlib.sha256(
        _canonical_json([context_sha256, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class ActivePipelineReplayContextLedger:
    """Append-only pre-data-access binding of the complete replay context."""

    def __init__(
        self,
        path: str | Path,
        *,
        replay_plan_ledger: Any,
        image_approval_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.replay_plan_ledger = replay_plan_ledger
        self.image_approval_ledger = image_approval_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Replay-context ledger has an incomplete final line."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank replay-context line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid replay-context JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Replay-context line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(self, **values: Any) -> dict[str, Any]:
        context = _resolve_active_pipeline_replay_context(
            replay_plan_ledger=self.replay_plan_ledger,
            image_approval_ledger=self.image_approval_ledger,
            **values,
        )
        payload = context.as_dict()
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "context_registration_id": _registration_id(context.context_sha256),
            "record_type": REGISTRATION_RECORD_TYPE,
            "status": "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS",
            "registered_at": payload["registered_at"],
            "registered_by": payload["registered_by"],
            "replay_plan_id": payload["replay_plan_id"],
            "replay_plan_record_hash": payload["replay_plan_record_hash"],
            "image_approval_id": payload["image_approval_id"],
            "image_approval_record_hash": payload["image_approval_record_hash"],
            "evaluation_data_access_not_before": payload[
                "evaluation_data_access_not_before"
            ],
            "context_canonical_json": context.canonical_json,
            "context_sha256": context.context_sha256,
        }
        return self._append(result)

    def verify(self) -> list[dict[str, Any]]:
        plans = {
            item.get("replay_plan_id"): item
            for item in self.replay_plan_ledger.verify()
            if isinstance(item, Mapping)
        }
        images = {
            item.get("image_approval_id"): item
            for item in self.image_approval_ledger.verify()
            if isinstance(item, Mapping)
        }
        previous = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_plans: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            try:
                if set(record) != REGISTRATION_FIELDS:
                    raise ValueError("missing or unsupported fields")
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                if (
                    record.get("previous_hash") != previous
                    or record.get("record_hash") != _record_hash(material)
                ):
                    raise ValueError("invalid hash chain")
                canonical = _required(
                    record.get("context_canonical_json"),
                    "context_canonical_json",
                    500_000,
                )
                payload = json.loads(canonical)
                if not isinstance(payload, dict) or _canonical_json(payload) != canonical:
                    raise ValueError("context is not canonical JSON")
                context_hash = _sha256(record.get("context_sha256"), "context_sha256")
                if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != context_hash:
                    raise ValueError("context digest mismatch")
                registration_id = _registration_id(context_hash)
                plan_id = _required(record.get("replay_plan_id"), "replay_plan_id", 100)
                image_id = _required(
                    record.get("image_approval_id"), "image_approval_id", 100
                )
                plan = plans.get(plan_id)
                image = images.get(image_id)
                if not isinstance(plan, Mapping) or not isinstance(image, Mapping):
                    raise ValueError("verified parent record is missing")
                registered = _timestamp(record.get("registered_at"), "registered_at")
                access = _timestamp(
                    record.get("evaluation_data_access_not_before"),
                    "evaluation_data_access_not_before",
                )
                boundary = (
                    record.get("schema_version") == SCHEMA_VERSION
                    and record.get("policy_version") == POLICY_VERSION
                    and record.get("context_registration_id") == registration_id
                    and registration_id not in seen_ids
                    and plan_id not in seen_plans
                    and record.get("record_type") == REGISTRATION_RECORD_TYPE
                    and record.get("status")
                    == "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS"
                    and registered < access
                    and record.get("registered_at") == payload.get("registered_at")
                    and record.get("registered_by") == payload.get("registered_by")
                    and record.get("replay_plan_id") == payload.get("replay_plan_id")
                    and record.get("image_approval_id") == payload.get("image_approval_id")
                    and record.get("evaluation_data_access_not_before")
                    == payload.get("evaluation_data_access_not_before")
                    and _sha256(record.get("replay_plan_record_hash"), "plan hash")
                    == _sha256(plan.get("record_hash"), "verified plan hash")
                    == _sha256(payload.get("replay_plan_record_hash"), "context plan hash")
                    and _sha256(record.get("image_approval_record_hash"), "image hash")
                    == _sha256(image.get("record_hash"), "verified image hash")
                    == _sha256(payload.get("image_approval_record_hash"), "context image hash")
                )
                if not boundary:
                    raise ValueError("registration boundary violation")
                _validate_context_payload(payload, plan=plan, image=image)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Replay-context record {index} violates its boundary."
                ) from error
            seen_ids.add(record["context_registration_id"])
            seen_plans.add(record["replay_plan_id"])
            previous = record["record_hash"]
        return records

    def require_data_access_open(self, context_registration_id: str) -> ActivePipelineReplayContext:
        """Return the verified context only after the real embargo has lifted."""

        if not isinstance(context_registration_id, str):
            raise ValueError(
                "context_registration_id must identify a verified replay-context record"
            )
        identifier = _required(
            context_registration_id, "context_registration_id", 100
        )
        matches = [
            record
            for record in self.verify()
            if record.get("context_registration_id") == identifier
        ]
        if len(matches) != 1:
            raise ValueError("exactly one verified replay-context registration is required")
        record = matches[0]
        if datetime.now(timezone.utc) < _timestamp(
            record["evaluation_data_access_not_before"],
            "evaluation_data_access_not_before",
        ):
            raise PermissionError("evaluation data-access embargo has not lifted")
        return ActivePipelineReplayContext(
            canonical_json=record["context_canonical_json"],
            context_sha256=record["context_sha256"],
            released_from_registration_id=record["context_registration_id"],
        )

    def open_invocation(
        self,
        context_registration_id: str,
        *,
        as_of_index: int,
        engine_registry: Mapping[str, Any],
        authenticated_source_ledger_path: str | Path,
        authenticated_source_blobs_directory: str | Path,
        learning_state_path: str | Path,
    ) -> SealedReplayInvocation:
        """Issue one exact replay invocation from the verified post-embargo ledger."""

        from core.orchestration.sealed_replay_invocation import (
            _issue_sealed_replay_invocation,
            active_pipeline_component_registry,
            authenticated_source_snapshot_digests,
            describe_engine_registry,
            learning_state_digest,
        )

        context = self.require_data_access_open(context_registration_id)
        payload = context.as_dict()
        if isinstance(as_of_index, bool) or not isinstance(as_of_index, int):
            raise ValueError("as_of_index must be an integer")
        schedule = payload["as_of_schedule"]
        if as_of_index < 0 or as_of_index >= len(schedule):
            raise ValueError("as_of_index is outside the preregistered schedule")

        identities = describe_engine_registry(
            engine_registry,
            required_keys=REQUIRED_ENGINE_KEYS,
        )
        if identities != payload["engine_dependencies"]:
            raise ValueError("engine_registry does not match the sealed identities")
        component_identities = describe_engine_registry(
            active_pipeline_component_registry(engine_registry),
            required_keys=REQUIRED_COMPONENTS,
        )
        if component_identities != payload["pipeline_components"]:
            raise ValueError("active pipeline does not match the sealed components")

        project_root = Path(__file__).resolve().parents[2]
        revision = current_git_revision(project_root)
        if revision != payload["git_revision"]:
            raise ValueError("checked-out Git revision does not match the sealed context")

        try:
            ledger_digest, blobs_manifest_digest = (
                authenticated_source_snapshot_digests(
                    authenticated_source_ledger_path,
                    authenticated_source_blobs_directory,
                )
            )
        except (OSError, LedgerIntegrityError, ValueError) as error:
            raise ValueError(
                "authenticated source ledger or blobs failed verification"
            ) from error
        if ledger_digest != payload["authenticated_source_ledger_sha256"]:
            raise ValueError("authenticated source ledger does not match the sealed digest")
        if (
            blobs_manifest_digest
            != payload["authenticated_source_blobs_manifest_sha256"]
        ):
            raise ValueError("authenticated source blobs do not match the sealed manifest")
        try:
            actual_learning_digest = learning_state_digest(learning_state_path)
        except (OSError, ValueError) as error:
            raise ValueError("learning state failed verification") from error
        if actual_learning_digest != payload["learning_state_sha256"]:
            raise ValueError("learning state does not match the sealed digest")

        return _issue_sealed_replay_invocation(
            registration_id=context.released_from_registration_id,
            context_sha256=context.context_sha256,
            as_of=schedule[as_of_index],
            as_of_index=as_of_index,
            git_revision=revision,
            engine_registry=engine_registry,
            engine_identities=identities,
            pipeline_component_identities=component_identities,
            authenticated_source_ledger_path=Path(
                authenticated_source_ledger_path
            ).resolve(),
            authenticated_source_blobs_directory=Path(
                authenticated_source_blobs_directory
            ).resolve(),
            learning_state_path=Path(learning_state_path).resolve(),
            authenticated_source_ledger_sha256=ledger_digest,
            authenticated_source_blobs_manifest_sha256=blobs_manifest_digest,
            learning_state_sha256=actual_learning_digest,
        )

    def _append(self, result: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["context_registration_id"]
                    == result["context_registration_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Replay context already exists with different values.")
            if any(item["replay_plan_id"] == result["replay_plan_id"] for item in records):
                raise LedgerIntegrityError("Replay plan already has a registered context.")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
