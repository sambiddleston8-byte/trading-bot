from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import core.orchestration.pit_daily_bars as module
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.pit_daily_bars import (
    PITDailyBarLedger,
    PITDailyBarReconciliation,
    PITDailyBarResearchInputs,
)
from core.orchestration.pit_session_partitions import PITSessionPartitionLedger
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


NY = ZoneInfo("America/New_York")
CLOCK = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
RETRIEVED = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)


def weekdays(start: str, count: int) -> list[date]:
    cursor = date.fromisoformat(start)
    result = []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def session(day: date, *, kind: str = "REGULAR") -> dict:
    close = time(13) if kind == "EARLY_CLOSE" else time(16)
    open_at = datetime.combine(day, time(9, 30), NY).astimezone(timezone.utc)
    close_at = datetime.combine(day, close, NY).astimezone(timezone.utc)
    known = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {
        "session_date": day.isoformat(),
        "session_type": kind,
        "open_at": open_at.isoformat(),
        "close_at": close_at.isoformat(),
        "effective_at": open_at.isoformat(),
        "reported_at": known.isoformat(),
        "available_at": known.isoformat(),
        "retrieved_at": known.isoformat(),
        "recorded_at": known.isoformat(),
    }


def append_calendar(
    ledger: PITSessionPartitionLedger,
    days: list[date],
    *,
    sessions: list[dict] | None = None,
    marker: str = "a",
    **changes,
) -> dict:
    arguments = {
        "sessions": sessions or [session(day) for day in days],
        "source_uri": "https://calendar.example.invalid/xnys",
        "source_locator": f"synthetic-calendar-{marker}",
        "source_payload_sha256": marker * 64,
        "synthetic_fixture": True,
    }
    arguments.update(changes)
    return ledger.append_calendar_snapshot(**arguments)


def append_manifest(
    ledger: PITSessionPartitionLedger,
    calendar: dict,
    days: list[date],
) -> dict:
    return ledger.append_partition_manifest(
        calendar_snapshot_id=calendar["calendar_snapshot_id"],
        train_start=days[0].isoformat(),
        train_end=days[7].isoformat(),
        validation_start=days[11].isoformat(),
        validation_end=days[20].isoformat(),
        test_start=days[24].isoformat(),
        test_end=days[30].isoformat(),
        longest_label_horizon_decision_periods=2,
        embargo_decision_periods=1,
    )


def master_event(
    master: PointInTimeSecurityMasterLedger,
    *,
    security_id: str,
    ticker: str,
    marker: str,
    event_type: str = "LISTED",
    effective_at: str = "2020-01-02T14:30:00+00:00",
    **changes,
) -> dict:
    arguments = {
        "security_id": security_id,
        "event_type": event_type,
        "ticker": ticker,
        "issuer_name": f"{ticker} synthetic issuer",
        "exchange_mic": "XNYS",
        "effective_at": effective_at,
        "reported_at": "2020-01-01T10:00:00+00:00",
        "available_at": "2020-01-01T11:00:00+00:00",
        "retrieved_at": "2020-01-01T12:00:00+00:00",
        "recorded_at": "2020-01-01T13:00:00+00:00",
        "source_uri": f"https://master.example.invalid/{security_id}",
        "source_input_sha256": marker * 64,
        "source_locator": f"$.{security_id}.{event_type}",
    }
    arguments.update(changes)
    return master.record_event(**arguments)


def environment(tmp_path: Path):
    days = weekdays("2025-01-02", 35)
    calendar_ledger = PITSessionPartitionLedger(
        tmp_path / "calendar.jsonl",
        clock=lambda: CLOCK,
    )
    calendar = append_calendar(calendar_ledger, days)
    manifest = append_manifest(calendar_ledger, calendar, days)
    master = PointInTimeSecurityMasterLedger(tmp_path / "master.jsonl")
    master_event(master, security_id="SEC-AAPL-001", ticker="AAPL", marker="b")
    master_event(master, security_id="SEC-MSFT-001", ticker="MSFT", marker="c")
    bars = PITDailyBarLedger(
        tmp_path / "bars.jsonl",
        calendar_ledger=calendar_ledger,
        security_master_ledger=master,
        clock=lambda: CLOCK,
    )
    return days, calendar_ledger, calendar, manifest, master, bars


