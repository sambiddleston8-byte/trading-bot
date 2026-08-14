import hashlib
import fcntl
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import stat
import subprocess

import pytest

from core.orchestration.active_pipeline_image_approval import (
    ActivePipelineImageApprovalLedger,
)
from core.orchestration.container_experiment_runner import (
    INPUT_ROOT_MARKER,
    ContainerExperimentRunner,
)
from core.orchestration.disposable_workspace import ROOT_MARKER, DisposableExperimentWorkspace


class VerifiedLedger:
    def __init__(self, records, experiments=None):
        self.records = records
        if experiments is not None:
            self.experiment_ledger = VerifiedLedger(experiments)
    def verify(self): return self.records


RUNNER_HASH = "a" * 64
IMAGE = "example/experiment-runner@sha256:" + RUNNER_HASH
GIT_REVISION = "1" * 40
DEPENDENCY_LOCK_HASH = "2" * 64
RUN_RECORD_HASH = "7" * 64
EXPERIMENT_RECORD_HASH = "8" * 64
RESULT = {
    "trials_completed": 2,
    "baseline_primary_metric": "0.1",
    "candidate_primary_metric": "0.2",
    "baseline_maximum_drawdown": "0.10",
    "candidate_maximum_drawdown": "0.11",
    "baseline_turnover": "0.5",
    "candidate_turnover": "0.6",
}


def setup(tmp_path):
    sandbox = tmp_path / "sandbox"; sandbox.mkdir()
    (sandbox / ".experiment-sandbox-root").write_text(ROOT_MARKER)
    inputs = tmp_path / "inputs"; inputs.mkdir()
    (inputs / ".sealed-experiment-input-root").write_text(INPUT_ROOT_MARKER)
    sealed = inputs / "dataset.json"; sealed.write_bytes(b"sealed data")
    experiment = {
        "experiment_id": "EXP-1",
        "record_hash": EXPERIMENT_RECORD_HASH,
        "baseline_strategy_version": "BASELINE-v1",
        "candidate_strategy_version": "CANDIDATE-v2",
        "candidate_change_description": "FREE TEXT MUST NOT ENTER CONTROL",
        "acceptance_rule": "FREE TEXT MUST NOT ENTER CONTROL",
        "rejection_rule": "FREE TEXT MUST NOT ENTER CONTROL",
        "point_in_time_data_cutoff": "2024-12-01T00:00:00+00:00",
        "out_of_sample_not_before": "2025-02-01T00:00:00+00:00",
        "out_of_sample_not_after": "2025-04-01T00:00:00+00:00",
        "primary_metric": "COMPLETE_BENCHMARK_RELATIVE_RETURN",
        "random_seed": 12345,
        "maximum_trials": 5,
    }
    manifest = {
        "run_manifest_id": "RUN-1", "record_hash": RUN_RECORD_HASH,
        "experiment_id": "EXP-1", "execution_environment": "CONTAINER_ISOLATED_NO_NETWORK",
        "experiment_record_hash": EXPERIMENT_RECORD_HASH,
        "simulation_only": True,
        "runner_sha256": RUNNER_HASH,
        "git_revision": GIT_REVISION,
        "dependency_lock_sha256": DEPENDENCY_LOCK_HASH,
        "dataset_manifest_sha256": hashlib.sha256(b"sealed data").hexdigest(),
        "maximum_runtime_seconds": 60, "maximum_memory_mb": 512,
        "maximum_cpu_cores": 2, "planned_trial_count": 2,
        "planned_at": "2025-01-01T00:00:00+00:00",
    }
    approved_at = datetime.now(timezone.utc)
    approvals = ActivePipelineImageApprovalLedger(tmp_path / "image-approvals.jsonl")
    approval = approvals.approve(
        image_reference=IMAGE,
        git_revision=GIT_REVISION,
        dependency_lock_sha256=DEPENDENCY_LOCK_HASH,
        runner_entrypoint_source_sha256="3" * 64,
        dockerfile_sha256="4" * 64,
        build_provenance_sha256="5" * 64,
        security_review_evidence_sha256="6" * 64,
        built_by="BUILD_SYSTEM",
        built_at=approved_at - timedelta(minutes=2),
        reviewed_by="SECURITY_REVIEWER",
        reviewed_at=approved_at - timedelta(minutes=1),
        approved_at=approved_at,
    )
    runner = ContainerExperimentRunner(
        run_manifest_ledger=VerifiedLedger([manifest], [experiment]),
        workspace_manager=DisposableExperimentWorkspace(sandbox),
        approved_input_root=inputs,
        image_approval_ledger=approvals,
        image_approval_id=approval["image_approval_id"],
        attempt_ledger_path=sandbox / "attempts.jsonl",
    )
    return runner, sealed, sandbox, manifest


