from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.guardrailed_backtest import (
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ResearchExemptionDataAttestation,
)
from core.research.conservative_baseline_campaign import SPLITS
from core.research.massive_research_exempt_replay import (
    ACQUISITION_END,
    ACQUISITION_START,
    LIMITATIONS,
    ResearchBar,
    ResearchExemptionLedger,
    _bars_from_captures,
    _capture_payloads,
    _evaluation_slices,
    _require_aligned_test_sessions,
    _require_registered_chain,
    execute_untouched_once,
    register_exemption,
    vectorbt_fixed_parameter_screen,
)


UTC = timezone.utc


def preregistration():
    return {
        "target_basket": ["AAPL", "MSFT", "SPY"],
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "splits": [dict(value) for value in SPLITS],
        "preregistration_id": "HQP-TEST",
        "record_hash": "a" * 64,
    }


def captures():
    return [
        {
            "record_hash": hashlib.sha256(str(index).encode()).hexdigest(),
            "quarantine_capture_id": f"HQCAP-{index:02d}",
        }
        for index in range(36)
    ]


def test_exemption_is_append_only_and_never_upgrades_provider_or_canonical_authority(tmp_path):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    first = register_exemption(
        ledger=ledger,
        preregistration=preregistration(),
        captures=captures(),
        authorized_by="EXPLICIT_TEST_USER",
    )
    second = register_exemption(
        ledger=ledger,
        preregistration=preregistration(),
        captures=captures(),
        authorized_by="EXPLICIT_TEST_USER",
    )
    assert first == second
    assert len(ledger.records()) == 1
    payload = first["payload"]
    assert payload["research_exemption"] is True
    assert payload["provider_evidence"] is False
    assert payload["authenticated_replay_evidence"] is False
    assert payload["canonical_dataset_admitted"] is False
    assert payload["performance_claim_allowed"] is False
    assert "NO_HISTORICAL_AVAILABILITY_PROOF" in LIMITATIONS


def test_exemption_rejects_incomplete_capture_chain(tmp_path):
    with pytest.raises(ValueError, match="complete 36-capture chain"):
        register_exemption(
            ledger=ResearchExemptionLedger(tmp_path / "audit.jsonl"),
            preregistration=preregistration(),
            captures=captures()[:-1],
            authorized_by="EXPLICIT_TEST_USER",
        )


def test_existing_exemption_must_match_the_complete_requested_payload(tmp_path):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    register_exemption(
        ledger=ledger,
        preregistration=preregistration(),
        captures=captures(),
        authorized_by="FIRST_USER",
    )
    with pytest.raises(LedgerIntegrityError, match="differs from request"):
        register_exemption(
            ledger=ledger,
            preregistration=preregistration(),
            captures=captures(),
            authorized_by="SUBSTITUTED_USER",
        )


def test_registered_capture_chain_is_enforced():
    rows = []
    roles = ["TRAIN"] * 21 + ["VALIDATION"] * 6 + ["UNTOUCHED_TEST"] * 9
    for index, role in enumerate(roles):
        rows.append(
            {
                "split_role": role,
                "record_hash": hashlib.sha256(str(index).encode()).hexdigest(),
                "preregistration_id": "HQP-TEST",
                "preregistration_record_hash": "a" * 64,
            }
        )
    exemption = {
        "payload": {
            "capture_chain_final_hash": rows[-1]["record_hash"],
            "preregistration_id": "HQP-TEST",
            "preregistration_record_hash": "a" * 64,
        }
    }
    _require_registered_chain(exemption, rows)
    changed = [dict(row) for row in rows]
    changed[-1]["record_hash"] = "f" * 64
    with pytest.raises(LedgerIntegrityError, match="registered research exemption chain"):
        _require_registered_chain(exemption, changed)
    changed = [dict(row) for row in rows]
    changed[21]["split_role"] = "TRAIN"
    with pytest.raises(LedgerIntegrityError, match="registered research exemption chain"):
        _require_registered_chain(exemption, changed)


def test_unique_ledger_event_is_enforced_inside_append_lock(tmp_path):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    ledger.append("UNTOUCHED_TEST_CONSUMPTION_STARTED", {"sequence": 1}, unique=True)
    with pytest.raises(ValueError, match="exactly once"):
        ledger.append("UNTOUCHED_TEST_CONSUMPTION_STARTED", {"sequence": 2}, unique=True)


def test_ledger_detects_tampering_and_chain_break(tmp_path):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    ledger.append("UNTOUCHED_TEST_RESULT_RECORDED", {"value": "original"})
    path = tmp_path / "audit.jsonl"
    path.chmod(0o600)
    row = json.loads(path.read_text())
    row["payload"]["value"] = "changed"
    path.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="line 1 is invalid"):
        ledger.records()