def raw_bars(calendar: dict, days: list[date], *, security_ids=None) -> list[dict]:
    identities = security_ids or (
        ("SEC-AAPL-001", "AAPL"),
        ("SEC-MSFT-001", "MSFT"),
    )
    sessions = {item["session_date"]: item for item in calendar["sessions"]}
    rows = []
    for day_index, day in enumerate(days):
        current = sessions[day.isoformat()]
        close_at = datetime.fromisoformat(current["close_at"])
        for security_index, (security_id, ticker) in enumerate(identities):
            base = 100 + day_index * 2 + security_index
            rows.append(
                {
                    "security_id": security_id,
                    "ticker": ticker,
                    "session_date": day.isoformat(),
                    "open_at": current["open_at"],
                    "close_at": current["close_at"],
                    "effective_at": current["close_at"],
                    "reported_at": current["close_at"],
                    "available_at": (close_at + timedelta(minutes=1)).isoformat(),
                    "retrieved_at": RETRIEVED.isoformat(),
                    "open": str(base),
                    "high": str(base + 3),
                    "low": str(base - 2),
                    "close": str(base + 1),
                    "volume": str(1_000_000 + day_index),
                    "source_payload_sha256": hashlib.sha256(
                        f"{security_id}:{day.isoformat()}".encode()
                    ).hexdigest(),
                    "source_row_locator": f"$.bars[{day_index}].{ticker}",
                }
            )
    return rows


def append_bars(
    ledger: PITDailyBarLedger,
    calendar: dict,
    manifest: dict,
    rows: list[dict],
    *,
    marker: str = "d",
    **changes,
) -> dict:
    arguments = {
        "calendar_snapshot_id": calendar["calendar_snapshot_id"],
        "partition_manifest_id": manifest["partition_manifest_id"],
        "bars": rows,
        "source_uri": "https://bars.example.invalid/synthetic-bundle",
        "source_locator": f"synthetic-daily-bars-{marker}",
        "source_payload_sha256": marker * 64,
        "synthetic_fixture": True,
    }
    arguments.update(changes)
    return ledger.append_snapshot(**arguments)


def test_snapshot_is_deterministic_pit_aligned_and_research_only(tmp_path):
    first = environment(tmp_path / "first")
    second = environment(tmp_path / "second")
    days = first[0]
    first_rows = raw_bars(first[2], first[0][:8])
    second_rows = list(reversed(raw_bars(second[2], second[0][:8])))
    left = append_bars(first[5], first[2], first[3], first_rows)
    right = append_bars(second[5], second[2], second[3], second_rows)

    assert left["bar_snapshot_id"] == right["bar_snapshot_id"]
    assert left["record_hash"] == right["record_hash"]
    assert left["calendar_snapshot_record_hash"] == first[2]["record_hash"]
    assert left["partition_manifest_record_hash"] == first[3]["record_hash"]
    assert left["partition_role"] == "TRAIN"
    assert left["security_ids"] == ["SEC-AAPL-001", "SEC-MSFT-001"]
    assert left["session_count"] == 8
    assert left["row_count"] == 16
    assert left["permanent_identity_used"] is True
    assert left["cross_sectionally_aligned"] is True
    assert left["coverage_shape"] == "STRICT_RECTANGLE_CONSTANT_MEMBERSHIP"
    assert left["engine_symbol_policy"] == "TICKER"
    assert left["coverage_intervals"] == [
        {
            "security_id": "SEC-AAPL-001",
            "coverage_start": days[0].isoformat(),
            "coverage_end": days[7].isoformat(),
            "session_count": 8,
            "ticker_segments": [{
                "ticker": "AAPL",
                "coverage_start": days[0].isoformat(),
                "coverage_end": days[7].isoformat(),
            }],
        },
        {
            "security_id": "SEC-MSFT-001",
            "coverage_start": days[0].isoformat(),
            "coverage_end": days[7].isoformat(),
            "session_count": 8,
            "ticker_segments": [{
                "ticker": "MSFT",
                "coverage_start": days[0].isoformat(),
                "coverage_end": days[7].isoformat(),
            }],
        },
    ]
    assert left["point_in_time_contract"] == (
        "effective_at/reported_at/available_at/retrieved_at/recorded_at"
    )
    assert left["synthetic_fixture"] is True
    assert all(
        left[name] is False
        for name in (
            "coverage_completeness_proven",
            "qualified",
            "train_admitted",
            "validation_admitted",
            "test_admitted",
            "engine_input_ready",
            "performance_claim_allowed",
            "promotion_allowed",
        )
    )

    materialized = first[5].materialize_research_inputs(left["bar_snapshot_id"])
    assert isinstance(materialized, PITDailyBarResearchInputs)
    assert len(materialized.bars) == 16
    assert [item.symbol for item in materialized.bars[:2]] == ["AAPL", "MSFT"]
    assert materialized.security_ids[:2] == ("SEC-AAPL-001", "SEC-MSFT-001")
    assert materialized.tickers[:2] == ("AAPL", "MSFT")
    assert materialized.coverage_shape == "STRICT_RECTANGLE_CONSTANT_MEMBERSHIP"
    assert materialized.engine_symbol_policy == "TICKER"
    assert materialized.universe_events == ()
    assert materialized.partition_role == "TRAIN"
    assert materialized.dataset_admitted is False
    assert materialized.performance_claim_allowed is False
    assert materialized.promotion_allowed is False


