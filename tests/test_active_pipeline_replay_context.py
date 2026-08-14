from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from core.orchestration.active_pipeline_image_approval import (
    FIXED_FALSE as IMAGE_FIXED_FALSE,
    REQUIRED_ENTRYPOINT,
    REQUIRED_ENVIRONMENT,
)
from core.orchestration.active_pipeline_replay_context import (
    ActivePipelineReplayContextLedger,
    FIXED_FALSE_FIELDS,
    REQUIRED_ENGINE_KEYS,
)
from core.orchestration import active_pipeline_replay_context as module
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.active_pipeline_replay_plan import REQUIRED_COMPONENTS
from core.research.investment_research_pipeline import InvestmentResearchPipeline


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class VerifiedLedger:
    def __init__(self, records):
        self._records = records

    def verify(self):
        return list(self._records)


def component(name: str) -> dict[str, str]:
    return {"version": f"{name}-v1", "source_sha256": digest(name)}


def values():
    registered_at = datetime.now(timezone.utc)
    components = {name: component(name) for name in REQUIRED_COMPONENTS}
    engines = {name: component(f"engine:{name}") for name in REQUIRED_ENGINE_KEYS}
    engines["master_decision"] = dict(
        components["master_portfolio_decision_engine"]
    )
    plan = {
        "replay_plan_id": "REPLAY-" + "1" * 32,
        "record_hash": digest("plan-record"),
        "git_revision": "1" * 40,
        "components": components,
        "dependency_lock_sha256": digest("dependency-lock"),
        "runner_sha256": digest("approved-runner-image"),
        "sealed_evaluation_dataset_commitment_sha256": digest("sealed-dataset"),
        "evaluation_not_before": "2020-01-01T00:00:00+00:00",
        "evaluation_not_after": "2021-01-01T00:00:00+00:00",
        "evaluation_data_access_not_before": (
            registered_at + timedelta(hours=1)
        ).isoformat(),
        "simulation_only": True,
        "active_route_only": True,
        "point_in_time_inputs_required": True,
        "evaluation_dataset_opened": False,
        "replay_executed": False,
        "performance_claim_allowed": False,
        "paper_broker_submission_enabled": False,
        "broker_connection_allowed": False,
        "live_trading_enabled": False,
    }
    image = {
        "image_approval_id": "IMGAPP-" + "2" * 32,
        "record_hash": digest("image-record"),
        "status": "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY",
        "simulation_only": True,
        "execution_environment": REQUIRED_ENVIRONMENT,
        "entrypoint": REQUIRED_ENTRYPOINT,
        "git_revision": plan["git_revision"],
        "dependency_lock_sha256": plan["dependency_lock_sha256"],
        "image_digest_sha256": plan["runner_sha256"],
        **{field: False for field in IMAGE_FIXED_FALSE},
    }
    arguments = {
        "replay_plan_ledger": VerifiedLedger([plan]),
        "replay_plan": plan,
        "image_approval_ledger": VerifiedLedger([image]),
        "image_approval": image,
        "engine_dependencies": engines,
        "factor_lineage_policy": components["factor_lineage_policy"],
        "as_of_schedule": [
            "2020-03-31T20:00:00+00:00",
            "2020-06-30T20:00:00+00:00",
            "2020-09-30T20:00:00+00:00",
            "2020-12-30T20:00:00+00:00",
        ],
        "learning_state_sha256": digest("sealed-learning-state"),
        "authenticated_source_ledger_sha256": digest("source-ledger"),
        "authenticated_source_blobs_manifest_sha256": digest("source-blobs"),
        "registered_by": "REPLAY_GOVERNANCE_TEST",
        "registered_at": registered_at,
    }
    return plan, image, engines, arguments