def completed(payload=RESULT, returncode=0, stderr=b""):
    return subprocess.CompletedProcess(
        [], returncode, stdout=json.dumps(payload).encode(), stderr=stderr
    )


def test_executes_with_hard_container_isolation_and_captures_only_bounded_result(
    tmp_path, monkeypatch
):
    runner, sealed, sandbox, manifest = setup(tmp_path)
    calls = []
    def fake(command, *, timeout_seconds, cidfile, container_name):
        calls.append((command, timeout_seconds, cidfile))
        value = completed()
        return value.returncode, value.stdout, value.stderr
    monkeypatch.setattr(runner, "_run_bounded", fake)
    result = runner.run(
        run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24,
        executed_by="Codex",
    )
    command, timeout_seconds, cidfile = calls[0]
    assert command[:7] == ["docker", "run", "--rm", "--pull", "never", "--network", "none"]
    for required in ("--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory", "--cpus", "--user"):
        assert required in command
    assert "no-new-privileges" in command
    assert "--name" in command and "--ulimit" in command
    assert all("/workspace" not in item for item in command)
    assert timeout_seconds == 60
    assert "--memory-swap" in command and "--cidfile" in command
    assert command[command.index("--cidfile") + 1] == str(cidfile)
    mount_indexes = [index for index, item in enumerate(command) if item == "--mount"]
    assert len(mount_indexes) == 2
    mount = command[mount_indexes[0] + 1]
    control_mount = command[mount_indexes[1] + 1]
    assert "verified-input.snapshot" in mount
    assert "dst=/input/dataset" in mount
    assert "experiment-control.snapshot" in control_mount
    assert "dst=/input/control" in control_mount
    assert str(sealed) not in mount and str(sealed) not in control_mount
    snapshot = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
    control_snapshot = Path(
        control_mount.split(",src=", 1)[1].split(",dst=", 1)[0]
    )
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o444
    assert stat.S_IMODE(control_snapshot.stat().st_mode) == 0o444
    control_bytes = control_snapshot.read_bytes()
    control = json.loads(control_bytes)
    assert "FREE TEXT MUST NOT ENTER CONTROL" not in control_snapshot.read_text()
    assert control["run_manifest_id"] == manifest["run_manifest_id"]
    assert control["planned_trial_count"] == manifest["planned_trial_count"]
    assert result["experiment_control_sha256"] == hashlib.sha256(control_bytes).hexdigest()
    assert command[-6:] == [
        "--input", "/input/dataset", "--control", "/input/control", "--output", "-",
    ]
    assert result["status"] == "ISOLATED_RESULT_CAPTURED_NOT_PROMOTED"
    assert result["image_approval_id"] == runner.image_approval_id
    assert result["image_reference"] == IMAGE
    for field in (
        "network_allowed", "authoritative_data_write_allowed",
        "broker_connection_allowed", "promotion_approved", "deployment_performed",
        "order_submitted", "live_trading_enabled",
    ):
        assert result[field] is False
    directory = sandbox / result["workspace_id"]
    with sqlite3.connect(directory / "experiment.sqlite3") as connection:
        row = connection.execute(
            "SELECT run_manifest_id, promotion_approved, order_submitted, live_trading_enabled FROM runner_results"
        ).fetchone()
        stored_control_hash = connection.execute(
            "SELECT experiment_control_sha256 FROM runner_results"
        ).fetchone()[0]
    assert row == (manifest["run_manifest_id"], 0, 0, 0)
    assert stored_control_hash == result["experiment_control_sha256"]
    attempt = json.loads((sandbox / "attempts.jsonl").read_text())
    assert attempt["experiment_control_sha256"] == result["experiment_control_sha256"]