def test_identical_retry_is_idempotent_across_append_times(tmp_path):
    days, calendars, calendar, manifest, master, ledger = environment(tmp_path)
    rows = raw_bars(calendar, days[:8])
    first = append_bars(ledger, calendar, manifest, rows)
    retry = PITDailyBarLedger(
        ledger.path,
        calendar_ledger=calendars,
        security_master_ledger=master,
        clock=lambda: CLOCK + timedelta(hours=1),
    )
    second = append_bars(retry, calendar, manifest, rows)
    assert second == first
    assert retry.verify() == [first]
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        append_bars(
            retry,
            calendar,
            manifest,
            rows,
            allow_existing=False,
        )


def test_source_correction_preserves_history_and_only_leaf_materializes(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    rows = raw_bars(calendar, days[:8])
    original = append_bars(ledger, calendar, manifest, rows)
    corrected_rows = raw_bars(calendar, days[:8])
    corrected_rows[0]["close"] = "102"
    corrected_rows[0]["source_payload_sha256"] = "e" * 64
    corrected = append_bars(
        ledger,
        calendar,
        manifest,
        corrected_rows,
        marker="e",
        supersedes_bar_snapshot_id=original["bar_snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )

    records = ledger.verify()
    assert records[0]["record_hash"] == original["record_hash"]
    assert corrected["bars"][0]["close"] == "102"
    status = ledger.reconcile_snapshot(original["bar_snapshot_id"])
    assert isinstance(status, PITDailyBarReconciliation)
    assert status.status == "SUPERSEDED"
    assert status.superseded_by_bar_snapshot_id == corrected["bar_snapshot_id"]
    assert status.reason_code == "SOURCE_CORRECTION"
    assert ledger.reconcile_snapshot(corrected["bar_snapshot_id"]).status == "CURRENT"
    with pytest.raises(ValueError, match="superseded.*cannot materialize"):
        ledger.materialize_research_inputs(original["bar_snapshot_id"])
    assert ledger.materialize_research_inputs(corrected["bar_snapshot_id"]).bars[0].close == 102


def test_calendar_correction_invalidates_old_consumption_and_has_recovery_path(tmp_path):
    days, calendars, calendar, manifest, _, ledger = environment(tmp_path)
    original = append_bars(ledger, calendar, manifest, raw_bars(calendar, days[:8]))
    corrected_sessions = [
        session(day, kind="EARLY_CLOSE" if index == 15 else "REGULAR")
        for index, day in enumerate(days)
    ]
    replacement_calendar = append_calendar(
        calendars,
        days,
        sessions=corrected_sessions,
        marker="e",
        supersedes_calendar_snapshot_id=calendar["calendar_snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )
    replacement_manifest = append_manifest(calendars, replacement_calendar, days)

    assert ledger.verify() == [original]
    with pytest.raises(ValueError, match="superseded PIT calendar"):
        ledger.materialize_research_inputs(original["bar_snapshot_id"])
    replacement = append_bars(
        ledger,
        replacement_calendar,
        replacement_manifest,
        raw_bars(replacement_calendar, days[:8]),
        marker="f",
        supersedes_bar_snapshot_id=original["bar_snapshot_id"],
        supersession_reason="CALENDAR_CORRECTION",
    )
    assert ledger.reconcile_snapshot(original["bar_snapshot_id"]).status == "SUPERSEDED"
    assert len(ledger.materialize_research_inputs(replacement["bar_snapshot_id"]).bars) == 16


def test_ambiguous_unknown_mismatched_and_forked_chains_fail_closed(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    rows = raw_bars(calendar, days[:8])
    original = append_bars(ledger, calendar, manifest, rows)
    with pytest.raises(LedgerIntegrityError, match="overlapping.*ambiguous"):
        append_bars(ledger, calendar, manifest, rows, marker="e")
    with pytest.raises(ValueError, match="id and reason.*together"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            marker="e",
            supersedes_bar_snapshot_id=original["bar_snapshot_id"],
        )
    with pytest.raises(ValueError, match="unsupported"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            marker="e",
            supersedes_bar_snapshot_id=original["bar_snapshot_id"],
            supersession_reason="OPTIMIZE_RESULT",
        )
    with pytest.raises(LedgerIntegrityError, match="target does not exist"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            marker="e",
            supersedes_bar_snapshot_id="PBAR-UNKNOWN",
            supersession_reason="SOURCE_CORRECTION",
        )
    with pytest.raises(LedgerIntegrityError, match="preserve calendar and coverage"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows[:-2],
            marker="e",
            supersedes_bar_snapshot_id=original["bar_snapshot_id"],
            supersession_reason="SOURCE_CORRECTION",
        )
    with pytest.raises(LedgerIntegrityError, match="descendant calendar"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            marker="e",
            supersedes_bar_snapshot_id=original["bar_snapshot_id"],
            supersession_reason="CALENDAR_CORRECTION",
        )

    replacement = append_bars(
        ledger,
        calendar,
        manifest,
        rows,
        marker="e",
        supersedes_bar_snapshot_id=original["bar_snapshot_id"],
        supersession_reason="COVERAGE_RECAPTURE",
    )
    with pytest.raises(LedgerIntegrityError, match="cannot fork"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            marker="f",
            supersedes_bar_snapshot_id=original["bar_snapshot_id"],
            supersession_reason="SOURCE_CORRECTION",
        )
    leaf = append_bars(
        ledger,
        calendar,
        manifest,
        rows,
        marker="0",
        supersedes_bar_snapshot_id=replacement["bar_snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )
    assert ledger.reconcile_snapshot(replacement["bar_snapshot_id"]).status == "SUPERSEDED"
    assert ledger.reconcile_snapshot(leaf["bar_snapshot_id"]).status == "CURRENT"


def test_nonoverlapping_train_windows_may_have_independent_roots(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    first = append_bars(ledger, calendar, manifest, raw_bars(calendar, days[:4]))
    second = append_bars(
        ledger,
        calendar,
        manifest,
        raw_bars(calendar, days[4:8]),
        marker="e",
    )
    assert first["coverage_end"] < second["coverage_start"]
    assert ledger.reconcile_snapshot(first["bar_snapshot_id"]).status == "CURRENT"
    assert ledger.reconcile_snapshot(second["bar_snapshot_id"]).status == "CURRENT"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.pop(3), "cross-sectionally aligned"),
        (lambda rows: rows.append(dict(rows[0])), "duplicate security/session"),
        (
            lambda rows: rows.__setitem__(
                0,
                {**rows[0], "ticker": "MSFT"},
            ),
            "permanent security identity",
        ),
        (
            lambda rows: rows.__setitem__(0, {**rows[0], "high": "99"}),
            "OHLC values are inconsistent",
        ),
        (
            lambda rows: rows.__setitem__(
                0,
                {**rows[0], "available_at": "2099-01-01T00:00:00+00:00"},
            ),
            "PIT order",
        ),
    ],
)
def test_alignment_identity_ohlc_and_pit_fail_closed(tmp_path, mutate, message):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    rows = raw_bars(calendar, days[:8])
    mutate(rows)
    with pytest.raises(ValueError, match=message):
        append_bars(ledger, calendar, manifest, rows)


def test_ticker_change_inside_snapshot_cannot_splice_engine_symbols(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    change_day = days[4]
    change_at = next(
        item["open_at"]
        for item in calendar["sessions"]
        if item["session_date"] == change_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-AAPL-001",
        ticker="APPL",
        marker="e",
        event_type="TICKER_CHANGED",
        effective_at=change_at,
        prior_ticker="AAPL",
        issuer_name="AAPL synthetic issuer",
    )
    rows = raw_bars(calendar, days[:8])
    for row in rows:
        if row["security_id"] == "SEC-AAPL-001" and row["session_date"] >= change_day.isoformat():
            row["ticker"] = "APPL"

    with pytest.raises(ValueError, match="mapping is not bijective"):
        append_bars(ledger, calendar, manifest, rows)


def test_simultaneous_ticker_swap_shape_is_rejected_at_bar_boundary(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    change_day = days[4]
    change_at = next(
        item["open_at"]
        for item in calendar["sessions"]
        if item["session_date"] == change_day.isoformat()
    )
    base = master.verify()
    changes = []
    for record, ticker, prior in (
        (base[0], "MSFT", "AAPL"),
        (base[1], "AAPL", "MSFT"),
    ):
        changes.append(
            {
                **record,
                "event_type": "TICKER_CHANGED",
                "ticker": ticker,
                "prior_ticker": prior,
                "effective_at": change_at,
                "available_at": "2024-01-01T11:00:00+00:00",
            }
        )
    rows = raw_bars(calendar, days[:8])
    for row in rows:
        if row["session_date"] >= change_day.isoformat():
            row["ticker"] = "MSFT" if row["security_id"] == "SEC-AAPL-001" else "AAPL"

    with pytest.raises(ValueError, match="mapping is not bijective"):
        ledger._normalize_bars(
            rows,
            appended_at=CLOCK,
            calendar=calendar,
            manifest=manifest,
            master_records=[*base, *changes],
        )


def test_delisting_inside_snapshot_declares_constant_membership_limit(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    delist_day = days[4]
    delist_at = next(
        item["close_at"]
        for item in calendar["sessions"]
        if item["session_date"] == delist_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-AAPL-001",
        ticker="AAPL",
        marker="e",
        event_type="DELISTED",
        effective_at=delist_at,
        issuer_name="AAPL synthetic issuer",
        terminal_outcome_treatment="LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
    )
    rows = raw_bars(calendar, days[:8])
    with pytest.raises(ValueError, match="permanent security identity"):
        append_bars(ledger, calendar, manifest, rows)

    eligible_only = [
        row
        for row in rows
        if not (
            row["security_id"] == "SEC-AAPL-001"
            and row["session_date"] >= delist_day.isoformat()
        )
    ]
    with pytest.raises(ValueError, match="cross-sectionally aligned"):
        append_bars(ledger, calendar, manifest, eligible_only)


def test_master_timestamps_are_parsed_once_per_normalization(tmp_path, monkeypatch):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    base = master.verify()
    future = [
        {
            **base[0],
            "event_type": "TICKER_CHANGED",
            "ticker": "ZZZ",
            "prior_ticker": "AAPL",
            "effective_at": f"{2030 + index:04d}-01-01T00:00:00+00:00",
        }
        for index in range(200)
    ]
    original_timestamp = module._timestamp
    calls = 0

    def counted_timestamp(value, name):
        nonlocal calls
        calls += 1
        return original_timestamp(value, name)

    monkeypatch.setattr(module, "_timestamp", counted_timestamp)
    normalized = ledger._normalize_bars(
        raw_bars(calendar, days[:8]),
        appended_at=CLOCK,
        calendar=calendar,
        manifest=manifest,
        master_records=[*base, *future],
    )
    assert len(normalized) == 16
    assert calls < 700


def test_timestamp_rejects_naive_canonical_value_before_host_timezone_conversion(monkeypatch):
    monkeypatch.setattr(module, "canonical_timestamp", lambda value: "2025-01-02T16:00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        module._timestamp("ignored", "sample")


def test_per_security_intervals_materialize_permanent_ids_and_membership_events(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    master_event(
        master,
        security_id="SEC-AAPL-001",
        ticker="AAPL",
        marker="d",
        event_type="INDEX_ADDED",
        effective_at="2020-01-03T14:30:00+00:00",
        universe="SP500",
        issuer_name="AAPL synthetic issuer",
    )
    master_event(
        master,
        security_id="SEC-MSFT-001",
        ticker="MSFT",
        marker="e",
        event_type="INDEX_ADDED",
        effective_at="2020-01-03T14:30:00+00:00",
        universe="SP500",
        issuer_name="MSFT synthetic issuer",
    )
    change_day = days[4]
    change_open = next(
        item["open_at"]
        for item in calendar["sessions"]
        if item["session_date"] == change_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-AAPL-001",
        ticker="APPL",
        marker="f",
        event_type="TICKER_CHANGED",
        effective_at=change_open,
        prior_ticker="AAPL",
        issuer_name="AAPL synthetic issuer",
    )
    delist_day = days[5]
    delist_close = next(
        item["close_at"]
        for item in calendar["sessions"]
        if item["session_date"] == delist_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-MSFT-001",
        ticker="MSFT",
        marker="0",
        event_type="DELISTED",
        effective_at=delist_close,
        issuer_name="MSFT synthetic issuer",
        terminal_outcome_treatment="LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
    )
    rows = raw_bars(calendar, days[:8])
    rows = [
        row
        for row in rows
        if row["security_id"] != "SEC-MSFT-001"
        or row["session_date"] <= delist_day.isoformat()
    ]
    for row in rows:
        if (
            row["security_id"] == "SEC-AAPL-001"
            and row["session_date"] >= change_day.isoformat()
        ):
            row["ticker"] = "APPL"

    snapshot = append_bars(
        ledger,
        calendar,
        manifest,
        rows,
        coverage_shape="PER_SECURITY_PIT_INTERVALS",
    )
    assert snapshot["engine_symbol_policy"] == "PERMANENT_SECURITY_ID"
    assert snapshot["cross_sectionally_aligned"] is False
    assert snapshot["row_count"] == 14
    assert snapshot["coverage_intervals"] == [
        {
            "security_id": "SEC-AAPL-001",
            "coverage_start": days[0].isoformat(),
            "coverage_end": days[7].isoformat(),
            "session_count": 8,
            "ticker_segments": [
                {
                    "ticker": "AAPL",
                    "coverage_start": days[0].isoformat(),
                    "coverage_end": days[3].isoformat(),
                },
                {
                    "ticker": "APPL",
                    "coverage_start": days[4].isoformat(),
                    "coverage_end": days[7].isoformat(),
                },
            ],
        },
        {
            "security_id": "SEC-MSFT-001",
            "coverage_start": days[0].isoformat(),
            "coverage_end": days[5].isoformat(),
            "session_count": 6,
            "ticker_segments": [{
                "ticker": "MSFT",
                "coverage_start": days[0].isoformat(),
                "coverage_end": days[5].isoformat(),
            }],
        },
    ]
    materialized = ledger.materialize_research_inputs(snapshot["bar_snapshot_id"])
    assert len(materialized.bars) == len(rows)
    assert {
        bar.symbol for bar in materialized.bars
    } == {"SEC-AAPL-001", "SEC-MSFT-001"}
    assert materialized.security_ids == tuple(bar.symbol for bar in materialized.bars)
    assert [
        (event.symbol, event.action)
        for event in materialized.universe_events
    ] == [
        ("SEC-AAPL-001", "ADD"),
        ("SEC-MSFT-001", "ADD"),
        ("SEC-MSFT-001", "REMOVE"),
    ]
    assert materialized.dataset_admitted is False
    assert materialized.performance_claim_allowed is False


def test_per_security_interval_internal_gap_and_missing_session_fail_closed(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    for security_id, ticker, marker in (
        ("SEC-AAPL-001", "AAPL", "d"),
        ("SEC-MSFT-001", "MSFT", "e"),
    ):
        master_event(
            master,
            security_id=security_id,
            ticker=ticker,
            marker=marker,
            event_type="INDEX_ADDED",
            effective_at="2020-01-03T14:30:00+00:00",
            universe="SP500",
            issuer_name=f"{ticker} synthetic issuer",
        )
    rows = raw_bars(calendar, days[:8])
    rows = [
        row
        for row in rows
        if not (
            row["security_id"] == "SEC-MSFT-001"
            and row["session_date"] == days[3].isoformat()
        )
    ]
    with pytest.raises(ValueError, match="interval contains an internal gap"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )

    without_day = [
        row for row in rows if row["session_date"] != days[3].isoformat()
    ]
    with pytest.raises(ValueError, match="missing calendar session"):
        append_bars(
            ledger,
            calendar,
            manifest,
            without_day,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )


def test_per_security_intervals_require_every_pit_member_and_removal_exit_bar(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    for security_id, ticker, marker in (
        ("SEC-AAPL-001", "AAPL", "d"),
        ("SEC-MSFT-001", "MSFT", "e"),
    ):
        master_event(
            master,
            security_id=security_id,
            ticker=ticker,
            marker=marker,
            event_type="INDEX_ADDED",
            effective_at="2020-01-03T14:30:00+00:00",
            universe="SP500",
            issuer_name=f"{ticker} synthetic issuer",
        )
    aapl_only = raw_bars(
        calendar,
        days[:8],
        security_ids=(("SEC-AAPL-001", "AAPL"),),
    )
    with pytest.raises(ValueError, match="lacks PIT membership coverage.*SEC-MSFT"):
        append_bars(
            ledger,
            calendar,
            manifest,
            aapl_only,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )

    remove_day = days[4]
    remove_close = next(
        item["close_at"]
        for item in calendar["sessions"]
        if item["session_date"] == remove_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-MSFT-001",
        ticker="MSFT",
        marker="f",
        event_type="INDEX_REMOVED",
        effective_at=remove_close,
        universe="SP500",
        issuer_name="MSFT synthetic issuer",
    )
    rows = [
        row
        for row in raw_bars(calendar, days[:8])
        if row["security_id"] != "SEC-MSFT-001"
        or row["session_date"] <= remove_day.isoformat()
    ]
    with pytest.raises(ValueError, match="lacks PIT membership coverage.*SEC-MSFT"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )


def test_open_effective_removal_requires_that_immediate_exit_session_bar(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    for security_id, ticker, marker in (
        ("SEC-AAPL-001", "AAPL", "d"),
        ("SEC-MSFT-001", "MSFT", "e"),
    ):
        master_event(
            master,
            security_id=security_id,
            ticker=ticker,
            marker=marker,
            event_type="INDEX_ADDED",
            effective_at="2020-01-03T14:30:00+00:00",
            universe="SP500",
            issuer_name=f"{ticker} synthetic issuer",
        )
    remove_day = days[5]
    remove_open = next(
        item["open_at"]
        for item in calendar["sessions"]
        if item["session_date"] == remove_day.isoformat()
    )
    master_event(
        master,
        security_id="SEC-MSFT-001",
        ticker="MSFT",
        marker="f",
        event_type="INDEX_REMOVED",
        effective_at=remove_open,
        universe="SP500",
        issuer_name="MSFT synthetic issuer",
    )
    missing_exit_session = [
        row
        for row in raw_bars(calendar, days[:8])
        if row["security_id"] != "SEC-MSFT-001"
        or row["session_date"] < remove_day.isoformat()
    ]
    with pytest.raises(ValueError, match="lacks PIT membership coverage.*SEC-MSFT"):
        append_bars(
            ledger,
            calendar,
            manifest,
            missing_exit_session,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )

    overlapping_cutoff = [dict(row) for row in missing_exit_session]
    previous_day = days[4].isoformat()
    for row in overlapping_cutoff:
        if row["session_date"] == previous_day:
            row["available_at"] = remove_open
    with pytest.raises(ValueError, match="session cutoffs must be strictly chronological"):
        append_bars(
            ledger,
            calendar,
            manifest,
            overlapping_cutoff,
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )

    with_exit_session = [
        row
        for row in raw_bars(calendar, days[:8])
        if row["security_id"] != "SEC-MSFT-001"
        or row["session_date"] <= remove_day.isoformat()
    ]
    snapshot = append_bars(
        ledger,
        calendar,
        manifest,
        with_exit_session,
        coverage_shape="PER_SECURITY_PIT_INTERVALS",
    )
    assert snapshot["coverage_intervals"][1]["coverage_end"] == remove_day.isoformat()


def test_interval_security_count_is_bounded_before_group_scans(tmp_path, monkeypatch):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    for security_id, ticker, marker in (
        ("SEC-AAPL-001", "AAPL", "d"),
        ("SEC-MSFT-001", "MSFT", "e"),
    ):
        master_event(
            master,
            security_id=security_id,
            ticker=ticker,
            marker=marker,
            event_type="INDEX_ADDED",
            effective_at="2020-01-03T14:30:00+00:00",
            universe="SP500",
            issuer_name=f"{ticker} synthetic issuer",
        )
    monkeypatch.setattr(module, "MAX_SECURITIES", 1)
    with pytest.raises(ValueError, match="security-count limit"):
        append_bars(
            ledger,
            calendar,
            manifest,
            raw_bars(calendar, days[:2]),
            coverage_shape="PER_SECURITY_PIT_INTERVALS",
        )


def test_schema_v1_strict_snapshot_remains_verifiable_and_materializable(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    current = append_bars(
        ledger,
        calendar,
        manifest,
        raw_bars(calendar, days[:8]),
    )
    legacy = dict(current)
    legacy.pop("coverage_intervals")
    legacy.pop("engine_symbol_policy")
    legacy["schema_version"] = module.LEGACY_SCHEMA_VERSION
    legacy["policy_version"] = module.LEGACY_POLICY_VERSION
    legacy["bar_snapshot_id"] = ""
    legacy["bar_snapshot_id"] = module._bar_snapshot_id(legacy)
    material = {key: value for key, value in legacy.items() if key != "record_hash"}
    legacy["record_hash"] = module._record_hash(material)
    ledger.path.write_text(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")) + "\n"
    )

    assert ledger.verify() == [legacy]
    inputs = ledger.materialize_research_inputs(legacy["bar_snapshot_id"])
    assert inputs.coverage_shape == "STRICT_RECTANGLE_CONSTANT_MEMBERSHIP"
    assert inputs.engine_symbol_policy == "TICKER"
    assert {bar.symbol for bar in inputs.bars} == {"AAPL", "MSFT"}

    malformed = dict(legacy)
    malformed.pop("coverage_shape")
    malformed["bar_snapshot_id"] = ""
    malformed["bar_snapshot_id"] = module._bar_snapshot_id(malformed)
    malformed_material = {
        key: value for key, value in malformed.items() if key != "record_hash"
    }
    malformed["record_hash"] = module._record_hash(malformed_material)
    ledger.path.write_text(
        json.dumps(malformed, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_dead_zone_validation_and_test_rows_never_enter_snapshot(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    for selected in (days[8:9], days[11:12], days[24:25]):
        with pytest.raises(ValueError, match="TRAIN sessions only"):
            append_bars(
                ledger,
                calendar,
                manifest,
                raw_bars(calendar, selected),
            )
    assert ledger.records() == []


def test_security_master_prefix_is_pinned_and_later_unrelated_event_is_inert(tmp_path):
    days, _, calendar, manifest, master, ledger = environment(tmp_path)
    snapshot = append_bars(ledger, calendar, manifest, raw_bars(calendar, days[:8]))
    pinned_tip = snapshot["security_master_tip_record_hash"]
    master_event(master, security_id="SEC-OTHER-001", ticker="ZZZ", marker="e")
    assert master.verify()[-1]["record_hash"] != pinned_tip
    assert ledger.verify() == [snapshot]
    assert len(ledger.materialize_research_inputs(snapshot["bar_snapshot_id"]).bars) == 16


def test_record_tampering_and_unsafe_target_fail_closed(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    append_bars(ledger, calendar, manifest, raw_bars(calendar, days[:8]))
    values = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    values[0]["bars"][0]["close"] = "999"
    ledger.path.write_text("\n".join(json.dumps(item) for item in values) + "\n")
    ledger.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        ledger.verify()

    other = PITDailyBarLedger(
        tmp_path / "unsafe.jsonl",
        calendar_ledger=ledger.calendar_ledger,
        security_master_ledger=ledger.security_master_ledger,
        clock=lambda: CLOCK,
    )
    other.path.write_text("")
    other.path.chmod(0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        other.records()


def test_hash_valid_authority_tamper_and_projected_size_fail_closed(tmp_path, monkeypatch):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    append_bars(ledger, calendar, manifest, raw_bars(calendar, days[:8]))
    value = json.loads(ledger.path.read_text())
    value["performance_claim_allowed"] = True
    value["bar_snapshot_id"] = module._bar_snapshot_id(value)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    ledger.path.write_text(json.dumps(value) + "\n")
    ledger.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        ledger.verify()

    fresh = environment(tmp_path / "size")
    monkeypatch.setattr(module, "MAX_LEDGER_BYTES", 100)
    with pytest.raises(LedgerIntegrityError, match="size limit"):
        append_bars(fresh[5], fresh[2], fresh[3], raw_bars(fresh[2], fresh[0][:8]))
    assert fresh[5].records() == []

    per_snapshot = environment(tmp_path / "snapshot-size")
    monkeypatch.setattr(module, "MAX_LEDGER_BYTES", 512 * 1024 * 1024)
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 100)
    with pytest.raises(LedgerIntegrityError, match="snapshot exceeds its size limit"):
        append_bars(
            per_snapshot[5],
            per_snapshot[2],
            per_snapshot[3],
            raw_bars(per_snapshot[2], per_snapshot[0][:8]),
        )
    assert per_snapshot[5].records() == []


def test_reconciliation_and_materialization_carriers_cannot_assert_authority():
    reconciliation = {
        "bar_snapshot_id": "PBAR-TEST",
        "bar_snapshot_record_hash": "a" * 64,
        "status": "CURRENT",
    }
    with pytest.raises(ValueError, match="cannot assert authority"):
        PITDailyBarReconciliation(**reconciliation, promotion_allowed=True)
    with pytest.raises(ValueError, match="cannot assert supersession"):
        PITDailyBarReconciliation(**reconciliation, reason_code="SOURCE_CORRECTION")
    with pytest.raises(ValueError, match="allowed reason"):
        PITDailyBarReconciliation(
            **{**reconciliation, "status": "SUPERSEDED"},
            superseded_by_bar_snapshot_id="PBAR-CHILD",
            reason_code="MASTER_BACKFILL",
        )


def test_unknown_snapshot_and_non_synthetic_input_fail_closed(tmp_path):
    days, _, calendar, manifest, _, ledger = environment(tmp_path)
    rows = raw_bars(calendar, days[:8])
    with pytest.raises(ValueError, match="synthetic fixtures only"):
        append_bars(
            ledger,
            calendar,
            manifest,
            rows,
            synthetic_fixture=False,
        )
    with pytest.raises(ValueError, match="not present"):
        ledger.reconcile_snapshot("PBAR-UNKNOWN")
    with pytest.raises(ValueError, match="not present"):
        ledger.materialize_research_inputs("PBAR-UNKNOWN")
