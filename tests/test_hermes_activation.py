from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import HermesActivationLedger
from core.orchestration.hermes_activation import READ_ROOT_MARKER, WRITE_ROOT_MARKER


class Stub:
    def __init__(self, records=None):
        self.items = list(records or [])

    def verify(self):
        return self.items


def setup(tmp_path):
    now = datetime.now(timezone.utc)
    read_root = tmp_path / "evidence"
    write_root = tmp_path / "sandbox"
    read_root.mkdir()
    write_root.mkdir()
    (read_root / ".verified-local-evidence-root").write_text(READ_ROOT_MARKER)
    (write_root / ".hermes-sandbox-write-root").write_text(WRITE_ROOT_MARKER)
    policy = {
        "policy_id": "HMPOL-1",
        "record_hash": "policy-record-hash",
        "recorded_at": (now - timedelta(minutes=10)).isoformat(),
        "effective_not_before": (now + timedelta(minutes=5)).isoformat(),
        "status": "PREREGISTERED_INACTIVE",
        "allowed_actions": [
            "READ_VERIFIED_LOCAL_EVIDENCE",
            "WRITE_SANDBOX_LESSON_PROPOSAL",
            "WRITE_SANDBOX_EXPERIMENT_PROPOSAL",
            "REQUEST_HUMAN_REVIEW",
        ],
        "max_scheduled_jobs_per_day": 4,
        "max_job_duration_seconds": 3600,
        "max_llm_calls_per_job": 2,
        "max_daily_ai_cost_usd": "10",
    }
    policies = Stub([policy])
    stops = Stub()
    target = HermesActivationLedger(tmp_path / "activations.jsonl", policies, stops)
    values = {
        "policy_id": policy["policy_id"],
        "active_from": (now + timedelta(minutes=6)).isoformat(),
        "active_until": (now + timedelta(hours=1)).isoformat(),
        "allowed_jobs": ["lesson-proposal"],
        "job_action": "WRITE_SANDBOX_LESSON_PROPOSAL",
        "max_concurrent_jobs": 1,
        "max_job_duration_seconds": 600,
        "max_llm_calls_per_job": 1,
        "max_ai_cost_per_job_usd": "1.50",
        "max_simulated_portfolio_exposure_usd": "10000",
        "stale_heartbeat_seconds": 60,
        "permitted_read_roots": [str(read_root)],
        "permitted_write_roots": [str(write_root)],
        "permitted_endpoints": ["NONE"],
        "permitted_credential_names": ["NONE"],
        "git_revision": "a" * 40,
        "approved_by": "Sam",
        "approval_reference": "future-human-approval-1",
        "human_activation_confirmed": True,
        "recorded_at": now.isoformat(),
    }
    return target, values, stops, now


def approve(target, values, **changes):
    requested = dict(values)
    requested.update(changes)
    return target.approve(**requested)


def rewrite(path, **changes):
    from core.orchestration import hermes_activation as module
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_human_approved_window_is_bounded_but_starts_nothing(tmp_path):
    target, values, _, _ = setup(tmp_path)
    result = approve(target, values)
    assert result["status"] == "APPROVED_FUTURE_BOUNDED_WINDOW"
    assert result["previous_hash"] == GENESIS_HASH
    assert result["scheduler_created"] is False
    assert result["job_started"] is False
    for field in (
        "network_access_allowed", "broker_access_allowed", "aws_access_allowed",
        "github_write_allowed", "production_rule_write_allowed", "promotion_allowed",
        "order_submission_allowed", "live_trading_enabled", "self_extension_allowed",
        "self_budget_change_allowed",
    ):
        assert result[field] is False
    assert target.verify() == [result]


