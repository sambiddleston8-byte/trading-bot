from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.broker.provider_paper_risk_policy import ProviderPaperRiskControlPolicyLedger


BASE = {
    "account_reference_sha256": "11" * 32,
    "portfolio_version": "port-v1",
    "strategy_version": "strat-v1",
    "max_order_notional_usd": "1000",
    "max_position_notional_usd": "5000",
    "max_gross_exposure_usd": "20000",
    "max_daily_loss_usd": "750",
    "max_account_snapshot_age_seconds": 120,
    "kill_switch_identifier": "local-provider-paper-kill-switch-v1",
    "decided_by": "Sam",
    "decision_reference": "human-provider-paper-risk-policy-1",
    "human_decision_confirmed": True,
    "effective_not_before": "2026-01-01T00:10:00+00:00",
    "git_revision": "abc123",
    "recorded_at": "2026-01-01T00:00:00+00:00",
}


def register(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.preregister(**values)


def rewrite(path, **changes):
    from core.broker import provider_paper_risk_policy as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_policy_is_bounded_and_inactive_by_default(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    result = register(ledger)
    assert result["status"] == "PREREGISTERED_INACTIVE"
    assert result["broker"] == "ALPACA"
    assert result["environment"] == "PAPER"
    assert "ACCESS_BROKER_CREDENTIALS" in result["denied_actions"]
    assert "SUBMIT_BROKER_ORDER" in result["denied_actions"]
    assert "ENABLE_LIVE_TRADING" in result["denied_actions"]
    for field in (
        "broker_access_enabled", "paper_order_submission_enabled", "live_order_submission_enabled",
        "automatic_activation_allowed", "self_limit_change_allowed", "risk_limits_enforced",
        "order_route_exists", "external_head_anchor_present",
        "cryptographic_authentication_present", "live_trading_enabled",
    ):
        assert result[field] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


def test_exact_decimal_normalization_and_rejects_float(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    result = register(ledger, max_order_notional_usd="1000.100", max_daily_loss_usd="750.00")
    assert result["max_order_notional_usd"] == "1000.1"
    assert result["max_daily_loss_usd"] == "750"
    with pytest.raises(ValueError, match="decimal string"):
        register(ledger, max_order_notional_usd=1000.5)


def test_limit_relationship_is_enforced(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    with pytest.raises(ValueError, match="max_order_notional_usd"):
        register(ledger, max_order_notional_usd="6000")


def test_human_confirmation_and_future_lead_required(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    with pytest.raises(ValueError, match="human"):
        register(ledger, human_decision_confirmed=False)
    with pytest.raises(ValueError, match="five minutes"):
        register(ledger, effective_not_before="2026-01-01T00:04:59+00:00")


def test_invalid_account_reference_is_rejected(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    with pytest.raises(ValueError, match="SHA-256"):
        register(ledger, account_reference_sha256="not-a-hash")


def test_uppercase_account_reference_is_rejected(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    with pytest.raises(ValueError, match="SHA-256"):
        register(ledger, account_reference_sha256=("AB" * 32))


@pytest.mark.parametrize(
    "value",
    [0, 3601, "not-an-int"],
)
def test_invalid_snapshot_age_is_rejected(tmp_path, value):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    with pytest.raises(ValueError):
        register(ledger, max_account_snapshot_age_seconds=value)


def test_concurrent_retry_is_idempotent(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    effective = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    values = {
        key: value for key, value in BASE.items() if key != "recorded_at"
    }
    values["effective_not_before"] = effective
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: ledger.preregister(**values), range(2))
        )
    assert first == second
    assert len(ledger.verify()) == 1


def test_multiple_policy_chain_verifies_and_reordering_fails(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    first = register(ledger)
    second = register(
        ledger,
        strategy_version="strat-v2",
        decision_reference="human-provider-paper-risk-policy-2",
        effective_not_before="2026-01-01T00:20:00+00:00",
    )
    assert ledger.verify() == [first, second]
    lines = ledger.path.read_text().splitlines()
    ledger.path.write_text("\n".join(reversed(lines)) + "\n")
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"broker": "IBKR"},
        {"environment": "LIVE"},
        {"denied_actions": []},
        {"max_order_notional_usd": "999999"},
        {"decided_by": "Mallory"},
        {"decision_reference": "rewritten-decision"},
        {"broker_access_enabled": True},
        {"paper_order_submission_enabled": True},
        {"live_order_submission_enabled": True},
        {"automatic_activation_allowed": True},
        {"self_limit_change_allowed": True},
        {"risk_limits_enforced": True},
        {"order_route_exists": True},
        {"external_head_anchor_present": True},
        {"cryptographic_authentication_present": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    register(ledger)
    rewrite(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_rehashed_unexpected_field_is_rejected(tmp_path):
    ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    register(ledger)
    rewrite(ledger.path, injected_field="unexpected")
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()
