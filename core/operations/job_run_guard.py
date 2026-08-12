from __future__ import annotations

"""Non-overlap and append-only run history for portfolio-changing jobs."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from types import TracebackType
from uuid import uuid4


ALLOWED_EXECUTION_MODES = {"RECORD_ONLY", "PAPER_ONLY"}
ROOT = Path(__file__).resolve().parents[2]


class JobAlreadyRunning(RuntimeError):
    """Raised when a job cannot safely acquire the required lock set."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_revision() -> str:
    configured = os.getenv("GIT_REVISION") or os.getenv("IMAGE_REVISION")
    if configured:
        return configured
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


class JobRunGuard:
    """Acquire locks in a fixed order and retain append-only lifecycle events."""

    def __init__(
        self,
        command: str,
        *,
        runtime_directory: Path | None = None,
        execution_mode: str | None = None,
    ) -> None:
        self.command = str(command)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.command):
            raise ValueError("command must be a lowercase hyphenated job name")
        if self.command == "portfolio-mutation":
            raise ValueError("command cannot use the reserved global lock name")
        self.runtime_directory = Path(
            runtime_directory
            or os.getenv("JOB_RUNTIME_DIRECTORY")
            or ROOT / "data" / "runtime"
        )
        configured_mode = (
            execution_mode
            if execution_mode is not None
            else os.getenv("EXECUTION_MODE", "RECORD_ONLY")
        )
        self.execution_mode = str(configured_mode).upper()
        if self.execution_mode not in ALLOWED_EXECUTION_MODES:
            raise ValueError(
                "EXECUTION_MODE must be RECORD_ONLY or PAPER_ONLY; live execution is unsupported"
            )
        self.run_id = str(uuid4())
        self.git_revision = _git_revision()
        self.image_revision = os.getenv("IMAGE_REVISION") or self.git_revision
        self._locks: list[object] = []
        self._started = False

    @property
    def event_path(self) -> Path:
        return self.runtime_directory / "job_run_events.jsonl"

    def _append_event(self, state: str, *, error_type: str | None = None) -> None:
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        event = {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "run_id": self.run_id,
            "command": self.command,
            "state": state,
            "timestamp": _utc_now(),
            "git_revision": self.git_revision,
            "image_revision": self.image_revision,
            "execution_mode": self.execution_mode,
        }
        if error_type:
            event["error_type"] = error_type
        line = json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
        lock_path = self.runtime_directory / "job_run_events.lock"
        with lock_path.open("a", encoding="utf-8") as log_lock:
            fcntl.flock(log_lock.fileno(), fcntl.LOCK_EX)
            with self.event_path.open("a", encoding="utf-8") as target:
                target.write(line)
                target.flush()
                os.fsync(target.fileno())
            fcntl.flock(log_lock.fileno(), fcntl.LOCK_UN)

    def _acquire(self, name: str) -> None:
        path = self.runtime_directory / f"{name}.lock"
        handle = path.open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise JobAlreadyRunning(
                f"Another portfolio-changing job holds the {name} lock"
            ) from error
        self._locks.append(handle)

    def _release_all(self) -> None:
        while self._locks:
            handle = self._locks.pop()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def __enter__(self) -> "JobRunGuard":
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        try:
            # All mutation-capable jobs use this exact global-then-job order.
            self._acquire("portfolio-mutation")
            self._acquire(self.command)
        except JobAlreadyRunning:
            self._release_all()
            self._append_event("FAILED", error_type="JobAlreadyRunning")
            raise
        except Exception as error:
            self._release_all()
            self._append_event("FAILED", error_type=type(error).__name__)
            raise
        try:
            self._append_event("STARTED")
            self._started = True
        except Exception:
            self._release_all()
            raise
        return self

    def heartbeat(self) -> None:
        if not self._started:
            raise RuntimeError("Cannot heartbeat a job that has not started")
        self._append_event("HEARTBEAT")

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        try:
            if self._started:
                self._append_event(
                    "FAILED" if error_type else "SUCCEEDED",
                    error_type=error_type.__name__ if error_type else None,
                )
        finally:
            self._started = False
            self._release_all()
        return False
