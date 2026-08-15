from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

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


def test_vectorbt_screen_uses_only_the_sole_preregistered_parameter_set():
    result = vectorbt_fixed_parameter_screen(research_rows())
    assert result["candidate_parameter_count"] == 1
    assert result["parameter_search_allowed"] is False
    assert result["screen_type"].endswith("NOT_OPTIMIZATION")
    assert result["performance_claim_allowed"] is False
    assert len(result["results"]) == 4


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
