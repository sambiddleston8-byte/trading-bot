from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest

from core.guardrailed_backtest import ACTION_ENTER_LONG, ACTION_EXIT_LONG, MarketBar
from core.research.ensemble_signal_adapter import (
    EnsembleSignalAdapter,
    PITRiskRegimeSpecialistBot,
    PITTechnicalSpecialistBot,
    ensemble_signal_parameters,
)
from core.research.sec_form4_insider_specialist import (
    OFFICIAL_SOURCE_URLS,
    LEGACY_ADMITTED_SCHEMA_VERSION,
    SECForm4InsiderSpecialistBot,
    _role_category,
    normalize_form4_train_sources,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    SpecialistSignal,
)


UTC = timezone.utc
RETRIEVED = "2026-08-17T08:00:00+00:00"


def _tsv(rows):
    fields = list(rows[0])
    return ("\t".join(fields) + "\n" + "".join("\t".join(row[field] for field in fields) + "\n" for row in rows)).encode()


def _archive(submissions, owners, transactions):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SUBMISSION.tsv", _tsv(submissions))
        archive.writestr("REPORTINGOWNER.tsv", _tsv(owners))
        archive.writestr("NONDERIV_TRANS.tsv", _tsv(transactions))
    return stream.getvalue()


def _submission(accession, symbol, cik):
    return {
        "ACCESSION_NUMBER": accession,
        "FILING_DATE": "17-OCT-2024",
        "PERIOD_OF_REPORT": "15-OCT-2024",
        "DOCUMENT_TYPE": "4",
        "ISSUERCIK": cik,
        "ISSUERNAME": f"{symbol} INC",
        "ISSUERTRADINGSYMBOL": symbol,
    }


def _owner(accession, cik, relationship, title):
    return {
        "ACCESSION_NUMBER": accession,
        "RPTOWNERCIK": cik,
        "RPTOWNERNAME": "REDACTED AFTER NORMALIZATION",
        "RPTOWNER_RELATIONSHIP": relationship,
        "RPTOWNER_TITLE": title,
    }


def _transaction(accession, date, code, acquired):
    return {
        "ACCESSION_NUMBER": accession,
        "NONDERIV_TRANS_SK": "1",
        "SECURITY_TITLE": "Common Stock",
        "TRANS_DATE": date,
        "TRANS_FORM_TYPE": "4",
        "TRANS_CODE": code,
        "TRANS_SHARES": "100",
        "TRANS_PRICEPERSHARE": "200",
        "TRANS_ACQUIRED_DISP_CD": acquired,
        "DIRECT_INDIRECT_OWNERSHIP": "D",
    }


def _submissions_json(cik, rows):
    return json.dumps(
        {
            "cik": int(cik),
            "filings": {
                "recent": {
                    "accessionNumber": [row[0] for row in rows],
                    "form": ["4" for _ in rows],
                    "acceptanceDateTime": [row[1] for row in rows],
                }
            },
        }
    ).encode()


