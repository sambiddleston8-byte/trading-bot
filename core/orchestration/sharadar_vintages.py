from __future__ import annotations

"""Aggregate-only comparison of two immutable Sharadar capture observations.

The comparison uses SHA-256 canonical row multisets while spilling only row
digests and literal date ordinals to owner-local temporary files.  It never
emits licensed identifiers or rows.  Two current-vintage observations can
surface row churn, but cannot by themselves qualify provider PIT semantics,
historical availability, admission, performance, or promotion.
"""

from datetime import date, datetime
import hashlib
import heapq
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Iterable, Mapping

from core.orchestration.sharadar_foundation import (
    FALSE_AUTHORITIES,
    _archive_rows,
    _canonical_json,
    _day,
)
from core.orchestration.sharadar_quarantine import (
    QUARANTINE_RELATIVE_PATH,
    TEN_YEAR_TABLES,
    load_verified_bulk_capture_set,
    load_verified_foundation_observations,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "sharadar-cross-vintage-row-multiset-v1"
STATUS = "CROSS_VINTAGE_CHURN_MEASURED_AVAILABILITY_UNQUALIFIED"
DATE_FIELDS = {
    "tickers": None,
    "stocks": "date",
    "actions": "date",
    "sp500": "date",
    "fundamentals": "datekey",
}
CHUNK_ROWS = 250_000
SPILL_RECORD_BYTES = 36
MIN_FREE_HEADROOM_BYTES = 512 * 1024 * 1024
MAX_COMPARISON_BYTES = 1024 * 1024


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("Sharadar vintage spill write made no progress")
        offset += written


def _write_chunk(
    directory: Path,
    prefix: str,
    index: int,
    rows: list[tuple[bytes, int]],
) -> Path:
    rows.sort()
    required = len(rows) * SPILL_RECORD_BYTES
    if shutil.disk_usage(directory).free < required + MIN_FREE_HEADROOM_BYTES:
        raise OSError("Sharadar vintage comparison lacks spill-disk headroom")
    path = directory / f"{prefix}-{index:06d}.digests"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = bytearray(required)
        offset = 0
        for digest, ordinal in rows:
            payload[offset : offset + 32] = digest
            payload[offset + 32 : offset + 36] = ordinal.to_bytes(4, "big")
            offset += SPILL_RECORD_BYTES
        _write_all(descriptor, bytes(payload))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _spill_capture_rows(
    root: Path,
    record: Mapping[str, Any],
    table: str,
    directory: Path,
    prefix: str,
) -> tuple[tuple[Path, ...], int, str | None, str | None]:
    date_field = DATE_FIELDS[table]
    chunks: list[Path] = []
    pending: list[tuple[bytes, int]] = []
    row_count = 0
    min_day: str | None = None
    max_day: str | None = None
    for row in _archive_rows(root, record):
        digest = hashlib.sha256(_canonical_json(row)).digest()
        ordinal = 0
        if date_field is not None:
            observed_day = _day(row.get(date_field), f"{table} {date_field}")
            ordinal = date.fromisoformat(observed_day).toordinal()
            min_day = observed_day if min_day is None else min(min_day, observed_day)
            max_day = observed_day if max_day is None else max(max_day, observed_day)
        pending.append((digest, ordinal))
        row_count += 1
        if len(pending) == CHUNK_ROWS:
            chunks.append(
                _write_chunk(directory, prefix, len(chunks), pending)
            )
            pending = []
    if pending:
        chunks.append(_write_chunk(directory, prefix, len(chunks), pending))
    return tuple(chunks), row_count, min_day, max_day


def _chunk_rows(path: Path) -> Iterable[tuple[bytes, int]]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size % SPILL_RECORD_BYTES
        ):
            raise ValueError("Sharadar vintage spill file is unsafe")
        while True:
            payload = os.read(descriptor, SPILL_RECORD_BYTES)
            if not payload:
                return
            if len(payload) != SPILL_RECORD_BYTES:
                raise ValueError("Sharadar vintage spill file is truncated")
            yield payload[:32], int.from_bytes(payload[32:], "big")
    finally:
        os.close(descriptor)


def _merged_rows(paths: tuple[Path, ...]) -> Iterable[tuple[bytes, int]]:
    return heapq.merge(*(_chunk_rows(path) for path in paths))


