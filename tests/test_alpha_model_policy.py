from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import AlphaModelPolicyLedger


BASE = {
    "portfolio_version": "PORT-001",
    "model_family": "CAPM_SP500_SOFR",
    "evaluation_not_before": "2021-01-01T00:10:00+00:00",
    "evaluation_not_after": "2024-01-02T00:10:00+00:00",
    "decided_by": "Sam",
    "decision_reference": "human-alpha-choice-1",
    "human_decision_confirmed": True,
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
    "recorded_at": "2021-01-01T00:00:00+00:00",
}


def register(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.preregister(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import alpha_model_policy as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_preregisters_fixed_future_capm_policy(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    result = register(ledger)
    assert result["status"] == "PREREGISTERED"
    assert result["factor_names"] == ["SP500_EXCESS_RETURN_OVER_MATCHED_SOFR"]
    assert result["risk_free_basis"] == "MATCHED_DAILY_SOFR_INDEX_RETURN"
    assert result["regression_method"] == "OLS_WITH_INTERCEPT"
    assert result["inference_covariance"] == "NEWEY_WEST_HAC"
    assert result["minimum_observations"] == 756
    assert result["complete_date_intersection_required"] is True
    assert result["cross_strategy_pooling_allowed"] is False
    assert result["cross_model_version_pooling_allowed"] is False
    assert result["model_selection_after_outcomes_allowed"] is False
    assert result["optional_stopping_allowed"] is False
    assert result["alpha_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "family,factors,risk_free,official",
    [
        ("CAPM_SP500_SOFR", ["SP500_EXCESS_RETURN_OVER_MATCHED_SOFR"], "MATCHED_DAILY_SOFR_INDEX_RETURN", False),
        ("KEN_FRENCH_US_3_FACTOR", ["MKT_RF", "SMB", "HML"], "KEN_FRENCH_DATASET_RF", True),
        ("KEN_FRENCH_US_5_FACTOR", ["MKT_RF", "SMB", "HML", "RMW", "CMA"], "KEN_FRENCH_DATASET_RF", True),
    ],
)
def test_supported_model_families_fix_consistent_factor_and_rf_basis(
    tmp_path, family, factors, risk_free, official
):
    ledger = AlphaModelPolicyLedger(tmp_path / f"{family}.jsonl")
    result = register(ledger, model_family=family)
    assert result["factor_names"] == factors
    assert result["risk_free_basis"] == risk_free
    assert result["official_ken_french_data_required"] is official


def test_human_confirmation_future_lead_and_fixed_window_are_required(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    with pytest.raises(ValueError, match="human"):
        register(ledger, human_decision_confirmed=False)
    with pytest.raises(ValueError, match="five minutes"):
        register(ledger, evaluation_not_before="2021-01-01T00:04:59+00:00")
    with pytest.raises(ValueError, match="1095"):
        register(ledger, evaluation_not_after="2023-01-01T00:10:00+00:00")


def test_unsupported_or_inconsistent_model_is_rejected(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    with pytest.raises(ValueError, match="model_family"):
        register(ledger, model_family="PICK_WHICHEVER_LOOKS_BEST")


def test_same_window_cannot_be_redefined(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    register(ledger)
    with pytest.raises(LedgerIntegrityError, match="already registered"):
        register(ledger, model_family="KEN_FRENCH_US_3_FACTOR")


def test_identical_concurrent_retries_append_once(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: register(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"factor_names": ["MKT_RF"]},
        {"risk_free_basis": "MIXED"},
        {"minimum_observations": 30},
        {"missing_observation_imputation_allowed": True},
        {"cross_strategy_pooling_allowed": True},
        {"cross_model_version_pooling_allowed": True},
        {"inference_covariance": "NONE"},
        {"model_selection_after_outcomes_allowed": True},
        {"optional_stopping_allowed": True},
        {"retrospective_application_allowed": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    register(ledger)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    ledger = AlphaModelPolicyLedger(tmp_path / "alpha_policy.jsonl")
    register(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
