from __future__ import annotations

import json

import pytest

from core.operations import JobAlreadyRunning, JobRunGuard


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_successful_run_has_append_only_start_and_finish_events(tmp_path):
    guard = JobRunGuard("research-cycle", runtime_directory=tmp_path)

    with guard:
        guard.heartbeat()

    events = _events(guard.event_path)
    assert [event["state"] for event in events] == ["STARTED", "HEARTBEAT", "SUCCEEDED"]
    assert {event["run_id"] for event in events} == {guard.run_id}
    assert all(event["execution_mode"] == "RECORD_ONLY" for event in events)


def test_failure_is_recorded_without_swallowing_the_error(tmp_path):
    guard = JobRunGuard("portfolio-monitor", runtime_directory=tmp_path)

    with pytest.raises(RuntimeError, match="simulated"):
        with guard:
            raise RuntimeError("simulated")

    events = _events(guard.event_path)
    assert [event["state"] for event in events] == ["STARTED", "FAILED"]
    assert events[-1]["error_type"] == "RuntimeError"
    assert "simulated" not in guard.event_path.read_text()


def test_global_mutation_lock_prevents_overlapping_jobs(tmp_path):
    first = JobRunGuard("research-cycle", runtime_directory=tmp_path)
    second = JobRunGuard("portfolio-monitor", runtime_directory=tmp_path)

    with first:
        with pytest.raises(JobAlreadyRunning):
            with second:
                pass

    second_events = [event for event in _events(first.event_path) if event["run_id"] == second.run_id]
    assert [event["state"] for event in second_events] == ["FAILED"]
    assert second_events[0]["error_type"] == "JobAlreadyRunning"


@pytest.mark.parametrize("mode", ["LIVE", "REAL_MONEY", "", "production"])
def test_live_or_unknown_execution_modes_are_rejected(tmp_path, mode):
    with pytest.raises(ValueError, match="live execution is unsupported"):
        JobRunGuard("research-cycle", runtime_directory=tmp_path, execution_mode=mode)


def test_paper_only_is_explicitly_supported_without_broker_route(tmp_path):
    guard = JobRunGuard(
        "portfolio-monitor", runtime_directory=tmp_path, execution_mode="PAPER_ONLY"
    )
    with guard:
        pass
    assert _events(guard.event_path)[0]["execution_mode"] == "PAPER_ONLY"


def test_job_name_cannot_escape_the_runtime_directory(tmp_path):
    with pytest.raises(ValueError, match="job name"):
        JobRunGuard("../unsafe", runtime_directory=tmp_path)


def test_reserved_global_lock_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="reserved global lock"):
        JobRunGuard("portfolio-mutation", runtime_directory=tmp_path)


def test_start_event_failure_releases_both_locks(tmp_path, monkeypatch):
    first = JobRunGuard("research-cycle", runtime_directory=tmp_path)
    monkeypatch.setattr(first, "_append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        with first:
            pass

    second = JobRunGuard("portfolio-monitor", runtime_directory=tmp_path)
    with second:
        pass


def test_unexpected_second_lock_failure_releases_global_lock(tmp_path, monkeypatch):
    first = JobRunGuard("research-cycle", runtime_directory=tmp_path)
    original_acquire = first._acquire
    calls = 0

    def fail_second_lock(name):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError()
        original_acquire(name)

    monkeypatch.setattr(first, "_acquire", fail_second_lock)
    with pytest.raises(PermissionError):
        with first:
            pass

    second = JobRunGuard("portfolio-monitor", runtime_directory=tmp_path)
    with second:
        pass
    failed = [event for event in _events(first.event_path) if event["run_id"] == first.run_id]
    assert failed[0]["error_type"] == "PermissionError"