def test_resolves_every_dependency_into_one_deterministic_inert_context():
    plan, image, engines, arguments = values()

    first = module._resolve_active_pipeline_replay_context(**arguments)
    reordered = dict(arguments)
    reordered["engine_dependencies"] = dict(reversed(list(engines.items())))
    second = module._resolve_active_pipeline_replay_context(**reordered)

    assert first.canonical_bytes == second.canonical_bytes
    assert first.context_sha256 == second.context_sha256
    payload = first.as_dict()
    assert payload["status"] == "SEALED_PREREQUISITES_ONLY_NOT_EXECUTED"
    assert payload["replay_plan_record_hash"] == plan["record_hash"]
    assert payload["image_approval_record_hash"] == image["record_hash"]
    assert set(payload["engine_dependencies"]) == REQUIRED_ENGINE_KEYS
    assert payload["strict_stage_errors"] is True
    assert payload["simulation_only"] is True
    assert all(payload[field] is False for field in FIXED_FALSE_FIELDS)
    assert not hasattr(first, "run")
    assert not hasattr(first, "save")
    assert first.released_from_registration_id is None


def test_returned_payload_is_a_copy_not_mutable_context_state():
    _, _, _, arguments = values()
    context = module._resolve_active_pipeline_replay_context(**arguments)
    payload = context.as_dict()
    payload["live_trading_enabled"] = True

    assert context.as_dict()["live_trading_enabled"] is False
    assert hashlib.sha256(context.canonical_bytes).hexdigest() == context.context_sha256


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENGINE_KEYS))
def test_incomplete_engine_registry_fails_closed(missing):
    _, _, engines, arguments = values()
    arguments["engine_dependencies"] = {
        name: value for name, value in engines.items() if name != missing
    }

    with pytest.raises(ValueError, match="pin exactly"):
        module._resolve_active_pipeline_replay_context(**arguments)


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("version", "", "version is required"),
        ("source_sha256", "not-a-digest", "SHA-256"),
    ],
)
def test_unpinned_engine_identity_fails_closed(field, value, fragment):
    _, _, engines, arguments = values()
    engines["valuation"] = {**engines["valuation"], field: value}

    with pytest.raises(ValueError, match=fragment):
        module._resolve_active_pipeline_replay_context(**arguments)


def test_master_decision_and_factor_lineage_must_match_preregistered_components():
    _, _, engines, arguments = values()
    engines["master_decision"] = component("different-master-decision")
    with pytest.raises(ValueError, match="master decision"):
        module._resolve_active_pipeline_replay_context(**arguments)

    _, _, _, arguments = values()
    arguments["factor_lineage_policy"] = component("different-lineage")
    with pytest.raises(ValueError, match="factor-lineage"):
        module._resolve_active_pipeline_replay_context(**arguments)


@pytest.mark.parametrize(
    "as_of_schedule",
    [
        ["2019-12-31T23:59:59+00:00"],
        ["2021-01-01T00:00:00+00:00"],
        ["2020-06-01T00:00:00+00:00", "2020-06-01T00:00:00+00:00"],
        ["2020-09-01T00:00:00+00:00", "2020-06-01T00:00:00+00:00"],
    ],
)
def test_as_of_schedule_must_be_in_window_strictly_increasing_and_unique(
    as_of_schedule,
):
    _, _, _, arguments = values()
    arguments["as_of_schedule"] = as_of_schedule
    with pytest.raises(ValueError, match="as-of|as_of_schedule"):
        module._resolve_active_pipeline_replay_context(**arguments)


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("git_revision", "2" * 40, "Git revision"),
        ("dependency_lock_sha256", digest("other-lock"), "dependency lock"),
        ("image_digest_sha256", digest("other-image"), "replay runner"),
        ("network_allowed", True, "forbidden authority"),
    ],
)
def test_image_mismatch_or_authority_fails_closed(field, value, fragment):
    _, image, _, arguments = values()
    image[field] = value
    with pytest.raises(ValueError, match=fragment):
        module._resolve_active_pipeline_replay_context(**arguments)


def test_unverified_supplied_plan_or_image_fails_closed():
    plan, image, _, arguments = values()
    arguments["replay_plan"] = {**plan, "record_hash": digest("unverified-plan")}
    with pytest.raises(ValueError, match="does not match"):
        module._resolve_active_pipeline_replay_context(**arguments)

    _, _, _, arguments = values()
    arguments["image_approval"] = {
        **image,
        "record_hash": digest("unverified-image"),
    }
    with pytest.raises(ValueError, match="does not match"):
        module._resolve_active_pipeline_replay_context(**arguments)


