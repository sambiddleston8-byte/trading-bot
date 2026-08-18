from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import pytest

import core.orchestration.pit_train_source_bundle as bundle_module
from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import LedgerIntegrityError
from core.guardrailed_backtest import (
    ACTION_HOLD,
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    ResearchExemptionDataAttestation,
)
from core.orchestration.pit_corporate_actions import PITCorporateActionLedger
from core.orchestration.pit_daily_bars import PITDailyBarLedger
from core.orchestration.pit_session_partitions import PITSessionPartitionLedger
from core.orchestration.pit_train_source_bundle import (
    PITTrainSourceBundleInputs,
    PITTrainSourceBundleLedger,
    STATUS,
)
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


NY = ZoneInfo("America/New_York")
CLOCK = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
KNOWN = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
SECURITY_ID = "SEC-AAPL-001"
DELISTED_SECURITY_ID = "SEC-BBB-002"


class HoldOnlyStrategy:
    version = "synthetic-pit-source-bundle-v1"

    def decide(self, symbol, history_through_signal_close, parameters):
        return ACTION_HOLD


def weekdays(start: str, count: int) -> list[date]:
    cursor = date.fromisoformat(start)
    result: list[date] = []
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def session(day: date) -> dict:
    opened = datetime.combine(day, time(9, 30), NY).astimezone(timezone.utc)
    closed = datetime.combine(day, time(16), NY).astimezone(timezone.utc)
    return {
        "session_date": day.isoformat(),
        "session_type": "REGULAR",
        "open_at": opened.isoformat(),
        "close_at": closed.isoformat(),
        "effective_at": opened.isoformat(),
        "reported_at": KNOWN.isoformat(),
        "available_at": KNOWN.isoformat(),
        "retrieved_at": KNOWN.isoformat(),
        "recorded_at": KNOWN.isoformat(),
    }


def authenticated(
    ledger: AuthenticatedSourceContentLedger,
    *,
    uri: str,
    marker: str,
) -> tuple[dict, str]:
    payload = f"deterministic-source-payload:{marker}".encode()
    record = ledger.ingest(
        source_uri=uri,
        payload=payload,
        media_type="application/json",
        publicly_available_at=KNOWN,
        retrieved_at=KNOWN,
        recorded_at=KNOWN + timedelta(minutes=1),
        source_locator=f"$.{marker}",
    )
    return record, hashlib.sha256(payload).hexdigest()


