from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from core.decision_ledger import current_git_revision
from core.orchestration.active_pipeline_image_approval import (
    FIXED_FALSE as IMAGE_FIXED_FALSE,
    REQUIRED_ENTRYPOINT,
    REQUIRED_ENVIRONMENT,
)
from core.orchestration.active_pipeline_replay_context import (
    ActivePipelineReplayContext,
    ActivePipelineReplayContextLedger,
    REQUIRED_ENGINE_KEYS,
)
from core.orchestration import active_pipeline_replay_context as context_module
from core.orchestration.active_pipeline_replay_plan import REQUIRED_COMPONENTS
from core.orchestration.sealed_replay_invocation import (
    SealedReplayInvocation,
    active_pipeline_component_registry,
    authenticated_source_snapshot_digests,
    describe_engine_registry,
    learning_state_digest,
)
from core.orchestration import sealed_replay_invocation as invocation_module
from core.research.investment_research_pipeline import InvestmentResearchPipeline
from core.research import investment_research_pipeline as pipeline_module


ROOT = Path(__file__).resolve().parents[1]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class VerifiedLedger:
    def __init__(self, records):
        self.records = records

    def verify(self):
        return list(self.records)


class RegistryEngine:
    VERSION = "sealed-registry-test-v1"


def registry():
    return {name: RegistryEngine for name in REQUIRED_ENGINE_KEYS}