def test_mutable_learning_and_source_evidence_must_be_content_pinned():
    _, _, _, arguments = values()
    for field in (
        "learning_state_sha256",
        "authenticated_source_ledger_sha256",
        "authenticated_source_blobs_manifest_sha256",
    ):
        changed = dict(arguments)
        changed[field] = "missing"
        with pytest.raises(ValueError, match="SHA-256"):
            module._resolve_active_pipeline_replay_context(**changed)


def test_context_is_preregistered_once_in_an_append_only_ledger(tmp_path):
    plan, image, _, arguments = values()
    ledger = ActivePipelineReplayContextLedger(
        tmp_path / "replay-context.jsonl",
        replay_plan_ledger=arguments["replay_plan_ledger"],
        image_approval_ledger=arguments["image_approval_ledger"],
    )

    record = ledger.preregister(**{
        key: value
        for key, value in arguments.items()
        if key not in {"replay_plan_ledger", "image_approval_ledger"}
    })

    assert record["status"] == "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS"
    assert record["replay_plan_record_hash"] == plan["record_hash"]
    assert record["image_approval_record_hash"] == image["record_hash"]
    assert record["registered_at"] < record["evaluation_data_access_not_before"]
    assert ledger.verify() == [record]
    assert ledger.preregister(**{
        key: value
        for key, value in arguments.items()
        if key not in {"replay_plan_ledger", "image_approval_ledger"}
    }) == record


def test_registration_after_data_access_embargo_fails_closed():
    _, _, _, arguments = values()
    arguments["replay_plan"]["evaluation_data_access_not_before"] = (
        arguments["registered_at"] - timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(ValueError, match="before evaluation data access"):
        module._resolve_active_pipeline_replay_context(**arguments)


def test_verified_context_is_unavailable_until_real_embargo_lifts(
    tmp_path,
    monkeypatch,
):
    _, _, _, arguments = values()
    registered_at = arguments["registered_at"]
    access_at = registered_at + timedelta(hours=1)
    arguments["replay_plan"]["evaluation_data_access_not_before"] = access_at.isoformat()

    class FrozenDateTime(datetime):
        current = registered_at

        @classmethod
        def now(cls, tz=None):
            value = cls.current
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(module, "datetime", FrozenDateTime)
    ledger = ActivePipelineReplayContextLedger(
        tmp_path / "replay-context.jsonl",
        replay_plan_ledger=arguments["replay_plan_ledger"],
        image_approval_ledger=arguments["image_approval_ledger"],
    )
    record = ledger.preregister(**{
        key: value
        for key, value in arguments.items()
        if key not in {"replay_plan_ledger", "image_approval_ledger"}
    })

    with pytest.raises(PermissionError, match="embargo has not lifted"):
        ledger.require_data_access_open(record["context_registration_id"])

    FrozenDateTime.current = access_at
    context = ledger.require_data_access_open(record["context_registration_id"])
    assert context.context_sha256 == record["context_sha256"]
    assert context.released_from_registration_id == record["context_registration_id"]


def test_rehashed_context_or_chain_tampering_is_detected(tmp_path):
    _, _, _, arguments = values()
    ledger = ActivePipelineReplayContextLedger(
        tmp_path / "replay-context.jsonl",
        replay_plan_ledger=arguments["replay_plan_ledger"],
        image_approval_ledger=arguments["image_approval_ledger"],
    )
    ledger.preregister(**{
        key: value
        for key, value in arguments.items()
        if key not in {"replay_plan_ledger", "image_approval_ledger"}
    })
    record = json.loads(ledger.path.read_text(encoding="utf-8"))
    payload = json.loads(record["context_canonical_json"])
    payload["live_trading_enabled"] = True
    record["context_canonical_json"] = module._canonical_json(payload)
    record["context_sha256"] = hashlib.sha256(
        record["context_canonical_json"].encode("utf-8")
    ).hexdigest()
    record["context_registration_id"] = module._registration_id(
        record["context_sha256"]
    )
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    ledger.path.write_text(module._canonical_json(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        ledger.verify()


def test_engine_manifest_key_set_cannot_drift_from_the_active_pipeline():
    assert REQUIRED_ENGINE_KEYS == set(InvestmentResearchPipeline.load_engines())
