"""Fail-closed, paced JSON access for official SEC EDGAR hosts."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    safe_access_dict,
)


SEC_ALLOWED_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
SEC_ALLOWED_PATHS = {
    "data.sec.gov": (
        re.compile(r"/submissions/CIK[0-9]{10}\.json"),
        re.compile(r"/api/xbrl/companyfacts/CIK[0-9]{10}\.json"),
    ),
    "www.sec.gov": (
        re.compile(r"/files/company_tickers\.json"),
        re.compile(r"/files/company_tickers_exchange\.json"),
    ),
}
# Local parsing-resource policy, not a claim about an SEC response-size limit.
SEC_MAX_JSON_BYTES = 25 * 1024 * 1024
SEC_ACCESS_POLICY = ProviderAccessPolicy(
    minimum_interval_seconds=0.11,
    maximum_attempts=2,
    base_backoff_seconds=0.5,
    retry_after_cap_seconds=5.0,
    failure_threshold=5,
    cooldown_seconds=60.0,
)


class SECProviderError(RuntimeError):
    """Secret- and request-detail-free SEC access failure."""

    def __init__(self, message: str, *, reason_code: str, access: Any = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.access = safe_access_dict(access)


def _official_sec_url(url: Any) -> str:
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(not 0x21 <= ord(character) <= 0x7E for character in url)
    ):
        raise SECProviderError(
            "SEC request target is invalid.", reason_code="INVALID_TARGET"
        )
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in SEC_ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or not any(
            pattern.fullmatch(parsed.path)
            for pattern in SEC_ALLOWED_PATHS.get(parsed.hostname or "", ())
        )
    ):
        raise SECProviderError(
            "SEC request target is invalid.", reason_code="INVALID_TARGET"
        )
    return urlunsplit(("https", parsed.hostname, parsed.path, "", ""))


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise SECProviderError(
            "SEC returned an unusable JSON response.", reason_code="INVALID_RESPONSE"
        )
    if len(raw) > SEC_MAX_JSON_BYTES:
        raise SECProviderError(
            "SEC returned a JSON response above the local parsing limit.",
            reason_code="RESPONSE_TOO_LARGE",
        )

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SECProviderError(
            "SEC returned an unusable JSON response.", reason_code="INVALID_RESPONSE"
        ) from error
    if not isinstance(payload, dict):
        raise SECProviderError(
            "SEC returned an unusable JSON response.", reason_code="INVALID_RESPONSE"
        )
    return payload


class SECJSONClient:
    """Read official SEC JSON with shared pacing and no redirect following."""

    def __init__(
        self,
        *,
        user_agent: str,
        session: requests.Session | None = None,
        access: ProviderAccessCoordinator | None = None,
    ) -> None:
        if (
            not isinstance(user_agent, str)
            or not user_agent.strip()
            or user_agent != user_agent.strip()
            or len(user_agent) > 256
            or any(not 0x20 <= ord(character) <= 0x7E for character in user_agent)
        ):
            raise ValueError("SEC user agent must be a bounded non-empty string")
        self.session = session or requests.Session()
        self.access = access or ProviderAccessCoordinator.for_provider(
            "SEC_EDGAR",
            "SEC EDGAR",
            policy=SEC_ACCESS_POLICY,
        )
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

    def get_json(self, url: Any) -> dict[str, Any]:
        target = _official_sec_url(url)
        try:
            result = self.access.get(
                self.session,
                target,
                headers=self.headers,
                timeout=30,
                allow_redirects=False,
            )
        except ProviderAccessError as error:
            reason = (
                "CIRCUIT_OPEN"
                if error.reason_code == "CIRCUIT_OPEN"
                else "TRANSPORT_FAILURE"
            )
            message = (
                "SEC access is temporarily paused."
                if reason == "CIRCUIT_OPEN"
                else "SEC could not be reached."
            )
            raise SECProviderError(
                message, reason_code=reason, access=error.metadata
            ) from None

        response = result.response
        status = getattr(response, "status_code", None)
        safe_status = (
            status if isinstance(status, int) and not isinstance(status, bool) else 0
        )
        if not 200 <= safe_status < 300:
            raise SECProviderError(
                f"SEC rejected the request (HTTP {safe_status}).",
                reason_code="HTTP_REJECTED",
                access=result.metadata,
            )
        raw = getattr(response, "content", None)
        if not isinstance(raw, bytes):
            raise SECProviderError(
                "SEC returned an unusable JSON response.",
                reason_code="INVALID_RESPONSE",
                access=result.metadata,
            )
        try:
            return _strict_json_object(raw)
        except SECProviderError as error:
            raise SECProviderError(
                str(error), reason_code=error.reason_code, access=result.metadata
            ) from None