def build_ledger(
    tmp_path,
    monkeypatch,
    *,
    sealed_identities=None,
    custom_registry=None,
):
    registered_at = datetime.now(timezone.utc)
    access_at = registered_at + timedelta(hours=1)
    engine_registry = custom_registry or registry()
    actual_identities = describe_engine_registry(
        engine_registry, required_keys=REQUIRED_ENGINE_KEYS
    )
    identities = sealed_identities or actual_identities
    components = describe_engine_registry(
        active_pipeline_component_registry(engine_registry),
        required_keys=REQUIRED_COMPONENTS,
    )
    components["master_portfolio_decision_engine"] = dict(
        identities["master_decision"]
    )
    plan = {
        "replay_plan_id": "REPLAY-" + "1" * 32,
        "record_hash": digest("plan-record"),
        "git_revision": current_git_revision(ROOT),
        "components": components,
        "dependency_lock_sha256": digest("dependency-lock"),
        "runner_sha256": digest("approved-runner-image"),
        "sealed_evaluation_dataset_commitment_sha256": digest("sealed-dataset"),
        "evaluation_not_before": "2020-01-01T00:00:00+00:00",
        "evaluation_not_after": "2021-01-01T00:00:00+00:00",
        "evaluation_data_access_not_before": access_at.isoformat(),
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
    source_ledger = tmp_path / "authenticated-sources.jsonl"
    source_ledger.write_bytes(b"")
    source_blobs = tmp_path / "source-blobs"
    source_blobs.mkdir()
    learning_state = tmp_path / "learning-state.json"
    learning_state.write_text("[]", encoding="utf-8")
    source_ledger_digest, source_manifest_digest = (
        authenticated_source_snapshot_digests(source_ledger, source_blobs)
    )

    class FrozenDateTime(datetime):
        current = registered_at

        @classmethod
        def now(cls, tz=None):
            value = cls.current
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(context_module, "datetime", FrozenDateTime)
    plan_ledger = VerifiedLedger([plan])
    image_ledger = VerifiedLedger([image])
    ledger = ActivePipelineReplayContextLedger(
        tmp_path / "replay-context.jsonl",
        replay_plan_ledger=plan_ledger,
        image_approval_ledger=image_ledger,
    )
    record = ledger.preregister(
        replay_plan=plan,
        image_approval=image,
        engine_dependencies=identities,
        factor_lineage_policy=components["factor_lineage_policy"],
        as_of_schedule=[
            "2020-03-31T20:00:00+00:00",
            "2020-06-30T20:00:00+00:00",
        ],
        learning_state_sha256=learning_state_digest(learning_state),
        authenticated_source_ledger_sha256=source_ledger_digest,
        authenticated_source_blobs_manifest_sha256=source_manifest_digest,
        registered_by="SEALED_INVOCATION_TEST",
        registered_at=registered_at,
    )
    FrozenDateTime.current = access_at
    return {
        "ledger": ledger,
        "record": record,
        "registry": engine_registry,
        "source_ledger": source_ledger,
        "source_blobs": source_blobs,
        "learning_state": learning_state,
        "clock": FrozenDateTime,
        "plan": plan,
    }


def open_invocation(values, **changes):
    arguments = {
        "as_of_index": 1,
        "engine_registry": values["registry"],
        "authenticated_source_ledger_path": values["source_ledger"],
        "authenticated_source_blobs_directory": values["source_blobs"],
        "learning_state_path": values["learning_state"],
        **changes,
    }
    return values["ledger"].open_invocation(
        values["record"]["context_registration_id"],
        **arguments,
    )


def test_ledger_issues_one_exact_immutable_schedule_invocation(tmp_path, monkeypatch):
    values = build_ledger(tmp_path, monkeypatch)
    invocation = open_invocation(values)

    assert invocation.as_of_index == 1
    assert invocation.as_of == "2020-06-30T20:00:00.000000+00:00"
    assert invocation.now() == invocation.as_of
    assert invocation.git_revision == values["plan"]["git_revision"]
    assert invocation.registration_id == values["record"]["context_registration_id"]
    assert set(invocation.engine_registry) == REQUIRED_ENGINE_KEYS
    with pytest.raises(TypeError):
        invocation.engine_registry["valuation"] = RegistryEngine
    assert not hasattr(invocation, "run")
    assert not hasattr(invocation, "save")
    assert not hasattr(invocation, "submit")


def test_direct_or_fabricated_invocation_authority_is_rejected(tmp_path, monkeypatch):
    values = build_ledger(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="verified context ledger"):
        SealedReplayInvocation(
            registration_id="RCTX-FAKE",
            context_sha256="0" * 64,
            as_of="2020-03-31T20:00:00+00:00",
            as_of_index=0,
            git_revision="0" * 40,
            engine_registry=values["registry"],
            engine_identities={},
            pipeline_component_identities={},
            authenticated_source_ledger_path=values["source_ledger"],
            authenticated_source_blobs_directory=values["source_blobs"],
            learning_state_path=values["learning_state"],
            authenticated_source_ledger_sha256="0" * 64,
            authenticated_source_blobs_manifest_sha256="0" * 64,
            learning_state_sha256="0" * 64,
        )

    fabricated_context = ActivePipelineReplayContext(
        canonical_json="{}",
        context_sha256="0" * 64,
        released_from_registration_id=values["record"]["context_registration_id"],
    )
    with pytest.raises(ValueError, match="verified replay-context"):
        values["ledger"].open_invocation(
            fabricated_context,
            as_of_index=0,
            engine_registry=values["registry"],
            authenticated_source_ledger_path=values["source_ledger"],
            authenticated_source_blobs_directory=values["source_blobs"],
            learning_state_path=values["learning_state"],
        )


@pytest.mark.parametrize("as_of_index", [-1, 2, True, 1.0])
def test_as_of_index_must_select_one_preregistered_instant(
    tmp_path, monkeypatch, as_of_index
):
    values = build_ledger(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="as_of_index"):
        open_invocation(values, as_of_index=as_of_index)


@pytest.mark.parametrize("changed_key", sorted(REQUIRED_ENGINE_KEYS))
def test_every_engine_identity_must_match_the_preregistered_source(
    tmp_path, monkeypatch, changed_key
):
    actual = describe_engine_registry(registry(), required_keys=REQUIRED_ENGINE_KEYS)
    sealed = {key: dict(value) for key, value in actual.items()}
    sealed[changed_key]["version"] += "-different"
    values = build_ledger(tmp_path, monkeypatch, sealed_identities=sealed)

    with pytest.raises(ValueError, match="sealed identities"):
        open_invocation(values)


def test_missing_or_extra_engine_and_git_drift_fail_closed(tmp_path, monkeypatch):
    values = build_ledger(tmp_path, monkeypatch)
    missing = dict(values["registry"])
    missing.pop("valuation")
    with pytest.raises(ValueError, match="exactly"):
        open_invocation(values, engine_registry=missing)

    extra = {**values["registry"], "unexpected": RegistryEngine}
    with pytest.raises(ValueError, match="exactly"):
        open_invocation(values, engine_registry=extra)

    monkeypatch.setattr(context_module, "current_git_revision", lambda root: "f" * 40)
    with pytest.raises(ValueError, match="Git revision"):
        open_invocation(values)


@pytest.mark.parametrize("target", ["source_ledger", "learning_state"])
def test_sealed_source_or_learning_bytes_cannot_change(
    tmp_path, monkeypatch, target
):
    values = build_ledger(tmp_path, monkeypatch)
    values[target].write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source ledger|learning state"):
        open_invocation(values)


@pytest.mark.parametrize("target", ["source_ledger", "learning_state"])
def test_invocation_revalidates_immutable_bytes_at_pipeline_use(
    tmp_path, monkeypatch, target
):
    values = build_ledger(tmp_path, monkeypatch)
    invocation = open_invocation(values)
    values[target].write_text("[]\n" if target == "learning_state" else "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source ledger|learning state"):
        invocation.require_valid()


def test_invocation_revalidates_engine_and_active_component_sources(
    tmp_path, monkeypatch
):
    values = build_ledger(tmp_path, monkeypatch)
    invocation = open_invocation(values)
    monkeypatch.setattr(RegistryEngine, "VERSION", "changed-after-issue")
    with pytest.raises(ValueError, match="engine source identity"):
        invocation.require_valid()

    monkeypatch.setattr(RegistryEngine, "VERSION", "sealed-registry-test-v1")
    actual_components = active_pipeline_component_registry(values["registry"])
    monkeypatch.setattr(
        invocation_module,
        "active_pipeline_component_registry",
        lambda engine_registry: {
            **actual_components,
            "factor_lineage_policy": RegistryEngine,
        },
    )
    with pytest.raises(ValueError, match="active pipeline source identity"):
        invocation.require_valid()


class FundamentalStub:
    VERSION = "fundamental-stub-v1"

    def __init__(self):
        self.last_context = {"as_of": "sealed"}

    def analyse(self, ticker):
        return {
            "ticker": ticker,
            "growth": {},
            "forecast_validation": {},
            "data_quality": {},
            "validation": {},
            "provenance": {},
        }


class ValuationStub:
    VERSION = "valuation-stub-v1"

    def analyse(self, ticker):
        return {"ticker": ticker}


class DecisionStub:
    VERSION = "decision-stub-v1"

    def analyse(self, fundamental, valuation):
        return {
            "decision": "HOLD",
            "scores": {},
            "valuation": {},
            "confidence": {},
        }


class ValuationQualityStub:
    VERSION = "valuation-quality-stub-v1"

    @staticmethod
    def assess(valuation, decision, sources):
        return {"status": "STUBBED"}


class SourceLedgerStub:
    VERSION = "source-ledger-stub-v1"
    last_paths = None

    def __init__(self, path, blob_directory):
        type(self).last_paths = (Path(path), Path(blob_directory))


class MarketSignalsStub:
    VERSION = "market-signals-stub-v1"

    def analyse(self, ticker, context=None):
        return {"ticker": ticker, "context": context}


class SpecialistStub:
    VERSION = "specialist-stub-v1"

    @staticmethod
    def analyse(context=None):
        return {"context": context}


class NewsStub:
    VERSION = "news-stub-v1"

    def analyse(self, ticker):
        return {"status": "COMPLETE", "items": []}


class FailingNewsStub(NewsStub):
    VERSION = "failing-news-stub-v1"

    def analyse(self, ticker):
        raise RuntimeError("news stage failed")


class SentimentStub:
    VERSION = "sentiment-stub-v1"

    @staticmethod
    def analyse(news):
        return {"score": 0}


class MarketRegimeStub:
    VERSION = "market-regime-stub-v1"

    def analyse(self):
        return {"regime": "NEUTRAL"}


class MacroStub:
    VERSION = "macro-stub-v1"

    def analyse(self):
        return {"environment": "NEUTRAL"}


class CatalystStub:
    VERSION = "catalyst-stub-v1"

    def analyse(self, ticker):
        return {"catalysts": [{"name": "stub catalyst"}]}


class FailingCatalystStub(CatalystStub):
    VERSION = "failing-catalyst-stub-v1"

    def analyse(self, ticker):
        raise RuntimeError("catalyst stage failed")


class CatalystProbabilityStub:
    VERSION = "catalyst-probability-stub-v1"

    @staticmethod
    def assess(catalyst):
        return {"probability": 0.5}


class FailingCatalystProbabilityStub(CatalystProbabilityStub):
    VERSION = "failing-catalyst-probability-stub-v1"

    @staticmethod
    def assess(catalyst):
        raise RuntimeError("catalyst validation failed")


class CatalystValidationStub:
    VERSION = "catalyst-validation-stub-v1"

    @staticmethod
    def validate(catalyst):
        return catalyst

    @staticmethod
    def summary(catalysts):
        return {"positive_score": 0, "negative_score": 0}


class ThesisStub:
    VERSION = "thesis-stub-v1"

    @staticmethod
    def build(**values):
        return dict(values)

    @staticmethod
    def populate_findings(investigation):
        return investigation

    @staticmethod
    def calculate_result(investigation):
        return dict(investigation)

    @staticmethod
    def summary(result):
        return {
            "result": "THESIS_SURVIVES",
            "challenge_count": 1,
            "tested": 1,
            "material_negative": 0,
            "thesis_survives": True,
        }


class FailingThesisStub(ThesisStub):
    VERSION = "failing-thesis-stub-v1"

    @staticmethod
    def build(**values):
        raise RuntimeError("thesis stage failed")


class SynthesisStub:
    VERSION = "synthesis-stub-v1"

    @staticmethod
    def synthesise(values):
        return {"decision": "HOLD", "investment_case_score": 50}


class AuditStub:
    VERSION = "audit-stub-v1"

    @staticmethod
    def audit(values):
        return {"status": "PASS"}


class SupplementalStub:
    VERSION = "supplemental-stub-v1"

    @staticmethod
    def collect(ticker):
        return {"summary": {}, "ticker": ticker}

    @staticmethod
    def access_observations(evidence):
        return []


class FailingTelemetryStub(SupplementalStub):
    VERSION = "failing-telemetry-stub-v1"

    @staticmethod
    def access_observations(evidence):
        raise RuntimeError("provider telemetry failed")


class MasterDecisionStub:
    VERSION = "master-decision-stub-v1"

    @staticmethod
    def evaluate(canonical, *, catalysts, learning_adjustment):
        return {
            "decision": canonical.get("decision"),
            "learning_adjustment": learning_adjustment,
        }


class LearningStub:
    VERSION = "learning-stub-v1"
    last_path = None

    @classmethod
    def for_decision(cls, decision, path=None):
        cls.last_path = None if path is None else Path(path)
        return {"status": "STUBBED", "adjustment": 0}


class DiagnosticsStub:
    VERSION = "diagnostics-stub-v1"

    @staticmethod
    def analyse(result):
        return {"status": "STUBBED"}


class UnusedStub:
    VERSION = "unused-stub-v1"


def full_pipeline_registry(**overrides):
    result = {
        "fundamental": FundamentalStub,
        "valuation": ValuationStub,
        "decision": DecisionStub,
        "catalyst": CatalystStub,
        "news": NewsStub,
        "catalyst_bridge": UnusedStub,
        "catalyst_validation": CatalystValidationStub,
        "catalyst_probability": CatalystProbabilityStub,
        "thesis": ThesisStub,
        "synthesis": SynthesisStub,
        "audit": AuditStub,
        "market_signals": MarketSignalsStub,
        "sentiment": SentimentStub,
        "market_regime": MarketRegimeStub,
        "macro_environment": MacroStub,
        "specialist_research": SpecialistStub,
        "master_decision": MasterDecisionStub,
        "outcome_learning": LearningStub,
        "valuation_quality": ValuationQualityStub,
        "authenticated_sources": SourceLedgerStub,
        "diagnostics": DiagnosticsStub,
        "supplemental_evidence": SupplementalStub,
    }
    result.update(overrides)
    return result


def test_pipeline_uses_only_ledger_issued_inputs_and_frozen_time(
    tmp_path, monkeypatch
):
    engine_registry = full_pipeline_registry()
    values = build_ledger(
        tmp_path,
        monkeypatch,
        custom_registry=engine_registry,
    )
    invocation = open_invocation(values)

    def ambient_state_forbidden(*args, **kwargs):
        raise AssertionError("ambient state was consulted")

    monkeypatch.setattr(
        InvestmentResearchPipeline,
        "load_engines",
        staticmethod(ambient_state_forbidden),
    )
    monkeypatch.setattr(
        InvestmentResearchPipeline,
        "now",
        staticmethod(ambient_state_forbidden),
    )
    monkeypatch.setattr(
        pipeline_module,
        "current_git_revision",
        ambient_state_forbidden,
    )

    result = InvestmentResearchPipeline.analyse(
        "TEST",
        save=False,
        replay=invocation,
    )

    assert result["status"] == "COMPLETE"
    assert result["started_at"] == invocation.as_of
    assert result["completed_at"] == invocation.as_of
    assert result["source_git_revision"] == invocation.git_revision
    assert SourceLedgerStub.last_paths == (
        invocation.authenticated_source_ledger_path,
        invocation.authenticated_source_blobs_directory,
    )
    assert LearningStub.last_path == invocation.learning_state_path


def test_replay_save_is_rejected_before_any_work_or_write(tmp_path, monkeypatch):
    values = build_ledger(
        tmp_path,
        monkeypatch,
        custom_registry=full_pipeline_registry(),
    )
    invocation = open_invocation(values)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(PermissionError, match="cannot save"):
        InvestmentResearchPipeline.analyse("TEST", replay=invocation)

    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "override,use_telemetry,error",
    [
        ({"news": FailingNewsStub}, False, "news stage failed"),
        ({"catalyst": FailingCatalystStub}, False, "catalyst stage failed"),
        (
            {"catalyst_probability": FailingCatalystProbabilityStub},
            False,
            "catalyst validation failed",
        ),
        ({"thesis": FailingThesisStub}, False, "thesis stage failed"),
        (
            {"supplemental_evidence": FailingTelemetryStub},
            True,
            "provider telemetry failed",
        ),
    ],
)
def test_every_degraded_stage_is_fatal_only_in_replay_mode(
    tmp_path,
    monkeypatch,
    override,
    use_telemetry,
    error,
):
    engine_registry = full_pipeline_registry(**override)
    values = build_ledger(
        tmp_path,
        monkeypatch,
        custom_registry=engine_registry,
    )
    invocation = open_invocation(values)

    with pytest.raises(RuntimeError, match=error):
        if use_telemetry:
            InvestmentResearchPipeline.analyse_with_telemetry(
                "TEST",
                save=False,
                replay=invocation,
            )
        else:
            InvestmentResearchPipeline.analyse(
                "TEST",
                save=False,
                replay=invocation,
            )

    monkeypatch.setattr(
        InvestmentResearchPipeline,
        "load_engines",
        staticmethod(lambda: engine_registry),
    )
    if use_telemetry:
        normal_result, _ = InvestmentResearchPipeline.analyse_with_telemetry(
            "TEST",
            save=False,
        )
    else:
        normal_result = InvestmentResearchPipeline.analyse("TEST", save=False)
    assert normal_result["status"] == "COMPLETE"


def test_identical_replay_invocations_are_byte_identical(tmp_path, monkeypatch):
    values = build_ledger(
        tmp_path,
        monkeypatch,
        custom_registry=full_pipeline_registry(),
    )
    invocation = open_invocation(values)

    first = InvestmentResearchPipeline.analyse("TEST", save=False, replay=invocation)
    second = InvestmentResearchPipeline.analyse("TEST", save=False, replay=invocation)

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second,
        sort_keys=True,
        separators=(",", ":"),
    )
