from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import OUTCOME_HORIZONS, OutcomeObservationLedger


def ledgers(
    tmp_path,
    *,
    created_at="2025-01-02T15:00:00+00:00",
    filled_at="2025-01-02T15:01:00+00:00",
):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="NVDA",
        side="BUY",
        quantity=2,
        reference_price=100,
        target_weight=0.1,
        strategy_version="strategy-v1",
        model_versions=[{"component": "portfolio", "version": "1.0"}],
        created_at=created_at,
        git_revision="abc123",
        order_id="PORD-001",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill = executions.simulate_full_fill(
        order_id="PORD-001",
        fill_price=101,
        fees=0.2,
        filled_at=filled_at,
    )
    return OutcomeObservationLedger(tmp_path / "observations.jsonl", executions), fill


def observe_entry(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "ENTRY",
        "benchmark_price": 5_900,
        "benchmark_price_effective_at": "2025-01-02T15:00:00+00:00",
        "retrieved_at": "2025-01-02T15:02:00+00:00",
        "data_source": "TEST_FIXTURE",
        "source_version": "fixture-v1",
    }
    values.update(overrides)
    return ledger.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import outcome_observation as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_all_master_roadmap_horizons_are_predeclared():
    assert list(OUTCOME_HORIZONS) == [
        "ENTRY",
        "1_DAY",
        "1_WEEK",
        "1_MONTH",
        "3_MONTHS",
        "6_MONTHS",
        "12_MONTHS",
        "24_MONTHS",
    ]