def test_massive_conversion_records_assumed_close_availability_and_exact_provenance():
    payload = json.dumps(
        {
            "adjusted": False,
            "status": "OK",
            "ticker": "AAPL",
            "resultsCount": 1,
            "results": [
                {
                    "o": 100,
                    "h": 102,
                    "l": 99,
                    "c": 101,
                    "v": 1000,
                    "t": 1754020800000,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    capture = {
        "quarantine_capture_id": "HQCAP-ONE",
        "record_hash": "b" * 64,
        "payload_sha256": digest,
        "symbol": "AAPL",
        "split_role": "TRAIN",
        "request_start": "2025-08-01",
        "request_end": "2025-08-31",
        "retrieved_at": "2026-08-15T12:00:00+00:00",
    }
    rows = _bars_from_captures(
        captures=[capture],
        payloads={"HQCAP-ONE": payload},
        exemption_id="RIXA-TEST",
    )
    assert len(rows) == 1
    material = rows[0].material()
    assert material["available_at"] == rows[0].market_bar.close_at.isoformat()
    assert material["effective_at"] == rows[0].market_bar.close_at.isoformat()
    assert material["retrieved_at"] == capture["retrieved_at"]
    assert material["payload_sha256"] == digest
    assert material["availability_basis"].startswith("EXPLICIT_HUMAN_RESEARCH_ASSUMPTION")

    changed = dict(capture)
    changed["payload_sha256"] = "0" * 64
    with pytest.raises(LedgerIntegrityError, match="payload no longer matches"):
        _bars_from_captures(
            captures=[changed],
            payloads={"HQCAP-ONE": payload},
            exemption_id="RIXA-TEST",
        )

    wrong_symbol = json.loads(payload)
    wrong_symbol["ticker"] = "MSFT"
    wrong_payload = json.dumps(wrong_symbol, separators=(",", ":")).encode()
    changed = dict(capture)
    changed["payload_sha256"] = hashlib.sha256(wrong_payload).hexdigest()
    with pytest.raises(LedgerIntegrityError, match="symbol violates"):
        _bars_from_captures(
            captures=[changed],
            payloads={"HQCAP-ONE": wrong_payload},
            exemption_id="RIXA-TEST",
        )


def test_untouched_and_path_escape_are_rejected_before_blob_read(tmp_path):
    class Store:
        blob_directory = tmp_path / "blobs"

    Store.blob_directory.mkdir()
    with pytest.raises(ValueError, match="one-shot consumption authority"):
        _capture_payloads(
            Store(),
            [{"split_role": "UNTOUCHED_TEST", "blob_relative_path": "unused"}],
            allow_untouched=False,
        )
    outside = tmp_path / "outside.blob"
    outside.write_bytes(b"payload")
    outside.chmod(0o400)
    with pytest.raises(LedgerIntegrityError, match="escaped"):
        _capture_payloads(
            Store(),
            [
                {
                    "split_role": "TRAIN",
                    "blob_relative_path": "../outside.blob",
                    "quarantine_capture_id": "HQCAP-ESCAPE",
                }
            ],
            allow_untouched=False,
        )


def research_rows() -> list[ResearchBar]:
    rows: list[ResearchBar] = []
    start = datetime(2025, 8, 1, 13, 30, tzinfo=UTC)
    for symbol_offset, symbol in enumerate(("AAPL", "MSFT")):
        price = Decimal(100 + symbol_offset * 20)
        for index in range(120):
            opened = start + timedelta(days=index)
            closed = opened + timedelta(hours=6, minutes=30)
            price += Decimal("0.8") if index < 80 else Decimal("-0.5")
            bar = MarketBar(
                symbol=symbol,
                open_at=opened,
                close_at=closed,
                available_at=closed,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + Decimal("0.2"),
                volume=Decimal("1000000"),
            )
            rows.append(
                ResearchBar(
                    market_bar=bar,
                    split_role="TRAIN" if index < 70 else "VALIDATION",
                    capture_id=f"{symbol}-{index}",
                    capture_record_hash="c" * 64,
                    payload_sha256="d" * 64,
                    provider_window_start_at=opened.isoformat(),
                    effective_at=closed.isoformat(),
                    available_at=closed.isoformat(),
                    retrieved_at="2026-08-15T12:00:00+00:00",
                    exemption_id="RIXA-TEST",
                )
            )
    return rows


def test_vectorbt_screen_uses_only_the_sole_preregistered_parameter_set(tmp_path):
    rows = research_rows()
    ledger = ResearchExemptionLedger(tmp_path / "open.jsonl")
    ledger.append("RESEARCH_EXEMPTION_REGISTERED", {"exemption_id": "RIXA-TEST"})
    result = vectorbt_fixed_parameter_screen(
        rows,
        campaign_state_ledger=ledger,
    )
    assert result["candidate_parameter_count"] == 1
    assert result["parameter_search_allowed"] is False
    assert result["screen_type"].endswith("NOT_OPTIMIZATION")
    assert result["performance_claim_allowed"] is False
    assert len(result["results"]) == 4
    aapl = [row for row in rows if row.market_bar.symbol == "AAPL"]
    train = [row for row in aapl if row.split_role == "TRAIN"]
    expected = train[-1].market_bar.open / train[0].market_bar.close - Decimal("1")
    assert Decimal(result["results"][0]["benchmark_buy_hold_return"]) == expected


def test_research_attestation_cannot_be_forged_and_grants_no_authority():
    values = {
        "source_id": "RESEARCH_EXEMPTION:RIXA-TEST",
        "source_content_sha256": "a" * 64,
        "validation_receipt_sha256": "b" * 64,
        "derivation_policy_version": "test-v1",
        "evidence_role_hashes": (("ASSUMED_INDEX_MEMBERSHIP", "c" * 64),),
        "exemption_id": "RIXA-TEST",
        "exemption_record_sha256": "d" * 64,
    }
    with pytest.raises(ValueError, match="explicit factory"):
        ResearchExemptionDataAttestation(**values)
    issued = ResearchExemptionDataAttestation._from_explicit_research_exemption(**values)
    assert issued.provider_evidence is False
    assert issued.performance_claim_allowed is False
    GuardrailedBacktestEngine(
        config=BacktestConfig(initial_cash=Decimal("100000")),
        fee_schedule=ExchangeFeeSchedule(
            "TEST", (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),)
        ),
        data_attestation=issued,
    )
    class AttestationSubclass(ResearchExemptionDataAttestation):
        pass

    subclass = object.__new__(AttestationSubclass)
    for name in issued.__dataclass_fields__:
        object.__setattr__(subclass, name, getattr(issued, name))
    with pytest.raises(ValueError, match="unsupported authority type"):
        GuardrailedBacktestEngine(
            config=BacktestConfig(initial_cash=Decimal("100000")),
            fee_schedule=ExchangeFeeSchedule(
                "TEST", (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),)
            ),
            data_attestation=subclass,
        )


def test_preregistered_purge_and_embargo_are_applied_to_future_evaluations():
    admitted = research_rows()
    source = [row for row in admitted if row.market_bar.symbol == "AAPL"][-5:]
    test = [replace(row, split_role="UNTOUCHED_TEST") for row in source]
    warmup, evaluation = _evaluation_slices(
        admitted_bars=admitted,
        test_bars=test,
        symbol="AAPL",
        protocol={
            "warmup_observations": 50,
            "purge_observations": 1,
            "embargo_observations": 1,
        },
    )
    aapl_prior = [row for row in admitted if row.market_bar.symbol == "AAPL"]
    assert len(warmup) == 50
    assert warmup[-1] == aapl_prior[-2]
    assert evaluation == test[1:]


def test_candidate_and_benchmark_test_sessions_must_align():
    rows = research_rows()
    template = [row for row in rows if row.market_bar.symbol == "AAPL"][:3]
    test: list[ResearchBar] = []
    for symbol in ("AAPL", "MSFT", "SPY"):
        for row in template:
            market = replace(row.market_bar, symbol=symbol)
            test.append(replace(row, market_bar=market, split_role="UNTOUCHED_TEST"))
    _require_aligned_test_sessions(test)
    changed = list(test)
    changed[-1] = replace(
        changed[-1],
        market_bar=replace(
            changed[-1].market_bar,
            open_at=changed[-1].market_bar.open_at + timedelta(days=1),
            close_at=changed[-1].market_bar.close_at + timedelta(days=1),
            available_at=changed[-1].market_bar.available_at + timedelta(days=1),
        ),
    )
    with pytest.raises(LedgerIntegrityError, match="sessions do not align"):
        _require_aligned_test_sessions(changed)


def test_untouched_consumption_refuses_a_second_attempt_before_store_access(tmp_path):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    exemption = register_exemption(
        ledger=ledger,
        preregistration=preregistration(),
        captures=captures(),
        authorized_by="EXPLICIT_TEST_USER",
    )
    ledger.append(
        "UNTOUCHED_TEST_CONSUMPTION_STARTED",
        {"exemption_id": exemption["payload"]["exemption_id"]},
    )
    with pytest.raises(ValueError, match="already been consumed"):
        execute_untouched_once(
            ledger=ledger,
            store=object(),
            admitted_bars=(),
            vectorbt_screen={},
        )
