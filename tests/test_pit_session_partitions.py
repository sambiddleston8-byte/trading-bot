from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.orchestration.pit_session_partitions import PITSessionPartitionLedger


NY = ZoneInfo("America/New_York")
CLOCK = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
SOURCE_HASH = hashlib.sha256(b"deterministic synthetic XNYS fixture").hexdigest()


def weekdays(start: str, count: int) -> list[date]:
    cursor = date.fromisoformat(start)
    result = []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def session(day: date, *, kind: str = "REGULAR", timestamps: dict | None = None) -> dict:
    close = time(13) if kind == "EARLY_CLOSE" else time(16)
    open_at = datetime.combine(day, time(9, 30), NY).astimezone(timezone.utc)
    close_at = datetime.combine(day, close, NY).astimezone(timezone.utc)
    known = datetime(2024, 1, 1, tzinfo=timezone.utc)
    value = {
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
    value.update(timestamps or {})
    return value


def calendar(ledger: PITSessionPartitionLedger, days: list[date], **changes):
    arguments = {
        "sessions": [session(day) for day in days],
        "source_uri": "https://calendar.example.invalid/xnys",
        "source_locator": "synthetic-fixture-v1",
        "source_payload_sha256": SOURCE_HASH,
        "synthetic_fixture": True,
    }
    arguments.update(changes)
    return ledger.append_calendar_snapshot(**arguments)


def manifest(ledger: PITSessionPartitionLedger, snapshot: dict, days: list[date], **changes):
    arguments = {
        "calendar_snapshot_id": snapshot["calendar_snapshot_id"],
        "train_start": days[0].isoformat(),
        "train_end": days[7].isoformat(),
        "validation_start": days[11].isoformat(),
        "validation_end": days[20].isoformat(),
        "test_start": days[24].isoformat(),
        "test_end": days[30].isoformat(),
        "longest_label_horizon_decision_periods": 2,
        "embargo_decision_periods": 1,
    }
    arguments.update(changes)
    return ledger.append_partition_manifest(**arguments)


def test_calendar_snapshot_is_deterministic_provider_neutral_and_research_only(tmp_path):
    days = weekdays("2025-01-02", 35)
    first_ledger = PITSessionPartitionLedger(tmp_path / "first.jsonl", clock=lambda: CLOCK)
    second_ledger = PITSessionPartitionLedger(tmp_path / "second.jsonl", clock=lambda: CLOCK)

    first = calendar(first_ledger, days)
    second = calendar(second_ledger, list(reversed(days)))

    assert first["calendar_snapshot_id"] == second["calendar_snapshot_id"]
    assert first["record_hash"] == second["record_hash"]
    assert first["exchange"] == "XNYS"
    assert first["point_in_time_contract"] == (
        "effective_at/reported_at/available_at/retrieved_at/recorded_at"
    )
    assert first["session_count"] == 35
    assert first["sessions"][0]["close_at"].endswith("+00:00")
    assert first["synthetic_fixture"] is True
    assert first["coverage_completeness_proven"] is False
    assert first["qualified"] is False
    assert first["train_admitted"] is False
    assert first["validation_admitted"] is False
    assert first["test_admitted"] is False
    assert first["performance_claim_allowed"] is False
    assert first["promotion_allowed"] is False


def test_calendar_enforces_dst_early_close_and_five_timestamp_order(tmp_path):
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    regular = session(date(2025, 6, 2))
    early = session(date(2025, 11, 28), kind="EARLY_CLOSE")
    record = calendar(ledger, [date(2025, 6, 2), date(2025, 11, 28)], sessions=[regular, early])
    assert record["sessions"][0]["open_at"].startswith("2025-06-02T13:30:00")
    assert record["sessions"][1]["close_at"].startswith("2025-11-28T18:00:00")

    bad_close = session(date(2025, 6, 3), timestamps={"close_at": "2025-06-03T19:59:00+00:00"})
    with pytest.raises(ValueError, match="local schedule"):
        calendar(PITSessionPartitionLedger(tmp_path / "bad-close.jsonl"), [date(2025, 6, 3)], sessions=[bad_close])

    bad_pit = session(
        date(2025, 6, 3),
        timestamps={
            "available_at": "2024-01-03T00:00:00+00:00",
            "retrieved_at": "2024-01-02T00:00:00+00:00",
        },
    )
    with pytest.raises(ValueError, match="reported <= available <= retrieved <= recorded"):
        calendar(PITSessionPartitionLedger(tmp_path / "bad-pit.jsonl"), [date(2025, 6, 3)], sessions=[bad_pit])

    future_recorded = session(
        date(2025, 6, 3),
        timestamps={"recorded_at": "2027-01-01T00:00:00+00:00"},
    )
    with pytest.raises(ValueError, match="cannot follow the immutable append time"):
        calendar(
            PITSessionPartitionLedger(tmp_path / "future-recorded.jsonl", clock=lambda: CLOCK),
            [date(2025, 6, 3)],
            sessions=[future_recorded],
        )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"source_uri": "https://user:secret@calendar.example.invalid/xnys"}, "credential-free"),
        ({"source_uri": "https://calendar.example.invalid/xnys?token=secret"}, "credential-free"),
        ({"synthetic_fixture": False}, "synthetic fixtures only"),
        ({"source_payload_sha256": "not-a-hash"}, "SHA-256"),
    ],
)
def test_calendar_rejects_credentials_false_authority_and_unpinned_bytes(tmp_path, change, message):
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    with pytest.raises(ValueError, match=message):
        calendar(ledger, weekdays("2025-01-02", 3), **change)


