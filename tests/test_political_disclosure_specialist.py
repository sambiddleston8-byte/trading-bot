from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import pandas as pd
import pytest

from core.research.political_disclosure_specialist import (
    PoliticalDisclosureSpecialistBot,
    build_political_disclosure_artifact,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    PoliticalResearchExecutiveAggregatorBot,
)


UTC = timezone.utc
RETRIEVED = "2025-03-01T00:00:00+00:00"


def _row(
    transaction_key="AAPL-MEMBER-2024-10-01",
    revision=1,
    available="2024-11-01T14:00:00+00:00",
    transaction_type="PURCHASE",
    minimum="10000",
    maximum="10000",
    source="OFFICIAL_HOUSE",
):
    return {
        "symbol": "AAPL", "transaction_key": transaction_key,
        "disclosure_id": f"DISC-{transaction_key}-{revision}", "source": source,
        "effective_at": "2024-10-01T00:00:00+00:00",
        "reported_at": "2024-10-20T14:00:00+00:00",
        "available_at": available, "revision": revision,
        "transaction_type": transaction_type,
        "amount_min_usd": minimum, "amount_max_usd": maximum,
        "raw_document_sha256": hashlib.sha256(f"raw:{transaction_key}:{revision}".encode()).hexdigest(),
        "availability_evidence_sha256": hashlib.sha256(f"available:{transaction_key}:{revision}".encode()).hexdigest(),
        "source_locator": f"synthetic://political/{transaction_key}/{revision}",
    }


def _artifact(rows):
    return build_political_disclosure_artifact(rows, retrieved_at=RETRIEVED)


def test_political_artifact_is_train_only_immutable_official_and_non_copy_trade():
    artifact = _artifact([_row()])
    record = artifact["records"][0]
    assert record["available_at"] == record["observation_cutoff_at"]
    assert artifact["availability_semantics"] == "OFFICIAL_PUBLICATION_TIMESTAMP_NOT_TRANSACTION_DATE"
    assert artifact["external_data_calls"] is False and artifact["copy_trade_allowed"] is False
    with pytest.raises(ValueError, match="TRAIN"):
        build_political_disclosure_artifact([_row()], retrieved_at=RETRIEVED, partition_role="VALIDATION")
    with pytest.raises(ValueError, match="official"):
        _artifact([_row(source="LICENSED_PROVIDER")])
    tampered = json.loads(json.dumps(artifact))
    tampered["records"][0]["amount_max_usd"] = "999999"
    with pytest.raises(ValueError, match="SHA-256"):
        PoliticalDisclosureSpecialistBot(tampered, expected_sha256=artifact["artifact_sha256"])


def test_transaction_date_and_filing_do_not_make_unpublished_disclosure_available():
    artifact = _artifact([_row()])
    bot = PoliticalDisclosureSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-10-25T14:00:00+00:00")
    after = bot.score_tick("AAPL", decision_at="2024-11-02T14:00:00+00:00")
    assert before.status == "ABSTAIN" and before.score == 0
    assert after.status == "ACTIVE" and after.score == 1
    assert after.maximum_input_available_at == "2024-11-01T14:00:00+00:00"


def test_tick_vector_parity_bounds_future_missing_and_stale_fail_closed():
    artifact = _artifact([_row()])
    bot = PoliticalDisclosureSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    decision = datetime(2024, 11, 2, 14, tzinfo=UTC)
    signal = bot.score_tick("AAPL", decision_at=decision)
    assert signal.score == Decimal("1") and signal.status == "ACTIVE"
    assert bot.score_tick("MSFT", decision_at=decision).status == "ABSTAIN"
    stale = bot.score_tick("AAPL", decision_at="2025-03-01T14:00:00+00:00")
    assert stale.status == "STALE" and stale.score == 0
    frame = bot.score_frame(pd.DataFrame({"symbol": ["AAPL"], "decision_at": [decision]}))
    assert frame.iloc[0]["score"] == "1"


def test_range_overlap_and_exchange_evidence_are_neutral_not_invented_direction():
    purchase = _row(transaction_key="BUY", minimum="1", maximum="100")
    sale = _row(transaction_key="SELL", transaction_type="SALE", minimum="50", maximum="150")
    exchange = _row(transaction_key="EXCHANGE", transaction_type="EXCHANGE", minimum="500", maximum="500")
    artifact = _artifact([purchase, sale, exchange])
    signal = PoliticalDisclosureSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"]).score_tick(
        "AAPL", decision_at="2024-11-02T14:00:00+00:00"
    )
    assert signal.status == "NEUTRAL" and signal.score == 0 and signal.confidence == 0


def test_revisions_require_monotonic_availability_and_replace_only_when_published():
    first = _row(transaction_type="PURCHASE")
    second = _row(revision=2, available="2024-11-05T14:00:00+00:00", transaction_type="SALE")
    artifact = _artifact([first, second])
    assert artifact["records"][1]["prior_revision_sha256"] == artifact["records"][0]["record_sha256"]
    bot = PoliticalDisclosureSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"])
    before = bot.score_tick("AAPL", decision_at="2024-11-03T14:00:00+00:00")
    after = bot.score_tick("AAPL", decision_at="2024-11-06T14:00:00+00:00")
    assert before.score == 1 and before.evidence_count == 1
    assert after.score == -1 and after.evidence_count == 1
    assert before.evidence_sha256 != after.evidence_sha256
    with pytest.raises(ValueError, match="available after its parent"):
        _artifact([first, _row(revision=2, available=first["available_at"], transaction_type="SALE")])


def test_amount_ranges_are_validated_and_scores_remain_bounded():
    with pytest.raises(ValueError, match="amount range"):
        _artifact([_row(minimum="100", maximum="10")])
    artifact = _artifact([_row(minimum="1001", maximum="15000")])
    signal = PoliticalDisclosureSpecialistBot(artifact, expected_sha256=artifact["artifact_sha256"]).score_tick(
        "AAPL", decision_at="2024-11-02T14:00:00+00:00"
    )
    assert Decimal("0") < signal.score < Decimal("1")
    assert signal.confidence == signal.score


def test_political_candidate_is_isolated_from_registered_executive_and_risk_vote():
    assert PoliticalResearchExecutiveAggregatorBot.VERSION.endswith("research-v1")
    assert "POLITICAL_DISCLOSURE" in PoliticalResearchExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "POLITICAL_DISCLOSURE" not in ExecutiveAggregatorBot.REQUIRED_SPECIALISTS
    assert "RISK_REGIME" not in PoliticalResearchExecutiveAggregatorBot.WEIGHTS
