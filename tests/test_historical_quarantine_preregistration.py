from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

import core.orchestration.historical_quarantine_preregistration as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
    TARGET_BASKET,
)


UTC = timezone.utc


def definition(**overrides):
    now = datetime.now(UTC)
    values = {
        "registered_by": "SAM_AND_PAT_LOCAL_RESEARCH",
        "acquisition_start": "2025-08-01",
        "acquisition_end": "2026-07-31",
        "splits": [
            {"role": "TRAIN", "start": "2025-08-01", "end": "2026-02-28"},
            {"role": "VALIDATION", "start": "2026-03-01", "end": "2026-04-30"},
            {
                "role": "UNTOUCHED_TEST",
                "start": "2026-05-01",
                "end": "2026-07-31",
            },
        ],
        "strategy_entrypoint": "research.baseline:MovingAverageCross",
        "strategy_source_path": "research/baseline.py",
        "strategy_version": "baseline-grid-v1",
        "parameter_space": {
            "fast_window": [10, 20],
            "slow_window": [50, 100],
            "maximum_position_fraction": ["0.25", "0.50"],
        },
        "evaluation_protocol": {
            "primary_metric": "TOTAL_RETURN",
            "optimization_direction": "MAXIMIZE",
            "tie_break_metrics": [
                {"metric": "MAXIMUM_DRAWDOWN", "direction": "MINIMIZE"}
            ],
            "success_thresholds": {"minimum_total_return": "0.00"},
            "warmup_observations": 100,
            "purge_observations": 1,
            "embargo_observations": 1,
            "maximum_untouched_test_evaluations": 1,
            "execution_policy_version": "synthetic-pilot-policy-v1",
            "execution_policy_sha256": "d" * 64,
            "selection_rule_version": "single-primary-metric-v1",
        },
        "entitlement_metadata": {
            "plan_name": "STOCKS_BASIC_FREE",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": (now - timedelta(minutes=1)).isoformat(),
            "terms_payload_sha256": "c" * 64,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
        },
    }
    values.update(overrides)
    return values


def ledger(tmp_path, **kwargs):
    source = tmp_path / "research" / "baseline.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("class MovingAverageCross:\n    pass\n")
    return HistoricalQuarantinePreregistrationLedger(
        tmp_path / "massive-preregistration.jsonl",
        repository_root=tmp_path,
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: True,
        **kwargs,
    )


def test_preregisters_exact_basket_window_strategy_grid_and_untouched_split(tmp_path):
    target = ledger(tmp_path)

    record = target.preregister(**definition())

    assert record["previous_hash"] == GENESIS_HASH
    assert record["target_basket"] == list(TARGET_BASKET)
    assert record["acquisition_start"] == "2025-08-01"
    assert record["acquisition_end"] == "2026-07-31"
    assert [item["role"] for item in record["splits"]] == [
        "TRAIN",
        "VALIDATION",
        "UNTOUCHED_TEST",
    ]
    assert record["parameter_space_sha256"] == module.hashlib.sha256(
        record["parameter_space_canonical_json"].encode()
    ).hexdigest()
    assert record["strategy_source_sha256"] == module.hashlib.sha256(
        (tmp_path / "research" / "baseline.py").read_bytes()
    ).hexdigest()
    assert record["evaluation_protocol"]["maximum_untouched_test_evaluations"] == 1
    assert record["git_worktree_clean"] is True
    assert record["externally_anchored"] is False
    assert record["entitlement_metadata"]["account_entitlement_authenticated"] is False
    assert record["entitlement_metadata"]["historical_replay_use_confirmed"] is False
    assert record["quarantine_only"] is True
    assert record["data_access_not_before"] == record["registered_at"]
    assert all(record[name] is False for name in module.FIXED_FALSE)
    assert target.verify() == [record]
    assert target.path.stat().st_mode & 0o777 == 0o600


def test_identical_preregistration_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    values = definition()

    first = target.preregister(**values)
    second = target.preregister(**values)

    assert first == second
    assert len(target.verify()) == 1


