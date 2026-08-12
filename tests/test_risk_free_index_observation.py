from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import RiskFreeIndexObservationLedger


def ledger(tmp_path):
    return RiskFreeIndexObservationLedger(tmp_path / "sofr_index.jsonl")


def observe(item, **overrides):
    value_date = overrides.pop("value_date", "2025-02-03")
    index_value = overrides.pop("index_value", "1.17345678")
    revision = overrides.pop("revisionIndicator", "")
    source_payload = json.dumps(
        {
            "refRates": [
                {
                    "effectiveDate": value_date,
                    "type": "SOFRAI",
                    "index": index_value,
                    "revisionIndicator": revision,
                }
            ]
        },
        separators=(",", ":"),
    )
    values = {
        "source_payload": source_payload,
        "retrieved_at": "2025-02-03T15:01:00-05:00",
        "recorded_at": "2025-02-03T15:02:00-05:00",
        "source_uri": (
            "https://markets.newyorkfed.org/read?productCode=50&eventCodes=525&"
            f"startDt={value_date}&endDt={value_date}&format=json"
        ),
    }
    values.update(overrides)
    return item.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import risk_free_index_observation as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_exact_final_official_sofr_index_without_calculating_metric(tmp_path):
    item = ledger(tmp_path)
    result = observe(item)
    assert result["record_type"] == "OFFICIAL_RISK_FREE_INDEX_EVIDENCE"
    assert result["provider"] == "FEDERAL_RESERVE_BANK_OF_NEW_YORK"
    assert result["series"] == "SOFR_INDEX"
    assert result["index_value"] == "1.17345678"
    assert result["exact_index_value"] == {
        "numerator": "58672839",
        "denominator": "50000000",
    }
    assert result["availability"] == "CONTEMPORANEOUS_FINAL_SAME_DAY"
    assert result["revision_status"] == "FINAL_AFTER_SAME_DAY_REVISION_WINDOW"
    assert result["risk_free_period_return_calculated"] is False
    assert result["sharpe_calculated"] is False
    assert result["sortino_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["live_trading_enabled"] is False
    assert result["provider_download_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


def test_historical_retrieval_is_disclosed_as_backfilled(tmp_path):
    item = ledger(tmp_path)
    result = observe(
        item,
        retrieved_at="2025-02-04T12:00:00-05:00",
        recorded_at="2025-02-04T12:01:00-05:00",
    )
    assert result["availability"] == "BACKFILLED_FINAL"


def test_official_empty_payload_for_nonpublication_date_is_rejected(tmp_path):
    item = ledger(tmp_path)
    with pytest.raises(ValueError, match="exactly one valid official SOFRAI"):
        observe(
            item,
            source_payload='{"refRates":[]}',
            source_uri=(
                "https://markets.newyorkfed.org/read?productCode=50&eventCodes=525&"
                "startDt=2025-02-01&endDt=2025-02-01&format=json"
            ),
        )


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"index_value": "0"}, "positive finite"),
        ({"index_value": "NaN"}, "positive finite"),
        ({"index_value": "1.123456789"}, "eight decimal"),
        ({"value_date": "2018-04-01"}, "cannot predate"),
        ({"value_date": "not-a-date"}, "ISO calendar"),
        (
            {"retrieved_at": "2025-02-03T14:59:59-05:00"},
            "15:00 New York revision window",
        ),
        (
            {"source_uri": "http://markets.newyorkfed.org/read"},
            "official credential-free",
        ),
        (
            {"source_uri": "https://evil.example/read"},
            "official credential-free",
        ),
        (
            {"source_uri": "https://user:secret@markets.newyorkfed.org/read"},
            "official credential-free",
        ),
        (
            {
                "source_uri": (
                    "https://markets.newyorkfed.org/read?productCode=50&eventCodes=525&"
                    "startDt=2025-02-04&endDt=2025-02-04&format=json"
                )
            },
            "request exactly",
        ),
        (
            {"recorded_at": "2025-02-03T14:00:00-05:00"},
            "cannot predate retrieval",
        ),
        ({"recorded_at": "2099-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_or_nonfinal_evidence_is_rejected(tmp_path, overrides, fragment):
    item = ledger(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        observe(item, **overrides)


def test_daylight_saving_revision_window_uses_new_york_time(tmp_path):
    item = ledger(tmp_path)
    result = observe(
        item,
        value_date="2025-07-01",
        source_uri=(
            "https://markets.newyorkfed.org/read?productCode=50&eventCodes=525&"
            "startDt=2025-07-01&endDt=2025-07-01&format=json"
        ),
        retrieved_at="2025-07-01T19:00:00+00:00",
        recorded_at="2025-07-01T19:01:00+00:00",
    )
    assert result["availability"] == "CONTEMPORANEOUS_FINAL_SAME_DAY"


def test_official_revised_indicator_is_retained(tmp_path):
    item = ledger(tmp_path)
    result = observe(item, revisionIndicator="R")
    assert result["source_revision_indicator"] == "R"
    assert item.verify() == [result]


def test_conflicting_same_date_evidence_is_rejected(tmp_path):
    item = ledger(tmp_path)
    observe(item)
    with pytest.raises(LedgerIntegrityError, match="Conflicting SOFR Index evidence"):
        observe(item, index_value="1.17345679")


def test_identical_concurrent_retries_create_one_record(tmp_path):
    item = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: observe(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"index_value": "9"},
        {"availability": "BACKFILLED_FINAL"},
        {"revision_status": "PRELIMINARY"},
        {"methodology_uri": "https://www.newyorkfed.org/changed"},
        {"risk_free_period_return_calculated": True},
        {"sharpe_calculated": True},
        {"sortino_calculated": True},
        {"performance_metric_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
        {"provider_download_enabled": True},
        {"source_observation_sha256": "0" * 64},
        {"source_payload_sha256": "0" * 64},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item = ledger(tmp_path)
    observe(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()


def test_malformed_exact_value_is_integrity_error(tmp_path):
    item = ledger(tmp_path)
    observe(item)
    rewrite_with_valid_hash(item.path, exact_index_value=None)
    with pytest.raises(LedgerIntegrityError, match="invalid values"):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item = ledger(tmp_path)
    result = observe(item)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
