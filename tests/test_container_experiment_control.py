from dataclasses import FrozenInstanceError
import hashlib

import pytest

from core.orchestration.active_pipeline_image_approval import FIXED_FALSE
from core.orchestration.container_experiment_control import (
    FIXED_FALSE_FIELDS,
    RECORD_TYPE,
    ContainerExperimentControl,
    resolve_container_experiment_control,
)


class Ledger:
    def __init__(self, records, experiments=None):
        self.records = records
        if experiments is not None:
            self.experiment_ledger = Ledger(experiments)

    def verify(self):
        return self.records


def records():
    experiment = {
        "experiment_id": "EXP-1",
        "record_hash": "1" * 64,
        "baseline_strategy_version": "BASE-v1",
        "candidate_strategy_version": "CANDIDATE-v2",
        "candidate_change_description": "SECRET-LIKE FREE TEXT",
        "acceptance_rule": "DO NOT COPY THIS RULE",
        "rejection_rule": "DO NOT COPY THIS RULE",
        "point_in_time_data_cutoff": "2025-01-01T00:00:00+00:00",
        "out_of_sample_not_before": "2025-02-01T00:00:00+00:00",
        "out_of_sample_not_after": "2025-04-01T00:00:00+00:00",
        "primary_metric": "COMPLETE_BENCHMARK_RELATIVE_RETURN",
        "random_seed": 123,
        "maximum_trials": 10,
    }
    manifest = {
        "run_manifest_id": "RUN-1",
        "record_hash": "2" * 64,
        "experiment_id": "EXP-1",
        "experiment_record_hash": "1" * 64,
        "dataset_manifest_sha256": "3" * 64,
        "dependency_lock_sha256": "4" * 64,
        "runner_sha256": "5" * 64,
        "git_revision": "6" * 40,
        "planned_trial_count": 4,
        "execution_environment": "CONTAINER_ISOLATED_NO_NETWORK",
        "simulation_only": True,
    }
    approval = {
        "image_approval_id": "IMGAPP-1",
        "record_hash": "7" * 64,
        "status": "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY",
        "simulation_only": True,
        "entrypoint": "/runner/experiment",
        "execution_environment": "CONTAINER_ISOLATED_NO_NETWORK",
        "image_digest_sha256": "5" * 64,
        "git_revision": "6" * 40,
        "dependency_lock_sha256": "4" * 64,
        **{field: False for field in FIXED_FALSE},
    }
    return experiment, manifest, approval


def resolve(experiment=None, manifest=None, approval=None):
    default_experiment, default_manifest, default_approval = records()
    resolved_experiment = experiment or default_experiment
    resolved_manifest = manifest or default_manifest
    resolved_approval = approval or default_approval
    return resolve_container_experiment_control(
        run_manifest_ledger=Ledger([resolved_manifest], [resolved_experiment]),
        run_manifest=resolved_manifest,
        image_approval=resolved_approval,
    )


def test_control_is_canonical_minimal_and_deterministic():
    first = resolve()
    second = resolve()
    payload = first.as_dict()

    assert isinstance(first, ContainerExperimentControl)
    assert first == second
    assert first.control_sha256 == hashlib.sha256(first.canonical_bytes).hexdigest()
    assert payload["record_type"] == RECORD_TYPE
    assert payload["baseline_strategy_version"] == "BASE-v1"
    assert payload["candidate_strategy_version"] == "CANDIDATE-v2"
    assert payload["planned_trial_count"] == 4
    assert payload["approved_image_digest_sha256"] == payload["runner_sha256"]
    assert "SECRET-LIKE FREE TEXT" not in first.canonical_json
    assert "acceptance_rule" not in payload
    assert "rejection_rule" not in payload
    assert "candidate_change_description" not in payload
    for field in FIXED_FALSE_FIELDS:
        assert payload[field] is False


def test_value_is_immutable_and_as_dict_returns_a_copy():
    value = resolve()
    copy = value.as_dict()
    copy["live_trading_enabled"] = True
    assert value.as_dict()["live_trading_enabled"] is False
    with pytest.raises(FrozenInstanceError):
        value.control_sha256 = "0" * 64


@pytest.mark.parametrize(
    "target,changes,fragment",
    [
        ("manifest", {"experiment_record_hash": "9" * 64}, "pin the verified"),
        ("manifest", {"runner_sha256": "9" * 64}, "digest does not match"),
        ("manifest", {"git_revision": "9" * 40}, "Git revision does not match"),
        ("manifest", {"dependency_lock_sha256": "9" * 64}, "dependency lock does not match"),
        ("manifest", {"simulation_only": False}, "simulation-only"),
        ("experiment", {"candidate_strategy_version": "BASE-v1"}, "differ"),
        ("experiment", {"primary_metric": "UNSUPPORTED"}, "not supported"),
        ("experiment", {"random_seed": True}, "must be an integer"),
        ("experiment", {"maximum_trials": 3}, "exceeds"),
        (
            "experiment",
            {"point_in_time_data_cutoff": "2025-03-01T00:00:00+00:00"},
            "not chronological",
        ),
        ("approval", {"entrypoint": "/bin/sh"}, "entrypoint"),
        ("approval", {"live_trading_enabled": True}, "forbidden authority"),
        ("approval", {"status": "REVOKED"}, "not approved"),
    ],
)
def test_mismatched_or_unsafe_control_identity_fails_closed(target, changes, fragment):
    experiment, manifest, approval = records()
    if target == "experiment":
        experiment = {**experiment, **changes}
    elif target == "manifest":
        manifest = {**manifest, **changes}
    else:
        approval = {**approval, **changes}
    with pytest.raises(ValueError, match=fragment):
        resolve(experiment, manifest, approval)


def test_missing_or_duplicate_experiment_fails_closed():
    experiment, manifest, approval = records()
    for candidates in ([], [experiment, dict(experiment)]):
        with pytest.raises(ValueError, match="exactly one verified"):
            resolve_container_experiment_control(
                run_manifest_ledger=Ledger([manifest], candidates),
                run_manifest=manifest,
                image_approval=approval,
            )


def test_run_manifest_without_verified_experiment_ledger_is_rejected():
    _, manifest, approval = records()
    with pytest.raises(ValueError, match="verified experiment ledger"):
        resolve_container_experiment_control(
            run_manifest_ledger=Ledger([manifest]),
            run_manifest=manifest,
            image_approval=approval,
        )