def test_entry_observation_inherits_fill_and_makes_no_performance_claim(tmp_path):
    ledger, fill = ledgers(tmp_path)
    record = observe_entry(ledger, fill, asset_price=999_999)

    assert record["asset_price"] == fill["fill_price"] == 101
    assert record["asset_price_basis"] == "SIMULATED_FILL"
    assert record["asset_price_effective_at"] == fill["filled_at"]
    assert record["benchmark_price_effective_at"] == "2025-01-02T15:00:00+00:00"
    assert record["benchmark_ticker"] == "^GSPC"
    assert record["performance_claim"] is False
    assert record["execution_record_hash"] == fill["record_hash"]
    assert len(record["source_observation_sha256"]) == 64
    assert record["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [record]


def test_calendar_month_horizon_handles_month_end(tmp_path):
    ledger, fill = ledgers(
        tmp_path,
        created_at="2025-01-31T15:00:00+00:00",
        filled_at="2025-01-31T15:01:00+00:00",
    )
    observe_entry(
        ledger,
        fill,
        benchmark_price_effective_at="2025-01-31T15:00:00+00:00",
        retrieved_at="2025-01-31T15:02:00+00:00",
    )
    record = ledger.observe(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        asset_price=110,
        benchmark_price=6_000,
        asset_price_effective_at="2025-02-28T16:00:00+00:00",
        benchmark_price_effective_at="2025-02-28T16:00:00+00:00",
        retrieved_at="2025-02-28T17:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
    )
    assert record["target_at"] == "2025-02-28T15:01:00+00:00"


def test_horizon_observation_cannot_be_recorded_early(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    with pytest.raises(ValueError, match="outside"):
        ledger.observe(
            fill_id=fill["fill_id"],
            horizon="1_MONTH",
            asset_price=110,
            benchmark_price=6_000,
            asset_price_effective_at="2025-01-20T16:00:00+00:00",
            benchmark_price_effective_at="2025-01-20T16:00:00+00:00",
            retrieved_at="2025-01-20T17:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
        )


def test_later_horizon_requires_entry_observation(tmp_path):
    ledger, fill = ledgers(tmp_path)
    with pytest.raises(ValueError, match="ENTRY"):
        ledger.observe(
            fill_id=fill["fill_id"],
            horizon="1_DAY",
            asset_price=102,
            benchmark_price=5_950,
            asset_price_effective_at="2025-01-03T16:00:00+00:00",
            benchmark_price_effective_at="2025-01-03T16:00:00+00:00",
            retrieved_at="2025-01-03T17:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
        )


def test_entry_benchmark_cannot_be_more_than_four_days_stale(tmp_path):
    ledger, fill = ledgers(tmp_path)
    with pytest.raises(ValueError, match="four days"):
        observe_entry(
            ledger,
            fill,
            benchmark_price_effective_at="2024-12-27T15:00:00+00:00",
            retrieved_at="2025-01-02T15:02:00+00:00",
        )


def test_market_price_basis_must_remain_consistent_across_horizons(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill, market_price_basis="ADJUSTED_CLOSE")
    with pytest.raises(ValueError, match="basis"):
        ledger.observe(
            fill_id=fill["fill_id"],
            horizon="1_DAY",
            asset_price=102,
            benchmark_price=5_950,
            asset_price_effective_at="2025-01-03T16:00:00+00:00",
            benchmark_price_effective_at="2025-01-03T16:00:00+00:00",
            retrieved_at="2025-01-03T17:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
            market_price_basis="UNADJUSTED_CLOSE",
        )


def test_backfill_is_explicit(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    record = ledger.observe(
        fill_id=fill["fill_id"],
        horizon="1_DAY",
        asset_price=102,
        benchmark_price=5_950,
        asset_price_effective_at="2025-01-03T16:00:00+00:00",
        benchmark_price_effective_at="2025-01-03T16:00:00+00:00",
        retrieved_at="2025-02-01T12:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
    )
    assert record["retrieval_mode"] == "BACKFILLED"


def test_observation_requires_verified_fill(tmp_path):
    ledger, _ = ledgers(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        ledger.observe(
            fill_id="SFILL-MISSING",
            horizon="ENTRY",
            benchmark_price=5_900,
            benchmark_price_effective_at="2025-01-02T15:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
        )


def test_identical_and_concurrent_retries_create_one_record(tmp_path):
    ledger, fill = ledgers(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: observe_entry(ledger, fill), range(2))
        )
    assert first == second
    assert len(ledger.verify()) == 1


def test_conflicting_retry_fails_closed(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        observe_entry(ledger, fill, benchmark_price=6_000)


def test_tampering_and_broken_fill_linkage_are_detected(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    rewrite_with_valid_hash(ledger.path, decision_id="DEC-FORGED")

    with pytest.raises(LedgerIntegrityError, match="linked identity"):
        ledger.verify()


@pytest.mark.parametrize(
    "field,value",
    [
        ("performance_claim", True),
        ("record_type", "CALCULATED_PERFORMANCE"),
        ("source_observation_sha256", "0" * 64),
    ],
)
def test_rehashed_observation_cannot_change_raw_data_boundary(
    tmp_path, field, value
):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    rewrite_with_valid_hash(ledger.path, **{field: value})

    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


@pytest.mark.parametrize(
    "field,value",
    [
        ("benchmark_price", 0),
        ("benchmark_price", float("nan")),
        ("market_price_basis", "UNKNOWN"),
        ("benchmark_ticker", "QQQ"),
        ("data_source", ""),
        ("source_version", ""),
    ],
)
def test_invalid_observation_inputs_fail_closed(tmp_path, field, value):
    ledger, fill = ledgers(tmp_path)
    with pytest.raises(ValueError):
        observe_entry(ledger, fill, **{field: value})


def test_non_entry_asset_price_is_required(tmp_path):
    ledger, fill = ledgers(tmp_path)
    observe_entry(ledger, fill)
    with pytest.raises(ValueError, match="asset_price"):
        ledger.observe(
            fill_id=fill["fill_id"],
            horizon="1_DAY",
            benchmark_price=5_950,
            asset_price_effective_at="2025-01-03T16:00:00+00:00",
            benchmark_price_effective_at="2025-01-03T16:00:00+00:00",
            retrieved_at="2025-01-03T17:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
        )


def test_retrieval_time_cannot_be_in_the_future(tmp_path):
    ledger, fill = ledgers(tmp_path)
    with pytest.raises(ValueError, match="future"):
        observe_entry(ledger, fill, retrieved_at="2030-01-01T00:00:00+00:00")


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger, fill = ledgers(tmp_path)
    first = observe_entry(ledger, fill)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]
