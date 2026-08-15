from __future__ import annotations

"""Immutable pre-access plan for a quarantined historical-data sample.

This ledger fixes the requested basket, acquisition window, chronological
train/validation/untouched-test split, strategy implementation and complete
parameter search space before any provider bytes are requested.  It is not a
replay plan, a source approval or a dataset admission.
"""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    current_git_revision,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-historical-quarantine-preregistration-v1"
TARGET_BASKET = ("AAPL", "MSFT", "SPY")
PROVIDER_ID = "MASSIVE"
PROVIDER_DATASET_ID = "MASSIVE_STOCKS_CUSTOM_BARS_V2"
ENDPOINT_TEMPLATE = (
    "https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}"
)
SPLIT_ROLES = ("TRAIN", "VALIDATION", "UNTOUCHED_TEST")
MAX_ACQUISITION_DAYS = 366
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "provider_bytes_accessed",
    "provider_payload_semantics_qualified",
    "historical_availability_qualified",
    "source_bytes_authenticated",
    "dataset_admitted",
    "replay_data_attestation_issued",
    "synthetic_pilot_attestation_issued",
    "parameter_search_executed",
    "untouched_test_opened",
    "guardrailed_replay_executed",
    "performance_claim_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "preregistration_id",
        "record_type",
        "status",
        "registered_at",
        "registered_by",
        "git_revision",
        "provider_id",
        "provider_dataset_id",
        "endpoint_template",
        "target_basket",
        "acquisition_start",
        "acquisition_end",
        "splits",
        "strategy_entrypoint",
        "strategy_source_path",
        "strategy_version",
        "strategy_source_sha256",
        "parameter_space_canonical_json",
        "parameter_space_sha256",
        "evaluation_protocol",
        "evaluation_protocol_sha256",
        "entitlement_metadata",
        "entitlement_metadata_sha256",
        "unadjusted_daily_bars_only",
        "sort_order",
        "request_record_limit",
        "maximum_chunk_days",
        "quarantine_only",
        "git_worktree_clean",
        "externally_anchored",
        "data_access_not_before",
        "previous_hash",
        "record_hash",
        *FIXED_FALSE,
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_bounded_json_mapping(value: Mapping[str, Any], name: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    count = 0

    def validate(item: Any, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > 1_000 or depth > 10:
            raise ValueError(f"{name} exceeds the bounded structure")
        if isinstance(item, Mapping):
            if len(item) > 100 or any(
                not isinstance(key, str) or not key or len(key) > 200
                for key in item
            ):
                raise ValueError(f"{name} keys are invalid or excessive")
            for child in item.values():
                validate(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            if len(item) > 100:
                raise ValueError(f"{name} sequence is excessive")
            for child in item:
                validate(child, depth + 1)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{name} numbers must be finite")
        elif isinstance(item, str) and len(item) > 10_000:
            raise ValueError(f"{name} text is excessive")
        elif item is not None and not isinstance(item, (bool, int, float, str)):
            raise ValueError(f"{name} contains an unsupported value")

    validate(value)
    payload = _canonical_json(value)
    if len(payload.encode("utf-8")) > 100_000:
        raise ValueError(f"{name} exceeds 100000 bytes")
    return payload


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("historical quarantine preregistration append made no progress")
        offset += count


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _required(value: Any, name: str, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} must not contain control characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        resolved = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if resolved.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return resolved


def _git_revision(value: Any) -> str:
    resolved = _required(value, "git_revision", 64).lower()
    if GIT_REVISION_PATTERN.fullmatch(resolved) is None:
        raise ValueError("git_revision must be a full lowercase Git commit ID")
    return resolved


def _git_worktree_clean(project_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Git worktree cleanliness could not be established") from error
    return not result.stdout


def _strategy_source_identity(project_root: Path, value: Any) -> tuple[str, str]:
    relative_text = _strategy_source_path(value)
    relative = Path(relative_text)
    source = project_root / relative
    if source.is_symlink() or not source.is_file():
        raise ValueError("strategy source must be a regular non-symlink file")
    resolved_root = project_root.resolve()
    resolved_source = source.resolve()
    if resolved_root not in resolved_source.parents:
        raise ValueError("strategy source must remain inside the repository")
    if not 0 < resolved_source.stat().st_size <= 5 * 1024 * 1024:
        raise ValueError("strategy source size is outside the safe boundary")
    return relative_text, hashlib.sha256(resolved_source.read_bytes()).hexdigest()


def _strategy_source_path(value: Any) -> str:
    relative_text = _required(value, "strategy_source_path", 500)
    relative = Path(relative_text)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != relative_text
        or relative.suffix != ".py"
    ):
        raise ValueError(
            "strategy_source_path must be a canonical repository-relative Python path"
        )
    return relative_text


def _normalise_evaluation_protocol(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "primary_metric",
        "optimization_direction",
        "tie_break_metrics",
        "success_thresholds",
        "warmup_observations",
        "purge_observations",
        "embargo_observations",
        "maximum_untouched_test_evaluations",
        "execution_policy_version",
        "execution_policy_sha256",
        "selection_rule_version",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("evaluation_protocol has missing or unsupported fields")
    direction = _required(
        value.get("optimization_direction"), "optimization_direction", 20
    ).upper()
    if direction not in {"MAXIMIZE", "MINIMIZE"}:
        raise ValueError("optimization_direction must be MAXIMIZE or MINIMIZE")
    ties = value.get("tie_break_metrics")
    if not isinstance(ties, list) or len(ties) > 10:
        raise ValueError("tie_break_metrics must be a bounded list")
    normalized_ties: list[dict[str, str]] = []
    names: set[str] = set()
    primary = _required(value.get("primary_metric"), "primary_metric", 100)
    names.add(primary)
    for item in ties:
        if not isinstance(item, Mapping) or set(item) != {"metric", "direction"}:
            raise ValueError("each tie-break must contain metric and direction")
        metric = _required(item.get("metric"), "tie-break metric", 100)
        tie_direction = _required(
            item.get("direction"), "tie-break direction", 20
        ).upper()
        if tie_direction not in {"MAXIMIZE", "MINIMIZE"} or metric in names:
            raise ValueError("tie-break metrics must be unique with a valid direction")
        names.add(metric)
        normalized_ties.append({"metric": metric, "direction": tie_direction})
    counts: dict[str, int] = {}
    for field in ("warmup_observations", "purge_observations", "embargo_observations"):
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 10_000:
            raise ValueError(f"{field} must be an integer from 0 through 10000")
        counts[field] = item
    maximum_tests = value.get("maximum_untouched_test_evaluations")
    if maximum_tests != 1 or isinstance(maximum_tests, bool):
        raise ValueError("maximum_untouched_test_evaluations must be exactly 1")
    thresholds_json = _canonical_bounded_json_mapping(
        value.get("success_thresholds"), "success_thresholds"
    )
    return {
        "primary_metric": primary,
        "optimization_direction": direction,
        "tie_break_metrics": normalized_ties,
        "success_thresholds_canonical_json": thresholds_json,
        **counts,
        "maximum_untouched_test_evaluations": 1,
        "execution_policy_version": _required(
            value.get("execution_policy_version"), "execution_policy_version", 200
        ),
        "execution_policy_sha256": _sha256(
            value.get("execution_policy_sha256"), "execution_policy_sha256"
        ),
        "selection_rule_version": _required(
            value.get("selection_rule_version"), "selection_rule_version", 200
        ),
    }


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name, 2_000)
    parsed = urlsplit(resolved)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    return resolved


def _normalise_splits(
    values: Sequence[Mapping[str, Any]],
    *,
    acquisition_start: date,
    acquisition_end: date,
) -> list[dict[str, str]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("splits must be an ordered sequence")
    if len(values) != len(SPLIT_ROLES):
        raise ValueError("splits must contain TRAIN, VALIDATION and UNTOUCHED_TEST")
    normalized: list[dict[str, str]] = []
    expected_start = acquisition_start
    for expected_role, value in zip(SPLIT_ROLES, values):
        if not isinstance(value, Mapping) or set(value) != {"role", "start", "end"}:
            raise ValueError("each split must contain exactly role, start and end")
        role = _required(value.get("role"), "split role", 30).upper()
        start = _date(value.get("start"), f"{role} start")
        end = _date(value.get("end"), f"{role} end")
        if role != expected_role or start != expected_start or end < start:
            raise ValueError("splits must be contiguous, ordered and nonempty")
        normalized.append({"role": role, "start": start.isoformat(), "end": end.isoformat()})
        expected_start = end + timedelta(days=1)
    if normalized[-1]["end"] != acquisition_end.isoformat():
        raise ValueError("splits must cover the complete acquisition window exactly")
    return normalized


def _normalise_entitlement(value: Mapping[str, Any], *, registered_at: datetime) -> dict[str, Any]:
    required = {
        "plan_name",
        "terms_uri",
        "terms_retrieved_at",
        "terms_payload_sha256",
        "asserted_request_limit_per_minute",
        "asserted_incremental_cost_usd",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("entitlement_metadata has missing or unsupported fields")
    terms_uri = _https_uri(value.get("terms_uri"), "terms_uri")
    if urlsplit(terms_uri).hostname not in {"massive.com", "www.massive.com"}:
        raise ValueError("terms_uri must use an official Massive host")
    retrieved = _timestamp(value.get("terms_retrieved_at"), "terms_retrieved_at")
    request_limit = value.get("asserted_request_limit_per_minute")
    if isinstance(request_limit, bool) or not isinstance(request_limit, int) or request_limit != 5:
        raise ValueError("Massive free pilot request_limit_per_minute must be exactly 5")
    if value.get("asserted_incremental_cost_usd") != "0.00":
        raise ValueError("Massive free pilot incremental_cost_usd must be exactly 0.00")
    if retrieved > registered_at:
        raise ValueError("entitlement evidence cannot be retrieved after preregistration")
    return {
        "plan_name": _required(value.get("plan_name"), "plan_name", 100),
        "terms_uri": terms_uri,
        "terms_retrieved_at": retrieved.isoformat(timespec="microseconds"),
        "terms_payload_sha256": _sha256(
            value.get("terms_payload_sha256"), "terms_payload_sha256"
        ),
        "asserted_request_limit_per_minute": 5,
        "asserted_incremental_cost_usd": "0.00",
        "public_terms_assertions_authenticated": False,
        "account_entitlement_authenticated": False,
        "historical_replay_use_confirmed": False,
        "redistribution_allowed": False,
        "terms_bytes_authenticated": False,
    }


def _identity(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "registered_by",
            "git_revision",
            "provider_id",
            "provider_dataset_id",
            "endpoint_template",
            "target_basket",
            "acquisition_start",
            "acquisition_end",
            "splits",
            "strategy_entrypoint",
            "strategy_source_path",
            "strategy_version",
            "strategy_source_sha256",
            "parameter_space_canonical_json",
            "parameter_space_sha256",
            "evaluation_protocol",
            "evaluation_protocol_sha256",
            "entitlement_metadata",
            "entitlement_metadata_sha256",
        )
    }


def _preregistration_id(identity: Mapping[str, Any]) -> str:
    return "HQP-" + hashlib.sha256(
        _canonical_json([identity, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class HistoricalQuarantinePreregistrationLedger:
    """Append and verify immutable pre-access quarantine plans."""

    def __init__(
        self,
        path: str | Path,
        *,
        repository_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        git_revision_resolver: Callable[[Path], str] | None = None,
        worktree_clean_resolver: Callable[[Path], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._git_revision_resolver = git_revision_resolver or current_git_revision
        self._worktree_clean_resolver = worktree_clean_resolver or _git_worktree_clean

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDONLY | _no_follow())
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError("quarantine preregistration ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("quarantine preregistration ledger has an incomplete final line")
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"invalid quarantine preregistration JSON at line {line_number}"
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError("quarantine preregistration line is not an object")
            records.append(record)
        return records

    def preregister(
        self,
        *,
        registered_by: str,
        acquisition_start: str,
        acquisition_end: str,
        splits: Sequence[Mapping[str, Any]],
        strategy_entrypoint: str,
        strategy_source_path: str,
        strategy_version: str,
        parameter_space: Mapping[str, Any],
        evaluation_protocol: Mapping[str, Any],
        entitlement_metadata: Mapping[str, Any],
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        registered = _timestamp(self._clock(), "registration clock")
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= registered <= now + MAX_CLOCK_SKEW:
            raise ValueError("registration clock must match actual append time")
        start = _date(acquisition_start, "acquisition_start")
        end = _date(acquisition_end, "acquisition_end")
        if (
            end < start
            or (end - start).days + 1 > MAX_ACQUISITION_DAYS
            or end >= registered.date()
        ):
            raise ValueError(
                "acquisition window must be historical, ordered and at most 366 days"
            )
        normalized_splits = _normalise_splits(
            splits, acquisition_start=start, acquisition_end=end
        )
        canonical_parameters = _canonical_bounded_json_mapping(
            parameter_space, "parameter_space"
        )
        parameter_hash = hashlib.sha256(canonical_parameters.encode("utf-8")).hexdigest()
        normalized_evaluation = _normalise_evaluation_protocol(evaluation_protocol)
        evaluation_hash = hashlib.sha256(
            _canonical_json(normalized_evaluation).encode("utf-8")
        ).hexdigest()
        source_path, source_hash = _strategy_source_identity(
            self.repository_root, strategy_source_path
        )
        entrypoint = _required(strategy_entrypoint, "strategy_entrypoint", 300)
        module_name, separator, qualified_name = entrypoint.partition(":")
        source_module = source_path.removesuffix(".py").replace("/", ".")
        if source_module.endswith(".__init__"):
            source_module = source_module.removesuffix(".__init__")
        if (
            separator != ":"
            or not qualified_name
            or module_name != source_module
            or any(not part.isidentifier() for part in module_name.split("."))
        ):
            raise ValueError("strategy entrypoint does not match its source path")
        git_revision = _git_revision(self._git_revision_resolver(self.repository_root))
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError("Git worktree must be clean before preregistration")
        entitlement = _normalise_entitlement(
            entitlement_metadata, registered_at=registered
        )
        entitlement_hash = hashlib.sha256(
            _canonical_json(entitlement).encode("utf-8")
        ).hexdigest()
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "record_type": "HISTORICAL_SAMPLE_QUARANTINE_PREREGISTRATION",
            "status": "PREREGISTERED_BEFORE_PROVIDER_DATA_ACCESS",
            "registered_at": registered.isoformat(timespec="microseconds"),
            "registered_by": _required(registered_by, "registered_by", 100),
            "git_revision": git_revision,
            "git_worktree_clean": True,
            "externally_anchored": False,
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": PROVIDER_DATASET_ID,
            "endpoint_template": ENDPOINT_TEMPLATE,
            "target_basket": list(TARGET_BASKET),
            "acquisition_start": start.isoformat(),
            "acquisition_end": end.isoformat(),
            "splits": normalized_splits,
            "strategy_entrypoint": entrypoint,
            "strategy_source_path": source_path,
            "strategy_version": _required(strategy_version, "strategy_version", 200),
            "strategy_source_sha256": source_hash,
            "parameter_space_canonical_json": canonical_parameters,
            "parameter_space_sha256": parameter_hash,
            "evaluation_protocol": normalized_evaluation,
            "evaluation_protocol_sha256": evaluation_hash,
            "entitlement_metadata": entitlement,
            "entitlement_metadata_sha256": entitlement_hash,
            "unadjusted_daily_bars_only": True,
            "sort_order": "ASC",
            "request_record_limit": 120,
            "maximum_chunk_days": 31,
            "quarantine_only": True,
            "data_access_not_before": registered.isoformat(timespec="microseconds"),
            **{name: False for name in FIXED_FALSE},
        }
        identity = _identity(record)
        record["preregistration_id"] = _preregistration_id(identity)
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        previous_registered: datetime | None = None
        seen: set[str] = set()
        records = self.records()
        if len(records) > 1:
            raise LedgerIntegrityError(
                "a Massive quarantine campaign may contain only one preregistration"
            )
        for index, record in enumerate(records, start=1):
            try:
                if set(record) != REQUIRED_FIELDS:
                    raise ValueError("missing or unsupported fields")
                registered = _timestamp(record["registered_at"], "registered_at")
                data_access = _timestamp(
                    record["data_access_not_before"], "data_access_not_before"
                )
                start = _date(record["acquisition_start"], "acquisition_start")
                end = _date(record["acquisition_end"], "acquisition_end")
                splits = _normalise_splits(
                    record["splits"], acquisition_start=start, acquisition_end=end
                )
                canonical_parameters = _canonical_bounded_json_mapping(
                    json.loads(record["parameter_space_canonical_json"]),
                    "parameter_space",
                )
                parameter_hash = hashlib.sha256(
                    canonical_parameters.encode("utf-8")
                ).hexdigest()
                entitlement = _normalise_entitlement(
                    {
                        key: record["entitlement_metadata"][key]
                        for key in (
                            "plan_name",
                            "terms_uri",
                            "terms_retrieved_at",
                            "terms_payload_sha256",
                            "asserted_request_limit_per_minute",
                            "asserted_incremental_cost_usd",
                        )
                    },
                    registered_at=registered,
                )
                entitlement_hash = hashlib.sha256(
                    _canonical_json(entitlement).encode("utf-8")
                ).hexdigest()
                evaluation = _normalise_evaluation_protocol(
                    {
                        **{
                            key: value
                            for key, value in record["evaluation_protocol"].items()
                            if key != "success_thresholds_canonical_json"
                        },
                        "success_thresholds": json.loads(
                            record["evaluation_protocol"][
                                "success_thresholds_canonical_json"
                            ]
                        ),
                    }
                )
                evaluation_hash = hashlib.sha256(
                    _canonical_json(evaluation).encode("utf-8")
                ).hexdigest()
                identity = _identity(record)
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                boundary = (
                    record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["record_type"]
                    == "HISTORICAL_SAMPLE_QUARANTINE_PREREGISTRATION"
                    and record["status"] == "PREREGISTERED_BEFORE_PROVIDER_DATA_ACCESS"
                    and record["preregistration_id"] == _preregistration_id(identity)
                    and record["preregistration_id"] not in seen
                    and registered == data_access
                    and registered <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and (previous_registered is None or registered > previous_registered)
                    and end < registered.date()
                    and record["registered_by"]
                    == _required(record["registered_by"], "registered_by", 100)
                    and record["git_revision"] == _git_revision(record["git_revision"])
                    and record["git_worktree_clean"] is True
                    and record["externally_anchored"] is False
                    and record["provider_id"] == PROVIDER_ID
                    and record["provider_dataset_id"] == PROVIDER_DATASET_ID
                    and record["endpoint_template"] == ENDPOINT_TEMPLATE
                    and record["target_basket"] == list(TARGET_BASKET)
                    and 0 <= (end - start).days < MAX_ACQUISITION_DAYS
                    and record["splits"] == splits
                    and record["strategy_entrypoint"]
                    == _required(record["strategy_entrypoint"], "strategy_entrypoint", 300)
                    and record["strategy_source_path"]
                    == _strategy_source_path(record["strategy_source_path"])
                    and record["strategy_version"]
                    == _required(record["strategy_version"], "strategy_version", 200)
                    and record["strategy_source_sha256"]
                    == _sha256(record["strategy_source_sha256"], "strategy_source_sha256")
                    and record["parameter_space_canonical_json"] == canonical_parameters
                    and record["parameter_space_sha256"] == parameter_hash
                    and record["evaluation_protocol"] == evaluation
                    and record["evaluation_protocol_sha256"] == evaluation_hash
                    and record["entitlement_metadata"] == entitlement
                    and record["entitlement_metadata_sha256"] == entitlement_hash
                    and record["unadjusted_daily_bars_only"] is True
                    and record["sort_order"] == "ASC"
                    and record["request_record_limit"] == 120
                    and record["maximum_chunk_days"] == 31
                    and record["quarantine_only"] is True
                    and all(record[name] is False for name in FIXED_FALSE)
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _record_hash(material)
                )
            except (
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise LedgerIntegrityError(
                    f"historical quarantine preregistration {index} is invalid"
                ) from error
            if not boundary:
                raise LedgerIntegrityError(
                    f"historical quarantine preregistration {index} violates its boundary"
                )
            seen.add(record["preregistration_id"])
            previous = record["record_hash"]
            previous_registered = registered
        return records

    def require(self, preregistration_id: str) -> dict[str, Any]:
        identifier = _required(preregistration_id, "preregistration_id", 100)
        record = next(
            (item for item in self.verify() if item["preregistration_id"] == identifier),
            None,
        )
        if record is None:
            raise ValueError("verified quarantine preregistration was not found")
        return record

    def _append(self, record: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            lock_details = os.fstat(lock)
            if (
                not stat.S_ISREG(lock_details.st_mode)
                or stat.S_IMODE(lock_details.st_mode) != 0o600
                or lock_details.st_nlink != 1
            ):
                raise LedgerIntegrityError("quarantine preregistration lock is unsafe")
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["preregistration_id"] == record["preregistration_id"]
                ),
                None,
            )
            if existing is not None:
                ignored = {"registered_at", "data_access_not_before", "previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in record.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("quarantine preregistration already exists")
            if records:
                raise LedgerIntegrityError(
                    "this quarantine campaign already has a different preregistration"
                )
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            complete = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                details = os.fstat(target)
                if (
                    not stat.S_ISREG(details.st_mode)
                    or stat.S_IMODE(details.st_mode) != 0o600
                    or details.st_nlink != 1
                ):
                    raise LedgerIntegrityError("quarantine preregistration ledger is unsafe")
                _write_all(target, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