def test_campaign_rejects_a_competing_plan(tmp_path):
    target = ledger(tmp_path)
    target.preregister(**definition())

    with pytest.raises(LedgerIntegrityError, match="already has a different"):
        target.preregister(
            **definition(strategy_version="post-hoc-alternative-v2")
        )

    assert len(target.verify()) == 1


@pytest.mark.parametrize(
    ("splits", "message"),
    [
        (
            [
                {"role": "TRAIN", "start": "2025-08-01", "end": "2026-02-28"},
                {"role": "VALIDATION", "start": "2026-03-02", "end": "2026-04-30"},
                {"role": "UNTOUCHED_TEST", "start": "2026-05-01", "end": "2026-07-31"},
            ],
            "contiguous",
        ),
        (
            [
                {"role": "TRAIN", "start": "2025-08-01", "end": "2026-02-28"},
                {"role": "UNTOUCHED_TEST", "start": "2026-03-01", "end": "2026-04-30"},
                {"role": "VALIDATION", "start": "2026-05-01", "end": "2026-07-31"},
            ],
            "contiguous",
        ),
    ],
)
def test_split_gaps_overlap_or_reordering_fail_before_write(tmp_path, splits, message):
    target = ledger(tmp_path)

    with pytest.raises(ValueError, match=message):
        target.preregister(**definition(splits=splits))

    assert target.records() == []


def test_invalid_strategy_space_entitlement_or_window_fail_before_write(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        target.preregister(**definition(parameter_space={"threshold": float("nan")}))
    with pytest.raises(ValueError, match="official Massive host"):
        metadata = definition()["entitlement_metadata"]
        metadata["terms_uri"] = "https://example.com/terms"
        target.preregister(**definition(entitlement_metadata=metadata))
    with pytest.raises(ValueError, match="exactly 5"):
        metadata = definition()["entitlement_metadata"]
        metadata["asserted_request_limit_per_minute"] = 6
        target.preregister(**definition(entitlement_metadata=metadata))
    with pytest.raises(ValueError, match="at most 366 days"):
        target.preregister(
            **definition(
                acquisition_start="2024-01-01",
                acquisition_end="2026-07-31",
            )
        )
    assert target.records() == []


def test_invalid_evaluation_protocol_fails_before_write(tmp_path):
    target = ledger(tmp_path)
    protocol = definition()["evaluation_protocol"]
    protocol["maximum_untouched_test_evaluations"] = 2

    with pytest.raises(ValueError, match="exactly 1"):
        target.preregister(**definition(evaluation_protocol=protocol))

    assert target.records() == []


def test_strategy_source_and_clean_revision_are_derived_before_write(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        target.preregister(
            **definition(strategy_entrypoint="other.module:MovingAverageCross")
        )
    dirty = HistoricalQuarantinePreregistrationLedger(
        tmp_path / "dirty.jsonl",
        repository_root=tmp_path,
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: False,
    )
    with pytest.raises(ValueError, match="worktree must be clean"):
        dirty.preregister(**definition())

    assert target.records() == []
    assert dirty.records() == []


def test_registration_cannot_be_backdated(tmp_path):
    target = ledger(
        tmp_path,
        clock=lambda: datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="actual append time"):
        target.preregister(**definition())


@pytest.mark.parametrize(
    "change",
    [
        {"provider_bytes_accessed": True},
        {"dataset_admitted": True},
        {"untouched_test_opened": True},
        {"guardrailed_replay_executed": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_authority_tampering_is_rejected(tmp_path, change):
    target = ledger(tmp_path)
    record = target.preregister(**definition())
    record.update(change)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record, separators=(",", ":")) + "\n")

    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        target.verify()


def test_incomplete_or_permission_unsafe_ledger_fails_closed(tmp_path):
    target = ledger(tmp_path)
    target.preregister(**definition())
    target.path.write_text(target.path.read_text().rstrip("\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.verify()

    target.path.chmod(0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        target.verify()


def test_permission_unsafe_lock_fails_closed(tmp_path):
    target = ledger(tmp_path)
    lock = target.path.with_suffix(target.path.suffix + ".lock")
    lock.touch(mode=0o644)

    with pytest.raises(LedgerIntegrityError, match="lock is unsafe"):
        target.preregister(**definition())

    assert target.records() == []
