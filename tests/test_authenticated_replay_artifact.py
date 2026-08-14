import json
from pathlib import Path

import pytest

from core.orchestration.authenticated_replay_artifact import (
    load_authenticated_backtest_artifact_bundle,
    load_authenticated_replay_artifact,
    load_authenticated_replay_artifact_bundle,
)


class Admission:
    def __init__(self, record, content_ledger):
        self.record=record
        self.content_ledger=content_ledger
    def verify(self): return [self.record]


class Contents:
    def __init__(self, values, *, path=None, blob_directory=None):
        self.values=values
        self.path=path or Path("/tmp/authenticated-replay-content.jsonl")
        self.blob_directory=blob_directory or Path("/tmp/authenticated-replay-blobs")
    def read_verified(self, identifier): return self.values[identifier]


FROM="2022-04-01T00:00:00Z"; THROUGH="2022-04-02T00:00:00Z"; KNOW="2022-04-03T00:00:00Z"


def coverage(role, **changes):
    result={"tickers":["AAPL"],"covers_from_at":FROM,"through_at":THROUGH,"knowledge_as_of_at":KNOW,"provider_declared_completeness":"COMPLETE"}
    if role=="TOTAL_RETURN_PRICES": result.update(observation_representation="POINT_IN_TIME_QUOTE_AND_TRADE",adjustment_basis="RAW_UNADJUSTED")
    result.update(changes);return result


def price(locator="p1", **changes):
    result={"ticker":"AAPL","observation_kind":"POINT_IN_TIME_QUOTE_AND_TRADE","quote_observed_at":"2022-04-01T10:00:00Z","trade_observed_at":"2022-04-01T10:00:00Z","available_at":"2022-04-01T10:00:01Z","bid":"99","ask":"101","last":"100","volume":"0","source_row_locator":locator}
    result.update(changes);return result


def calendar(): return [{"ticker":"AAPL","starts_at":FROM,"ends_at":"2022-04-01T09:30:00Z","available_at":FROM,"status":"CLOSED","source_row_locator":"c1"},{"ticker":"AAPL","starts_at":"2022-04-01T09:30:00Z","ends_at":"2022-04-01T16:00:00Z","available_at":FROM,"status":"OPEN","source_row_locator":"c2"},{"ticker":"AAPL","starts_at":"2022-04-01T16:00:00Z","ends_at":THROUGH,"available_at":"2022-04-01T15:00:00Z","status":"CLOSED","source_row_locator":"c3"}]


def corporate(event="e1",version=1,status="ACTIVE",supersedes=None,available="2022-04-01T08:00:00Z",locator="a1"):
    return {"ticker":"AAPL","event_id":event,"version":version,"event_type":"SPLIT","effective_at":"2022-04-01T12:00:00Z","available_at":available,"record_status":status,"supersedes_version":supersedes,"source_row_locator":locator}


def initial(state="ACTIVE",terminal=None): return {"ticker":"AAPL","state":state,"terminal_type":terminal,"as_of_at":FROM,"available_at":FROM,"source_row_locator":"s1"}


def outcome(event="d1",version=1,status="ACTIVE",supersedes=None,terminal="ACQUIRED",effective="2022-04-01T18:00:00Z",available="2022-04-01T08:00:00Z",locator="d1"):
    return {"ticker":"AAPL","event_id":event,"version":version,"terminal_type":terminal,"effective_at":effective,"available_at":available,"record_status":status,"supersedes_version":supersedes,"source_row_locator":locator}


def environment(*,prices=None,cal=None,actions=None,outcomes=None,states=None,coverage_changes=None):
    rows={"TOTAL_RETURN_PRICES":prices if prices is not None else [price()],"MARKET_CALENDARS_AND_HALTS":cal if cal is not None else calendar(),"CORPORATE_ACTIONS":actions if actions is not None else [],"DELISTING_OUTCOMES":outcomes if outcomes is not None else []}
    contents={}; refs=[]
    for index,role in enumerate(rows):
        root={"schema_version":"1.1","artifact_role":role,"coverage":coverage(role,**(coverage_changes or {}).get(role,{})),"rows":rows[role]}
        if role=="DELISTING_OUTCOMES":root["initial_instrument_states"]=states if states is not None else [initial()]
        identifier=f"SRC-{index}";payload=json.dumps(root,separators=(",", ":")).encode();record={"content_evidence_id":identifier,"record_hash":str(index)*64,"source_input_sha256":str(index+1)*64};contents[identifier]=(record,payload);refs.append({"role":role,"content_evidence_id":identifier})
    admission={"admission_id":"RDA-1","record_hash":"a"*64,"replay_plan_id":"REPLAY-1","replay_plan_record_hash":"b"*64,"dataset_commitment_sha256":"c"*64,"artifacts":refs}
    content_ledger=Contents(contents)
    return Admission(admission,content_ledger),content_ledger


