from __future__ import annotations

"""Offline structural profiling for the frozen Sharadar foundation.

The profiler rereads every licensed CSV row from owner-local quarantine and
emits only deterministic aggregate metadata.  It proves byte integrity, row
shape, bounded parsing, lexical dates, and basic numerical invariants.  It does
not turn a current-vintage bulk snapshot into historical availability proof,
qualify provider semantics, admit a partition, open VALIDATION/TEST, or
authorize performance, promotion, brokerage, or trading.
"""

from collections import Counter, defaultdict
import csv
from datetime import date, datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable, Mapping
import zipfile

from core.orchestration.sharadar_quarantine import (
    QUARANTINE_RELATIVE_PATH,
    load_verified_bulk_captures,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "sharadar-foundation-structural-profile-v1"
STATUS = "STRUCTURE_VERIFIED_SEMANTICS_AND_AVAILABILITY_UNQUALIFIED"
MAX_ROWS_PER_TABLE = 100_000_000
MAX_CELL_CHARACTERS = 1_000_000
MAX_PROFILE_BYTES = 1024 * 1024
ALLOWED_FUNDAMENTAL_DIMENSIONS = frozenset(
    {"ARQ", "ARY", "ART", "MRQ", "MRY", "MRT"}
)
TRADABLE_MASTER_TABLES = ("SEP", "SF1", "SFP")
IDENTITY_MISSING = "MISSING"
IDENTITY_UNIQUE = "UNIQUE"
IDENTITY_AMBIGUOUS = "AMBIGUOUS"
COUNTERPARTY_NOT_PROVIDED = "NOT_PROVIDED"
FALSE_AUTHORITIES = (
    "primary_key_uniqueness_proven",
    "cross_table_identity_complete",
    "provider_payload_semantics_qualified",
    "historical_availability_qualified",
    "coverage_completeness_proven",
    "point_in_time_semantics_qualified",
    "security_master_admitted",
    "corporate_actions_admitted",
    "daily_bars_admitted",
    "fundamentals_admitted",
    "dataset_admitted",
    "train_admitted",
    "validation_admitted",
    "validation_access_authorized",
    "test_admitted",
    "test_access_authorized",
    "performance_claim_allowed",
    "candidate_freeze_allowed",
    "promotion_allowed",
    "broker_submission_enabled",
    "live_trading_enabled",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _day(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"Sharadar {name} must be an ISO date")
    try:
        resolved = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Sharadar {name} must be an ISO date") from error
    if resolved.isoformat() != value:
        raise ValueError(f"Sharadar {name} must be an ISO date")
    return value


def _text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > MAX_CELL_CHARACTERS
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ValueError(f"Sharadar {name} must be bounded nonempty text")
    return value


def _update_range(profile: dict[str, Any], name: str, value: str) -> None:
    resolved = _day(value, name)
    low = f"min_{name}"
    high = f"max_{name}"
    profile[low] = resolved if low not in profile else min(profile[low], resolved)
    profile[high] = resolved if high not in profile else max(profile[high], resolved)


def _identity_state(
    master: Mapping[tuple[str, str], set[str]],
    ticker: str,
    tables: Iterable[str],
) -> str:
    """Classify a ticker join without guessing across permanent identities."""

    identities = {
        permaticker
        for table in tables
        for permaticker in master.get((table, ticker), ())
    }
    if not identities:
        return IDENTITY_MISSING
    if len(identities) == 1:
        return IDENTITY_UNIQUE
    return IDENTITY_AMBIGUOUS


def _archive_rows(
    root: Path,
    record: Mapping[str, Any],
) -> Iterable[dict[str, str]]:
    archive_path = root / str(record["blob_relative_path"])
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != record["archive_member"]:
                raise ValueError("Sharadar foundation archive member changed")
            with archive.open(members[0]) as raw:
                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                    reader = csv.DictReader(text, strict=True)
                    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                        raise ValueError("Sharadar foundation CSV header is invalid")
                    for row_number, row in enumerate(reader, start=1):
                        if row_number > MAX_ROWS_PER_TABLE:
                            raise ValueError("Sharadar foundation table exceeds its row boundary")
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("Sharadar foundation CSV row shape is invalid")
                        yield row
    except (OSError, UnicodeDecodeError, csv.Error, zipfile.BadZipFile) as error:
        raise ValueError("Sharadar foundation archive could not be profiled") from error


def _profile_tickers(
    root: Path,
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[tuple[str, str], set[str]]]:
    profile: dict[str, Any] = {"row_count": 0}
    table_counts: Counter[str] = Counter()
    master: dict[tuple[str, str], set[str]] = defaultdict(set)
    identities: set[tuple[str, str, str]] = set()
    delisted = 0
    for row in _archive_rows(root, record):
        profile["row_count"] += 1
        table = _text(row.get("table"), "ticker table")
        ticker = _text(row.get("ticker"), "ticker")
        permaticker = _text(row.get("permaticker"), "permaticker")
        identity = (table, permaticker, ticker)
        if identity in identities:
            raise ValueError("Sharadar tickers contain a duplicate primary key")
        identities.add(identity)
        table_counts[table] += 1
        master[(table, ticker)].add(permaticker)
        isdelisted = row.get("isdelisted")
        if table in {"SEP", "SF1", "SFP"} and isdelisted not in {"Y", "N"}:
            raise ValueError("Sharadar tradable ticker has invalid delisting state")
        delisted += isdelisted == "Y"
        for field in ("firstpricedate", "lastpricedate", "lastupdated"):
            value = row.get(field)
            if value:
                _update_range(profile, field, value)
    profile.update(
        {
            "table_counts": dict(sorted(table_counts.items())),
            "unique_table_tickers": len(master),
            "delisted_rows": delisted,
            "ticker_reuse_groups": sum(len(values) > 1 for values in master.values()),
        }
    )
    return profile, master


def _profile_stocks(
    root: Path,
    record: Mapping[str, Any],
    master: Mapping[tuple[str, str], set[str]],
) -> dict[str, Any]:
    profile: dict[str, Any] = {"row_count": 0}
    tickers: set[str] = set()
    ticker_identity_states: dict[str, str] = {}
    rows_by_identity_state: Counter[str] = Counter()
    for row in _archive_rows(root, record):
        profile["row_count"] += 1
        ticker = _text(row.get("ticker"), "stock ticker")
        tickers.add(ticker)
        identity_state = ticker_identity_states.get(ticker)
        if identity_state is None:
            identity_state = _identity_state(master, ticker, ("SEP",))
            ticker_identity_states[ticker] = identity_state
        rows_by_identity_state[identity_state] += 1
        _update_range(profile, "date", row.get("date"))
        _update_range(profile, "lastupdated", row.get("lastupdated"))
        try:
            open_, high, low, close = (
                float(row[name]) for name in ("open", "high", "low", "close")
            )
            volume = float(row["volume"])
            closeadj = float(row["closeadj"])
            closeunadj = float(row["closeunadj"])
        except (TypeError, ValueError) as error:
            raise ValueError("Sharadar stock row contains invalid numerics") from error
        if not all(
            math.isfinite(value)
            for value in (open_, high, low, close, volume, closeadj, closeunadj)
        ):
            raise ValueError("Sharadar stock row contains invalid numerics")
        if (
            min(open_, high, low, close, closeadj, closeunadj) <= 0
            or volume < 0
            or high < max(open_, close)
            or low > min(open_, close)
        ):
            raise ValueError("Sharadar stock row violates OHLCV invariants")
    identity_state_counts = Counter(ticker_identity_states.values())
    profile.update(
        {
            "unique_tickers": len(tickers),
            "identity_state_counts": dict(sorted(identity_state_counts.items())),
            "rows_by_identity_state": dict(sorted(rows_by_identity_state.items())),
            "tickers_missing_sep_identity": identity_state_counts[IDENTITY_MISSING],
            "tickers_ambiguous_sep_identity": identity_state_counts[
                IDENTITY_AMBIGUOUS
            ],
            "structural_identity_join_ready": (
                identity_state_counts[IDENTITY_MISSING] == 0
                and identity_state_counts[IDENTITY_AMBIGUOUS] == 0
            ),
        }
    )
    return profile


def _profile_event_table(
    root: Path,
    record: Mapping[str, Any],
    master: Mapping[tuple[str, str], set[str]],
) -> dict[str, Any]:
    profile: dict[str, Any] = {"row_count": 0}
    actions: Counter[str] = Counter()
    tickers: set[str] = set()
    ticker_identity_states: dict[str, str] = {}
    rows_by_primary_identity_state: Counter[str] = Counter()
    unresolved_primary_actions: Counter[str] = Counter()
    unresolved_primary_counterparty_states: Counter[str] = Counter()
    capture_day = datetime.fromisoformat(str(record["retrieved_at"])).date().isoformat()
    future_effective_rows = 0
    for row in _archive_rows(root, record):
        profile["row_count"] += 1
        ticker = _text(row.get("ticker"), "event ticker")
        action = _text(row.get("action"), "event action")
        event_day = _day(row.get("date"), "event date")
        contra_value = row.get("contraticker", "")
        contra = _text(contra_value, "event contra ticker") if contra_value else ""
        tickers.add(ticker)
        actions[action] += 1
        primary_state = ticker_identity_states.get(ticker)
        if primary_state is None:
            primary_state = _identity_state(master, ticker, TRADABLE_MASTER_TABLES)
            ticker_identity_states[ticker] = primary_state
        rows_by_primary_identity_state[primary_state] += 1
        if primary_state != IDENTITY_UNIQUE:
            unresolved_primary_actions[action] += 1
            counterparty_state = (
                _identity_state(master, contra, TRADABLE_MASTER_TABLES)
                if contra
                else COUNTERPARTY_NOT_PROVIDED
            )
            unresolved_primary_counterparty_states[counterparty_state] += 1
        _update_range(profile, "date", event_day)
        future_effective_rows += event_day > capture_day
    identity_state_counts = Counter(ticker_identity_states.values())
    profile.update(
        {
            "unique_tickers": len(tickers),
            "action_counts": dict(sorted(actions.items())),
            "future_effective_rows_at_capture": future_effective_rows,
            "primary_identity_state_counts": dict(sorted(identity_state_counts.items())),
            "rows_by_primary_identity_state": dict(
                sorted(rows_by_primary_identity_state.items())
            ),
            "unresolved_primary_action_counts": dict(
                sorted(unresolved_primary_actions.items())
            ),
            "unresolved_primary_counterparty_state_counts": dict(
                sorted(unresolved_primary_counterparty_states.items())
            ),
            "tickers_missing_any_tradable_identity": identity_state_counts[
                IDENTITY_MISSING
            ],
            "tickers_ambiguous_any_tradable_identity": identity_state_counts[
                IDENTITY_AMBIGUOUS
            ],
            "structural_identity_join_ready": (
                identity_state_counts[IDENTITY_MISSING] == 0
                and identity_state_counts[IDENTITY_AMBIGUOUS] == 0
            ),
        }
    )
    return profile


def _profile_fundamentals(
    root: Path,
    record: Mapping[str, Any],
    master: Mapping[tuple[str, str], set[str]],
) -> dict[str, Any]:
    profile: dict[str, Any] = {"row_count": 0}
    dimensions: Counter[str] = Counter()
    tickers: set[str] = set()
    ticker_identity_states: dict[str, str] = {}
    rows_by_identity_state: Counter[str] = Counter()
    as_reported_rows = 0
    for row in _archive_rows(root, record):
        profile["row_count"] += 1
        ticker = _text(row.get("ticker"), "fundamental ticker")
        dimension = _text(row.get("dimension"), "fundamental dimension")
        if dimension not in ALLOWED_FUNDAMENTAL_DIMENSIONS:
            raise ValueError("Sharadar fundamental dimension is unsupported")
        tickers.add(ticker)
        identity_state = ticker_identity_states.get(ticker)
        if identity_state is None:
            identity_state = _identity_state(master, ticker, ("SF1",))
            ticker_identity_states[ticker] = identity_state
        rows_by_identity_state[identity_state] += 1
        dimensions[dimension] += 1
        for field in ("datekey", "calendardate", "reportperiod", "lastupdated"):
            _update_range(profile, field, row.get(field))
        if dimension.startswith("AR"):
            as_reported_rows += 1
            if row["datekey"] < row["reportperiod"]:
                raise ValueError("Sharadar as-reported row predates its report period")
    identity_state_counts = Counter(ticker_identity_states.values())
    profile.update(
        {
            "unique_tickers": len(tickers),
            "dimension_counts": dict(sorted(dimensions.items())),
            "as_reported_rows": as_reported_rows,
            "identity_state_counts": dict(sorted(identity_state_counts.items())),
            "rows_by_identity_state": dict(sorted(rows_by_identity_state.items())),
            "tickers_missing_sf1_identity": identity_state_counts[IDENTITY_MISSING],
            "tickers_ambiguous_sf1_identity": identity_state_counts[
                IDENTITY_AMBIGUOUS
            ],
            "structural_identity_join_ready": (
                identity_state_counts[IDENTITY_MISSING] == 0
                and identity_state_counts[IDENTITY_AMBIGUOUS] == 0
            ),
        }
    )
    return profile


def build_foundation_profile(
    repository_root: Path,
    *,
    synthetic_fixture: bool = False,
) -> Mapping[str, Any]:
    """Build deterministic aggregate evidence without admitting provider rows."""

    if type(synthetic_fixture) is not bool:
        raise TypeError("synthetic_fixture must be a bool")
    records = load_verified_bulk_captures(repository_root)
    by_table = {str(record["table"]): record for record in records}
    root = repository_root / QUARANTINE_RELATIVE_PATH
    tickers, master = _profile_tickers(root, by_table["tickers"])
    tables = {
        "tickers": tickers,
        "stocks": _profile_stocks(root, by_table["stocks"], master),
        "actions": _profile_event_table(root, by_table["actions"], master),
        "sp500": _profile_event_table(root, by_table["sp500"], master),
        "fundamentals": _profile_fundamentals(root, by_table["fundamentals"], master),
    }
    stocks = tables["stocks"]
    structural_identity_missing = (
        stocks["tickers_missing_sep_identity"]
        + tables["actions"]["tickers_missing_any_tradable_identity"]
        + tables["sp500"]["tickers_missing_any_tradable_identity"]
        + tables["fundamentals"]["tickers_missing_sf1_identity"]
    )
    structural_identity_ambiguous = (
        stocks["tickers_ambiguous_sep_identity"]
        + tables["actions"]["tickers_ambiguous_any_tradable_identity"]
        + tables["sp500"]["tickers_ambiguous_any_tradable_identity"]
        + tables["fundamentals"]["tickers_ambiguous_sf1_identity"]
    )
    structural_identity_gaps = (
        structural_identity_missing + structural_identity_ambiguous
    )
    material: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "record_type": "SHARADAR_FOUNDATION_STRUCTURAL_PROFILE",
        "status": STATUS,
        "capture_record_hashes": {
            table: str(by_table[table]["record_hash"]) for table in sorted(by_table)
        },
        "capture_payload_sha256": {
            table: str(by_table[table]["payload_sha256"]) for table in sorted(by_table)
        },
        "tables": tables,
        "archive_integrity_verified": True,
        "every_row_stream_parsed": True,
        "row_shape_verified": True,
        "lexical_date_validity_verified": True,
        "basic_ohlcv_invariants_verified": True,
        "structural_identity_missing_count": structural_identity_missing,
        "structural_identity_ambiguous_count": structural_identity_ambiguous,
        "structural_identity_gap_count": structural_identity_gaps,
        "structural_identity_gap_count_basis": (
            "SUM_OF_DEPENDENT_TABLE_UNIQUE_TICKER_REFERENCES"
        ),
        "structural_identity_join_ready": structural_identity_gaps == 0,
        "observed_stock_date_span_days": (
            date.fromisoformat(stocks["max_date"])
            - date.fromisoformat(stocks["min_date"])
        ).days,
        "license_restricted": True,
        "owner_local_profile": True,
        "synthetic_fixture": synthetic_fixture,
        **{name: False for name in FALSE_AUTHORITIES},
    }
    profile_sha256 = hashlib.sha256(_canonical_json(material)).hexdigest()
    return {**material, "profile_sha256": profile_sha256}


def persist_foundation_profile(repository_root: Path) -> Mapping[str, Any]:
    """Persist one content-addressed owner-local aggregate profile."""

    profile = build_foundation_profile(repository_root, synthetic_fixture=False)
    root = repository_root / QUARANTINE_RELATIVE_PATH
    target = root / f"foundation-profile-{profile['profile_sha256']}.json"
    payload = _canonical_json(profile) + b"\n"
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        descriptor = None
    if descriptor is not None:
        details = os.fstat(descriptor)
        existing = b""
        try:
            if details.st_size > MAX_PROFILE_BYTES:
                raise ValueError("Sharadar foundation profile exceeds its size boundary")
            chunks = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PROFILE_BYTES:
                    raise ValueError("Sharadar foundation profile exceeds its size boundary")
                chunks.append(chunk)
            existing = b"".join(chunks)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o400
            or details.st_nlink != 1
            or existing != payload
        ):
            raise ValueError("Sharadar foundation profile failed verification")
        return profile
    descriptor, raw_path = tempfile.mkstemp(prefix=".foundation-profile-", dir=root)
    partial = Path(raw_path)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("Sharadar foundation profile write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(partial, 0o400)
        try:
            os.link(partial, target, follow_symlinks=False)
        except FileExistsError:
            raise ValueError("Sharadar foundation profile already exists") from None
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
    return profile
