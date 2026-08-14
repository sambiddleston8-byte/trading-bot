"""Fail-closed access to the project's fixed public read-only endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
import unicodedata

import requests

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    safe_access_dict,
)


PUBLIC_READ_POLICY = ProviderAccessPolicy(
    minimum_interval_seconds=0.25,
    maximum_attempts=2,
    base_backoff_seconds=0.5,
    retry_after_cap_seconds=5.0,
    failure_threshold=5,
    cooldown_seconds=60.0,
)


class PublicReadError(RuntimeError):
    """URL-, body- and raw-exception-free public-source failure."""

    def __init__(self, message: str, *, reason_code: str, access: Any = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.access = safe_access_dict(access)


@dataclass(frozen=True)
class PublicReadEndpoint:
    provider_key: str
    provider_name: str
    host: str
    paths: frozenset[str]
    parameter_names: frozenset[str]
    maximum_bytes: int

    def __post_init__(self) -> None:
        if (
            not self.provider_key
            or not self.provider_name
            or not self.host
            or not self.paths
            or self.maximum_bytes <= 0
        ):
            raise ValueError("public endpoint policy is invalid")
        if any(not path.startswith("/") for path in self.paths):
            raise ValueError("public endpoint paths must be absolute")


GITHUB_UNIVERSE_ENDPOINT = PublicReadEndpoint(
    provider_key="PUBLIC_GITHUB_RAW_UNIVERSE",
    provider_name="GitHub raw universe CSV",
    host="raw.githubusercontent.com",
    paths=frozenset(
        {
            "/datasets/s-and-p-500-companies/main/data/constituents.csv",
            "/Gary-Strauss/NASDAQ100_Constituents/master/data/nasdaq100_constituents.csv",
        }
    ),
    parameter_names=frozenset(),
    maximum_bytes=5 * 1024 * 1024,
)
FRED_GRAPH_ENDPOINT = PublicReadEndpoint(
    provider_key="PUBLIC_FRED_GRAPH",
    provider_name="FRED graph CSV",
    host="fred.stlouisfed.org",
    paths=frozenset({"/graph/fredgraph.csv"}),
    parameter_names=frozenset({"id"}),
    maximum_bytes=5 * 1024 * 1024,
)
GOOGLE_NEWS_ENDPOINT = PublicReadEndpoint(
    provider_key="PUBLIC_GOOGLE_NEWS_RSS",
    provider_name="Google News RSS",
    host="news.google.com",
    paths=frozenset({"/rss/search"}),
    parameter_names=frozenset({"q", "hl", "gl", "ceid"}),
    maximum_bytes=2 * 1024 * 1024,
)


def _fixed_target(url: Any, endpoint: PublicReadEndpoint) -> str:
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(not 0x21 <= ord(character) <= 0x7E for character in url)
    ):
        raise PublicReadError(
            "Public-source request target is invalid.", reason_code="INVALID_TARGET"
        )
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != endpoint.host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in endpoint.paths
    ):
        raise PublicReadError(
            "Public-source request target is invalid.", reason_code="INVALID_TARGET"
        )
    return urlunsplit(("https", endpoint.host, parsed.path, "", ""))


class PublicTextClient:
    """Read bounded strict UTF-8 text from one fixed endpoint family."""

    def __init__(
        self,
        endpoint: PublicReadEndpoint,
        *,
        session: requests.Session | None = None,
        access: ProviderAccessCoordinator | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.session = session or requests.Session()
        self.access = access or ProviderAccessCoordinator.for_provider(
            endpoint.provider_key,
            endpoint.provider_name,
            policy=PUBLIC_READ_POLICY,
        )

    def get_text(
        self,
        url: Any,
        *,
        params: Mapping[str, Any] | None = None,
        accept: str,
    ) -> str:
        target = _fixed_target(url, self.endpoint)
        if (
            not isinstance(accept, str)
            or not accept
            or len(accept) > 256
            or any(not 0x20 <= ord(character) <= 0x7E for character in accept)
        ):
            raise PublicReadError(
                "Public-source accept header is invalid.",
                reason_code="INVALID_HEADERS",
            )
        if params is not None and not isinstance(params, Mapping):
            raise PublicReadError(
                "Public-source request parameters are invalid.",
                reason_code="INVALID_PARAMETERS",
            )
        safe_params = dict(params or {})
        if set(safe_params) != set(self.endpoint.parameter_names):
            raise PublicReadError(
                "Public-source request parameters are invalid.",
                reason_code="INVALID_PARAMETERS",
            )
        for key, value in safe_params.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not value
                or len(value) > 512
                or any(unicodedata.category(character).startswith("C") for character in value)
            ):
                raise PublicReadError(
                    "Public-source request parameters are invalid.",
                    reason_code="INVALID_PARAMETERS",
                )
        try:
            result = self.access.get(
                self.session,
                target,
                params=safe_params or None,
                headers={"Accept": accept},
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
                "Public-source access is temporarily paused."
                if reason == "CIRCUIT_OPEN"
                else "Public source could not be reached."
            )
            raise PublicReadError(
                message, reason_code=reason, access=error.metadata
            ) from None

        response = result.response
        status = getattr(response, "status_code", None)
        safe_status = (
            status if isinstance(status, int) and not isinstance(status, bool) else 0
        )
        if not 200 <= safe_status < 300:
            raise PublicReadError(
                f"Public source rejected the request (HTTP {safe_status}).",
                reason_code="HTTP_REJECTED",
                access=result.metadata,
            )
        raw = getattr(response, "content", None)
        if not isinstance(raw, bytes) or not raw:
            raise PublicReadError(
                "Public source returned an unusable response.",
                reason_code="INVALID_RESPONSE",
                access=result.metadata,
            )
        if len(raw) > self.endpoint.maximum_bytes:
            raise PublicReadError(
                "Public source returned a response above the local parsing limit.",
                reason_code="RESPONSE_TOO_LARGE",
                access=result.metadata,
            )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PublicReadError(
                "Public source returned an unusable response.",
                reason_code="INVALID_RESPONSE",
                access=result.metadata,
            ) from None
