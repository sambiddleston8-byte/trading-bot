from __future__ import annotations

"""Secret-safe resilience controls for synchronous optional-provider GETs."""

from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Callable, ClassVar, Mapping

import requests


RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})
CIRCUIT_STATES = frozenset({"CLOSED", "OPEN"})
RECORDED_FLAGS = (
    "request_url_recorded",
    "request_parameters_recorded",
    "request_headers_recorded",
    "response_body_recorded",
)


@dataclass(frozen=True)
class ProviderAccessPolicy:
    minimum_interval_seconds: float = 0.1
    maximum_attempts: int = 2
    base_backoff_seconds: float = 0.25
    retry_after_cap_seconds: float = 5.0
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        numeric = {
            "minimum_interval_seconds": self.minimum_interval_seconds,
            "base_backoff_seconds": self.base_backoff_seconds,
            "retry_after_cap_seconds": self.retry_after_cap_seconds,
            "cooldown_seconds": self.cooldown_seconds,
        }
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in numeric.values()
        ):
            raise ValueError("provider access durations must be finite and non-negative")
        if (
            isinstance(self.maximum_attempts, bool)
            or not isinstance(self.maximum_attempts, int)
            or self.maximum_attempts not in {1, 2}
        ):
            raise ValueError("provider access permits only one or two total attempts")
        if (
            isinstance(self.failure_threshold, bool)
            or not isinstance(self.failure_threshold, int)
            or self.failure_threshold < 1
        ):
            raise ValueError("failure_threshold must be a positive integer")


@dataclass(frozen=True)
class ProviderAttemptMetadata:
    provider: str
    attempts: int
    retry_count: int
    retried_status_codes: tuple[int, ...]
    total_wait_seconds: float
    elapsed_seconds: float
    circuit_state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "retried_status_codes": list(self.retried_status_codes),
            "total_wait_seconds": self.total_wait_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "circuit_state": self.circuit_state,
            **{flag: False for flag in RECORDED_FLAGS},
        }


@dataclass(frozen=True)
class ProviderHTTPResult:
    response: Any
    metadata: ProviderAttemptMetadata


def safe_access_dict(value: Any) -> dict[str, Any] | None:
    """Return a freshly built whitelisted access dict, or None when unusable.

    Every field is revalidated and rebuilt from scalars, so no caller mapping,
    exception attribute or provider payload is ever passed through.  Anything
    unexpected degrades to ``None`` rather than leaking an unknown structure.
    """
    if isinstance(value, ProviderAttemptMetadata):
        source: Mapping[str, Any] = value.as_dict()
    elif isinstance(value, Mapping):
        source = value
    else:
        return None

    provider = source.get("provider")
    if not isinstance(provider, str) or not provider.strip() or len(provider) > 100:
        return None

    counts: dict[str, int] = {}
    for name in ("attempts", "retry_count"):
        count = source.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        counts[name] = count

    durations: dict[str, float] = {}
    for name in ("total_wait_seconds", "elapsed_seconds"):
        duration = source.get(name)
        if isinstance(duration, bool):
            return None
        try:
            resolved = float(duration)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(resolved) or resolved < 0:
            return None
        durations[name] = resolved

    codes = source.get("retried_status_codes")
    if isinstance(codes, (str, bytes)) or not isinstance(codes, (list, tuple)):
        return None
    resolved_codes: list[int] = []
    for code in codes:
        if isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599:
            return None
        resolved_codes.append(code)

    circuit_state = source.get("circuit_state")
    if circuit_state not in CIRCUIT_STATES:
        return None

    attempts = counts["attempts"]
    retry_count = counts["retry_count"]
    if attempts == 0:
        if (
            retry_count != 0
            or resolved_codes
            or durations["total_wait_seconds"] != 0.0
            or durations["elapsed_seconds"] != 0.0
            or circuit_state != "OPEN"
        ):
            return None
    elif retry_count > attempts - 1 or len(resolved_codes) > retry_count:
        return None

    return {
        "provider": provider.strip(),
        "attempts": attempts,
        "retry_count": retry_count,
        "retried_status_codes": resolved_codes,
        "total_wait_seconds": durations["total_wait_seconds"],
        "elapsed_seconds": durations["elapsed_seconds"],
        "circuit_state": circuit_state,
        # Always asserted, never copied: these can only ever be False here.
        **{flag: False for flag in RECORDED_FLAGS},
    }