def build_environment(
    tmp_path: Path,
    *,
    short_action_coverage: bool = False,
    segmented_action_coverage: bool = False,
    include_midwindow_delisting: bool = False,
    extended_action_coverage: bool = False,
    coverage_shape: str = "PER_SECURITY_PIT_INTERVALS",
) -> dict:
    days = weekdays("2025-01-02", 35)
    sources = AuthenticatedSourceContentLedger(
        tmp_path / "sources.jsonl",
        tmp_path / "source-blobs",
    )
    source_rows: list[dict] = []

    master = PointInTimeSecurityMasterLedger(tmp_path / "master.jsonl")
    master_specs = [
        (
            "LISTED",
            "https://master.example.invalid/aapl/snapshot",
            "master-snapshot",
            "2020-01-02T14:30:00+00:00",
        ),
        (
            "INDEX_ADDED",
            "https://master.example.invalid/aapl/snapshot",
            "master-snapshot",
            "2020-01-03T14:30:00+00:00",
        ),
    ]
    for event_type, uri, marker, effective_at in master_specs:
        evidence, digest = authenticated(sources, uri=uri, marker=marker)
        if evidence["content_evidence_id"] not in {
            item["content_evidence_id"] for item in source_rows
        }:
            source_rows.append(evidence)
        master.record_event(
            security_id=SECURITY_ID,
            event_type=event_type,
            ticker="AAPL",
            issuer_name="Apple synthetic issuer",
            exchange_mic="XNYS",
            universe="SP500" if event_type == "INDEX_ADDED" else None,
            effective_at=effective_at,
            reported_at="2020-01-01T10:00:00+00:00",
            available_at="2020-01-01T11:00:00+00:00",
            retrieved_at="2020-01-01T12:00:00+00:00",
            recorded_at="2020-01-01T13:00:00+00:00",
            source_uri=uri,
            source_input_sha256=digest,
            source_locator=f"$.{marker}",
        )

    delisting = None
    if include_midwindow_delisting:
        second_events = [
            (
                "LISTED",
                "2020-01-02T14:30:00+00:00",
                None,
                "NOT_APPLICABLE",
            ),
            (
                "INDEX_ADDED",
                session(days[2])["open_at"],
                "SP500",
                "NOT_APPLICABLE",
            ),
            (
                "DELISTED",
                session(days[5])["close_at"],
                None,
                "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
            ),
        ]
        for index, (event_type, effective_at, universe, treatment) in enumerate(
            second_events
        ):
            marker = f"bbb-{event_type.lower()}"
            evidence, digest = authenticated(
                sources,
                uri=f"https://master.example.invalid/bbb/{event_type.lower()}",
                marker=marker,
            )
            source_rows.append(evidence)
            effective = datetime.fromisoformat(effective_at)
            recorded = min(effective - timedelta(hours=11), KNOWN)
            delisting = master.record_event(
                security_id=DELISTED_SECURITY_ID,
                event_type=event_type,
                ticker="BBB",
                issuer_name="BBB synthetic issuer",
                exchange_mic="XNYS",
                universe=universe,
                effective_at=effective,
                reported_at=recorded - timedelta(hours=3),
                available_at=recorded - timedelta(hours=2),
                retrieved_at=recorded - timedelta(hours=1),
                recorded_at=recorded,
                terminal_outcome_treatment=treatment,
                source_uri=evidence["source_uri"],
                source_input_sha256=digest,
                source_locator=f"$.{marker}",
            )

    calendar_uri = "https://calendar.example.invalid/xnys"
    calendar_evidence, calendar_digest = authenticated(
        sources,
        uri=calendar_uri,
        marker="calendar",
    )
    source_rows.append(calendar_evidence)
    calendar_ledger = PITSessionPartitionLedger(
        tmp_path / "calendar.jsonl",
        clock=lambda: CLOCK,
    )
    calendar = calendar_ledger.append_calendar_snapshot(
        sessions=[session(day) for day in days],
        source_uri=calendar_uri,
        source_locator="$.calendar",
        source_payload_sha256=calendar_digest,
        synthetic_fixture=True,
    )
    manifest = calendar_ledger.append_partition_manifest(
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

    sessions = {item["session_date"]: item for item in calendar["sessions"]}
    rows = []
    for index, day in enumerate(days[:8]):
        observed = sessions[day.isoformat()]
        close_at = datetime.fromisoformat(observed["close_at"])
        rows.append(
            {
                "security_id": SECURITY_ID,
                "ticker": "AAPL",
                "session_date": day.isoformat(),
                "open_at": observed["open_at"],
                "close_at": observed["close_at"],
                "effective_at": observed["close_at"],
                "reported_at": observed["close_at"],
                "available_at": (close_at + timedelta(minutes=1)).isoformat(),
                "retrieved_at": datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat(),
                "open": str(100 + index),
                "high": str(102 + index),
                "low": str(99 + index),
                "close": str(101 + index),
                "volume": str(1_000_000 + index),
                "source_payload_sha256": hashlib.sha256(
                    f"{SECURITY_ID}:{day.isoformat()}".encode()
                ).hexdigest(),
                "source_row_locator": f"$.bars[{index}]",
            }
        )
    if include_midwindow_delisting:
        for index, day in enumerate(days[2:6], start=2):
            observed = sessions[day.isoformat()]
            close_at = datetime.fromisoformat(observed["close_at"])
            rows.append(
                {
                    "security_id": DELISTED_SECURITY_ID,
                    "ticker": "BBB",
                    "session_date": day.isoformat(),
                    "open_at": observed["open_at"],
                    "close_at": observed["close_at"],
                    "effective_at": observed["close_at"],
                    "reported_at": observed["close_at"],
                    "available_at": (close_at + timedelta(minutes=1)).isoformat(),
                    "retrieved_at": datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat(),
                    "open": str(50 + index),
                    "high": str(52 + index),
                    "low": str(49 + index),
                    "close": str(51 + index),
                    "volume": str(500_000 + index),
                    "source_payload_sha256": hashlib.sha256(
                        f"{DELISTED_SECURITY_ID}:{day.isoformat()}".encode()
                    ).hexdigest(),
                    "source_row_locator": f"$.bars.bbb[{index}]",
                }
            )
    bar_uri = "https://bars.example.invalid/aapl-train"
    bar_evidence, bar_digest = authenticated(sources, uri=bar_uri, marker="bars")
    source_rows.append(bar_evidence)
    bars = PITDailyBarLedger(
        tmp_path / "bars.jsonl",
        calendar_ledger=calendar_ledger,
        security_master_ledger=master,
        clock=lambda: CLOCK,
    )
    bar_snapshot = bars.append_snapshot(
        calendar_snapshot_id=calendar["calendar_snapshot_id"],
        partition_manifest_id=manifest["partition_manifest_id"],
        bars=rows,
        coverage_shape=coverage_shape,
        source_uri=bar_uri,
        source_locator="$.bars",
        source_payload_sha256=bar_digest,
        synthetic_fixture=True,
    )

    actions = PITCorporateActionLedger(
        tmp_path / "actions.jsonl",
        master,
        clock=lambda: CLOCK,
    )
    coverage_start = datetime.fromisoformat(sessions[days[0].isoformat()]["open_at"])
    coverage_end = datetime.fromisoformat(sessions[days[7].isoformat()]["close_at"])
    if short_action_coverage:
        coverage_end = datetime.fromisoformat(sessions[days[6].isoformat()]["close_at"])
    if extended_action_coverage:
        coverage_end = datetime.fromisoformat(sessions[days[12].isoformat()]["close_at"])
    split_at = datetime.fromisoformat(sessions[days[2].isoformat()]["open_at"])
    split_events = [
        {
            "source_event_id": "SPLIT-1",
            "event_type": "SPLIT",
            "effective_at": split_at.isoformat(),
            "reported_at": (split_at - timedelta(days=2)).isoformat(),
            "available_at": (split_at - timedelta(days=1)).isoformat(),
            "retrieved_at": (split_at - timedelta(hours=12)).isoformat(),
            "recorded_at": (split_at - timedelta(hours=11)).isoformat(),
            "source_locator": "$.actions[0]",
            "split_ratio": "2",
        }
    ]
    action_specs = [
        (
            "https://actions.example.invalid/aapl",
            "actions",
            coverage_start,
            coverage_end,
            split_events,
        )
    ]
    if segmented_action_coverage:
        action_specs = [
            (
                "https://actions.example.invalid/aapl/segment-1",
                "actions-segment-1",
                coverage_start,
                datetime.fromisoformat(sessions[days[4].isoformat()]["open_at"])
                - timedelta(microseconds=1),
                split_events,
            ),
            (
                "https://actions.example.invalid/aapl/segment-2",
                "actions-segment-2",
                datetime.fromisoformat(sessions[days[4].isoformat()]["open_at"]),
                coverage_end,
                [],
            ),
        ]
    if include_midwindow_delisting:
        assert delisting is not None
        terminal_at = datetime.fromisoformat(sessions[days[5].isoformat()]["close_at"])
        known_at = terminal_at - timedelta(days=1)
        action_specs.append(
            (
                "https://actions.example.invalid/bbb",
                "actions-bbb",
                datetime.fromisoformat(sessions[days[2].isoformat()]["open_at"]),
                datetime.fromisoformat(sessions[days[5].isoformat()]["close_at"]),
                [
                    {
                        "source_event_id": "BBB-TERMINAL-1",
                        "event_type": "TERMINAL_OUTCOME",
                        "effective_at": terminal_at.isoformat(),
                        "reported_at": (known_at - timedelta(hours=3)).isoformat(),
                        "available_at": (known_at - timedelta(hours=2)).isoformat(),
                        "retrieved_at": (known_at - timedelta(hours=1)).isoformat(),
                        "recorded_at": known_at.isoformat(),
                        "source_locator": "$.actions-bbb[0]",
                        "terminal_type": "BANKRUPT",
                        "recovery_per_share": "0",
                        "currency": "USD",
                        "cash_settled_at": terminal_at.isoformat(),
                        "delisting_event_id": delisting["event_id"],
                        "delisting_event_record_hash": delisting["record_hash"],
                    }
                ],
            )
        )
    action_snapshots = []
    for action_uri, marker, covers_from, through, events in action_specs:
        action_evidence, action_digest = authenticated(
            sources,
            uri=action_uri,
            marker=marker,
        )
        source_rows.append(action_evidence)
        action_snapshots.append(
            actions.append_snapshot(
                security_id=(
                    DELISTED_SECURITY_ID if marker == "actions-bbb" else SECURITY_ID
                ),
                ticker="BBB" if marker == "actions-bbb" else "AAPL",
                covers_from_at=covers_from,
                through_at=through,
                events=events,
                source_uri=action_uri,
                source_locator=f"$.{marker}",
                source_payload_sha256=action_digest,
                synthetic_fixture=True,
            )
        )
    action_snapshot = action_snapshots[0]

    bundle = PITTrainSourceBundleLedger(
        tmp_path / "bundle.jsonl",
        authenticated_sources=sources,
        security_master=master,
        session_partitions=calendar_ledger,
        daily_bars=bars,
        corporate_actions=actions,
        clock=lambda: CLOCK,
    )
    return {
        "sources": sources,
        "source_rows": source_rows,
        "master": master,
        "calendar_ledger": calendar_ledger,
        "calendar": calendar,
        "manifest": manifest,
        "bars": bars,
        "bar_snapshot": bar_snapshot,
        "actions": actions,
        "action_snapshot": action_snapshot,
        "action_snapshots": action_snapshots,
        "bundle": bundle,
    }


def append_bundle(environment: dict, **changes) -> dict:
    arguments = {
        "bar_snapshot_id": environment["bar_snapshot"]["bar_snapshot_id"],
        "corporate_action_snapshot_ids": [
            item["snapshot_id"] for item in environment["action_snapshots"]
        ],
        "content_evidence_ids": [
            item["content_evidence_id"] for item in environment["source_rows"]
        ],
        "synthetic_fixture": True,
    }
    arguments.update(changes)
    return environment["bundle"].append_bundle(**arguments)


def test_bundle_binds_exact_ledgers_and_materializes_permanent_ids(tmp_path):
    environment = build_environment(tmp_path)
    record = append_bundle(environment)

    assert record["status"] == STATUS
    assert record["partition_role"] == "TRAIN"
    assert record["security_ids"] == [SECURITY_ID]
    assert len(record["source_bindings"]) == 5
    assert len({item["content_evidence_id"] for item in record["source_bindings"]}) == 4
    assert record["authenticated_top_level_payloads_only"] is True
    assert record["per_row_bar_payloads_independently_authenticated"] is False
    assert record["coverage_completeness_proven"] is False
    assert record["qualified"] is False
    assert record["train_admitted"] is False
    assert record["validation_access_authorized"] is False
    assert record["test_access_authorized"] is False
    assert record["performance_claim_allowed"] is False
    assert record["promotion_allowed"] is False

    materialized = environment["bundle"].materialize_research_inputs(record["bundle_id"])
    assert isinstance(materialized, PITTrainSourceBundleInputs)
    assert materialized.engine_symbol_policy == "PERMANENT_SECURITY_ID"
    assert {bar.symbol for bar in materialized.bars} == {SECURITY_ID}
    assert [action.symbol for action in materialized.corporate_actions] == [SECURITY_ID]
    assert materialized.terminal_outcomes == ()
    assert materialized.universe_events
    assert materialized.coverage_completeness_proven is False
    assert materialized.semantic_claim_validated is False
    assert materialized.train_admitted is False
    assert materialized.validation_admitted is False
    assert materialized.validation_access_authorized is False
    assert materialized.test_admitted is False
    assert materialized.test_access_authorized is False
    assert materialized.candidate_freeze_allowed is False

    attestation = ResearchExemptionDataAttestation._from_explicit_research_exemption(
        source_id="RESEARCH_EXEMPTION:SYNTHETIC:PIT_TRAIN_SOURCE_BUNDLE",
        source_content_sha256=record["record_hash"],
        validation_receipt_sha256="a" * 64,
        derivation_policy_version="pit-train-source-bundle-v1",
        evidence_role_hashes=(("ASSUMED_SYNTHETIC_CROSS_LEDGER_BUNDLE", "b" * 64),),
        exemption_id="SYNTHETIC-PIT-TRAIN-SOURCE-BUNDLE",
        exemption_record_sha256="c" * 64,
    )
    result = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"),
            atr_window=2,
            lagged_liquidity_lookback=2,
        ),
        fee_schedule=ExchangeFeeSchedule(
            "SYNTHETIC-FEES",
            (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),),
        ),
        data_attestation=attestation,
    ).run(
        bars=materialized.bars,
        universe_events=materialized.universe_events,
        terminal_outcomes=materialized.terminal_outcomes,
        corporate_actions=materialized.corporate_actions,
        prices_are_unadjusted=True,
        strategy=HoldOnlyStrategy(),
        parameters={},
        evaluation_start=materialized.bars[0].close_at,
        evaluation_end=materialized.bars[-1].close_at,
    )
    assert result.performance_claim_allowed is False
    assert result.orders_submitted is False


