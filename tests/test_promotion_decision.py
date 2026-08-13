from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import PromotionDecisionLedger


class Stub:
    def __init__(self, values): self.values = values
    def verify(self): return self.values


def setup(tmp_path):
    now = datetime.now(timezone.utc)
    bundle = {
        "promotion_bundle_id": "PROMOTION-1", "record_hash": "bundle-hash",
        "status": "COMPLETE_AWAITING_EXPLICIT_HUMAN_DECISION",
        "assembled_at": (now - timedelta(minutes=1)).isoformat(),
        "shadow_result_id": "SHADOW-1", "candidate_strategy_version": "candidate-v2",
        "baseline_strategy_version": "incumbent-v1", "candidate_git_revision": "a" * 40,
    }
    target = PromotionDecisionLedger(tmp_path / "decisions.jsonl", Stub([bundle]))
    values = {
        "promotion_bundle_id": bundle["promotion_bundle_id"],
        "decision": "APPROVE_FOR_SEPARATE_IMPLEMENTATION",
        "rationale": "The complete preregistered evidence package met the approved gates.",
        "decided_by": "Sam", "human_decision_confirmed": True,
        "decided_at": now.isoformat(),
    }
    return target, values


def decide(target, values, **changes):
    requested = dict(values); requested.update(changes); return target.decide(**requested)


@pytest.mark.parametrize("decision,approved,rejected,status", [
    ("APPROVE_FOR_SEPARATE_IMPLEMENTATION", True, False, "HUMAN_APPROVED_FOR_SEPARATE_IMPLEMENTATION_NOT_ACTIVATED"),
    ("REJECT_CANDIDATE", False, True, "HUMAN_REJECTED_CANDIDATE"),
])
def test_records_human_decision_without_changing_incumbent_or_runtime(
    tmp_path, decision, approved, rejected, status
):
    target, values = setup(tmp_path)
    result = decide(target, values, decision=decision)
    assert result["status"] == status and result["previous_hash"] == GENESIS_HASH
    assert result["implementation_authorized"] is approved
    assert result["candidate_rejected"] is rejected
    assert result["incumbent_unchanged"] is True
    for field in (
        "code_changed", "production_rule_changed", "production_active",
        "deployment_performed", "broker_connection_allowed", "order_submitted",
        "live_trading_enabled", "self_promotion_allowed",
    ):
        assert result[field] is False
    assert target.verify() == [result]


@pytest.mark.parametrize("change,fragment", [
    ({"human_decision_confirmed": False}, "human"),
    ({"promotion_bundle_id": "unknown"}, "verified"),
    ({"decision": "PROMOTE_AND_DEPLOY"}, "approve implementation or reject"),
    ({"rationale": ""}, "required"),
    ({"decided_by": ""}, "required"),
    ({"decided_at": "2000-01-01T00:00:00+00:00"}, "actual decision time"),
])
def test_invalid_or_ambiguous_decision_is_rejected(tmp_path, change, fragment):
    target, values = setup(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        decide(target, values, **change)


def test_concurrent_retry_is_idempotent_and_conflicting_decision_is_blocked(tmp_path):
    target, values = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: decide(target, values), range(2)))
    assert first == second and len(target.verify()) == 1
    with pytest.raises(LedgerIntegrityError, match="already has"):
        decide(target, values, decision="REJECT_CANDIDATE")


@pytest.mark.parametrize("changes", [
    {"decision": "REJECT_CANDIDATE"},
    {"status": "PRODUCTION_ACTIVE"},
    {"human_decision_confirmed": False},
    {"implementation_authorized": False},
    {"candidate_rejected": True},
    {"incumbent_unchanged": False},
    {"code_changed": True},
    {"production_active": True},
    {"deployment_performed": True},
    {"broker_connection_allowed": True},
    {"order_submitted": True},
    {"live_trading_enabled": True},
    {"self_promotion_allowed": True},
])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target, values = setup(tmp_path); decide(target, values)
    from core.orchestration import promotion_decision as module
    value = json.loads(target.path.read_text()); value.update(changes)
    material = {key: item for key, item in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(value) + "\n")
    with pytest.raises(LedgerIntegrityError): target.verify()
