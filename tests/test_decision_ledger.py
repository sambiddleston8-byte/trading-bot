import json

import pytest

from core.decision_ledger import (
    GENESIS_HASH,
    InvestmentDecision,
    InvestmentDecisionLedger,
    LedgerIntegrityError,
    ModelVersion,
)


def append_example(ledger, ticker="NVDA", decision_id=None):
    return ledger.append(
        ticker=ticker,
        decision="BUY",
        decision_payload={"confidence": 72, "expected_return_12m": 0.18},
        model_versions=[
            ModelVersion(component="portfolio", version="0.7"),
            ModelVersion(
                component="investment_committee",
                version="2.7",
                provider="openai",
                model="test-model",
                prompt_version="committee-v1",
            ),
        ],
        data_as_of="2026-08-11T12:00:00+00:00",
        portfolio_version="PORT-20260811-001",
        git_revision="abc123",
        decided_at="2026-08-11T12:01:00+00:00",
        decision_id=decision_id,
    )


def test_append_creates_complete_record_only_decision(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    record = append_example(ledger, decision_id="DEC-001")

    assert record["decision_id"] == "DEC-001"
    assert record["ticker"] == "NVDA"
    assert record["execution_mode"] == "RECORD_ONLY"
    assert record["previous_hash"] == GENESIS_HASH
    assert record["git_revision"] == "abc123"
    assert record["model_versions"][1]["prompt_version"] == "committee-v1"
    assert ledger.verify() == [record]


def test_records_are_appended_and_hash_chained(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    first = append_example(ledger, decision_id="DEC-001")
    second = append_example(ledger, ticker="AAPL", decision_id="DEC-002")

    assert second["previous_hash"] == first["record_hash"]
    assert [item["decision_id"] for item in ledger.verify()] == ["DEC-001", "DEC-002"]


def test_tampering_is_detected_before_another_append(tmp_path):
    path = tmp_path / "decisions.jsonl"
    ledger = InvestmentDecisionLedger(path)
    append_example(ledger, decision_id="DEC-001")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["decision"] = "SELL"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="modified"):
        ledger.verify()
    with pytest.raises(LedgerIntegrityError):
        append_example(ledger, decision_id="DEC-002")


def test_duplicate_decision_id_is_rejected(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    append_example(ledger, decision_id="DEC-001")

    with pytest.raises(LedgerIntegrityError, match="already exists"):
        append_example(ledger, decision_id="DEC-001")


def test_canonical_adapter_keeps_every_roadmap_field(tmp_path):
    decision = InvestmentDecision.from_canonical(
        {
            "ticker": "nvda",
            "decision": "BUY",
            "decision_reason": "Demand and cash generation support the base case.",
            "current_price": 100.0,
            "expected_return": 0.25,
            "valuation_horizon_years": 2,
            "master_decision": {"confidence": 74.0},
            "monitoring_conditions": ["Revenue growth falls below 20%."],
            "market_signals": {"risk_components": ["High volatility"]},
        },
        holding={"weight": 0.08},
    )
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    record = ledger.append_investment_decision(
        decision,
        model_versions=[ModelVersion(component="portfolio", version="0.7")],
        data_as_of="2026-08-11T12:00:00+00:00",
        git_revision="abc123",
        decision_id="DEC-STRICT-001",
    )

    payload = record["decision_payload"]
    assert decision.ticker == "NVDA"
    assert payload["price_at_decision"] == 100.0
    assert payload["target_portfolio_weight"] == 0.08
    assert payload["expected_return_horizon_days"] == 730
    assert payload["thesis_invalidation_conditions"] == [
        "Revenue growth falls below 20%."
    ]
    assert set(payload) == {
        "price_at_decision",
        "target_portfolio_weight",
        "expected_return",
        "expected_return_horizon_days",
        "confidence",
        "bull_case",
        "base_case",
        "bear_case",
        "catalysts",
        "risks",
        "investment_thesis",
        "thesis_invalidation_conditions",
        "expected_catalyst_window",
        "reassessment_date",
        "thesis_expiry_date",
    }