def test_per_security_intervals_cover_a_midwindow_delisting_and_terminal_outcome(
    tmp_path,
):
    environment = build_environment(tmp_path, include_midwindow_delisting=True)
    record = append_bundle(environment)

    assert record["security_ids"] == [SECURITY_ID, DELISTED_SECURITY_ID]
    intervals = {
        item["security_id"]: item
        for item in environment["bar_snapshot"]["coverage_intervals"]
    }
    days = weekdays("2025-01-02", 35)
    assert intervals[DELISTED_SECURITY_ID] == {
        "security_id": DELISTED_SECURITY_ID,
        "coverage_start": days[2].isoformat(),
        "coverage_end": days[5].isoformat(),
        "session_count": 4,
        "ticker_segments": [
            {
                "ticker": "BBB",
                "coverage_start": days[2].isoformat(),
                "coverage_end": days[5].isoformat(),
            }
        ],
    }
    materialized = environment["bundle"].materialize_research_inputs(
        record["bundle_id"]
    )
    assert {bar.symbol for bar in materialized.bars} == {
        SECURITY_ID,
        DELISTED_SECURITY_ID,
    }
    assert [(item.symbol, item.terminal_type) for item in materialized.terminal_outcomes] == [
        (DELISTED_SECURITY_ID, "BANKRUPT")
    ]


