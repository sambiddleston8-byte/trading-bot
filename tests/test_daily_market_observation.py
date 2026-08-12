from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import DailyMarketObservationLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fill(fill_id="SFILL-1", ticker="AAA"):
    return {
        "fill_id": fill_id,
        "record_hash": f"hash-{fill_id}",
        "order_id": f"ORDER-{fill_id}",
        "decision_id": f"DEC-{fill_id}",
        "portfolio_version": "PORT-001",
        "ticker": ticker,
        "filled_quantity": 2.0,
        "filled_at": "2025-01-02T15:01:00+00:00",
        **IDENTITY,
    }


class ExecutionLedgerStub:
    def __init__(self, values=None):
        self.values = values or [fill()]

    def verify(self):
        return self.values


def ledger(tmp_path, values=None):
    executions = ExecutionLedgerStub(values)
    observations = DailyMarketObservationLedger(
        tmp_path / "daily_market.jsonl", executions
    )
    return executions, observations


def observe(observations, **overrides):
    values = {
        "fill_id": "SFILL-1",
        "market_session_date": "2025-02-03",
        "close_price": "123.45",
        "price_effective_at": "2025-02-03T21:00:00+00:00",
        "retrieved_at": "2025-02-03T21:05:00+00:00",
        "recorded_at": "2025-02-03T21:06:00+00:00",
        "provider": "TEST_PROVIDER",
        "source_version": "v1",
        "source_uri": "https://provider.example/prices/AAA/2025-02-03",
        "source_input_sha256": "a" * 64,
    }
    values.update(overrides)
    return observations.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_market_observation as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_exact_official_close_without_calculating_performance(tmp_path):
    _, observations = ledger(tmp_path)
    result = observe(observations)
    assert result["record_type"] == "SIMULATED_POSITION_DAILY_MARKET_CLOSE_EVIDENCE"
    assert result["close_price"] == "123.45"
    assert result["exact_close_price"] == {"numerator": "2469", "denominator": "20"}
    assert result["price_basis"] == "UNADJUSTED_CLOSE"
    assert result["market_session_status"] == "OFFICIAL_CLOSE"
    assert result["availability"] == "CONTEMPORANEOUS"
    assert len(result["source_observation_sha256"]) == 64
    assert result["daily_portfolio_valuation_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["recommendation_provided"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert observations.verify() == [result]


def test_backfill_after_72_hours_is_disclosed(tmp_path):
    _, observations = ledger(tmp_path)
    result = observe(
        observations,
        retrieved_at="2025-02-07T21:05:00+00:00",
        recorded_at="2025-02-07T21:06:00+00:00",
    )
    assert result["availability"] == "BACKFILLED_AFTER_72_HOURS"


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"close_price": "0"}, "positive finite"),
        ({"close_price": "NaN"}, "positive finite"),
        ({"market_session_date": "2025-02-04"}, "New York effective date"),
        (
            {
                "price_effective_at": "2025-02-03T14:35:00-05:00",
                "retrieved_at": "2025-02-03T14:36:00-05:00",
                "recorded_at": "2025-02-03T14:37:00-05:00",
            },
            "exactly at 16:00 New York time",
        ),
        (
            {
                "price_effective_at": "2025-02-03T13:00:00-05:00",
                "retrieved_at": "2025-02-03T13:01:00-05:00",
                "recorded_at": "2025-02-03T13:02:00-05:00",
            },
            "exactly at 16:00 New York time",
        ),
        (
            {
                "market_session_date": "2024-12-31",
                "price_effective_at": "2024-12-31T21:00:00+00:00",
                "retrieved_at": "2024-12-31T21:05:00+00:00",
                "recorded_at": "2024-12-31T21:06:00+00:00",
            },
            "predate the simulated fill",
        ),
        ({"retrieved_at": "2025-02-03T20:59:00+00:00"}, "predate the official close"),
        ({"source_uri": "http://provider.example/price"}, "HTTPS"),
        ({"source_uri": "https://user:secret@provider.example/price"}, "credentials"),
        ({"source_input_sha256": "not-a-hash"}, "SHA-256"),
    ],
)
def test_invalid_or_misaligned_source_evidence_is_rejected(tmp_path, overrides, fragment):
    _, observations = ledger(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        observe(observations, **overrides)


def test_missing_fill_is_rejected(tmp_path):
    _, observations = ledger(tmp_path)
    with pytest.raises(ValueError, match="verified simulated fill"):
        observe(observations, fill_id="UNKNOWN")


def test_conflicting_same_fill_session_evidence_is_rejected(tmp_path):
    _, observations = ledger(tmp_path)
    observe(observations)
    with pytest.raises(LedgerIntegrityError, match="Conflicting daily close evidence"):
        observe(
            observations,
            close_price="124",
            source_input_sha256="b" * 64,
        )


def test_multiple_fills_can_have_evidence_for_same_session(tmp_path):
    values = [fill(), fill("SFILL-2", "BBB")]
    _, observations = ledger(tmp_path, values)
    first = observe(observations)
    second = observe(
        observations,
        fill_id="SFILL-2",
        close_price="50",
        source_uri="https://provider.example/prices/BBB/2025-02-03",
        source_input_sha256="b" * 64,
    )
    assert observations.verify() == [first, second]


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, observations = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: observe(observations), range(2)))
    assert first == second
    assert len(observations.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"close_price": "999"},
        {"price_basis": "ADJUSTED_CLOSE"},
        {"market_session_status": "ESTIMATED"},
        {"daily_portfolio_valuation_calculated": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"source_observation_sha256": "0" * 64},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, observations = ledger(tmp_path)
    observe(observations)
    rewrite_with_valid_hash(observations.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        observations.verify()


def test_malformed_exact_price_raises_ledger_integrity_error(tmp_path):
    _, observations = ledger(tmp_path)
    observe(observations)
    rewrite_with_valid_hash(observations.path, exact_close_price=None)
    with pytest.raises(LedgerIntegrityError, match="invalid values"):
        observations.verify()


def test_changed_fill_support_is_detected(tmp_path):
    executions, observations = ledger(tmp_path)
    observe(observations)
    executions.values[0] = {**executions.values[0], "record_hash": "changed-fill-hash"}
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        observations.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, observations = ledger(tmp_path)
    result = observe(observations)
    with observations.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        observations.verify()
    backup = observations.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert observations.verify() == [result]