def bundle(**changes):
    admission,contents=environment(**changes)
    return load_authenticated_replay_artifact_bundle(admission_ledger=admission,content_ledger=contents,admission_id="RDA-1")


def backtest_environment(*, role_changes=None, coverage_changes=None):
    admission, contents = environment()
    roots = {
        role: json.loads(contents.values[f"SRC-{index}"][1])
        for index, role in enumerate((
            "TOTAL_RETURN_PRICES", "MARKET_CALENDARS_AND_HALTS",
            "CORPORATE_ACTIONS", "DELISTING_OUTCOMES",
        ))
    }
    roots["CORPORATE_ACTIONS"].update(schema_version="1.2", rows=[{
        **corporate(), "economics": {"split_ratio": "2"},
    }])
    roots["DELISTING_OUTCOMES"]["schema_version"] = "1.2"
    roots["UNIVERSE_MEMBERSHIP"] = {
        "schema_version": "1.2", "artifact_role": "UNIVERSE_MEMBERSHIP",
        "coverage": coverage("UNIVERSE_MEMBERSHIP", universe_id="SP500"),
        "rows": [{
            "ticker": "AAPL", "event_id": "m1", "version": 1,
            "membership_action": "ADD", "effective_at": "2022-04-01T09:00:00Z",
            "available_at": "2022-04-01T08:00:00Z", "record_status": "ACTIVE",
            "supersedes_version": None, "source_row_locator": "m1",
        }],
        "initial_membership_states": [{
            "ticker": "AAPL", "state": "NON_MEMBER", "as_of_at": FROM,
            "available_at": FROM, "source_row_locator": "ms1",
        }],
    }
    roots["RAW_DAILY_SESSION_BARS"] = {
        "schema_version": "1.2", "artifact_role": "RAW_DAILY_SESSION_BARS",
        "coverage": coverage(
            "RAW_DAILY_SESSION_BARS",
            observation_representation="SESSION_AGGREGATE_OHLCV",
            adjustment_basis="RAW_UNADJUSTED", quote_currency="USD",
        ),
        "rows": [{
            "ticker": "AAPL", "session_opens_at": "2022-04-01T09:30:00Z",
            "session_closes_at": "2022-04-01T16:00:00Z",
            "available_at": "2022-04-01T16:00:01Z", "open": "100",
            "high": "105", "low": "99", "close": "103", "volume": "1000000",
            "source_row_locator": "b1",
        }],
    }
    for role, changes in (role_changes or {}).items():
        roots[role].update(changes)
    for role, changes in (coverage_changes or {}).items():
        roots[role]["coverage"].update(changes)
    contents.values = {}
    admission.record["artifacts"] = []
    for index, role in enumerate(roots):
        identifier = f"BACKTEST-{index}"
        record = {
            "content_evidence_id": identifier, "record_hash": f"{index + 1:x}" * 64,
            "source_input_sha256": f"{index + 2:x}" * 64,
        }
        contents.values[identifier] = (
            record, json.dumps(roots[role], separators=(",", ":")).encode()
        )
        admission.record["artifacts"].append({
            "role": role, "content_evidence_id": identifier,
        })
    return admission, contents


def backtest_bundle(**changes):
    admission, contents = backtest_environment(**changes)
    return load_authenticated_backtest_artifact_bundle(
        admission_ledger=admission, content_ledger=contents, admission_id="RDA-1"
    )


