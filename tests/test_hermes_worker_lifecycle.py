from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import HermesWorkerLifecycleLedger


class PolicyLedger:
    def __init__(self, policy): self.policy = policy
    def verify(self): return [self.policy]


class ActivationLedger:
    def __init__(self, activation, policy):
        self.activation = activation
        self.policy_ledger = PolicyLedger(policy)
    def verify(self): return [self.activation]
    def admission(self, **request):
        now = request["now"]
        if request["activation_id"] != self.activation["activation_id"]:
            return {"work_allowed": False, "reason": "UNKNOWN_ACTIVATION"}
        return {
            "work_allowed": True, "activation_id": self.activation["activation_id"],
            "policy_id": self.activation["policy_id"], "job_name": request["job_name"],
            "deadline": (now + timedelta(seconds=request["requested_duration_seconds"])).isoformat(),
            "approved_llm_calls": request["requested_llm_calls"],
            "approved_ai_cost_usd": str(request["requested_ai_cost_usd"]),
            "approved_simulated_exposure_usd": str(request["requested_simulated_exposure_usd"]),
        }


class StopLedger:
    def __init__(self): self.items = []
    def verify(self): return self.items
    def trigger(self, **values):
        record = {
            "stop_id": "HSTOP-1", "policy_id": values["policy_id"],
            "reason": values["reason"],
        }
        self.items = [record]
        return record


def setup(tmp_path, *, failures=3, jobs=4, daily_cost="10", concurrency=2):
    now = datetime.now(timezone.utc)
    policy = {
        "policy_id": "HMPOL-1", "record_hash": "policy-hash",
        "emergency_stop_identifier": "stop-v1", "max_scheduled_jobs_per_day": jobs,
        "max_daily_ai_cost_usd": daily_cost, "max_consecutive_failures": failures,
    }
    activation = {
        "activation_id": "HMACT-1", "record_hash": "activation-hash",
        "policy_id": policy["policy_id"], "active_from": (now - timedelta(hours=1)).isoformat(),
        "active_until": (now + timedelta(hours=3)).isoformat(),
        "allowed_jobs": ["lesson-proposal"], "git_revision": "a" * 40,
        "max_concurrent_jobs": concurrency, "max_job_duration_seconds": 600,
        "max_llm_calls_per_job": 2, "max_ai_cost_per_job_usd": "3",
        "max_simulated_portfolio_exposure_usd": "10000", "stale_heartbeat_seconds": 60,
    }
    stops = StopLedger()
    target = HermesWorkerLifecycleLedger(
        tmp_path / "lifecycle.jsonl", ActivationLedger(activation, policy), stops
    )
    return target, now, stops


def reserve(target, now, request="request-1", **changes):
    values = {
        "activation_id": "HMACT-1", "request_id": request,
        "job_name": "lesson-proposal", "git_revision": "a" * 40,
        "requested_duration_seconds": 300, "requested_llm_calls": 1,
        "requested_ai_cost_usd": "2", "requested_simulated_exposure_usd": "1000",
        "requested_at": now,
    }
    values.update(changes)
    return target.reserve(**values)


def set_clock(monkeypatch, value):
    clock = [value]
    monkeypatch.setattr(
        "core.orchestration.hermes_worker_lifecycle._now", lambda: clock[0]
    )
    return clock


