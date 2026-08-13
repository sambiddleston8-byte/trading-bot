from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.performance.portfolio_valuation import _record_hash
from core.research_run_telemetry_ledger import ResearchRunTelemetryLedger


OBSERVED_AT = "2026-08-13T12:00:00+00:00"


def append(ledger: ResearchRunTelemetryLedger, **overrides):
    values = {
        "batch_id": "portfolio_research_batch_test",
        "ticker": "NVDA",
        "sequence_index": 0,
        "outcome": "COMPLETE",
        "wall_duration_ms": 5.25,
        "pacing_delay_seconds_configured": 1.0,
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return ledger.append_observation(**values)


def rewrite_record(path, transform):
    record = json.loads(path.read_text(encoding="utf-8"))
    transform(record)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = _record_hash(material)
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def test_append_creates_genesis_record_and_hash_chain(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    first = append(ledger)
    second = append(ledger, ticker="AAPL", sequence_index=1)

    assert first["previous_hash"] == "0" * 64
    assert second["previous_hash"] == first["record_hash"]
    assert ledger.verify() == [first, second]


def test_identical_duplicate_is_idempotent_and_changed_duplicate_is_rejected(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    first = append(ledger)

    assert append(ledger) == first
    assert len(ledger.verify()) == 1
    with pytest.raises(LedgerIntegrityError):
        append(ledger, wall_duration_ms=6.0)


def test_default_timestamp_retry_retains_first_observation(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    values = {
        "batch_id": "portfolio_research_batch_test",
        "ticker": "NVDA",
        "sequence_index": 0,
        "outcome": "COMPLETE",
        "wall_duration_ms": 5.25,
        "pacing_delay_seconds_configured": 1.0,
    }
    first = ledger.append_observation(**values)
    second = ledger.append_observation(**values)

    assert second == first
    assert len(ledger.verify()) == 1


def test_concurrent_identical_append_writes_once(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: append(ledger), range(2)))

    assert records[0] == records[1]
    assert len(ledger.verify()) == 1


def test_hash_valid_semantic_tampering_is_detected_and_blocks_append(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    ledger = ResearchRunTelemetryLedger(path)
    append(ledger)
    rewrite_record(path, lambda record: record.update(wall_duration_ms=-1))

    with pytest.raises(LedgerIntegrityError):
        ledger.verify()
    with pytest.raises(LedgerIntegrityError):
        append(ledger, ticker="AAPL", sequence_index=1)


def test_hash_valid_unknown_top_level_field_is_rejected(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    ledger = ResearchRunTelemetryLedger(path)
    append(ledger)
    rewrite_record(path, lambda record: record.update(api_key="secret"))

    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [("ERROR", None), ("COMPLETE", "TimeoutError")],
)
def test_outcome_and_error_type_must_agree(tmp_path, outcome, error_type):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    with pytest.raises(ValueError):
        append(ledger, outcome=outcome, error_type=error_type)


def test_secret_shaped_component_keys_and_free_text_errors_are_rejected(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    with pytest.raises(ValueError):
        append(
            ledger,
            component_observations=[
                {"component": "prices", "url": "https://example.test?key=secret"}
            ],
        )
    with pytest.raises(ValueError):
        append(
            ledger,
            outcome="ERROR",
            error_type="Timeout at https://example.test?key=secret",
        )


def test_unknown_component_data_is_null_and_timing_fields_are_independent(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    record = append(
        ledger, wall_duration_ms=5.0, pacing_delay_seconds_configured=1.0
    )

    assert record["component_observations"] is None
    assert record["wall_duration_ms"] == 5.0
    assert record["pacing_delay_seconds_configured"] == 1.0


def test_measured_component_fields_remain_optional(tmp_path):
    ledger = ResearchRunTelemetryLedger(tmp_path / "telemetry.jsonl")
    record = append(
        ledger,
        component_observations=[
            {"component": "prices", "provider": "fmp", "duration_ms": 2.5},
            {"component": "news", "retry_count": 0, "cache_hit": False},
        ],
    )

    assert record["component_observations"] == [
        {"component": "prices", "provider": "fmp", "duration_ms": 2.5},
        {"component": "news", "retry_count": 0, "cache_hit": False},
    ]


def test_explicit_repair_preserves_malformed_tail_and_valid_prefix(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    ledger = ResearchRunTelemetryLedger(path)
    record = append(ledger)
    with path.open("ab") as target:
        target.write(b'{"partial":')

    backup = ledger.repair_incomplete_tail()

    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [record]