def test_loads_exact_point_in_time_bundle_without_execution():
    result=bundle();row=result["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    assert row["quote_observed_at"]=="2022-04-01T10:00:00.000000+00:00"
    assert result["cross_role_header_boundaries_reconciled"] is True
    assert result["cross_role_financial_coherence_proven"] is False
    assert result["plan_coverage_adequacy_proven"] is False
    assert result["provider_completeness_attested_not_independently_proven"] is True
    for field in ("observation_selected","replay_executed","costs_calculated","fills_generated","performance_calculated","network_allowed","broker_connection_allowed","orders_submitted","live_trading_enabled"): assert result[field] is False


def test_loads_authenticated_backtest_roles_without_claiming_readiness_or_execution():
    result = backtest_bundle()
    assert set(result["artifacts"]) == {
        "TOTAL_RETURN_PRICES", "MARKET_CALENDARS_AND_HALTS", "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES", "UNIVERSE_MEMBERSHIP", "RAW_DAILY_SESSION_BARS",
    }
    assert result["quote_currency"] == "USD"
    assert result["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"][0]["open"] == "100"
    membership = result["artifacts"]["UNIVERSE_MEMBERSHIP"]
    assert membership["resolved_membership_states"]["AAPL"]["resolved_state"] == "MEMBER"
    assert result["authenticated_engine_input_roles_present"] is True
    for field in (
        "backtest_input_ready", "performance_claim_allowed",
        "paper_broker_submission_enabled", "broker_connection_allowed",
        "orders_submitted", "live_trading_enabled",
    ):
        assert result[field] is False


def test_version_1_1_execution_rows_keep_their_original_shape():
    result = bundle(
        actions=[corporate()], outcomes=[outcome()],
    )
    assert "economics" not in result["artifacts"]["CORPORATE_ACTIONS"]["rows"][0]
    assert "settlement" not in result["artifacts"]["DELISTING_OUTCOMES"]["rows"][0]


def test_backtest_schema_rejects_impossible_bars_and_dividend_payment_chronology():
    admission, contents = backtest_environment()
    record, payload = contents.values["BACKTEST-5"]
    root = json.loads(payload); root["rows"][0]["low"] = "104"
    contents.values["BACKTEST-5"] = (record, json.dumps(root).encode())
    with pytest.raises(ValueError, match="OHLC"):
        load_authenticated_backtest_artifact_bundle(
            admission_ledger=admission, content_ledger=contents, admission_id="RDA-1"
        )
    with pytest.raises(ValueError, match="paid before"):
        backtest_bundle(role_changes={"CORPORATE_ACTIONS": {"rows": [{
            **corporate(), "event_type": "CASH_DIVIDEND", "economics": {
                "cash_per_share": "1", "currency": "USD", "cash_paid_at": FROM,
            },
        }]}})


def test_backtest_bundle_reconciles_explicit_event_cash_currency():
    with pytest.raises(ValueError, match="cash currency"):
        backtest_bundle(role_changes={"CORPORATE_ACTIONS": {"rows": [{
            **corporate(), "event_type": "CASH_DIVIDEND", "economics": {
                "cash_per_share": "1", "currency": "GBP",
                "cash_paid_at": "2022-04-01T13:00:00Z",
            },
        }]}})


def test_backtest_loader_requires_economic_and_population_schema_1_2():
    admission, contents = backtest_environment()
    record, payload = contents.values["BACKTEST-2"]
    root = json.loads(payload); root["schema_version"] = "1.1"
    for row in root["rows"]: row.pop("economics")
    contents.values["BACKTEST-2"] = (record, json.dumps(root).encode())
    with pytest.raises(ValueError, match="require schema 1.2"):
        load_authenticated_backtest_artifact_bundle(
            admission_ledger=admission, content_ledger=contents, admission_id="RDA-1"
        )


def test_membership_actions_must_alternate_from_authenticated_initial_state():
    with pytest.raises(ValueError, match="do not alternate"):
        backtest_bundle(role_changes={"UNIVERSE_MEMBERSHIP": {"rows": [{
            "ticker": "AAPL", "event_id": "m1", "version": 1,
            "membership_action": "REMOVE", "effective_at": "2022-04-01T09:00:00Z",
            "available_at": "2022-04-01T08:00:00Z", "record_status": "ACTIVE",
            "supersedes_version": None, "source_row_locator": "m1",
        }]}})


def test_aggregated_prices_and_post_cutoff_rows_fail():
    with pytest.raises(ValueError,match="aggregated"):
        bundle(prices=[price(observation_kind="BAR")])
    with pytest.raises(ValueError,match="chronology"):
        bundle(prices=[price(available_at="2022-04-04T00:00:00Z")])


def test_price_semantic_duplicates_fail_even_with_distinct_locators():
    with pytest.raises(ValueError,match="semantically unique"):
        bundle(prices=[price("p1"),price("p2")])


def test_calendar_requires_total_partition_and_rejects_adjacent_equal_status():
    with pytest.raises(ValueError,match="exactly reach"):
        bundle(cal=calendar()[:-1])
    rows=calendar();rows[1]["status"]="CLOSED"
    with pytest.raises(ValueError,match="adjacent"):
        bundle(cal=rows)


def test_calendar_availability_is_bitemporal_and_post_cutoff_fails():
    rows=calendar();rows[1]["available_at"]="2022-04-04T00:00:00Z"
    with pytest.raises(ValueError,match="outside coverage"):
        bundle(cal=rows)


def test_event_versions_are_strict_and_retraction_terminal():
    rows=[corporate(),corporate(version=2,status="RETRACTED",supersedes=1,available="2022-04-01T13:00:00Z",locator="a2")]
    assert len(bundle(actions=rows)["artifacts"]["CORPORATE_ACTIONS"]["rows"])==2
    rows.append(corporate(version=3,supersedes=2,available="2022-04-01T14:00:00Z",locator="a3"))
    with pytest.raises(ValueError,match="chain"):
        bundle(actions=rows)


def test_active_at_effective_then_retracted_is_hash_pinned_ambiguity():
    rows=[outcome(),outcome(version=2,status="RETRACTED",supersedes=1,terminal=None,available="2022-04-01T19:00:00Z",locator="d2")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert all(row["actual_state_ambiguous"] for row in result["rows"])
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"ACTIVE_AT_EFFECTIVE_THEN_REVISED"}


def test_retraction_with_corrected_earlier_effective_time_stays_retracted():
    rows=[outcome(version=2,status="RETRACTED",supersedes=1,terminal=None,effective="2022-04-01T12:00:00Z",available="2022-04-01T19:00:00Z",locator="d2"),outcome()]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert result["resolved_ticker_states"]["AAPL"]["canonical_terminal_outcome"] is None
    assert all(row["actual_state_ambiguous"] for row in result["rows"])


def test_late_reported_active_outcome_then_retracted_is_ambiguous():
    rows=[outcome(available="2022-04-01T18:30:00Z"),outcome(version=2,status="RETRACTED",supersedes=1,terminal=None,available="2022-04-01T20:00:00Z",locator="d2")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"ACTIVE_AT_EFFECTIVE_THEN_REVISED"}


def test_active_restatement_after_prior_effective_time_is_ambiguous():
    rows=[outcome(),outcome(version=2,supersedes=1,effective="2022-04-01T20:00:00Z",available="2022-04-01T21:00:00Z",locator="d2")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"ACTIVE_AT_EFFECTIVE_THEN_REVISED"}
    assert result["resolved_ticker_states"]["AAPL"]["actual_state_ambiguous"] is True


def test_intermediate_revision_reverted_by_final_version_remains_ambiguous():
    rows=[
        outcome(version=1,effective="2022-04-01T10:00:00Z",available="2022-04-01T08:00:00Z",locator="d1"),
        outcome(version=3,supersedes=2,effective="2022-04-01T10:00:00Z",available="2022-04-01T13:00:00Z",locator="d3"),
        outcome(version=2,supersedes=1,effective="2022-04-01T23:00:00Z",available="2022-04-01T12:00:00Z",locator="d2"),
    ]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"ACTIVE_AT_EFFECTIVE_THEN_REVISED"}


