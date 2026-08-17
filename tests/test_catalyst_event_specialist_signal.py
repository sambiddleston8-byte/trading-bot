from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pandas as pd
import pytest

from core.research.catalyst_event_specialist import (
    CatalystEventSpecialistBot,
    build_catalyst_event_artifact,
)
from core.research.specialist_signals import (
    CatalystResearchExecutiveAggregatorBot,
    ExecutiveAggregatorBot,
)


UTC = timezone.utc
RETRIEVED = "2025-03-01T00:00:00+00:00"


def _row(event_id="AAPL-Q3", revision=1, available="2024-11-01T21:00:00+00:00", event_type="EARNINGS_RESULT", impact="0.8"):
    return {
        "symbol": "AAPL", "event_id": event_id, "event_type": event_type,
        "effective_at": "2024-10-31T21:00:00+00:00", "reported_at": available,
        "available_at": available, "revision": revision,
        "directional_impact": impact, "confidence": "0.75",
        "source_payload_sha256": hashlib.sha256(f"{event_id}:{revision}".encode()).hexdigest(),
        "source_locator": f"synthetic://catalyst/{event_id}/{revision}",
    }


def _artifact(rows):
    return build_catalyst_event_artifact(rows, retrieved_at=RETRIEVED)


def test_catalyst_artifact_is_train_only_immutable_and_pit_bound():
    artifact = _artifact([_row()])
    record = artifact["records"][0]
    assert record["available_at"] == record["observation_cutoff_at"]
    assert artifact["external_data_calls"] is False
    with pytest.raises(ValueError, match="TRAIN"):
        build_catalyst_event_artifact([_row()], retrieved_at=RETRIEVED, partition_role="VALIDATION")
    tampered = json.loads(json.dumps(artifact))
    tampered["records"][0]["directional_impact"] = "-1"
    with pytest.raises(ValueError, match="SHA-256"):
        CatalystEventSpecialistBot(tampered, expected_sha256=artifact["artifact_sha256"])


def test_scheduled_event_timing_never_invents_a_directional_outcome():
    scheduled = _row(event_type="SCHEDULED_EARNINGS", impact=None)
    artifact = _artifact([scheduled])
    bot = CatalystEventSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    signal = bot.score_tick("AAPL", decision_at="2024-11-02T21:00:00+00:00")
    assert signal.status == "ABSTAIN" and signal.score == 0
    assert signal.reason == "SCHEDULED_EVENT_OUTCOME_UNKNOWN"
    with pytest.raises(ValueError, match="cannot invent"):
        _artifact([_row(event_type="SCHEDULED_EARNINGS", impact="1")])


def test_tick_vector_parity_bounds_future_missing_and_stale_fail_closed():
    artifact = _artifact([_row()])
    bot = CatalystEventSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    decision = datetime(2024, 11, 2, 21, tzinfo=UTC)
    signal = bot.score_tick("AAPL", decision_at=decision)
    assert signal.score == Decimal("0.8") and signal.status == "ACTIVE"
    assert bot.score_tick("AAPL", decision_at="2024-10-30T21:00:00+00:00").status == "ABSTAIN"
    assert bot.score_tick("MSFT", decision_at=decision).status == "ABSTAIN"
    stale = bot.score_tick("AAPL", decision_at="2025-01-01T21:00:00+00:00")
    assert stale.status == "STALE" and stale.score == 0
    frame = bot.score_frame(pd.DataFrame({"symbol": ["AAPL"], "decision_at": [decision]}))
    assert frame.iloc[0]["score"] == "0.8"


def test_catalyst_revisions_require_contiguous_monotonic_availability():
    first = _row()
    second = _row(revision=2, available="2024-11-02T21:00:00+00:00", impact="-0.4")
    artifact = _artifact([first, second])
    assert artifact["records"][1]["prior_revision_sha256"] == artifact["records"][0]["record_sha256"]
    for invalid_available in ("2024-10-01T21:00:00+00:00", first["available_at"]):
        with pytest.raises(ValueError, match="available after its parent"):
            _artifact([first, _row(revision=2, available=invalid_available)])


def test_only_latest_available_revision_contributes_to_score_and_evidence():
    first = _row(impact="0.8")
    second = _row(
        revision=2,
        available="2024-11-05T21:00:00+00:00",
        impact="-0.5",
    )
    artifact = _artifact([first, second])
    bot = CatalystEventSpecialistBot(
        artifact, expected_sha256=artifact["artifact_sha256"]
    )
    before = bot.score_tick("AAPL", decision_at="2024-11-03T21:00:00+00:00")
    after = bot.score_tick("AAPL", decision_at="2024-11-10T21:00:00+00:00")
    assert before.score == Decimal("0.8") and before.evidence_count == 1
    assert after.score == Decimal("-0.5") and after.evidence_count == 1
    assert before.evidence_sha256 != after.evidence_sha256


def test_confidence_weighting_is_bounded_and_zero_confidence_is_neutral():
    positive = _row(event_id="POS", impact="1")
    negative = _row(event_id="NEG", impact="-1")
    negative["confidence"] = "0.25"
    artifact = _artifact([positive, negative])
    bot = CatalystEventSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    assert bot.score_tick("AAPL", decision_at="2024-11-02T21:00:00+00:00").score == Decimal("0.5")
    zero = _row(event_id="ZERO", impact="1")
    zero["confidence"] = "0"
    zero_artifact = _artifact([zero])
    neutral = CatalystEventSpecialistBot(zero_artifact, expected_sha256=zero_artifact["artifact_sha256"]).score_tick("AAPL", decision_at="2024-11-02T21:00:00+00:00")
    assert neutral.status == "NEUTRAL" and neutral.score == 0


def test_catalyst_candidate_is_isolated_from_registered_executive_and_risk_vote():
    assert CatalystResearchExecutiveAggregatorBot.VERSION.endswith("research-v1")
    assert "CATALYST_EVENT" in CatalystResearchExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "CATALYST_EVENT" not in ExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "RISK_REGIME" not in CatalystResearchExecutiveAggregatorBot.WEIGHTS