def artifact():
    boundary = "0000320193-24-000099"
    aapl_buy = "0000320193-24-000100"
    aapl_sell = "0000320193-25-000010"
    msft_buy = "0000789019-24-000200"
    archives = {
        "2024Q4": _archive(
            [
                {**_submission(boundary, "AAPL", "320193"), "FILING_DATE": "01-OCT-2024"},
                _submission(aapl_buy, "AAPL", "320193"),
                _submission(msft_buy, "MSFT", "789019"),
            ],
            [
                _owner(boundary, "110", "DIRECTOR", "Director"),
                _owner(aapl_buy, "111", "OFFICER", "Chief Executive Officer"),
                _owner(msft_buy, "222", "DIRECTOR", "Director"),
            ],
            [
                _transaction(boundary, "30-SEP-2024", "S", "D"),
                _transaction(aapl_buy, "15-OCT-2024", "P", "A"),
                _transaction(msft_buy, "20-NOV-2024", "P", "A"),
            ],
        ),
        "2025Q1": _archive(
            [_submission(aapl_sell, "AAPL", "320193")],
            [_owner(aapl_sell, "333", "DIRECTOR", "Director")],
            [_transaction(aapl_sell, "10-JAN-2025", "S", "D")],
        ),
    }
    issuer = {
        "AAPL": _submissions_json(
            "320193",
            [
                (boundary, "2024-09-30T22:00:00.000Z"),
                (aapl_buy, "2024-10-17T20:15:30.000Z"),
                (aapl_sell, "2025-01-11T18:05:00.000Z"),
            ],
        ),
        "MSFT": _submissions_json(
            "789019", [(msft_buy, "2024-11-21T19:00:00.000Z")]
        ),
    }
    return normalize_form4_train_sources(
        quarter_archives=archives,
        issuer_submissions=issuer,
        retrieved_at=RETRIEVED,
        source_urls=OFFICIAL_SOURCE_URLS,
    )


def test_normalizer_binds_exact_acceptance_and_omits_owner_name():
    value = artifact()
    assert value["partition_role"] == "TRAIN"
    assert value["validation_data_read"] is False
    assert value["untouched_test_included"] is False
    assert len(value["records"]) == 3
    assert all(record["accession_number"] != "0000320193-24-000099" for record in value["records"])
    assert all(
        record["provenance"]["role_taxonomy_version"]
        == "whole-token-executive-role-v2"
        for record in value["records"]
    )
    first = value["records"][0]
    assert first["available_at"] == first["reported_at"]
    assert first["available_at"] in {
        "2024-10-17T20:15:30+00:00",
        "2024-11-21T19:00:00+00:00",
        "2025-01-11T18:05:00+00:00",
    }
    assert "RPTOWNERNAME" not in json.dumps(value)
    assert all(record["observation_cutoff_at"] == record["available_at"] for record in value["records"])


def test_role_taxonomy_does_not_promote_vice_presidents_by_substring():
    assert _role_category("OFFICER", "Senior Vice President") == (
        "OTHER_OFFICER", Decimal("1.25")
    )
    assert _role_category("OFFICER", "President") == (
        "SENIOR_EXECUTIVE", Decimal("1.5")
    )
    assert _role_category("TENPERCENTOWNER", "") == (
        "TEN_PERCENT_OWNER", Decimal("0.75")
    )
    assert _role_category("DIRECTOR", "Chairman of the Board") == (
        "SENIOR_EXECUTIVE", Decimal("1.5")
    )
    assert _role_category("OFFICER", "Chairman & C.E.O.") == (
        "SENIOR_EXECUTIVE", Decimal("1.5")
    )
    assert _role_category("DIRECTOR", "Vice Chairman") == (
        "DIRECTOR", Decimal("1")
    )
    assert _role_category("OFFICER", "Pres.") == (
        "SENIOR_EXECUTIVE", Decimal("1.5")
    )
    assert _role_category("10% OWNER", "") == (
        "TEN_PERCENT_OWNER", Decimal("0.75")
    )


def test_specialist_single_tick_and_vector_scores_reconcile_and_stay_bounded():
    value = artifact()
    specialist = SECForm4InsiderSpecialistBot(
        value, expected_sha256=value["artifact_sha256"]
    )
    positive_at = datetime(2024, 12, 1, 21, 1, tzinfo=UTC)
    negative_at = datetime(2025, 1, 20, 21, 1, tzinfo=UTC)
    positive = specialist.score_tick("AAPL", decision_at=positive_at)
    negative = specialist.score_tick("AAPL", decision_at=negative_at)
    neutral = specialist.score_tick("SPY", decision_at=negative_at)
    assert Decimal("0") < positive.score <= Decimal("1")
    assert Decimal("-1") <= negative.score < Decimal("0")
    assert neutral.score == 0
    assert neutral.reason == "NO_INSIDER_COVERAGE_FOR_SYMBOL"
    frame = specialist.score_frame(
        pd.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "SPY"],
                "decision_at": [positive_at, negative_at, negative_at],
            }
        )
    )
    assert frame["score"].tolist() == [
        str(positive.score), str(negative.score), "0"
    ]


