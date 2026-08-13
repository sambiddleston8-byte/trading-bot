from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import EffectivenessCohortAssignmentLedger


OUTCOME = {
    "result_id": "CRRET-1",
    "record_hash": "relative-hash",
    "asset_outcome_result_id": "FHRT-1",
    "decision_id": "DEC-1",
    "ticker": "AAA",
    "portfolio_version": "PORT-1",
    "horizon": "1_MONTH",
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "valuation", "version": "1.0"}],
    "calculated_at": "2025-02-03T18:00:00+00:00",
}
ASSET = {"result_id": "FHRT-1", "prediction_pair_id": "PAIR-1"}
PAIR = {
    "pair_id": "PAIR-1",
    "record_hash": "pair-hash",
    "decided_at": "2025-01-02T12:00:00+00:00",
}


class Stub:
    def __init__(self, values):
        self.values = values

    def verify(self):
        return self.values


def ledger(tmp_path, outcomes=None):
    pairs = Stub([PAIR])
    assets = Stub([ASSET])
    assets.prediction_pair_ledger = pairs
    relative = Stub([OUTCOME] if outcomes is None else outcomes)
    relative.asset_outcome_ledger = assets
    return EffectivenessCohortAssignmentLedger(tmp_path / "cohorts.jsonl", relative)


def labels(*, observed_at="2025-01-02T11:00:00+00:00"):
    names = (
        "market_regime",
        "sector",
        "company_size",
        "valuation_regime",
        "volatility_regime",
        "signal",
    )
    return {
        name: {
            "label": f"{name}-label",
            "taxonomy_name": f"{name}-taxonomy",
            "taxonomy_version": "1.0",
            "effective_at": "2025-01-01T00:00:00+00:00",
            "observed_at": observed_at,
            "source_uri": f"https://evidence.example/{name}",
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.{name}",
        }
        for index, name in enumerate(names, start=1)
    }


def assign(book, **overrides):
    values = {
        "relative_return_result_id": "CRRET-1",
        "labels": labels(),
        "recorded_at": "2025-02-03T19:00:00+00:00",
    }
    values.update(overrides)
    return book.assign(**values)


def rewrite(path, **changes):
    from core.performance import effectiveness_cohort as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_assigns_all_roadmap_dimensions_to_complete_relative_outcome(tmp_path):
    book = ledger(tmp_path)
    record = assign(book)

    assert record["record_type"] == "EVIDENCE_PINNED_EFFECTIVENESS_COHORT_ASSIGNMENT"
    assert record["relative_return_record_hash"] == "relative-hash"
    assert record["prediction_pair_record_hash"] == "pair-hash"
    assert record["investment_horizon"] == "1_MONTH"
    assert record["strategy_version"] == "strategy-v1"
    assert record["model_versions"] == [{"component": "valuation", "version": "1.0"}]
    assert set(record["labels"]) == {
        "market_regime", "sector", "company_size", "valuation_regime",
        "volatility_regime", "signal",
    }
    assert record["all_labels_available_by_decision"] is True
    assert record["effectiveness_claim_eligible"] is True
    assert record["effectiveness_calculated"] is False
    assert record["learning_eligible"] is False
    assert record["order_submission_enabled"] is False
    assert record["live_trading_enabled"] is False
    assert record["previous_hash"] == GENESIS_HASH
    assert book.verify() == [record]


def test_backfilled_label_is_preserved_but_not_claim_eligible(tmp_path):
    book = ledger(tmp_path)
    values = labels()
    values["market_regime"]["observed_at"] = "2025-02-03T18:30:00+00:00"
    record = assign(book, labels=values)

    assert record["labels"]["market_regime"]["availability"] == "BACKFILLED_AFTER_DECISION"
    assert record["all_labels_available_by_decision"] is False
    assert record["effectiveness_claim_eligible"] is False


def test_missing_outcome_fails_closed_without_appending(tmp_path):
    book = ledger(tmp_path, outcomes=[])
    result = assign(book)
    assert result["status"] == "NOT_ASSIGNABLE"
    assert result["record_appended"] is False
    assert result["live_trading_enabled"] is False
    assert book.records() == []


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda value: value.pop("signal"), "exactly"),
        (
            lambda value: value["market_regime"].update(source_uri="http://example.test/a"),
            "HTTPS",
        ),
        (
            lambda value: value["market_regime"].update(source_input_sha256="bad"),
            "SHA-256",
        ),
        (
            lambda value: value["market_regime"].update(
                effective_at="2025-02-04T00:00:00+00:00"
            ),
            "postdate",
        ),
        (
            lambda value: value["market_regime"].update(
                observed_at="2024-12-31T00:00:00+00:00"
            ),
            "predate effective_at",
        ),
        (
            lambda value: value["sector"].update(
                source_input_sha256=value["market_regime"]["source_input_sha256"],
                source_locator=value["market_regime"]["source_locator"],
            ),
            "distinct source evidence location",
        ),
    ],
)
def test_invalid_or_reused_evidence_fails_closed(tmp_path, mutate, fragment):
    book = ledger(tmp_path)
    values = labels()
    mutate(values)
    with pytest.raises(ValueError, match=fragment):
        assign(book, labels=values)
    assert book.records() == []


def test_tampering_is_detected_even_when_hash_is_recomputed(tmp_path):
    book = ledger(tmp_path)
    assign(book)
    rewrite(book.path, effectiveness_claim_eligible=False)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        book.verify()


def test_one_source_can_support_distinct_explicit_locations(tmp_path):
    book = ledger(tmp_path)
    values = labels()
    values["sector"]["source_input_sha256"] = values["market_regime"][
        "source_input_sha256"
    ]
    record = assign(book, labels=values)
    assert record["labels"]["sector"]["source_locator"] == "$.sector"


def test_only_one_assignment_can_exist_for_an_outcome(tmp_path):
    book = ledger(tmp_path)
    assign(book)
    changed = labels()
    changed["signal"].update(label="different", source_input_sha256="f" * 64)
    with pytest.raises(LedgerIntegrityError, match="already has"):
        assign(book, labels=changed, recorded_at="2025-02-03T19:01:00+00:00")


def test_concurrent_identical_assignment_is_idempotent(tmp_path):
    book = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: assign(book), range(2)))
    assert records[0]["cohort_assignment_id"] == records[1]["cohort_assignment_id"]
    assert len(book.verify()) == 1
