from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, InvestmentDecisionLedger, LedgerIntegrityError
from core.research import ActiveResearchInputManifestLedger
from core.research.point_in_time_inputs import REQUIRED_INPUT_FAMILIES


def decision_ledger(tmp_path):
    book = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    book.append(
        ticker="NVDA",
        decision="BUY",
        decision_payload={"expected_return": 0.20},
        model_versions=[{"component": "research", "version": "1.3"}],
        data_as_of="2026-08-12T10:00:00+00:00",
        portfolio_version="PORT-1",
        git_revision="abc123",
        decided_at="2026-08-12T10:01:00+00:00",
        decision_id="DEC-1",
    )
    return book


def ledger(tmp_path):
    return ActiveResearchInputManifestLedger(
        tmp_path / "research_inputs.jsonl", decision_ledger(tmp_path)
    )


def inputs():
    return {
        family: {
            "status": "AVAILABLE",
            "provider": f"TEST_{family.upper()}",
            "dataset_or_endpoint": f"dataset-{family}",
            "effective_at": "2026-08-11T20:00:00+00:00",
            "publicly_available_at": "2026-08-11T21:00:00+00:00",
            "retrieved_at": "2026-08-12T09:00:00+00:00",
            "source_uri": f"https://evidence.example/{family}",
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.{family}",
        }
        for index, family in enumerate(REQUIRED_INPUT_FAMILIES, start=1)
    }


def record(book, **overrides):
    values = {
        "decision_id": "DEC-1",
        "canonical_research_sha256": "a" * 64,
        "canonical_research_generated_at": "2026-08-12T09:30:00+00:00",
        "inputs": inputs(),
        "recorded_by": "RESEARCH_PIPELINE",
        "recorded_at": "2026-08-12T10:02:00+00:00",
    }
    values.update(overrides)
    return book.record(**values)


def rewrite(path, **changes):
    from core.research import point_in_time_inputs as module

    value = json.loads(path.read_text())
    value.update(changes)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_records_complete_point_in_time_inputs_for_active_route(tmp_path):
    book = ledger(tmp_path)
    value = record(book)

    assert value["record_type"] == "ACTIVE_RESEARCH_POINT_IN_TIME_INPUT_MANIFEST"
    assert value["status"] == "COMPLETE_POINT_IN_TIME_PROVENANCE"
    assert value["decision_record_hash"] == book.decision_ledger.verify()[0]["record_hash"]
    assert set(value["available_input_families"]) == set(REQUIRED_INPUT_FAMILIES)
    assert value["missing_input_families"] == []
    assert value["point_in_time_provenance_complete"] is True
    assert value["route"] == (
        "INVESTMENT_RESEARCH_PIPELINE_TO_CANONICAL_CONTRACT_TO_MASTER_DECISION"
    )
    assert value["forecast_validated"] is False
    assert value["methodology_gate_cleared"] is False
    assert value["broker_submission_enabled"] is False
    assert value["learning_eligible"] is False
    assert value["live_trading_enabled"] is False
    assert value["previous_hash"] == GENESIS_HASH
    assert book.verify() == [value]


def test_missing_input_is_explicit_and_manifest_stays_incomplete(tmp_path):
    book = ledger(tmp_path)
    evidence = inputs()
    evidence["analyst_estimates"] = {
        "status": "MISSING",
        "missing_reason": "No point-in-time estimate archive was available.",
    }
    value = record(book, inputs=evidence)
    assert value["status"] == "INCOMPLETE_PROVENANCE"
    assert value["missing_input_families"] == ["analyst_estimates"]
    assert value["point_in_time_provenance_complete"] is False


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda value: value.pop("market_price"), "exactly"),
        (
            lambda value: value["market_price"].update(status=True),
            "AVAILABLE or MISSING",
        ),
        (
            lambda value: value["market_price"].update(
                effective_at="2026-08-11T22:00:00+00:00"
            ),
            "postdate public availability",
        ),
        (
            lambda value: value["market_price"].update(
                retrieved_at="2026-08-11T20:30:00+00:00"
            ),
            "predate public availability",
        ),
        (
            lambda value: value["market_price"].update(
                retrieved_at="2026-08-12T09:31:00+00:00"
            ),
            "after canonical generation",
        ),
        (
            lambda value: value["market_price"].update(source_input_sha256="bad"),
            "SHA-256",
        ),
        (
            lambda value: value["market_price"].update(
                source_uri="http://evidence.example/price"
            ),
            "HTTPS",
        ),
        (
            lambda value: value["market_price"].update(
                source_input_sha256=value["financial_statements"]["source_input_sha256"],
                source_locator=value["financial_statements"]["source_locator"],
            ),
            "distinct source evidence location",
        ),
    ],
)
def test_lookahead_invalid_or_ambiguous_input_evidence_fails_closed(
    tmp_path, mutate, fragment
):
    book = ledger(tmp_path)
    evidence = inputs()
    mutate(evidence)
    with pytest.raises(ValueError, match=fragment):
        record(book, inputs=evidence)
    assert book.records() == []


def test_one_source_can_pin_distinct_input_locations(tmp_path):
    book = ledger(tmp_path)
    evidence = inputs()
    evidence["market_price"]["source_input_sha256"] = evidence[
        "financial_statements"
    ]["source_input_sha256"]
    value = record(book, inputs=evidence)
    assert value["inputs"]["market_price"]["source_locator"] != value["inputs"][
        "financial_statements"
    ]["source_locator"]


def test_manifest_requires_verified_decision_and_temporal_identity(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(ValueError, match="verified investment decision"):
        record(book, decision_id="DEC-MISSING")
    with pytest.raises(ValueError, match="canonical generation"):
        record(book, canonical_research_generated_at="2026-08-12T10:00:01+00:00")
    with pytest.raises(ValueError, match="predate the investment decision"):
        record(book, recorded_at="2026-08-12T10:00:30+00:00")


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "INCOMPLETE_PROVENANCE"),
        ("point_in_time_provenance_complete", False),
        ("forecast_validated", True),
        ("methodology_gate_cleared", True),
        ("broker_submission_enabled", True),
        ("learning_eligible", True),
        ("live_trading_enabled", True),
    ],
)
def test_rehashed_tampering_cannot_create_authority(tmp_path, field, value):
    book = ledger(tmp_path)
    record(book)
    rewrite(book.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        book.verify()


def test_only_one_manifest_can_be_bound_to_a_decision(tmp_path):
    book = ledger(tmp_path)
    record(book)
    with pytest.raises(LedgerIntegrityError, match="already has"):
        record(book, canonical_research_sha256="b" * 64)


def test_concurrent_identical_record_is_idempotent(tmp_path):
    book = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: record(book), range(2)))
    assert values[0]["manifest_id"] == values[1]["manifest_id"]
    assert len(book.verify()) == 1