class ProviderAccessError(RuntimeError):
    """A deliberately secret-free provider transport or circuit failure."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        metadata: ProviderAttemptMetadata,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.metadata = metadata


@dataclass
class _ProviderState:
    lock: threading.Lock
    next_request_at: float = 0.0
    consecutive_failures: int = 0
    circuit_opened_at: float | None = None


class ProviderAccessCoordinator:
    """Coordinate pacing, narrow retries and circuit state per provider key."""

    _states: ClassVar[dict[str, _ProviderState]] = {}
    _states_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        provider_key: str,
        provider_name: str,
        policy: ProviderAccessPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        state: _ProviderState | None = None,
    ) -> None:
        key = str(provider_key).strip()
        name = str(provider_name).strip()
        if not key or not name:
            raise ValueError("provider key and name are required")
        self.provider_key = key
        self.provider_name = name
        self.policy = policy or ProviderAccessPolicy()
        self.clock = clock
        self.sleep = sleep
        self._state = state or self._shared_state(key)

    @classmethod
    def _shared_state(cls, provider_key: str) -> _ProviderState:
        with cls._states_lock:
            return cls._states.setdefault(provider_key, _ProviderState(threading.Lock()))

    @classmethod
    def for_provider(
        cls,
        provider_key: str,
        provider_name: str,
        *,
        policy: ProviderAccessPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "ProviderAccessCoordinator":
        key = str(provider_key).strip()
        if not key:
            raise ValueError("provider key is required")
        return cls(
            provider_key=key,
            provider_name=provider_name,
            policy=policy,
            clock=clock,
            sleep=sleep,
            state=cls._shared_state(key),
        )

    @classmethod
    def reset(cls) -> None:
        """Clear process-local pacing/breaker state for deterministic tests."""
        with cls._states_lock:
            cls._states.clear()

    def _rejected_metadata(self) -> ProviderAttemptMetadata:
        """Describe a request the open circuit refused before any attempt.

        Every measurement is zero because nothing was sent, waited for or
        retried; reporting anything else would invent provider cost.
        """
        return ProviderAttemptMetadata(
            provider=self.provider_name,
            attempts=0,
            retry_count=0,
            retried_status_codes=(),
            total_wait_seconds=0.0,
            elapsed_seconds=0.0,
            circuit_state="OPEN",
        )

    def _circuit_allows_request(self) -> None:
        now = self.clock()
        with self._state.lock:
            opened = self._state.circuit_opened_at
            if opened is None:
                return
            if now - opened < self.policy.cooldown_seconds:
                raise ProviderAccessError(
                    f"{self.provider_name} access is temporarily paused after repeated failures.",
                    reason_code="CIRCUIT_OPEN",
                    metadata=self._rejected_metadata(),
                )
            # One post-cooldown request is allowed. A subsequent failure opens
            # the circuit again immediately; success fully resets the state.
            self._state.circuit_opened_at = None
            self._state.consecutive_failures = self.policy.failure_threshold - 1

    def _reserve_request_slot(self) -> float:
        with self._state.lock:
            now = self.clock()
            wait = max(0.0, self._state.next_request_at - now)
            start_at = max(now, self._state.next_request_at)
            self._state.next_request_at = start_at + self.policy.minimum_interval_seconds
        if wait:
            self.sleep(wait)
        return wait

    def _record_success(self) -> str:
        with self._state.lock:
            self._state.consecutive_failures = 0
            self._state.circuit_opened_at = None
        return "CLOSED"

    def _record_failure(self) -> str:
        with self._state.lock:
            self._state.consecutive_failures += 1
            if self._state.consecutive_failures >= self.policy.failure_threshold:
                self._state.circuit_opened_at = self.clock()
                return "OPEN"
        return "CLOSED"

    @staticmethod
    def _status_code(response: Any) -> int:
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        return 200 if getattr(response, "ok", False) else 500

    def _retry_delay(self, response: Any, retry_number: int) -> float:
        backoff = self.policy.base_backoff_seconds * (2 ** max(0, retry_number - 1))
        headers = getattr(response, "headers", None)
        raw = headers.get("Retry-After") if isinstance(headers, Mapping) else None
        try:
            retry_after = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            retry_after = 0.0
        if not math.isfinite(retry_after) or retry_after < 0:
            retry_after = 0.0
        return max(backoff, min(retry_after, self.policy.retry_after_cap_seconds))

    def get(
        self,
        session: Any,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20,
        allow_redirects: bool | None = None,
    ) -> ProviderHTTPResult:
        if allow_redirects is not None and not isinstance(allow_redirects, bool):
            raise TypeError("allow_redirects must be a boolean or None")
        self._circuit_allows_request()
        started = self.clock()
        total_wait = 0.0
        retried_status_codes: list[int] = []

        for attempt in range(1, self.policy.maximum_attempts + 1):
            total_wait += self._reserve_request_slot()
            try:
                request_kwargs = {
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                }
                if allow_redirects is not None:
                    request_kwargs["allow_redirects"] = allow_redirects
                response = session.get(url, **request_kwargs)
            except requests.RequestException:
                if attempt < self.policy.maximum_attempts:
                    delay = self.policy.base_backoff_seconds * (2 ** (attempt - 1))
                    if delay:
                        self.sleep(delay)
                        total_wait += delay
                    continue
                circuit = self._record_failure()
                raise ProviderAccessError(
                    f"{self.provider_name} could not be reached.",
                    reason_code="TRANSPORT_FAILURE",
                    metadata=ProviderAttemptMetadata(
                        provider=self.provider_name,
                        attempts=attempt,
                        retry_count=attempt - 1,
                        retried_status_codes=tuple(retried_status_codes),
                        total_wait_seconds=round(total_wait, 9),
                        elapsed_seconds=round(max(0.0, self.clock() - started), 9),
                        circuit_state=circuit,
                    ),
                ) from None

            status = self._status_code(response)
            if status in RETRYABLE_STATUS_CODES and attempt < self.policy.maximum_attempts:
                retried_status_codes.append(status)
                delay = self._retry_delay(response, attempt)
                if delay:
                    self.sleep(delay)
                    total_wait += delay
                continue

            circuit = self._record_success() if getattr(response, "ok", False) else self._record_failure()
            metadata = ProviderAttemptMetadata(
                provider=self.provider_name,
                attempts=attempt,
                retry_count=attempt - 1,
                retried_status_codes=tuple(retried_status_codes),
                total_wait_seconds=round(total_wait, 9),
                elapsed_seconds=round(max(0.0, self.clock() - started), 9),
                circuit_state=circuit,
            )
            return ProviderHTTPResult(response=response, metadata=metadata)

        raise AssertionError("provider access attempt loop terminated unexpectedly")