def _compare_spills(
    baseline_paths: tuple[Path, ...],
    candidate_paths: tuple[Path, ...],
    *,
    baseline_max_day: str | None,
) -> dict[str, Any]:
    baseline = iter(_merged_rows(baseline_paths))
    candidate = iter(_merged_rows(candidate_paths))
    left = next(baseline, None)
    right = next(candidate, None)
    identical = removed = added = 0
    added_historical = added_after = added_undated = 0
    baseline_digest = hashlib.sha256()
    candidate_digest = hashlib.sha256()
    baseline_max_ordinal = (
        date.fromisoformat(baseline_max_day).toordinal()
        if baseline_max_day is not None
        else None
    )
    while left is not None or right is not None:
        if right is None or (left is not None and left < right):
            baseline_digest.update(left[0])
            baseline_digest.update(left[1].to_bytes(4, "big"))
            removed += 1
            left = next(baseline, None)
        elif left is None or right < left:
            candidate_digest.update(right[0])
            candidate_digest.update(right[1].to_bytes(4, "big"))
            added += 1
            if baseline_max_ordinal is None:
                added_undated += 1
            elif right[1] <= baseline_max_ordinal:
                added_historical += 1
            else:
                added_after += 1
            right = next(candidate, None)
        else:
            baseline_digest.update(left[0])
            baseline_digest.update(left[1].to_bytes(4, "big"))
            candidate_digest.update(right[0])
            candidate_digest.update(right[1].to_bytes(4, "big"))
            identical += 1
            left = next(baseline, None)
            right = next(candidate, None)
    return {
        "identical_rows": identical,
        "removed_rows": removed,
        "added_rows": added,
        "added_rows_at_or_before_baseline_max_observed_date": added_historical,
        "added_rows_after_baseline_max_observed_date": added_after,
        "added_undated_rows": added_undated,
        "baseline_canonical_row_multiset_sha256": baseline_digest.hexdigest(),
        "candidate_canonical_row_multiset_sha256": candidate_digest.hexdigest(),
    }


def build_foundation_vintage_comparison(
    repository_root: Path,
    *,
    baseline_observation_hash: str,
    candidate_observation_hash: str,
    synthetic_fixture: bool = False,
    spill_root: Path | None = None,
) -> Mapping[str, Any]:
    """Compare two exact observation-bound foundations without admitting them."""

    if not isinstance(repository_root, Path) or type(synthetic_fixture) is not bool:
        raise TypeError("Sharadar vintage comparison arguments have invalid types")
    observations = load_verified_foundation_observations(repository_root)
    by_hash = {str(item["record_hash"]): item for item in observations}
    baseline_observation = by_hash.get(baseline_observation_hash)
    candidate_observation = by_hash.get(candidate_observation_hash)
    if baseline_observation is None or candidate_observation is None:
        raise ValueError("Sharadar foundation observation is unavailable")
    baseline_completed = datetime.fromisoformat(
        str(baseline_observation["observation_completed_at"])
    )
    candidate_completed = datetime.fromisoformat(
        str(candidate_observation["observation_completed_at"])
    )
    if candidate_completed <= baseline_completed:
        raise ValueError("Sharadar candidate observation must be later than baseline")
    for table in TEN_YEAR_TABLES:
        if (
            candidate_observation["status_retrieved_at"][table]
            <= baseline_observation["status_retrieved_at"][table]
            or candidate_observation["bytes_verified_at"][table]
            <= baseline_observation["bytes_verified_at"][table]
        ):
            raise ValueError(
                "Sharadar candidate must reobserve every foundation table later"
            )
    baseline_records = load_verified_bulk_capture_set(
        repository_root, baseline_observation["capture_record_hashes"]
    )
    candidate_records = load_verified_bulk_capture_set(
        repository_root, candidate_observation["capture_record_hashes"]
    )
    baseline_by_table = {str(record["table"]): record for record in baseline_records}
    candidate_by_table = {str(record["table"]): record for record in candidate_records}
    if any(
        baseline_by_table[table]["csv_header_sha256"]
        != candidate_by_table[table]["csv_header_sha256"]
        for table in TEN_YEAR_TABLES
    ):
        raise ValueError("Sharadar cross-vintage CSV schema changed")

    root = repository_root / QUARANTINE_RELATIVE_PATH
    temporary_parent = spill_root or root
    if not isinstance(temporary_parent, Path):
        raise TypeError("spill_root must be a Path")
    tables: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(
        prefix=".sharadar-vintage-", dir=temporary_parent
    ) as raw_directory:
        directory = Path(raw_directory)
        os.chmod(directory, 0o700)
        for table in TEN_YEAR_TABLES:
            baseline_spill = _spill_capture_rows(
                root,
                baseline_by_table[table],
                table,
                directory,
                f"{table}-baseline",
            )
            same_capture = (
                baseline_by_table[table]["record_hash"]
                == candidate_by_table[table]["record_hash"]
            )
            candidate_spill = (
                baseline_spill
                if same_capture
                else _spill_capture_rows(
                    root,
                    candidate_by_table[table],
                    table,
                    directory,
                    f"{table}-candidate",
                )
            )
            baseline_paths, baseline_rows, baseline_min, baseline_max = baseline_spill
            candidate_paths, candidate_rows, candidate_min, candidate_max = candidate_spill
            if baseline_rows == 0 or candidate_rows == 0:
                raise ValueError(
                    "Sharadar vintage comparison requires nonempty foundation tables"
                )
            comparison = _compare_spills(
                baseline_paths,
                candidate_paths,
                baseline_max_day=baseline_max,
            )
            if (
                comparison["identical_rows"] + comparison["removed_rows"]
                != baseline_rows
                or comparison["identical_rows"] + comparison["added_rows"]
                != candidate_rows
            ):
                raise ValueError("Sharadar vintage row counts did not reconcile")
            tables[table] = {
                "literal_date_field": DATE_FIELDS[table],
                "same_capture_record": same_capture,
                "baseline_rows": baseline_rows,
                "candidate_rows": candidate_rows,
                "baseline_min_observed_date": baseline_min,
                "baseline_max_observed_date": baseline_max,
                "candidate_min_observed_date": candidate_min,
                "candidate_max_observed_date": candidate_max,
                **comparison,
                "row_multiset_changed": bool(
                    comparison["removed_rows"] or comparison["added_rows"]
                ),
                "historical_row_churn_observed": bool(
                    DATE_FIELDS[table] is not None
                    and (
                        comparison["removed_rows"]
                        or comparison[
                            "added_rows_at_or_before_baseline_max_observed_date"
                        ]
                    )
                ),
            }

    historical_churn = sum(
        details["removed_rows"]
        + details["added_rows_at_or_before_baseline_max_observed_date"]
        for table, details in tables.items()
        if DATE_FIELDS[table] is not None
    )
    undated_master_churn = (
        tables["tickers"]["removed_rows"] + tables["tickers"]["added_rows"]
    )
    interval = candidate_completed - baseline_completed
    interval_microseconds = (
        interval.days * 86_400_000_000
        + interval.seconds * 1_000_000
        + interval.microseconds
    )
    material: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "record_type": "SHARADAR_FOUNDATION_VINTAGE_COMPARISON",
        "status": STATUS,
        "baseline_observation_hash": baseline_observation_hash,
        "candidate_observation_hash": candidate_observation_hash,
        "baseline_observation_completed_at": baseline_observation[
            "observation_completed_at"
        ],
        "candidate_observation_completed_at": candidate_observation[
            "observation_completed_at"
        ],
        "observation_interval_microseconds": interval_microseconds,
        "every_table_reobserved_later": True,
        "sha256_canonical_row_multisets_compared": True,
        "tables": tables,
        "historical_row_churn_count": historical_churn,
        "historical_row_churn_count_basis": (
            "REMOVED_PLUS_ADDED_AT_OR_BEFORE_BASELINE_MAX_LITERAL_DATE"
        ),
        "historical_row_churn_observed": historical_churn > 0,
        "undated_ticker_master_churn_count": undated_master_churn,
        "undated_ticker_master_churn_count_basis": (
            "REMOVED_PLUS_ADDED_FULL_ROW_MULTISET_DELTAS"
        ),
        "absence_of_observed_churn_is_not_availability_qualification": True,
        "license_restricted": True,
        "owner_local_comparison": True,
        "synthetic_fixture": synthetic_fixture,
        **{name: False for name in FALSE_AUTHORITIES},
    }
    comparison_sha256 = hashlib.sha256(_canonical_json(material)).hexdigest()
    return {**material, "comparison_sha256": comparison_sha256}