def test_bundle_is_deterministic_and_idempotent(tmp_path):
    first_environment = build_environment(tmp_path / "first")
    second_environment = build_environment(tmp_path / "second")
    first = append_bundle(first_environment)
    repeat = append_bundle(first_environment)
    second = append_bundle(second_environment)

    assert repeat == first
    assert second["bundle_id"] == first["bundle_id"]
    assert second["record_hash"] == first["record_hash"]
    assert len(first_environment["bundle"].verify()) == 1
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        append_bundle(first_environment, allow_existing=False)


def test_missing_or_extra_authenticated_bytes_fail_closed(tmp_path):
    environment = build_environment(tmp_path)
    identifiers = [item["content_evidence_id"] for item in environment["source_rows"]]

    with pytest.raises(ValueError, match="exactly one selected authenticated-byte binding"):
        append_bundle(environment, content_evidence_ids=identifiers[:-1])

    extra, _ = authenticated(
        environment["sources"],
        uri="https://extra.example.invalid/unused",
        marker="unused",
    )
    with pytest.raises(ValueError, match="unused authenticated-byte evidence"):
        append_bundle(
            environment,
            content_evidence_ids=[*identifiers, extra["content_evidence_id"]],
        )


def test_corporate_action_coverage_must_span_bar_window(tmp_path):
    environment = build_environment(tmp_path, short_action_coverage=True)

    with pytest.raises(ValueError, match="does not span every replay session"):
        append_bundle(environment)


