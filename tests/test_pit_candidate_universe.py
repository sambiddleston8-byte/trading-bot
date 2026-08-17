from datetime import datetime, timedelta, timezone

import pytest

from core.portfolio.pit_candidate_universe import reconstruct_buffered_universe


DECISION = "2026-07-31T20:30:00+00:00"


def snapshot(count=125):
    members = [
        {
            "security_id": f"SEC-{index:03d}",
            "ticker": f"S{index:03d}",
            "issuer_name": f"Issuer {index}",
            "exchange_mic": "XNYS",
            "listing_effective_at": "2020-01-01T00:00:00+00:00",
            "listing_event_id": f"LIST-{index}",
            "membership_event_id": f"MEM-{index}",
            "membership_event_record_hash": "a" * 64,
        }
        for index in range(1, count + 1)
    ]
    return {
        "snapshot_id": "SMSNAP-SYNTHETIC",
        "record_type": "POINT_IN_TIME_SECURITY_MASTER_SNAPSHOT",
        "universe": "SP500",
        "effective_as_of": DECISION,
        "known_as_of": DECISION,
        "permanent_identity_used": True,
        "current_membership_used": False,
        "members": members,
        "exclusions_retained": [],
    }


def observations(master):
    total = len(master["members"])
    return [
        {
            "security_id": member["security_id"],
            "effective_at": "2026-07-31T20:00:00+00:00",
            "available_at": "2026-07-31T20:20:00+00:00",
            "price": "25",
            "trailing_20_session_median_dollar_volume": str(
                (total - index + 21) * 1_000_000
            ),
        }
        for index, member in enumerate(master["members"], start=1)
    ]


def test_month_end_uses_top_100_entry_and_top_120_incumbent_buffer():
    master = snapshot()
    result = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=observations(master),
        incumbent_security_ids=["SEC-115", "SEC-121"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=True,
    )
    ids = [item["security_id"] for item in result["members"]]
    assert ids[:3] == ["SEC-001", "SEC-002", "SEC-003"]
    assert "SEC-100" in ids
    assert "SEC-115" in ids
    assert "SEC-101" not in ids
    assert "SEC-121" not in ids
    assert result["entry_count"] == 100
    assert result["retained_count"] == 1
    assert result["exits"] == [
        {"security_id": "SEC-121", "reason": "RETENTION_RANK_FAILED"}
    ]
    assert result["member_count"] == 101
    assert result["current_membership_used"] is False
    assert result["partition_admission_authorized"] is False


def test_non_review_session_never_adds_but_immediate_floor_failure_exits():
    master = snapshot(3)
    rows = observations(master)
    rows[0]["price"] = "4.99"
    result = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=rows,
        incumbent_security_ids=["SEC-001", "SEC-002"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=False,
    )
    assert [item["security_id"] for item in result["members"]] == ["SEC-002"]
    assert result["members"][0]["membership_status"] == "RETAINED"
    assert result["entry_count"] == 0
    assert result["exits"] == [
        {"security_id": "SEC-001", "reason": "PRICE_FLOOR_FAILED"}
    ]


def test_spy_is_excluded_as_alpha_asset():
    master = snapshot(2)
    master["members"][0]["security_id"] = "SEC-SPY"
    master["members"][0]["ticker"] = "NOTSPY"
    result = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=observations(master),
        incumbent_security_ids=["SEC-SPY"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=True,
    )
    assert [item["security_id"] for item in result["members"]] == ["SEC-002"]
    assert result["exits"] == [{
        "security_id": "SEC-SPY",
        "reason": "BENCHMARK_EXCLUDED_AS_ALPHA_ASSET",
    }]


def test_exact_liquidity_tie_breaks_by_permanent_security_id():
    master = snapshot(101)
    rows = observations(master)
    for row in rows:
        row["trailing_20_session_median_dollar_volume"] = "30000000"
    result = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=rows,
        incumbent_security_ids=[],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=True,
    )
    ids = [item["security_id"] for item in result["members"]]
    assert "SEC-100" in ids
    assert "SEC-101" not in ids


@pytest.mark.parametrize("field", ["effective_at", "available_at"])
def test_late_market_observation_fails_closed(field):
    master = snapshot(2)
    rows = observations(master)
    rows[0][field] = (
        datetime.fromisoformat(DECISION) + timedelta(seconds=1)
    ).astimezone(timezone.utc).isoformat()
    with pytest.raises(ValueError, match="not PIT-available"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=rows,
            incumbent_security_ids=[],
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=True,
        )


def test_missing_or_extra_market_observation_fails_closed():
    master = snapshot(2)
    rows = observations(master)
    with pytest.raises(ValueError, match="exactly one"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=rows[:-1],
            incumbent_security_ids=[],
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=True,
        )
    rows[0] = {**rows[0], "security_id": "SEC-999"}
    with pytest.raises(ValueError, match="outside"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=rows,
            incumbent_security_ids=[],
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=True,
        )


def test_output_is_input_order_invariant():
    master = snapshot(5)
    rows = observations(master)
    first = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=rows,
        incumbent_security_ids=["SEC-004", "SEC-002"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=True,
    )
    second = reconstruct_buffered_universe(
        security_master_snapshot={**master, "members": list(reversed(master["members"]))},
        market_observations=list(reversed(rows)),
        incumbent_security_ids=["SEC-002", "SEC-004"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=True,
    )
    assert first == second


def test_post_decision_master_knowledge_fails_closed():
    master = snapshot(2)
    master["known_as_of"] = "2026-08-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="knowledge cutoff cannot follow"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=observations(master),
            incumbent_security_ids=[],
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=True,
        )


def test_missing_incumbent_requires_exact_master_exit_evidence():
    master = snapshot(2)
    with pytest.raises(ValueError, match="lacks a verified master exit"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=observations(master),
            incumbent_security_ids=["SEC-999"],
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=False,
        )


def test_verified_master_exit_is_propagated_for_frozen_exit_policy():
    master = snapshot(2)
    master["exclusions_retained"] = [{
        "security_id": "SEC-999",
        "ticker": "OLD",
        "issuer_name": "Old issuer",
        "exit_type": "DELISTED",
        "exit_effective_at": DECISION,
        "exit_event_id": "SMEV-EXIT",
        "exit_event_record_hash": "f" * 64,
        "terminal_outcome_treatment": "LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
    }]
    result = reconstruct_buffered_universe(
        security_master_snapshot=master,
        market_observations=observations(master),
        incumbent_security_ids=["SEC-999"],
        benchmark_security_id="SEC-SPY",
        is_final_nyse_session_of_month=False,
    )
    assert result["exits"] == [{
        "security_id": "SEC-999",
        "reason": "DELISTED",
        "exit_effective_at": DECISION,
        "exit_event_id": "SMEV-EXIT",
        "exit_event_record_hash": "f" * 64,
        "terminal_outcome_treatment": "LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
    }]


def test_incumbent_state_cannot_exceed_hard_ceiling_between_reviews():
    master = snapshot(130)
    incumbents = [member["security_id"] for member in master["members"]]
    with pytest.raises(ValueError, match="hard ceiling"):
        reconstruct_buffered_universe(
            security_master_snapshot=master,
            market_observations=observations(master),
            incumbent_security_ids=incumbents,
            benchmark_security_id="SEC-SPY",
            is_final_nyse_session_of_month=False,
        )