def test_partition_manifest_records_exact_session_dead_zones_and_seals_validation_test(tmp_path):
    days = weekdays("2025-01-02", 35)
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    snapshot = calendar(ledger, days)
    record = manifest(ledger, snapshot, days)

    assert record["decision_period_unit"] == "XNYS_DAILY_SESSION"
    assert record["dead_zone_decision_periods"] == 3
    assert [item["role"] for item in record["partitions"]] == ["TRAIN", "VALIDATION", "TEST"]
    assert record["seams"][0]["dead_zone_session_ids"] == [
        f"XNYS:{day.isoformat()}" for day in days[8:11]
    ]
    assert record["seams"][1]["dead_zone_session_ids"] == [
        f"XNYS:{day.isoformat()}" for day in days[21:24]
    ]
    assert all(item["belongs_to_no_partition"] for item in record["seams"])
    assert record["train_admitted"] is False
    assert record["validation_admitted"] is False
    assert record["validation_access_authorized"] is False
    assert record["test_admitted"] is False
    assert record["test_access_authorized"] is False
    assert record["candidate_freeze_allowed"] is False
    assert record["broker_submission_enabled"] is False
    assert record["live_trading_enabled"] is False
    assert ledger.partition_role(record["partition_manifest_id"], days[7].isoformat()) == "TRAIN"
    assert ledger.partition_role(record["partition_manifest_id"], days[9].isoformat()) == "DEAD_ZONE"
    assert ledger.partition_role(record["partition_manifest_id"], days[11].isoformat()) == "VALIDATION"
    assert ledger.partition_role(record["partition_manifest_id"], days[24].isoformat()) == "TEST"
    assert ledger.partition_role(record["partition_manifest_id"], days[34].isoformat()) == "OUTSIDE"
    assert ledger.partition_role(record["partition_manifest_id"], "2025-01-04") == "OUTSIDE"


@pytest.mark.parametrize(
    "change",
    [
        {"validation_start": None},
        {"validation_start": "2025-12-31"},
        {"validation_start_index": 10},
        {"validation_start_index": 12},
        {"test_start_index": 23},
        {"test_start_index": 25},
        {"validation_end_index": 24},
    ],
)
def test_partition_boundaries_missing_overlapping_or_off_by_one_fail_closed(tmp_path, change):
    days = weekdays("2025-01-02", 35)
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    snapshot = calendar(ledger, days)
    resolved = dict(change)
    for name in list(resolved):
        if name.endswith("_index"):
            resolved[name.removesuffix("_index")] = days[resolved.pop(name)].isoformat()
    with pytest.raises(ValueError):
        manifest(ledger, snapshot, days, **resolved)
    assert len(ledger.verify()) == 1


def test_manifest_pins_calendar_hash_and_is_idempotent(tmp_path):
    days = weekdays("2025-01-02", 35)
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    snapshot = calendar(ledger, days)
    first = manifest(ledger, snapshot, days)
    second = manifest(ledger, snapshot, days)
    assert first == second
    assert first["calendar_snapshot_record_hash"] == snapshot["record_hash"]
    assert len(ledger.verify()) == 2
    with pytest.raises(ValueError, match="already recorded"):
        ledger.append_partition_manifest(
            calendar_snapshot_id=snapshot["calendar_snapshot_id"],
            train_start=days[0].isoformat(), train_end=days[7].isoformat(),
            validation_start=days[11].isoformat(), validation_end=days[20].isoformat(),
            test_start=days[24].isoformat(), test_end=days[30].isoformat(),
            longest_label_horizon_decision_periods=2, embargo_decision_periods=1,
            allow_existing=False,
        )


def test_synthetic_history_flags_can_describe_span_but_never_authorize_production(tmp_path):
    days = weekdays("2021-01-04", 1100)
    ledger = PITSessionPartitionLedger(tmp_path / "ledger.jsonl", clock=lambda: CLOCK)
    snapshot = calendar(ledger, days)
    record = ledger.append_partition_manifest(
        calendar_snapshot_id=snapshot["calendar_snapshot_id"],
        train_start=days[0].isoformat(), train_end=days[800].isoformat(),
        validation_start=days[804].isoformat(), validation_end=days[934].isoformat(),
        test_start=days[938].isoformat(), test_end=days[1099].isoformat(),
        longest_label_horizon_decision_periods=2, embargo_decision_periods=1,
    )
    assert record["train_span_at_least_three_years"] is True
    assert record["validation_span_at_least_six_months"] is True
    assert record["validation_at_least_60_decision_periods"] is True
    assert record["declared_drawdown_regime_evidence"] is False
    assert record["production_history_count_requirements_met"] is False
    assert record["qualified"] is False
    assert record["promotion_allowed"] is False


def test_rehashed_authority_or_seam_tampering_is_detected(tmp_path):
    days = weekdays("2025-01-02", 35)
    path = tmp_path / "ledger.jsonl"
    ledger = PITSessionPartitionLedger(path, clock=lambda: CLOCK)
    snapshot = calendar(ledger, days)
    manifest(ledger, snapshot, days)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[1]["test_access_authorized"] = True
    material = {key: value for key, value in records[1].items() if key != "record_hash"}
    records[1]["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text("\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in records) + "\n")
    path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_unsafe_or_incomplete_ledger_is_rejected(tmp_path):
    path = Path(tmp_path / "ledger.jsonl")
    path.write_text("{}")
    path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        PITSessionPartitionLedger(path).verify()

    path.write_text("{}\n")
    path.chmod(0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        PITSessionPartitionLedger(path).verify()