def test_specialist_rejects_post_decision_and_tampered_evidence():
    value = artifact()
    specialist = SECForm4InsiderSpecialistBot(
        value, expected_sha256=value["artifact_sha256"]
    )
    incomplete = specialist.score_tick(
        "AAPL", decision_at="2024-10-17T20:15:29+00:00"
    )
    assert incomplete.score == -1
    assert incomplete.reason == "INSUFFICIENT_TRAILING_LOOKBACK"
    before = specialist.score_tick(
        "AAPL", decision_at="2025-01-11T18:04:59+00:00"
    )
    assert before.score == 0
    tampered = json.loads(json.dumps(value))
    tampered["records"][0]["available_at"] = "2024-10-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="SHA-256"):
        SECForm4InsiderSpecialistBot(
            tampered, expected_sha256=value["artifact_sha256"]
        )


def test_legacy_admitted_schema_cannot_claim_v2_role_taxonomy():
    legacy = json.loads(json.dumps(artifact()))
    legacy["schema_version"] = LEGACY_ADMITTED_SCHEMA_VERSION
    for record in legacy["records"]:
        record["provenance"].pop("role_taxonomy_version")
        material = {key: value for key, value in record.items() if key != "record_sha256"}
        record["record_sha256"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    legacy.pop("artifact_sha256")
    legacy["artifact_sha256"] = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    SECForm4InsiderSpecialistBot(
        legacy, expected_sha256=legacy["artifact_sha256"]
    )
    forged = json.loads(json.dumps(legacy))
    forged["records"][0]["provenance"]["role_taxonomy_version"] = (
        "whole-token-executive-role-v2"
    )
    record = forged["records"][0]
    material = {key: value for key, value in record.items() if key != "record_sha256"}
    record["record_sha256"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    forged.pop("artifact_sha256")
    forged["artifact_sha256"] = hashlib.sha256(
        json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="legacy Form 4 evidence"):
        SECForm4InsiderSpecialistBot(
            forged, expected_sha256=forged["artifact_sha256"]
        )


def _signal(name, score):
    return SpecialistSignal(
        specialist_id=name,
        specialist_version=ExecutiveAggregatorBot.SPECIALIST_VERSIONS[name],
        symbol="AAPL",
        decision_at="2025-01-15T21:01:00+00:00",
        score=Decimal(score),
        evidence_count=1,
        evidence_sha256="a" * 64,
        reason="TEST",
    )


def test_executive_aggregator_requires_isolated_aligned_bounded_signals():
    result = ExecutiveAggregatorBot().aggregate(
        {
            "TECHNICAL": _signal("TECHNICAL", "1"),
            "RISK_REGIME": _signal("RISK_REGIME", "1"),
            "SEC_FORM4_INSIDER": _signal("SEC_FORM4_INSIDER", "-0.5"),
        },
        decision_at="2025-01-15T21:01:00+00:00",
    )
    assert result.score == Decimal("0.70")
    with pytest.raises(ValueError, match="exact specialist set"):
        ExecutiveAggregatorBot().aggregate(
            {"TECHNICAL": _signal("TECHNICAL", "1")},
            decision_at="2025-01-15T21:01:00+00:00",
        )
    frame = ExecutiveAggregatorBot().aggregate_frame(
        pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "decision_at": ["2025-01-15T21:01:00+00:00"],
                "TECHNICAL": [_signal("TECHNICAL", "1")],
                "RISK_REGIME": [_signal("RISK_REGIME", "1")],
                "SEC_FORM4_INSIDER": [
                    _signal("SEC_FORM4_INSIDER", "-0.5")
                ],
            }
        )
    )
    assert frame["score"].tolist() == ["0.7"]