def test_original_input_change_after_verification_cannot_change_mounted_snapshot(
    tmp_path, monkeypatch
):
    runner, sealed, _, _ = setup(tmp_path)
    mounted = []
    def fake(command, **_kwargs):
        sealed.write_bytes(b"changed after verification")
        mount = command[command.index("--mount") + 1]
        source = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
        mounted.append(source.read_bytes())
        value = completed()
        return value.returncode, value.stdout, value.stderr
    monkeypatch.setattr(runner, "_run_bounded", fake)
    runner.run(
        run_manifest_id="RUN-1", sealed_input_path=sealed,
        retention_hours=24, executed_by="Codex",
    )
    assert mounted == [b"sealed data"]


def test_image_and_input_must_match_preregistered_hashes(tmp_path, monkeypatch):
    runner, sealed, _, manifest = setup(tmp_path)
    runner.run_manifest_ledger.records = [{**manifest, "runner_sha256": "b" * 64}]
    with pytest.raises(ValueError, match="runner hash"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")
    runner.run_manifest_ledger.records = [manifest]
    sealed.write_bytes(b"changed")
    with pytest.raises(ValueError, match="dataset commitment"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")


@pytest.mark.parametrize(
    "changes,fragment",
    [
        ({"runner_sha256": "b" * 64}, "runner hash"),
        ({"git_revision": "b" * 40}, "Git revision"),
        ({"dependency_lock_sha256": "b" * 64}, "dependency lock"),
    ],
)
def test_image_approval_must_match_every_preregistered_build_identity(
    tmp_path, changes, fragment
):
    runner, sealed, sandbox, manifest = setup(tmp_path)
    runner.run_manifest_ledger.records = [{**manifest, **changes}]
    with pytest.raises(ValueError, match=fragment):
        runner.run(
            run_manifest_id="RUN-1",
            sealed_input_path=sealed,
            retention_hours=24,
            executed_by="Codex",
        )
    assert not (sandbox / "attempts.jsonl").exists()


def test_unknown_image_approval_fails_before_attempt_or_workspace(tmp_path):
    runner, sealed, sandbox, _ = setup(tmp_path)
    runner.image_approval_id = "IMGAPP-UNKNOWN"
    with pytest.raises(ValueError, match="exactly one verified image approval"):
        runner.run(
            run_manifest_id="RUN-1",
            sealed_input_path=sealed,
            retention_hours=24,
            executed_by="Codex",
        )
    assert not (sandbox / "attempts.jsonl").exists()
    assert not any(item.name.startswith("EWS-") for item in sandbox.iterdir())


@pytest.mark.parametrize(
    "target,changes,fragment",
    [
        ("manifest", {"experiment_record_hash": "9" * 64}, "pin the verified"),
        ("experiment", {"maximum_trials": 1}, "exceeds the preregistered"),
        ("experiment", {"primary_metric": "MADE_UP"}, "not supported"),
        (
            "experiment",
            {"out_of_sample_not_after": "2025-01-01T00:00:00+00:00"},
            "not chronological",
        ),
    ],
)
def test_invalid_experiment_control_fails_before_attempt(
    tmp_path, target, changes, fragment
):
    runner, sealed, sandbox, manifest = setup(tmp_path)
    if target == "manifest":
        runner.run_manifest_ledger.records = [{**manifest, **changes}]
    else:
        experiment = runner.run_manifest_ledger.experiment_ledger.records[0]
        runner.run_manifest_ledger.experiment_ledger.records = [
            {**experiment, **changes}
        ]
    with pytest.raises(ValueError, match=fragment):
        runner.run(
            run_manifest_id="RUN-1",
            sealed_input_path=sealed,
            retention_hours=24,
            executed_by="Codex",
        )
    assert not (sandbox / "attempts.jsonl").exists()


def test_missing_preregistered_experiment_fails_before_attempt(tmp_path):
    runner, sealed, sandbox, _ = setup(tmp_path)
    runner.run_manifest_ledger.experiment_ledger.records = []
    with pytest.raises(ValueError, match="exactly one verified"):
        runner.run(
            run_manifest_id="RUN-1",
            sealed_input_path=sealed,
            retention_hours=24,
            executed_by="Codex",
        )
    assert not (sandbox / "attempts.jsonl").exists()


def test_input_requires_exact_marked_root_and_direct_non_symlink_file(tmp_path):
    runner, sealed, _, _ = setup(tmp_path)
    (runner.approved_input_root / ".sealed-experiment-input-root").write_text("wrong")
    with pytest.raises(ValueError, match="safety marker"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")
    (runner.approved_input_root / ".sealed-experiment-input-root").write_text(INPUT_ROOT_MARKER)
    nested = runner.approved_input_root / "nested"; nested.mkdir(); other = nested / "data"; other.write_bytes(b"sealed data")
    with pytest.raises(ValueError, match="directly contained"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=other, retention_hours=24, executed_by="Codex")


def test_one_attempt_only_even_after_failure_prevents_result_shopping(tmp_path, monkeypatch):
    runner, sealed, sandbox, _ = setup(tmp_path)
    monkeypatch.setattr(runner, "_run_bounded", lambda *args, **kwargs: (7, b"", b""))
    with pytest.raises(RuntimeError, match="exit code 7"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")
    with pytest.raises(ValueError, match="single execution attempt"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")
    workspaces = [item for item in sandbox.iterdir() if item.name.startswith("EWS-")]
    assert len(workspaces) == 1
    with sqlite3.connect(workspaces[0] / "experiment.sqlite3") as connection:
        assert connection.execute("SELECT failure_status FROM runner_failures").fetchone()[0] == "FAILED_CLOSED_NO_RESULT"


@pytest.mark.parametrize("payload,fragment", [
    ({**RESULT, "unexpected": 1}, "fields"),
    ({**RESULT, "trials_completed": 1}, "planned trial count"),
    ({**RESULT, "candidate_turnover": [1]}, "scalar numeric"),
    ({**RESULT, "candidate_turnover": "NaN"}, "finite numeric"),
    ({**RESULT, "candidate_turnover": "1E9999999"}, "bounded numeric range"),
    ({**RESULT, "candidate_primary_metric": "1E-9999999"}, "bounded numeric range"),
    ({**RESULT, "candidate_maximum_drawdown": "-0.1"}, "cannot be negative"),
])
def test_unbounded_invalid_or_partial_output_fails_closed(tmp_path, monkeypatch, payload, fragment):
    runner, sealed, _, _ = setup(tmp_path)
    value = completed(payload=payload)
    monkeypatch.setattr(
        runner, "_run_bounded",
        lambda *args, **kwargs: (value.returncode, value.stdout, value.stderr),
    )
    with pytest.raises(ValueError, match=fragment):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")


def test_legacy_local_environment_and_missing_manifest_are_rejected(tmp_path):
    runner, sealed, _, manifest = setup(tmp_path)
    runner.run_manifest_ledger.records = []
    with pytest.raises(ValueError, match="verified"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")
    runner.run_manifest_ledger.records = [{**manifest, "execution_environment": "LOCAL_ISOLATED_NO_NETWORK"}]
    with pytest.raises(ValueError, match="container"):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")


@pytest.mark.parametrize("failure,fragment", [
    (RuntimeError("isolated experiment exceeded its runtime limit"), "runtime limit"),
    (FileNotFoundError("docker"), "could not start"),
])
def test_timeout_or_missing_docker_fails_closed(tmp_path, monkeypatch, failure, fragment):
    runner, sealed, _, _ = setup(tmp_path)
    def fail(*args, **kwargs): raise failure
    monkeypatch.setattr(runner, "_run_bounded", fail)
    with pytest.raises(RuntimeError, match=fragment):
        runner.run(run_manifest_id="RUN-1", sealed_input_path=sealed, retention_hours=24, executed_by="Codex")


def test_bounded_process_reader_kills_output_flood(tmp_path, monkeypatch):
    runner, _, _, _ = setup(tmp_path)
    class Stream:
        def fileno(self): return 10
    class Process:
        stdout = Stream(); stderr = Stream()
        killed = False
        def kill(self): self.killed = True
        def wait(self): return 1
        def poll(self): return None
    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"", b""),
    )
    class Key:
        fileobj = process.stdout; data = "stdout"
    class Selector:
        def register(self, *args): pass
        def get_map(self): return {1: Key()} if not process.killed else {}
        def select(self, timeout): return [(Key(), 1)]
        def unregister(self, fileobj): pass
        def close(self): pass
    monkeypatch.setattr("core.orchestration.container_experiment_runner.selectors.DefaultSelector", Selector)
    monkeypatch.setattr("core.orchestration.container_experiment_runner.os.read", lambda *args: b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="one-MiB"):
        runner._run_bounded(
            ["docker"], timeout_seconds=60, cidfile=tmp_path / "container.cid",
            container_name="sam-pat-ews-test",
        )
    assert process.killed is True


def test_forced_termination_kills_container_by_verified_id(tmp_path, monkeypatch):
    runner, _, sandbox, _ = setup(tmp_path)
    cidfile = sandbox / "container.cid"
    cidfile.write_text("a" * 64)
    class Process:
        killed = False
        def poll(self): return None
        def kill(self): self.killed = True
        def wait(self): return -9
    process = Process(); calls = []
    def cleanup(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", cleanup)
    runner._terminate(process, cidfile, "sam-pat-ews-test")
    assert process.killed is True
    assert calls == [["docker", "rm", "--force", "a" * 64]]
    cidfile.write_text("unsafe")
    with pytest.raises(RuntimeError, match="manual inspection"):
        runner._terminate(Process(), cidfile, "sam-pat-ews-test")


def test_forced_termination_uses_safe_name_before_cidfile_exists(tmp_path, monkeypatch):
    runner, _, _, _ = setup(tmp_path)
    calls = []
    class Process:
        def poll(self): return None
        def kill(self): pass
        def wait(self): return -9
    def cleanup(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", cleanup)
    runner._terminate(Process(), tmp_path / "missing.cid", "sam-pat-ews-test")
    assert calls == [["docker", "rm", "--force", "sam-pat-ews-test"]]


def test_docker_mount_rejects_comma_in_input_path(tmp_path):
    runner, _, _, _ = setup(tmp_path)
    unsafe = runner.approved_input_root / "data,other.json"
    unsafe.write_bytes(b"sealed data")
    with pytest.raises(ValueError, match="unsafe for a Docker bind mount"):
        runner.run(
            run_manifest_id="RUN-1", sealed_input_path=unsafe,
            retention_hours=24, executed_by="Codex",
        )


def test_attempt_ledger_must_stay_in_marked_sandbox_root(tmp_path):
    runner, _, _, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="marked sandbox root"):
        ContainerExperimentRunner(
            run_manifest_ledger=runner.run_manifest_ledger,
            workspace_manager=runner.workspace_manager,
            approved_input_root=runner.approved_input_root,
            image_approval_ledger=runner.image_approval_ledger,
            image_approval_id=runner.image_approval_id,
            attempt_ledger_path=tmp_path / "outside-attempts.jsonl",
        )


def test_concurrent_container_run_is_rejected_without_waiting(tmp_path):
    runner, _, sandbox, _ = setup(tmp_path)
    lock_path = sandbox / ".container-run.lock"
    descriptor = open(lock_path, "a+")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already running"):
            runner._acquire_run_slot()
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()