def test_complete_lifecycle_is_append_only_and_grants_no_authority(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    clock = set_clock(monkeypatch, now)
    reserved = reserve(target, now)
    clock[0] = now + timedelta(seconds=1); started = target.start(reserved["run_id"], started_at=clock[0])
    clock[0] = now + timedelta(seconds=30); heartbeat = target.heartbeat(reserved["run_id"], heartbeat_at=clock[0])
    clock[0] = now + timedelta(seconds=45); finished = target.finish(
        reserved["run_id"], succeeded=True, actual_llm_calls=1,
        actual_ai_cost_usd="1.25", output_sha256="b" * 64, finished_at=clock[0],
    )
    assert [item["state"] for item in target.verify()] == [
        "RESERVED_NOT_STARTED", "STARTED", "HEARTBEAT", "SUCCEEDED"
    ]
    assert reserved["previous_hash"] == GENESIS_HASH
    assert finished["actual_ai_cost_usd"] == "1.25"
    for item in (reserved, started, heartbeat, finished):
        for field in (
            "scheduler_created", "work_executed_by_ledger", "network_access_allowed",
            "broker_access_allowed", "aws_access_allowed", "github_write_allowed",
            "promotion_allowed", "order_submission_allowed", "live_trading_enabled",
        ):
            assert item[field] is False


def test_request_retry_is_idempotent_but_changed_request_is_rejected(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    set_clock(monkeypatch, now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: reserve(target, now), range(2)))
    assert first == second and len(target.verify()) == 1
    with pytest.raises(LedgerIntegrityError, match="reused"):
        reserve(target, now + timedelta(seconds=1), requested_ai_cost_usd="1")


def test_daily_job_cost_and_concurrency_are_reserved_before_work(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path, jobs=2, daily_cost="3", concurrency=2)
    set_clock(monkeypatch, now)
    reserve(target, now, "one", requested_ai_cost_usd="2")
    with pytest.raises(RuntimeError, match="cost"):
        reserve(target, now, "two", requested_ai_cost_usd="2")
    target2, now2, _ = setup(tmp_path / "other", jobs=3, daily_cost="9", concurrency=1)
    set_clock(monkeypatch, now2)
    reserve(target2, now2, "one")
    with pytest.raises(RuntimeError, match="concurrency"):
        reserve(target2, now2, "two")


def test_invalid_transitions_and_usage_fail_before_append(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    clock = set_clock(monkeypatch, now)
    run = reserve(target, now)
    with pytest.raises(RuntimeError, match="transition"):
        target.heartbeat(run["run_id"], heartbeat_at=now)
    clock[0] = now + timedelta(seconds=1); target.start(run["run_id"], started_at=clock[0])
    with pytest.raises(ValueError):
        target.finish(
            run["run_id"], succeeded=True, actual_llm_calls=2,
            actual_ai_cost_usd="4", output_sha256="bad", finished_at=clock[0],
        )
    assert len(target.verify()) == 2


def test_stale_worker_cannot_report_success_instead_of_being_stopped(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    clock = set_clock(monkeypatch, now)
    run = reserve(target, now)
    clock[0] = now + timedelta(seconds=1)
    target.start(run["run_id"], started_at=clock[0])
    clock[0] = now + timedelta(seconds=62)
    with pytest.raises(RuntimeError, match="heartbeat is stale"):
        target.finish(
            run["run_id"], succeeded=True, actual_llm_calls=0,
            actual_ai_cost_usd="0", output_sha256="d" * 64,
            finished_at=clock[0],
        )


def test_stop_latched_between_admission_and_reservation_denies_work(tmp_path, monkeypatch):
    target, now, stops = setup(tmp_path)
    set_clock(monkeypatch, now)
    original = target.activation_ledger.admission
    def latch_then_admit(**values):
        result = original(**values)
        stops.items = [{"policy_id": "HMPOL-1", "stop_id": "HSTOP-RACE"}]
        return result
    monkeypatch.setattr(target.activation_ledger, "admission", latch_then_admit)
    with pytest.raises(RuntimeError, match="stop is latched"):
        reserve(target, now)
    assert target.records() == []


@pytest.mark.parametrize("reason,offset", [
    ("STALE_HEARTBEAT", 62),
    ("JOB_DEADLINE_EXCEEDED", 301),
])
def test_enforcement_quarantines_and_latches_stop(tmp_path, monkeypatch, reason, offset):
    target, now, stops = setup(tmp_path)
    clock = set_clock(monkeypatch, now)
    run = reserve(target, now)
    if reason == "STALE_HEARTBEAT":
        clock[0] = now + timedelta(seconds=1)
        target.start(run["run_id"], started_at=clock[0])
    clock[0] = now + timedelta(seconds=offset)
    result = target.enforce(checked_at=clock[0])
    assert result["state"] == "STOPPED_LATCHED" and result["reason"] == reason
    assert stops.items[0]["reason"] == reason
    assert target.verify()[-1]["state"] == "QUARANTINED"


def test_consecutive_failures_latch_stop_and_success_resets_count(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path, failures=2, concurrency=1)
    clock = set_clock(monkeypatch, now)
    for index, succeeded in enumerate((False, True, False, False)):
        at = now + timedelta(minutes=index)
        clock[0] = at
        run = reserve(target, at, f"request-{index}")
        clock[0] = at + timedelta(seconds=1); target.start(run["run_id"], started_at=clock[0])
        clock[0] = at + timedelta(seconds=2); target.finish(
            run["run_id"], succeeded=succeeded, actual_llm_calls=0,
            actual_ai_cost_usd="0", output_sha256="c" * 64 if succeeded else None,
            failure_category=None if succeeded else "TEST_FAILURE", finished_at=clock[0],
        )
        if index < 3:
            assert target.enforce(checked_at=clock[0])["state"] == "WITHIN_LIMITS"
    result = target.enforce(checked_at=clock[0])
    assert result["reason"] == "CONSECUTIVE_FAILURE_LIMIT_REACHED"


def test_rehashed_tampering_is_detected(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    set_clock(monkeypatch, now)
    reserve(target, now)
    from core.orchestration import hermes_worker_lifecycle as module
    value = json.loads(target.path.read_text())
    value["broker_access_allowed"] = True
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(value) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_enforcement_rejects_noncurrent_supplied_time(tmp_path, monkeypatch):
    target, now, _ = setup(tmp_path)
    set_clock(monkeypatch, now)
    with pytest.raises(ValueError, match="actual time"):
        target.enforce(checked_at=now - timedelta(hours=1))