@pytest.mark.parametrize("change,fragment", [
    ({"human_activation_confirmed": False}, "human"),
    ({"active_from": "2000-01-01T00:00:00+00:00"}, "five minutes"),
    ({"active_until": "2030-01-01T00:00:00+00:00"}, "31 days"),
    ({"allowed_jobs": ["../escape"]}, "invalid job"),
    ({"job_action": "SUBMIT_BROKER_ORDER"}, "not permitted"),
    ({"max_concurrent_jobs": 5}, "daily policy"),
    ({"max_job_duration_seconds": 3601}, "between"),
    ({"max_llm_calls_per_job": 3}, "between"),
    ({"max_ai_cost_per_job_usd": "11"}, "approved bound"),
    ({"permitted_endpoints": ["https://example.com"]}, "local-only"),
    ({"permitted_credential_names": ["BROKER_KEY"]}, "local-only"),
    ({"git_revision": "abc123"}, "Git object"),
])
def test_unsafe_or_excessive_activation_is_rejected(tmp_path, change, fragment):
    target, values, _, _ = setup(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        approve(target, values, **change)


def test_existing_emergency_stop_prevents_activation(tmp_path):
    target, values, stops, _ = setup(tmp_path)
    stops.items = [{"policy_id": "HMPOL-1"}]
    with pytest.raises(ValueError, match="stopped"):
        approve(target, values)


def test_historical_verification_does_not_depend_on_root_still_existing(tmp_path):
    target, values, _, _ = setup(tmp_path)
    result = approve(target, values)
    read_root = Path(values["permitted_read_roots"][0])
    write_root = Path(values["permitted_write_roots"][0])
    (read_root / ".verified-local-evidence-root").unlink()
    (write_root / ".hermes-sandbox-write-root").unlink()
    read_root.rmdir()
    write_root.rmdir()
    assert target.verify() == [result]


def test_admission_denies_missing_or_replaced_permitted_root(tmp_path, monkeypatch):
    target, values, _, now = setup(tmp_path)
    activation = approve(target, values)
    write_root = Path(values["permitted_write_roots"][0])
    (write_root / ".hermes-sandbox-write-root").unlink()
    write_root.rmdir()
    monkeypatch.setattr(
        "core.orchestration.hermes_activation._now",
        lambda: now + timedelta(minutes=10),
    )
    result = target.admission(
        activation_id=activation["activation_id"], job_name="lesson-proposal",
        git_revision="a" * 40, requested_duration_seconds=300,
        requested_llm_calls=1, requested_ai_cost_usd="1",
        requested_simulated_exposure_usd="1000",
        now=(now + timedelta(minutes=10)).isoformat(),
    )
    assert result["reason"] == "PERMITTED_ROOT_UNAVAILABLE"


def test_roots_require_exact_distinct_safety_markers(tmp_path):
    target, values, _, _ = setup(tmp_path)
    read_root = Path(values["permitted_read_roots"][0])
    (read_root / ".verified-local-evidence-root").write_text("wrong")
    with pytest.raises(ValueError, match="safety marker"):
        approve(target, values)
    (read_root / ".verified-local-evidence-root").write_text(READ_ROOT_MARKER)
    values["permitted_write_roots"] = [str(read_root)]
    (read_root / ".hermes-sandbox-write-root").write_text(WRITE_ROOT_MARKER)
    with pytest.raises(ValueError, match="cannot overlap"):
        approve(target, values)


def test_concurrent_retry_is_idempotent(tmp_path):
    target, values, _, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: approve(target, values), range(2)))
    assert first == second
    assert len(target.verify()) == 1


def test_overlapping_window_for_same_policy_is_rejected(tmp_path):
    target, values, _, now = setup(tmp_path)
    approve(target, values)
    with pytest.raises(LedgerIntegrityError, match="overlapping"):
        approve(
            target, values,
            active_from=(now + timedelta(minutes=30)).isoformat(),
            active_until=(now + timedelta(hours=2)).isoformat(),
            approval_reference="future-human-approval-2",
        )