def test_corporate_action_coverage_cannot_cross_the_train_replay_boundary(tmp_path):
    environment = build_environment(tmp_path, extended_action_coverage=True)

    with pytest.raises(ValueError, match="cannot extend beyond.*TRAIN replay interval"):
        append_bundle(environment)


def test_session_complete_segmented_corporate_action_coverage_is_supported(tmp_path):
    environment = build_environment(tmp_path, segmented_action_coverage=True)
    record = append_bundle(environment)

    assert len(record["corporate_action_snapshot_ids"]) == 2
    materialized = environment["bundle"].materialize_research_inputs(record["bundle_id"])
    assert [action.symbol for action in materialized.corporate_actions] == [SECURITY_ID]


def test_bundle_rejects_stale_security_master_evidence(tmp_path):
    environment = build_environment(tmp_path)
    evidence, digest = authenticated(
        environment["sources"],
        uri="https://master.example.invalid/aapl/ticker-change",
        marker="master-ticker-change",
    )
    environment["source_rows"].append(evidence)
    environment["master"].record_event(
        security_id=SECURITY_ID,
        event_type="TICKER_CHANGED",
        ticker="AAPLX",
        prior_ticker="AAPL",
        issuer_name="Apple synthetic issuer",
        exchange_mic="XNYS",
        effective_at="2026-01-02T14:30:00+00:00",
        reported_at="2026-01-01T10:00:00+00:00",
        available_at="2026-01-01T11:00:00+00:00",
        retrieved_at="2026-01-01T12:00:00+00:00",
        recorded_at="2026-01-01T13:00:00+00:00",
        source_uri=evidence["source_uri"],
        source_input_sha256=digest,
        source_locator="$.master-ticker-change",
    )

    with pytest.raises(ValueError, match="current security-master tip"):
        append_bundle(environment)


