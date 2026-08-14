from __future__ import annotations

"""Canonical instructions for one isolated, preregistered simulation run."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping

from core.decision_ledger import canonical_timestamp
from core.orchestration.active_pipeline_image_approval import (
    FIXED_FALSE as IMAGE_FIXED_FALSE,
    REQUIRED_ENTRYPOINT,
    REQUIRED_ENVIRONMENT,
)
from core.orchestration.experiment_specification import ALLOWED_PRIMARY_METRICS
from core.performance.portfolio_valuation import _canonical_json


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "sealed-container-experiment-control-v1"
RECORD_TYPE = "SEALED_CONTAINER_EXPERIMENT_CONTROL"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
FIXED_FALSE_FIELDS = (
    "network_allowed",
    "provider_api_allowed",
    "broker_connection_allowed",
    "promotion_approved",
    "deployment_performed",
    "order_submitted",
    "live_trading_enabled",
)


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
        return datetime.fromisoformat(canonical_timestamp(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _positive_integer(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class ContainerExperimentControl:
    """Immutable canonical bytes and digest; callers receive only copied JSON."""

    canonical_json: str
    control_sha256: str

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_json.encode("utf-8")

    def as_dict(self) -> dict[str, Any]:
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):  # defensive; construction always uses an object
            raise RuntimeError("canonical experiment control is not an object")
        return value


def resolve_container_experiment_control(
    *,
    run_manifest_ledger: Any,
    run_manifest: Mapping[str, Any],
    image_approval: Mapping[str, Any],
) -> ContainerExperimentControl:
    """Resolve one verified experiment without exposing ledgers to the container."""

    if not isinstance(run_manifest, Mapping):
        raise ValueError("a verified run-manifest record is required")
    if not isinstance(image_approval, Mapping):
        raise ValueError("a verified image-approval record is required")
    experiment_ledger = getattr(run_manifest_ledger, "experiment_ledger", None)
    if experiment_ledger is None or not callable(getattr(experiment_ledger, "verify", None)):
        raise ValueError("run-manifest ledger must expose its verified experiment ledger")
    experiments = experiment_ledger.verify()
    experiment_id = _required(run_manifest.get("experiment_id"), "experiment_id", 100)
    experiment_hash = _sha256(
        run_manifest.get("experiment_record_hash"), "experiment_record_hash"
    )
    matches = [
        item
        for item in experiments
        if isinstance(item, Mapping) and item.get("experiment_id") == experiment_id
    ]
    if len(matches) != 1:
        raise ValueError("exactly one verified preregistered experiment is required")
    experiment = matches[0]
    if _sha256(experiment.get("record_hash"), "experiment.record_hash") != experiment_hash:
        raise ValueError("run manifest does not pin the verified experiment record")

    baseline = _required(
        experiment.get("baseline_strategy_version"), "baseline_strategy_version", 100
    )
    candidate = _required(
        experiment.get("candidate_strategy_version"), "candidate_strategy_version", 100
    )
    if baseline == candidate:
        raise ValueError("candidate strategy version must differ from baseline")
    metric = _required(experiment.get("primary_metric"), "primary_metric", 80).upper()
    if metric not in ALLOWED_PRIMARY_METRICS:
        raise ValueError("primary_metric is not supported")
    cutoff = _timestamp(experiment.get("point_in_time_data_cutoff"), "data cutoff")
    starts = _timestamp(experiment.get("out_of_sample_not_before"), "evaluation start")
    ends = _timestamp(experiment.get("out_of_sample_not_after"), "evaluation end")
    if not cutoff < starts < ends:
        raise ValueError("point-in-time cutoff and evaluation window are not chronological")
    seed = _positive_integer(experiment.get("random_seed"), "random_seed", 2_147_483_647)
    maximum_trials = _positive_integer(
        experiment.get("maximum_trials"), "maximum_trials", 100
    )
    trials = _positive_integer(
        run_manifest.get("planned_trial_count"), "planned_trial_count", 100
    )
    if trials > maximum_trials:
        raise ValueError("planned trial count exceeds the preregistered maximum")

    run_id = _required(run_manifest.get("run_manifest_id"), "run_manifest_id", 100)
    run_hash = _sha256(run_manifest.get("record_hash"), "run_manifest_record_hash")
    dataset_hash = _sha256(
        run_manifest.get("dataset_manifest_sha256"), "dataset_manifest_sha256"
    )
    dependency_hash = _sha256(
        run_manifest.get("dependency_lock_sha256"), "dependency_lock_sha256"
    )
    runner_hash = _sha256(run_manifest.get("runner_sha256"), "runner_sha256")
    revision = _git_revision(run_manifest.get("git_revision"))
    if run_manifest.get("execution_environment") != REQUIRED_ENVIRONMENT:
        raise ValueError("run manifest must require the no-network container environment")
    if run_manifest.get("simulation_only") is not True:
        raise ValueError("run manifest must be simulation-only")

    approval_id = _required(
        image_approval.get("image_approval_id"), "image_approval_id", 100
    )
    approval_hash = _sha256(
        image_approval.get("record_hash"), "image_approval_record_hash"
    )
    approved_digest = _sha256(
        image_approval.get("image_digest_sha256"), "approved image digest"
    )
    if image_approval.get("status") != "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY":
        raise ValueError("image is not approved for isolated simulation")
    if image_approval.get("simulation_only") is not True:
        raise ValueError("image approval must be simulation-only")
    if image_approval.get("entrypoint") != REQUIRED_ENTRYPOINT:
        raise ValueError("image approval does not pin the experiment entrypoint")
    if image_approval.get("execution_environment") != REQUIRED_ENVIRONMENT:
        raise ValueError("image approval does not require the no-network environment")
    if any(image_approval.get(field) is not False for field in IMAGE_FIXED_FALSE):
        raise ValueError("image approval contains forbidden authority")
    if approved_digest != runner_hash:
        raise ValueError("approved image digest does not match the run manifest")
    if _git_revision(image_approval.get("git_revision")) != revision:
        raise ValueError("approved image Git revision does not match the run manifest")
    if _sha256(
        image_approval.get("dependency_lock_sha256"),
        "approved dependency lock",
    ) != dependency_hash:
        raise ValueError("approved dependency lock does not match the run manifest")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "record_type": RECORD_TYPE,
        "run_manifest_id": run_id,
        "run_manifest_record_hash": run_hash,
        "experiment_id": experiment_id,
        "experiment_record_hash": experiment_hash,
        "image_approval_id": approval_id,
        "image_approval_record_hash": approval_hash,
        "baseline_strategy_version": baseline,
        "candidate_strategy_version": candidate,
        "point_in_time_data_cutoff": cutoff.isoformat(),
        "out_of_sample_not_before": starts.isoformat(),
        "out_of_sample_not_after": ends.isoformat(),
        "primary_metric": metric,
        "random_seed": seed,
        "planned_trial_count": trials,
        "dataset_manifest_sha256": dataset_hash,
        "git_revision": revision,
        "dependency_lock_sha256": dependency_hash,
        "approved_image_digest_sha256": approved_digest,
        "runner_sha256": runner_hash,
        "simulation_only": True,
        **{field: False for field in FIXED_FALSE_FIELDS},
    }
    canonical = _canonical_json(payload)
    return ContainerExperimentControl(
        canonical_json=canonical,
        control_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
