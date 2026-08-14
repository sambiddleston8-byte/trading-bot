from __future__ import annotations

import requests
import pytest

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, status_code: int, *, headers=None) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}


class Session:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def reset_provider_state():
    ProviderAccessCoordinator.reset()
    yield
    ProviderAccessCoordinator.reset()


def coordinator(clock: Clock, **changes) -> ProviderAccessCoordinator:
    policy = ProviderAccessPolicy(
        minimum_interval_seconds=changes.pop("minimum_interval_seconds", 0),
        maximum_attempts=changes.pop("maximum_attempts", 2),
        base_backoff_seconds=changes.pop("base_backoff_seconds", 0),
        retry_after_cap_seconds=changes.pop("retry_after_cap_seconds", 2),
        failure_threshold=changes.pop("failure_threshold", 5),
        cooldown_seconds=changes.pop("cooldown_seconds", 60),
    )
    assert not changes
    return ProviderAccessCoordinator.for_provider(
        "TEST_PROVIDER_KEY",
        "Test provider",
        policy=policy,
        clock=clock,
        sleep=clock.sleep,
    )


def test_shared_provider_state_paces_separate_client_instances():
    clock = Clock()
    first = coordinator(clock, minimum_interval_seconds=1, maximum_attempts=1)
    second = coordinator(clock, minimum_interval_seconds=1, maximum_attempts=1)

    first.get(Session(Response(200)), "https://secret.invalid/first")
    result = second.get(Session(Response(200)), "https://secret.invalid/second")

    assert clock.sleeps == [1.0]
    assert result.metadata.total_wait_seconds == 1.0


def test_503_retries_once_and_clamps_retry_after_without_recording_request():
    clock = Clock()
    client = coordinator(clock, base_backoff_seconds=0.25, retry_after_cap_seconds=2)
    session = Session(Response(503, headers={"Retry-After": "999"}), Response(200))

    result = client.get(
        session,
        "https://provider.invalid/path?apikey=secret",
        params={"api_token": "secret"},
        headers={"Authorization": "secret"},
    )

    assert session.calls == 2
    assert clock.sleeps == [2.0]
    assert result.metadata.attempts == 2
    assert result.metadata.retried_status_codes == (503,)
    assert "secret" not in repr(result.metadata)
    assert all(value is False for key, value in result.metadata.as_dict().items() if key.endswith("recorded"))


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_terminal_or_quota_statuses_are_never_retried(status):
    clock = Clock()
    session = Session(Response(status), Response(200))

    result = coordinator(clock).get(session, "https://provider.invalid")

    assert session.calls == 1
    assert result.response.status_code == status
    assert result.metadata.retry_count == 0


def test_transport_failure_retries_once_then_raises_without_secret_or_cause():
    clock = Clock()
    secret = "apikey=never-expose"
    session = Session(
        requests.ConnectionError(f"failed https://provider.invalid?{secret}"),
        requests.ConnectionError(f"failed https://provider.invalid?{secret}"),
    )

    with pytest.raises(ProviderAccessError) as caught:
        coordinator(clock).get(session, f"https://provider.invalid?{secret}")

    assert session.calls == 2
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.reason_code == "TRANSPORT_FAILURE"


def test_circuit_opens_after_consecutive_operations_and_blocks_network():
    clock = Clock()
    client = coordinator(clock, maximum_attempts=1, failure_threshold=2)
    session = Session(Response(500), Response(500), Response(200))

    assert client.get(session, "https://provider.invalid").metadata.circuit_state == "CLOSED"
    assert client.get(session, "https://provider.invalid").metadata.circuit_state == "OPEN"
    with pytest.raises(ProviderAccessError, match="temporarily paused") as caught:
        client.get(session, "https://provider.invalid")

    assert caught.value.reason_code == "CIRCUIT_OPEN"
    assert session.calls == 2


def test_success_resets_failure_count_and_cooldown_allows_one_probe():
    clock = Clock()
    client = coordinator(
        clock,
        maximum_attempts=1,
        failure_threshold=1,
        cooldown_seconds=10,
    )

    assert client.get(Session(Response(500)), "https://provider.invalid").metadata.circuit_state == "OPEN"
    clock.now = 10
    result = client.get(Session(Response(200)), "https://provider.invalid")
    assert result.metadata.circuit_state == "CLOSED"
    assert client.get(Session(Response(200)), "https://provider.invalid").metadata.circuit_state == "CLOSED"


@pytest.mark.parametrize(
    "change",
    [
        {"maximum_attempts": 0},
        {"maximum_attempts": 3},
        {"maximum_attempts": True},
        {"maximum_attempts": 1.0},
        {"failure_threshold": 0},
        {"minimum_interval_seconds": -1},
        {"cooldown_seconds": float("inf")},
    ],
)
def test_policy_rejects_unbounded_or_ambiguous_values(change):
    with pytest.raises(ValueError):
        ProviderAccessPolicy(**change)