def test_superseded_action_invalidates_materialization_but_not_audit(tmp_path):
    environment = build_environment(tmp_path)
    record = append_bundle(environment)
    old = environment["action_snapshot"]
    evidence, digest = authenticated(
        environment["sources"],
        uri="https://actions.example.invalid/aapl-corrected",
        marker="actions-corrected",
    )
    environment["actions"].append_snapshot(
        security_id=SECURITY_ID,
        ticker="AAPL",
        covers_from_at=old["covers_from_at"],
        through_at=old["through_at"],
        events=[
            {
                key: value
                for key, value in old["events"][0].items()
                if key
                in {
                    "source_event_id",
                    "event_type",
                    "effective_at",
                    "reported_at",
                    "available_at",
                    "retrieved_at",
                    "recorded_at",
                    "source_locator",
                    "split_ratio",
                }
            }
        ],
        source_uri=evidence["source_uri"],
        source_locator="$.actions-corrected",
        source_payload_sha256=digest,
        synthetic_fixture=True,
        supersedes_snapshot_id=old["snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )

    assert environment["bundle"].verify()[0]["bundle_id"] == record["bundle_id"]
    with pytest.raises(ValueError, match="active corporate-action snapshots"):
        environment["bundle"].materialize_research_inputs(record["bundle_id"])


def test_superseded_bar_invalidates_materialization_but_not_audit(tmp_path):
    environment = build_environment(tmp_path)
    record = append_bundle(environment)
    old = environment["bar_snapshot"]
    raw_fields = {
        "security_id",
        "ticker",
        "session_date",
        "open_at",
        "close_at",
        "effective_at",
        "reported_at",
        "available_at",
        "retrieved_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source_payload_sha256",
        "source_row_locator",
    }
    environment["bars"].append_snapshot(
        calendar_snapshot_id=environment["calendar"]["calendar_snapshot_id"],
        partition_manifest_id=environment["manifest"]["partition_manifest_id"],
        bars=[
            {key: value for key, value in item.items() if key in raw_fields}
            for item in old["bars"]
        ],
        coverage_shape="PER_SECURITY_PIT_INTERVALS",
        source_uri="https://bars.example.invalid/aapl-train-corrected",
        source_locator="$.bars-corrected",
        source_payload_sha256="c" * 64,
        synthetic_fixture=True,
        supersedes_bar_snapshot_id=old["bar_snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )

    assert environment["bundle"].verify()[0]["bundle_id"] == record["bundle_id"]
    with pytest.raises(ValueError, match="active daily-bar snapshot"):
        environment["bundle"].materialize_research_inputs(record["bundle_id"])


def test_authenticated_blob_tampering_invalidates_bundle(tmp_path):
    environment = build_environment(tmp_path)
    append_bundle(environment)
    source = environment["source_rows"][0]
    blob = environment["sources"].blob_directory / source["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")

    with pytest.raises(LedgerIntegrityError, match="Authenticated-source record 1"):
        environment["bundle"].verify()


def test_bundle_ledger_tampering_is_detected(tmp_path):
    environment = build_environment(tmp_path)
    append_bundle(environment)
    record = environment["bundle"].records()[0]
    record["qualified"] = True
    environment["bundle"].path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    environment["bundle"].path.chmod(0o600)

    with pytest.raises(LedgerIntegrityError, match="record 1 is invalid"):
        environment["bundle"].verify()


def test_non_synthetic_and_non_train_paths_are_unavailable(tmp_path):
    environment = build_environment(tmp_path)
    with pytest.raises(ValueError, match="synthetic fixtures only"):
        append_bundle(environment, synthetic_fixture=False)
    assert "validation" not in {
        name.lower() for name in dir(environment["bundle"]) if name.startswith("materialize")
    }


def test_bundle_rechecks_train_partition_instead_of_trusting_bar_label(tmp_path):
    environment = build_environment(tmp_path)
    verified_bars = environment["bars"].verify()
    validation = environment["manifest"]["partitions"][1]
    environment["bars"].verify = lambda: [
        {
            **verified_bars[0],
            "coverage_start": validation["start_session_date"],
            "coverage_end": validation["end_session_date"],
        }
    ]

    with pytest.raises(ValueError, match="wholly inside the pinned TRAIN partition"):
        append_bundle(environment)


def test_bundle_rejects_gap_or_overlap_even_if_action_dependency_regresses(tmp_path):
    environment = build_environment(tmp_path, segmented_action_coverage=True)
    verified_actions = environment["actions"].verify()
    first, second = verified_actions

    environment["actions"].verify = lambda: [
        first,
        {
            **second,
            "covers_from_at": (
                datetime.fromisoformat(first["through_at"]) + timedelta(minutes=1)
            ).isoformat(),
        },
    ]
    with pytest.raises(ValueError, match="contains a gap"):
        append_bundle(environment)

    environment["actions"].verify = lambda: [
        first,
        {**second, "covers_from_at": first["through_at"]},
    ]
    with pytest.raises(ValueError, match="overlaps"):
        append_bundle(environment)


def test_bundle_rejects_ticker_symbol_policy_and_late_source_binding(tmp_path):
    ticker_environment = build_environment(
        tmp_path / "ticker",
        coverage_shape="STRICT_RECTANGLE_CONSTANT_MEMBERSHIP",
    )
    with pytest.raises(ValueError, match="requires interval coverage"):
        append_bundle(ticker_environment)

    late_environment = build_environment(tmp_path / "late")
    verified_sources = late_environment["sources"].verify()
    late_environment["sources"].verify = lambda: [
        {
            **item,
            "recorded_at": (CLOCK + timedelta(minutes=1)).isoformat(),
        }
        for item in verified_sources
    ]
    with pytest.raises(ValueError, match="authenticated after the bundle append"):
        append_bundle(late_environment)


def test_bundle_dependency_lock_blocks_a_real_corporate_action_append(tmp_path):
    environment = build_environment(tmp_path)
    action_lock = environment["actions"].path.with_suffix(
        environment["actions"].path.suffix + ".lock"
    )
    assert action_lock in environment["bundle"]._dependency_locks()
    old = environment["action_snapshot"]
    evidence, digest = authenticated(
        environment["sources"],
        uri="https://actions.example.invalid/aapl-concurrent-correction",
        marker="actions-concurrent-correction",
    )
    started = Event()
    finished = Event()
    errors: list[BaseException] = []

    def contend() -> None:
        try:
            started.set()
            environment["actions"].append_snapshot(
                security_id=SECURITY_ID,
                ticker="AAPL",
                covers_from_at=old["covers_from_at"],
                through_at=old["through_at"],
                events=[
                    {
                        key: value
                        for key, value in old["events"][0].items()
                        if key
                        in {
                            "source_event_id",
                            "event_type",
                            "effective_at",
                            "reported_at",
                            "available_at",
                            "retrieved_at",
                            "recorded_at",
                            "source_locator",
                            "split_ratio",
                        }
                    }
                ],
                source_uri=evidence["source_uri"],
                source_locator="$.actions-concurrent-correction",
                source_payload_sha256=digest,
                synthetic_fixture=True,
                supersedes_snapshot_id=old["snapshot_id"],
                supersession_reason="SOURCE_CORRECTION",
            )
        except BaseException as error:  # surfaced after the synchronization assertion
            errors.append(error)
        finally:
            finished.set()

    with bundle_module._exclusive_locks(environment["bundle"]._dependency_locks()):
        worker = Thread(target=contend)
        worker.start()
        assert started.wait(1)
        assert not finished.wait(0.1)
    assert finished.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert errors == []
    assert len(environment["actions"].verify()) == 2


def test_bundle_dependency_lock_blocks_a_real_calendar_append(tmp_path):
    environment = build_environment(tmp_path)
    record = append_bundle(environment)
    calendar_lock = environment["calendar_ledger"].path.with_suffix(
        environment["calendar_ledger"].path.suffix + ".lock"
    )
    assert calendar_lock in environment["bundle"]._dependency_locks()
    current = environment["calendar"]
    started = Event()
    finished = Event()
    errors: list[BaseException] = []

    def contend() -> None:
        try:
            started.set()
            environment["calendar_ledger"].append_calendar_snapshot(
                sessions=[
                    session(date.fromisoformat(item["session_date"]))
                    for item in current["sessions"]
                ],
                source_uri="https://calendar.example.invalid/xnys-corrected",
                source_locator="$.calendar-corrected",
                source_payload_sha256="b" * 64,
                synthetic_fixture=True,
                supersedes_calendar_snapshot_id=current["calendar_snapshot_id"],
                supersession_reason="SOURCE_CORRECTION",
            )
        except BaseException as error:  # surfaced after the synchronization assertion
            errors.append(error)
        finally:
            finished.set()

    with bundle_module._exclusive_locks(environment["bundle"]._dependency_locks()):
        worker = Thread(target=contend)
        worker.start()
        assert started.wait(1)
        assert not finished.wait(0.1)
    assert finished.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert errors == []
    assert len(environment["calendar_ledger"].verify()) == 3
    assert environment["bundle"].verify()[0]["bundle_id"] == record["bundle_id"]
    with pytest.raises(ValueError, match="active XNYS calendar"):
        environment["bundle"].materialize_research_inputs(record["bundle_id"])