def test_admission_checks_window_revision_limits_and_authority(tmp_path, monkeypatch):
    target, values, _, now = setup(tmp_path)
    activation = approve(target, values)
    monkeypatch.setattr(
        "core.orchestration.hermes_activation._now",
        lambda: now + timedelta(minutes=10),
    )
    admitted = target.admission(
        activation_id=activation["activation_id"], job_name="lesson-proposal",
        git_revision="a" * 40, requested_duration_seconds=300,
        requested_llm_calls=1, requested_ai_cost_usd="1",
        requested_simulated_exposure_usd="1000",
        now=(now + timedelta(minutes=10)).isoformat(),
    )
    assert admitted["work_allowed"] is True
    assert admitted["state"] == "ADMISSION_CHECK_PASSED_NOT_SCHEDULED"
    assert admitted["job_started"] is False and admitted["scheduler_created"] is False
    for field in (
        "network_access_allowed", "broker_access_allowed", "aws_access_allowed",
        "github_write_allowed", "promotion_allowed", "order_submission_allowed",
        "live_trading_enabled",
    ):
        assert admitted[field] is False


@pytest.mark.parametrize("changes,reason", [
    ({"activation_id": "unknown"}, "UNKNOWN_ACTIVATION"),
    ({"job_name": "other-job"}, "JOB_NOT_ALLOWED"),
    ({"git_revision": "b" * 40}, "GIT_REVISION_MISMATCH"),
    ({"requested_duration_seconds": 601}, "INVALID_OR_EXCESSIVE_RESOURCE_REQUEST"),
    ({"requested_llm_calls": 2}, "INVALID_OR_EXCESSIVE_RESOURCE_REQUEST"),
    ({"requested_ai_cost_usd": "2"}, "INVALID_OR_EXCESSIVE_RESOURCE_REQUEST"),
    ({"requested_simulated_exposure_usd": "10001"}, "INVALID_OR_EXCESSIVE_RESOURCE_REQUEST"),
])
def test_admission_fails_closed(tmp_path, monkeypatch, changes, reason):
    target, values, _, now = setup(tmp_path)
    activation = approve(target, values)
    monkeypatch.setattr(
        "core.orchestration.hermes_activation._now",
        lambda: now + timedelta(minutes=10),
    )
    request = {
        "activation_id": activation["activation_id"], "job_name": "lesson-proposal",
        "git_revision": "a" * 40, "requested_duration_seconds": 300,
        "requested_llm_calls": 1, "requested_ai_cost_usd": "1",
        "requested_simulated_exposure_usd": "1000",
        "now": (now + timedelta(minutes=10)).isoformat(),
    }
    request.update(changes)
    result = target.admission(**request)
    assert result["work_allowed"] is False and result["reason"] == reason


def test_latched_stop_blocks_admission_and_window_end_is_hard(tmp_path, monkeypatch):
    target, values, stops, now = setup(tmp_path)
    activation = approve(target, values)
    clock = [now + timedelta(minutes=10)]
    monkeypatch.setattr("core.orchestration.hermes_activation._now", lambda: clock[0])
    request = {
        "activation_id": activation["activation_id"], "job_name": "lesson-proposal",
        "git_revision": "a" * 40, "requested_duration_seconds": 300,
        "requested_llm_calls": 1, "requested_ai_cost_usd": "1",
        "requested_simulated_exposure_usd": "1000",
        "now": (now + timedelta(minutes=10)).isoformat(),
    }
    stops.items = [{"policy_id": "HMPOL-1"}]
    assert target.admission(**request)["reason"] == "EMERGENCY_STOP_LATCHED"
    stops.items = []
    request["now"] = (now + timedelta(minutes=58)).isoformat()
    clock[0] = now + timedelta(minutes=58)
    assert target.admission(**request)["reason"] == "JOB_WOULD_OUTLIVE_APPROVED_WINDOW"


@pytest.mark.parametrize("changes", [
    {"status": "ACTIVE_FOREVER"},
    {"active_until": "2099-01-01T00:00:00+00:00"},
    {"max_ai_cost_per_job_usd": "999"},
    {"max_concurrent_jobs": "1"},
    {"max_job_duration_seconds": True},
    {"permitted_endpoints": ["https://broker.example"]},
    {"scheduler_created": True},
    {"job_started": True},
    {"broker_access_allowed": True},
    {"promotion_allowed": True},
    {"live_trading_enabled": True},
])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target, values, _, _ = setup(tmp_path)
    approve(target, values)
    rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()