def test_revision_before_prior_effective_time_is_not_actual_state_ambiguity():
    rows=[
        outcome(version=2,supersedes=1,effective="2022-04-01T20:00:00Z",available="2022-04-01T09:00:00Z",locator="d2"),
        outcome(version=1,effective="2022-04-01T23:00:00Z",available="2022-04-01T08:00:00Z",locator="d1"),
    ]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"NONE"}


def test_conflicting_same_time_terminal_types_have_highest_precedence():
    rows=[outcome(event="d1",terminal="ACQUIRED",locator="d1"),outcome(event="d2",terminal="BANKRUPT",locator="d2")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"CONFLICTING_TERMINAL_TYPES_AT_SAME_EFFECTIVE_TIME"}


def test_multiple_active_terminal_outcomes_at_different_times_are_ambiguous():
    rows=[outcome(event="d1",terminal="BANKRUPT",effective="2022-04-01T12:00:00Z",locator="d1"),outcome(event="d2",terminal="ACQUIRED",effective="2022-04-01T18:00:00Z",locator="d2")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    assert {row["ambiguity_reason"] for row in result["rows"]}=={"MULTIPLE_ACTIVE_TERMINAL_OUTCOMES"}
    assert result["resolved_ticker_states"]["AAPL"]["actual_state_ambiguous"] is True


def test_later_same_time_conflicting_terminal_types_get_highest_precedence():
    rows=[outcome(event="d1",terminal="DELISTED",effective="2022-04-01T12:00:00Z",locator="d1"),outcome(event="d2",terminal="ACQUIRED",effective="2022-04-01T18:00:00Z",locator="d2"),outcome(event="d3",terminal="BANKRUPT",effective="2022-04-01T18:00:00Z",locator="d3")]
    result=bundle(outcomes=rows)["artifacts"]["DELISTING_OUTCOMES"]
    reasons={row["event_id"]:row["ambiguity_reason"] for row in result["rows"]}
    assert reasons["d1"]=="MULTIPLE_ACTIVE_TERMINAL_OUTCOMES"
    assert reasons["d2"]==reasons["d3"]=="CONFLICTING_TERMINAL_TYPES_AT_SAME_EFFECTIVE_TIME"


def test_every_restatement_must_remain_after_initial_state_boundary():
    rows=[outcome(version=2,supersedes=1,effective=FROM,available="2022-04-01T19:00:00Z",locator="d2"),outcome()]
    with pytest.raises(ValueError,match="every outcome version"):
        bundle(outcomes=rows)


def test_initial_terminal_state_is_typed_and_forbids_outcomes():
    resolved=bundle(states=[initial("TERMINAL","DELISTED")])["artifacts"]["DELISTING_OUTCOMES"]["resolved_ticker_states"]["AAPL"]
    assert resolved["resolved_state"]=="TERMINAL"
    assert resolved["resolved_terminal_type"]=="DELISTED"
    with pytest.raises(ValueError,match="forbids"):
        bundle(states=[initial("TERMINAL","DELISTED")],outcomes=[outcome()])


def test_cross_role_boundaries_must_match_exactly():
    with pytest.raises(ValueError,match="share exact"):
        bundle(coverage_changes={"CORPORATE_ACTIONS":{"through_at":"2022-04-01T23:00:00Z"}})


@pytest.mark.parametrize("field,value",[("bid","Infinity"),("ask","NaN"),("last","1E+2"),("volume","0.0000000000001")])
def test_numeric_domain_is_exact_and_bounded(field,value):
    with pytest.raises(ValueError):bundle(prices=[price(**{field:value})])


def test_unknown_schema_fields_and_unverified_admission_fail():
    admission,contents=environment();record,payload=contents.values["SRC-0"];root=json.loads(payload);root["extra"]=True;contents.values["SRC-0"]=(record,json.dumps(root).encode())
    with pytest.raises(ValueError,match="unsupported fields"):load_authenticated_replay_artifact(admission_ledger=admission,content_ledger=contents,admission_id="RDA-1",role="TOTAL_RETURN_PRICES")
    with pytest.raises(ValueError,match="verified"):load_authenticated_replay_artifact(admission_ledger=admission,content_ledger=contents,admission_id="MISSING",role="TOTAL_RETURN_PRICES")


def test_malformed_verified_dependencies_fail_as_controlled_value_error():
    admission,contents=environment();del admission.record["artifacts"]
    with pytest.raises(ValueError,match="malformed"):
        load_authenticated_replay_artifact(admission_ledger=admission,content_ledger=contents,admission_id="RDA-1",role="TOTAL_RETURN_PRICES")


def test_artifact_bytes_must_come_from_the_store_verified_by_admission():
    admission, contents = environment()
    different_store = Contents(
        contents.values,
        path=Path("/tmp/different-replay-content.jsonl"),
        blob_directory=contents.blob_directory,
    )

    with pytest.raises(ValueError, match="content ledger does not match"):
        load_authenticated_replay_artifact(
            admission_ledger=admission,
            content_ledger=different_store,
            admission_id="RDA-1",
            role="TOTAL_RETURN_PRICES",
        )


def test_delisting_locators_are_unique_across_rows_and_initial_states():
    with pytest.raises(ValueError,match="globally unique"):
        bundle(outcomes=[outcome(locator="s1")])


def test_enum_literals_do_not_strip_or_change_case():
    rows=calendar();rows[0]["status"]=" CLOSED "
    with pytest.raises(ValueError,match="unsupported"):
        bundle(cal=rows)
