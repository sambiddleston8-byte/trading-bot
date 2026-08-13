from __future__ import annotations

"""Docker-enforced execution of preregistered experiments in disposable storage."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import sqlite3
import subprocess
import time
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp
from core.orchestration.disposable_workspace import DisposableExperimentWorkspace
from core.orchestration.experiment_run_manifest import SandboxExperimentRunManifestLedger
from core.performance.portfolio_valuation import _canonical_json


POLICY_VERSION = "container-no-network-experiment-runner-v2"
INPUT_ROOT_MARKER = "SAM_PAT_SEALED_EXPERIMENT_INPUTS_V1\n"
MAX_CLOCK_SKEW = timedelta(minutes=5)
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*@sha256:[0-9a-f]{64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_NAME = re.compile(r"^sam-pat-ews-[a-z0-9-]{1,80}$")
MAX_STDOUT_BYTES = 1024 * 1024
MAX_DECIMAL_ADJUSTED_EXPONENT = 12
ALLOWED_RESULT_FIELDS = {
    "trials_completed", "baseline_primary_metric", "candidate_primary_metric",
    "baseline_maximum_drawdown", "candidate_maximum_drawdown",
    "baseline_turnover", "candidate_turnover",
}


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _contained_file(path: str | Path, root: str | Path, *, name: str) -> Path:
    candidate = Path(path)
    allowed_root = Path(root)
    if allowed_root.is_symlink() or not allowed_root.is_dir():
        raise ValueError(f"{name}_root must be an existing non-symlink directory")
    root_resolved = allowed_root.resolve()
    marker = root_resolved / ".sealed-experiment-input-root"
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_text(encoding="utf-8") != INPUT_ROOT_MARKER
    ):
        raise ValueError(f"{name}_root is missing the exact safety marker")
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{name} must be an existing non-symlink file")
    resolved = candidate.resolve()
    if resolved.parent != root_resolved:
        raise ValueError(f"{name} must be directly contained in its approved root")
    if "," in str(resolved) or any(ord(character) < 32 for character in str(resolved)):
        raise ValueError(f"{name} path contains characters unsafe for a Docker bind mount")
    return resolved


class ContainerExperimentRunner:
    """Executes one sealed command; it cannot promote, deploy, submit orders or trade."""

    def __init__(
        self,
        *,
        run_manifest_ledger: SandboxExperimentRunManifestLedger,
        workspace_manager: DisposableExperimentWorkspace,
        approved_input_root: str | Path,
        approved_image_digest: str,
        attempt_ledger_path: str | Path,
    ) -> None:
        self.run_manifest_ledger = run_manifest_ledger
        self.workspace_manager = workspace_manager
        self.approved_input_root = Path(approved_input_root)
        self.approved_image_digest = str(approved_image_digest or "").strip()
        self.attempt_ledger_path = Path(attempt_ledger_path)
        if not _IMAGE.fullmatch(self.approved_image_digest):
            raise ValueError("approved_image_digest must pin an image by SHA-256 digest")
        sandbox_root = self.workspace_manager._verified_root()
        if "," in str(sandbox_root) or any(
            ord(character) < 32 for character in str(sandbox_root)
        ):
            raise ValueError("sandbox root contains characters unsafe for a Docker bind mount")
        if (
            self.attempt_ledger_path.is_symlink()
            or self.attempt_ledger_path.parent.resolve() != sandbox_root
        ):
            raise ValueError("attempt_ledger_path must be directly inside the marked sandbox root")

    def run(
        self,
        *,
        run_manifest_id: str,
        sealed_input_path: str | Path,
        retention_hours: int,
        executed_by: str,
        executed_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        manifest = next(
            (
                item for item in self.run_manifest_ledger.verify()
                if item["run_manifest_id"] == _required(run_manifest_id, "run_manifest_id")
            ),
            None,
        )
        if manifest is None:
            raise ValueError("A verified run manifest is required")
        if manifest["execution_environment"] != "CONTAINER_ISOLATED_NO_NETWORK":
            raise ValueError("run manifest must require the container no-network environment")
        if self.approved_image_digest.rsplit(":", 1)[-1] != manifest["runner_sha256"]:
            raise ValueError("container image digest must match the preregistered runner hash")
        executed = _timestamp(executed_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= executed <= now + MAX_CLOCK_SKEW:
            raise ValueError("executed_at must match the actual run time")
        if executed < _timestamp(manifest["planned_at"]):
            raise ValueError("execution cannot predate the run manifest")
        sealed_input = _contained_file(
            sealed_input_path, self.approved_input_root, name="sealed_input"
        )
        sealed_bytes = sealed_input.read_bytes()
        input_hash = hashlib.sha256(sealed_bytes).hexdigest()
        if input_hash != manifest["dataset_manifest_sha256"]:
            raise ValueError("sealed input does not match the run-manifest dataset commitment")
        slot = self._acquire_run_slot()
        try:
            self._reserve_attempt(manifest, executed)
            workspace = self.workspace_manager.create(
                experiment_id=manifest["experiment_id"],
                dataset_manifest_sha256=input_hash,
                retention_hours=retention_hours,
                created_at=executed,
            )
            workspace_directory = self.workspace_manager.root / workspace["workspace_id"]
            verified_input = workspace_directory / "verified-input.snapshot"
            descriptor = os.open(
                verified_input, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                offset = 0
                while offset < len(sealed_bytes):
                    written = os.write(descriptor, sealed_bytes[offset:])
                    if written <= 0:
                        raise OSError("Unable to snapshot verified experiment input")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(verified_input, 0o444)
            if hashlib.sha256(verified_input.read_bytes()).hexdigest() != input_hash:
                raise RuntimeError("verified input snapshot integrity check failed")
            cidfile = workspace_directory / "container.cid"
            container_name = f"sam-pat-{workspace['workspace_id'].lower()}"
            if not _CONTAINER_NAME.fullmatch(container_name):
                raise ValueError("generated Docker container name is invalid")
            command = self._command(manifest, verified_input, cidfile, container_name)
            try:
                try:
                    returncode, stdout, stderr = self._run_bounded(
                        command, timeout_seconds=int(manifest["maximum_runtime_seconds"]),
                        cidfile=cidfile, container_name=container_name,
                    )
                except OSError as error:
                    raise RuntimeError("Docker could not start the isolated experiment") from error
                if returncode != 0:
                    raise RuntimeError(
                        f"isolated experiment failed with exit code {int(returncode)}"
                    )
                result = self._result(stdout)
                if result["trials_completed"] != int(manifest["planned_trial_count"]):
                    raise ValueError("container result must complete exactly the planned trial count")
                output_hash = hashlib.sha256(stdout).hexdigest()
                self._store_result(workspace_directory, manifest, result, output_hash, executed_by)
            except BaseException:
                self._store_failure(workspace_directory, manifest, executed_by)
                raise
            finally:
                if cidfile.exists():
                    cidfile.unlink()
        finally:
            fcntl.flock(slot, fcntl.LOCK_UN)
            os.close(slot)
        return {
            "policy_version": POLICY_VERSION,
            "status": "ISOLATED_RESULT_CAPTURED_NOT_PROMOTED",
            "run_manifest_id": manifest["run_manifest_id"],
            "run_manifest_record_hash": manifest["record_hash"],
            "workspace_id": workspace["workspace_id"],
            "workspace_manifest_sha256": workspace["manifest_sha256"],
            "runner_output_sha256": output_hash,
            "result": result,
            "network_allowed": False,
            "authoritative_data_write_allowed": False,
            "broker_connection_allowed": False,
            "promotion_approved": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }

    def _acquire_run_slot(self) -> int:
        """Permit one local container experiment at a time, without waiting."""
        lock = self.workspace_manager._verified_root() / ".container-run.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise RuntimeError(
                "another isolated experiment is already running"
            ) from error
        return descriptor

    def _command(
        self,
        manifest: Mapping[str, Any],
        sealed_input: Path,
        cidfile: Path,
        container_name: str,
    ) -> list[str]:
        memory = f"{int(manifest['maximum_memory_mb'])}m"
        cpus = str(int(manifest["maximum_cpu_cores"]))
        return [
            "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", memory, "--memory-swap", memory,
            "--cpus", cpus, "--stop-timeout", "1", "--cidfile", str(cidfile),
            "--name", container_name, "--ulimit", "nofile=256:256",
            "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={sealed_input},dst=/input/dataset,readonly",
            self.approved_image_digest,
            "/runner/experiment", "--input", "/input/dataset", "--output", "-",
        ]

    @staticmethod
    def _run_bounded(
        command: Sequence[str], *, timeout_seconds: int, cidfile: Path,
        container_name: str,
    ) -> tuple[int, bytes, bytes]:
        process = subprocess.Popen(
            list(command), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:
            ContainerExperimentRunner._terminate(process, cidfile, container_name)
            raise RuntimeError("Docker output pipes were unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    ContainerExperimentRunner._terminate(process, cidfile, container_name)
                    raise RuntimeError("isolated experiment exceeded its runtime limit")
                events = selector.select(timeout=min(remaining, 0.25))
                if not events and process.poll() is not None:
                    events = [(key, selectors.EVENT_READ) for key in selector.get_map().values()]
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = output[key.data]
                    buffer.extend(chunk)
                    if len(buffer) > MAX_STDOUT_BYTES:
                        ContainerExperimentRunner._terminate(process, cidfile, container_name)
                        raise ValueError("experiment output exceeds the one-MiB safety limit")
            return process.wait(), bytes(output["stdout"]), bytes(output["stderr"])
        finally:
            selector.close()
            if process.poll() is None:
                ContainerExperimentRunner._terminate(process, cidfile, container_name)

    @staticmethod
    def _terminate(
        process: subprocess.Popen[bytes], cidfile: Path, container_name: str
    ) -> None:
        if process.poll() is None:
            process.kill()
            process.wait()
        target = container_name
        if not _CONTAINER_NAME.fullmatch(container_name):
            raise RuntimeError("Docker container name is invalid; manual inspection required")
        if not cidfile.is_symlink() and cidfile.is_file():
            container_id = cidfile.read_text(encoding="ascii").strip().lower()
            if not _CONTAINER_ID.fullmatch(container_id):
                raise RuntimeError("Docker container ID file is invalid; manual inspection required")
            target = container_id
        try:
            cleanup = subprocess.run(
                ["docker", "rm", "--force", target], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=10, check=False,
            )
            missing = f"No such container: {target}".encode()
            if cleanup.returncode != 0 and missing not in (cleanup.stderr or b""):
                raise RuntimeError(
                    "Docker cleanup could not be verified; manual inspection required"
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(
                "Docker cleanup could not be verified; manual inspection required"
            ) from error

    def _reserve_attempt(self, manifest: Mapping[str, Any], executed_at: datetime) -> None:
        self.attempt_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.attempt_ledger_path.with_suffix(
            self.attempt_ledger_path.suffix + ".lock"
        )
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            attempts: list[dict[str, Any]] = []
            if self.attempt_ledger_path.exists():
                raw = self.attempt_ledger_path.read_bytes()
                if raw and not raw.endswith(b"\n"):
                    raise ValueError("execution-attempt ledger has an incomplete final line")
                for line in raw.splitlines():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("execution-attempt ledger contains a non-object")
                    attempts.append(value)
            previous = "0" * 64
            for index, attempt in enumerate(attempts, start=1):
                material = {key: value for key, value in attempt.items() if key != "record_hash"}
                if (
                    attempt.get("previous_hash") != previous
                    or attempt.get("record_hash")
                    != hashlib.sha256(_canonical_json(material).encode()).hexdigest()
                ):
                    raise ValueError(f"execution-attempt ledger was modified at record {index}")
                previous = attempt["record_hash"]
            if any(
                item.get("run_manifest_id") == manifest["run_manifest_id"]
                for item in attempts
            ):
                raise ValueError("run manifest already has its single execution attempt")
            material = {
                "policy_version": POLICY_VERSION,
                "record_type": "CONTAINER_EXPERIMENT_EXECUTION_ATTEMPT",
                "status": "ATTEMPT_RESERVED_BEFORE_EXECUTION",
                "run_manifest_id": manifest["run_manifest_id"],
                "run_manifest_record_hash": manifest["record_hash"],
                "executed_at": executed_at.isoformat(),
                "network_allowed": False,
                "promotion_approved": False,
                "order_submitted": False,
                "live_trading_enabled": False,
                "previous_hash": previous,
            }
            record = {
                **material,
                "record_hash": hashlib.sha256(
                    _canonical_json(material).encode()
                ).hexdigest(),
            }
            target = os.open(
                self.attempt_ledger_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                payload = (_canonical_json(record) + "\n").encode()
                offset = 0
                while offset < len(payload):
                    count = os.write(target, payload[offset:])
                    if count <= 0:
                        raise OSError("Unable to reserve experiment execution attempt")
                    offset += count
                os.fsync(target)
            finally:
                os.close(target)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("execution-attempt ledger is unreadable") from error
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _result(payload: bytes) -> dict[str, Any]:
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("container output must be one valid JSON object") from error
        if not isinstance(value, dict) or set(value) != ALLOWED_RESULT_FIELDS:
            raise ValueError("container output fields do not match the mechanical result contract")
        if isinstance(value.get("trials_completed"), bool) or not isinstance(
            value.get("trials_completed"), int
        ):
            raise ValueError("trials_completed must be an integer")
        for field in ALLOWED_RESULT_FIELDS - {"trials_completed"}:
            if isinstance(value[field], (dict, list, bool)) or value[field] is None:
                raise ValueError(f"{field} must be a scalar numeric value")
            try:
                number = Decimal(str(value[field]))
            except (InvalidOperation, ValueError) as error:
                raise ValueError(f"{field} must be a finite numeric value") from error
            if not number.is_finite():
                raise ValueError(f"{field} must be a finite numeric value")
            if abs(number.adjusted()) > MAX_DECIMAL_ADJUSTED_EXPONENT:
                raise ValueError(f"{field} exponent is outside the bounded numeric range")
            if field.endswith(("maximum_drawdown", "turnover")) and number < 0:
                raise ValueError(f"{field} cannot be negative")
            value[field] = format(number, "f")
        return value

    @staticmethod
    def _store_result(
        directory: Path,
        manifest: Mapping[str, Any],
        result: Mapping[str, Any],
        output_hash: str,
        executed_by: str,
    ) -> None:
        database = directory / "experiment.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runner_results ("
                "run_manifest_id TEXT PRIMARY KEY, run_manifest_record_hash TEXT NOT NULL, "
                "runner_output_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, "
                "executed_by TEXT NOT NULL, promotion_approved INTEGER NOT NULL CHECK(promotion_approved=0), "
                "order_submitted INTEGER NOT NULL CHECK(order_submitted=0), "
                "live_trading_enabled INTEGER NOT NULL CHECK(live_trading_enabled=0))"
            )
            connection.execute(
                "INSERT INTO runner_results VALUES (?,?,?,?,?,0,0,0)",
                (
                    manifest["run_manifest_id"], manifest["record_hash"], output_hash,
                    _canonical_json(result), _required(executed_by, "executed_by", 100),
                ),
            )

    @staticmethod
    def _store_failure(
        directory: Path, manifest: Mapping[str, Any], executed_by: str
    ) -> None:
        database = directory / "experiment.sqlite3"
        try:
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS runner_failures ("
                    "run_manifest_id TEXT PRIMARY KEY, failure_status TEXT NOT NULL, "
                    "executed_by TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO runner_failures VALUES (?,?,?)",
                    (
                        manifest["run_manifest_id"], "FAILED_CLOSED_NO_RESULT",
                        _required(executed_by, "executed_by", 100),
                    ),
                )
        except (OSError, sqlite3.Error, ValueError):
            pass