def test_technical_and_risk_specialists_expose_vectorized_causal_interfaces():
    history = _bars()
    technical_bot = PITTechnicalSpecialistBot(_BullishConsumer())
    tick_technical = technical_bot.score_tick("AAPL", bar=history[-1])
    technical = technical_bot.score_frame(
        pd.DataFrame({"symbol": ["AAPL"], "bar": [history[-1]]})
    )
    assert technical["score"].tolist() == [str(tick_technical.score)]
    risk_bot = PITRiskRegimeSpecialistBot()
    tick_risk = risk_bot.score_tick(
        "AAPL",
        history=history,
        admitted_atr=Decimal("2"),
        parameters=ensemble_signal_parameters(),
    )
    risk = risk_bot.score_frame(
        pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "history": [history],
                "admitted_atr": ["2"],
                "parameters": [ensemble_signal_parameters()],
            }
        )
    )
    assert risk["score"].tolist() == [str(tick_risk.score)]


class _BullishConsumer:
    def consume_if_available(self, symbol, *, effective_at, decision_at):
        return SimpleNamespace(
            values={"sma_20": "110", "sma_50": "100", "momentum_20": "0.1", "atr_14": "2"},
            record_sha256=(symbol[0].lower() * 64),
        )


class _FixedInsider:
    def __init__(self, score, reason="TEST"):
        self.score = Decimal(score)
        self.reason = reason

    def score_tick(self, symbol, *, decision_at):
        return SpecialistSignal(
            specialist_id="SEC_FORM4_INSIDER",
            specialist_version=ExecutiveAggregatorBot.SPECIALIST_VERSIONS[
                "SEC_FORM4_INSIDER"
            ],
            symbol=symbol,
            decision_at=decision_at.isoformat(),
            score=self.score,
            evidence_count=1,
            evidence_sha256="f" * 64,
            reason=self.reason,
        )


def _bars():
    start = datetime(2024, 10, 1, 13, 30, tzinfo=UTC)
    result = []
    for index in range(40):
        opened = start + timedelta(days=index)
        close = Decimal("100") + Decimal(index) / Decimal("10")
        result.append(
            MarketBar(
                "AAPL",
                opened,
                opened + timedelta(hours=6, minutes=30),
                opened + timedelta(hours=6, minutes=31),
                close,
                close + Decimal("1"),
                close - Decimal("1"),
                close,
                Decimal("1000000"),
            )
        )
    return result


def test_ensemble_adapter_supports_single_tick_entry_and_insider_veto():
    history = _bars()
    permitted = EnsembleSignalAdapter(
        _BullishConsumer(),
        insider_specialist=_FixedInsider("0"),
        liquidation_signal_at=history[-1].close_at + timedelta(days=1),
    )
    vetoed = EnsembleSignalAdapter(
        _BullishConsumer(),
        insider_specialist=_FixedInsider("-1"),
        liquidation_signal_at=history[-1].close_at + timedelta(days=1),
    )
    incomplete = EnsembleSignalAdapter(
        _BullishConsumer(),
        insider_specialist=_FixedInsider(
            "-1", reason="INSUFFICIENT_TRAILING_LOOKBACK"
        ),
        liquidation_signal_at=history[-1].close_at + timedelta(days=1),
    )
    parameters = ensemble_signal_parameters()
    assert permitted.decide("AAPL", history, parameters) == ACTION_ENTER_LONG
    assert vetoed.decide("AAPL", history, parameters) == ACTION_EXIT_LONG
    assert incomplete.decide("AAPL", history, parameters) == ACTION_EXIT_LONG
    assert vetoed.diagnostics()["insider_veto_suppressions"] == 1
    assert incomplete.diagnostics()["insider_lookback_suppressions"] == 1