def persist_foundation_vintage_comparison(
    repository_root: Path,
    *,
    baseline_observation_hash: str,
    candidate_observation_hash: str,
) -> Mapping[str, Any]:
    """Persist one content-addressed owner-local aggregate comparison."""

    comparison = build_foundation_vintage_comparison(
        repository_root,
        baseline_observation_hash=baseline_observation_hash,
        candidate_observation_hash=candidate_observation_hash,
    )
    root = repository_root / QUARANTINE_RELATIVE_PATH
    target = root / f"foundation-vintage-{comparison['comparison_sha256']}.json"
    payload = _canonical_json(comparison) + b"\n"
    if len(payload) > MAX_COMPARISON_BYTES:
        raise ValueError("Sharadar vintage comparison exceeds its size boundary")
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        descriptor = None
    if descriptor is not None:
        try:
            details = os.fstat(descriptor)
            existing = os.read(descriptor, MAX_COMPARISON_BYTES + 1)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o400
            or details.st_nlink != 1
            or existing != payload
        ):
            raise ValueError("Sharadar vintage comparison failed verification")
        return comparison
    descriptor, raw_path = tempfile.mkstemp(prefix=".foundation-vintage-", dir=root)
    partial = Path(raw_path)
    try:
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(partial, 0o400)
        try:
            os.link(partial, target, follow_symlinks=False)
        except FileExistsError:
            raise ValueError("Sharadar vintage comparison already exists") from None
        partial.unlink()
        directory = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if partial.exists():
            partial.unlink()
    return comparison
