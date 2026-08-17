from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pandas as pd
import pytest

from core.research.macro_cross_asset_specialist import (
    FACTOR_NAMES,
    MacroCrossAssetSpecialistBot,
    build_macro_cross_asset_artifact,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    MacroResearchExecutiveAggregatorBot,
)


UTC = timezone.utc
RETRIEVED = "2025-03-01T00:00:00+00:00"


def _row(
    snapshot_id="2024-10-MACRO",
    revision=1,
    effective="2024-10-01T00:00:00+00:00",
    available="2024-11-01T14:00:00+00:00",
    factors=None,
    sensitivities=None,
):
    return {
        "symbol": "AAPL", "snapshot_id": snapshot_id,
        "effective_at": effective, "reported_at": available,
        "available_at": available, "revision": revision,
        "factors": factors or {name: "1" for name in FACTOR_NAMES},
        "symbol_sensitivities": sensitivities or {name: "1" for name in FACTOR_NAMES},
        "series_payload_sha256": {
            name: hashlib.sha256(f"{snapshot_id}:{revision}:{name}".encode()).hexdigest()
            for name in FACTOR_NAMES
        },
        "source_locator": f"synthetic://macro/{snapshot_id}/{revision}",
    }


def _artifact(rows):
    return build_macro_cross_asset_artifact(rows, retrieved_at=RETRIEVED)


def test_macro_artifact_is_train_only_immutable_and_has_no_risk_authority():
    artifact = _artifact([_row()])
    record = artifact["records"][0]
    assert record["available_at"] == record["observation_cutoff_at"]
    assert artifact["external_data_calls"] is False
    assert artifact["risk_authority"] is False and artifact["constraint_output_allowed"] is False
    with pytest.raises(ValueError, match="TRAIN"):
        build_macro_cross_asset_artifact([_row()], retrieved_at=RETRIEVED, partition_role="VALIDATION")
    tampered = json.loads(json.dumps(artifact))
    tampered["records"][0]["factors"]["rates"] = "-1"
    with pytest.raises(ValueError, match="SHA-256"):
        MacroCrossAssetSpecialistBot(tampered, expected_sha256=artifact["artifact_sha256"])


def test_unpublished_missing_and_stale_macro_evidence_fail_closed_with_tick_vector_parity():
    artifact = _artifact([_row()])
    bot = MacroCrossAssetSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-10-15T14:00:00+00:00")
    assert before.status == "ABSTAIN" and before.score == 0
    decision = datetime(2024, 11, 2, 14, tzinfo=UTC)
    signal = bot.score_tick("AAPL", decision_at=decision)
    assert signal.status == "ACTIVE" and signal.score == 1 and signal.confidence == 1
    assert bot.score_tick("MSFT", decision_at=decision).status == "ABSTAIN"
    stale = bot.score_tick("AAPL", decision_at="2025-03-02T14:00:00+00:00")
    assert stale.status == "STALE" and stale.score == 0
    frame = bot.score_frame(pd.DataFrame({"symbol": ["AAPL"], "decision_at": [decision]}))
    assert frame.iloc[0]["score"] == "1"


def test_macro_score_is_bounded_factor_sensitivity_opinion_not_a_constraint():
    factors = {"rates": "1", "inflation": "-1", "liquidity": "0.5", "cross_asset": "-0.5"}
    sensitivities = {"rates": "1", "inflation": "1", "liquidity": "-1", "cross_asset": "-1"}
    artifact = _artifact([_row(factors=factors, sensitivities=sensitivities)])
    signal = MacroCrossAssetSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"]).score_tick(
        "AAPL", decision_at="2024-11-02T14:00:00+00:00"
    )
    assert signal.score == Decimal("0") and signal.status == "NEUTRAL"
    assert signal.confidence == Decimal("0.75") and signal.coverage == 1
    assert signal.reason_codes == ("ALPHA_OPINION_ONLY", "NO_RISK_AUTHORITY")


def test_factor_family_hashes_and_bounds_are_strict():
    invalid = _row()
    invalid["factors"] = {**invalid["factors"], "rates": "1.1"}
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        _artifact([invalid])
    missing = _row()
    del missing["series_payload_sha256"]["inflation"]
    with pytest.raises((KeyError, ValueError)):
        _artifact([missing])
    malformed = _row()
    malformed["series_payload_sha256"]["rates"] = "not-a-hash"
    with pytest.raises(ValueError, match="SHA-256"):
        _artifact([malformed])


def test_revisions_preserve_period_advance_availability_and_replace_prior_state():
    first = _row(factors={name: "1" for name in FACTOR_NAMES})
    second = _row(
        revision=2, available="2024-11-05T14:00:00+00:00",
        factors={name: "-1" for name in FACTOR_NAMES},
    )
    artifact = _artifact([first, second])
    assert artifact["records"][1]["prior_revision_sha256"] == artifact["records"][0]["record_sha256"]
    bot = MacroCrossAssetSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-11-03T14:00:00+00:00")
    after = bot.score_tick("AAPL", decision_at="2024-11-06T14:00:00+00:00")
    assert before.score == 1 and after.score == -1
    assert before.evidence_count == after.evidence_count == 1
    with pytest.raises(ValueError, match="after its parent"):
        _artifact([first, _row(revision=2, available=first["available_at"])])
    with pytest.raises(ValueError, match="preserve its period"):
        _artifact([first, _row(revision=2, effective="2024-11-01T00:00:00+00:00", available="2024-11-05T14:00:00+00:00")])


def test_latest_available_effective_snapshot_is_the_only_scored_state():
    earlier = _row(snapshot_id="EARLIER", effective="2024-09-01T00:00:00+00:00", factors={name: "-1" for name in FACTOR_NAMES})
    later = _row(snapshot_id="LATER", effective="2024-10-01T00:00:00+00:00", available="2024-11-05T14:00:00+00:00")
    artifact = _artifact([earlier, later])
    bot = MacroCrossAssetSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-11-03T14:00:00+00:00")
    after = bot.score_tick("AAPL", decision_at="2024-11-06T14:00:00+00:00")
    assert before.score == -1 and after.score == 1
    assert before.evidence_count == after.evidence_count == 1


def test_macro_candidate_is_isolated_from_registered_executive_and_risk_vote():
    assert MacroResearchExecutiveAggregatorBot.VERSION.endswith("research-v1")
    assert "MACRO_CROSS_ASSET" in MacroResearchExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "MACRO_CROSS_ASSET" not in ExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "RISK_REGIME" not in MacroResearchExecutiveAggregatorBot.WEIGHTS
