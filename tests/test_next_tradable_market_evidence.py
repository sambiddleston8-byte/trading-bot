from datetime import datetime, timedelta, timezone

import pytest

import core.orchestration.next_tradable_market_evidence as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.next_tradable_market_evidence import NextTradableMarketEvidenceLedger


class Verified:
    def __init__(self,records):self.records=records
    def verify(self):return self.records


NOW=datetime.now(timezone.utc);DECIDED=NOW-timedelta(days=1);THROUGH=DECIDED+timedelta(minutes=60)


def bundle(*,prices=None,calendar=None,actions=None,outcomes=None,ambiguous=False,initial="ACTIVE"):
    pins=lambda role:{"content_evidence_id":f"SRC-{role}","content_record_hash":role[0].lower()*64,"source_input_sha256":role[-1].lower()*64}
    price_rows=prices if prices is not None else [{"ticker":"AAPL","observation_kind":"POINT_IN_TIME_QUOTE_AND_TRADE","quote_observed_at":(DECIDED+timedelta(minutes=1)).isoformat(),"trade_observed_at":(DECIDED+timedelta(minutes=1)).isoformat(),"available_at":(DECIDED+timedelta(minutes=1,seconds=1)).isoformat(),"bid":"99","ask":"101","last":"100","volume":"1000","source_row_locator":"p1"}]
    cal_rows=calendar if calendar is not None else [{"ticker":"AAPL","starts_at":(DECIDED-timedelta(days=1)).isoformat(),"ends_at":(THROUGH+timedelta(days=1)).isoformat(),"available_at":(DECIDED-timedelta(days=2)).isoformat(),"status":"OPEN","source_row_locator":"c1"}]
    action_rows=actions or [];outcome_rows=outcomes or []
    state={"initial_state":{"ticker":"AAPL","state":initial,"terminal_type":"DELISTED" if initial=="TERMINAL" else None},"canonical_terminal_outcome":None,"resolved_state":initial,"resolved_terminal_type":None,"actual_state_ambiguous":ambiguous}
    values={"TOTAL_RETURN_PRICES":price_rows,"MARKET_CALENDARS_AND_HALTS":cal_rows,"CORPORATE_ACTIONS":action_rows,"DELISTING_OUTCOMES":outcome_rows}
    artifacts={role:{**pins(role),"rows":rows} for role,rows in values.items()}
    artifacts["DELISTING_OUTCOMES"]["resolved_ticker_states"]={"AAPL":state}
    return {"tickers":["AAPL"],"covers_from_at":(DECIDED-timedelta(days=2)).isoformat(),"through_at":(THROUGH+timedelta(days=2)).isoformat(),"provider_completeness_attested_not_independently_proven":True,"plan_coverage_adequacy_proven":False,"artifacts":artifacts}


def target(tmp_path,monkeypatch,source_bundle=None,decision="BUY"):
    plan={"replay_plan_id":"REPLAY-1","record_hash":"a"*64,"evaluation_not_before":(DECIDED-timedelta(days=1)).isoformat(),"evaluation_not_after":(DECIDED+timedelta(days=1)).isoformat()}
    admission={"admission_id":"RDA-1","record_hash":"b"*64,"replay_plan_id":"REPLAY-1","dataset_commitment_sha256":"c"*64}
    policy={"replay_execution_policy_record_id":"REXP-1","record_hash":"d"*64,"replay_plan_id":"REPLAY-1","execution_policy_id":"EXEC-1","scenarios":{"BASE":{"maximum_order_age_minutes":"30"},"PESSIMISTIC":{"maximum_order_age_minutes":"60"}}}
    decision_record={"decision_id":"DEC-1","record_hash":"e"*64,"ticker":"AAPL","decision":decision,"decided_at":DECIDED.isoformat(),"data_as_of":(DECIDED-timedelta(minutes=1)).isoformat()}
    policies=Verified([policy]);policies.plan_ledger=Verified([plan])
    monkeypatch.setattr(module,"load_authenticated_replay_artifact_bundle",lambda **kwargs:source_bundle or bundle())
    return NextTradableMarketEvidenceLedger(tmp_path/"evidence.jsonl",admission_ledger=Verified([admission]),policy_ledger=policies,decision_ledger=Verified([decision_record]),content_ledger=object())


def append(ledger,**changes):
    values={"admission_id":"RDA-1","replay_execution_policy_record_id":"REXP-1","decision_id":"DEC-1","assessed_by":"Codex"};values.update(changes);return ledger.append(**values)


def test_records_first_causally_available_observation_without_execution(tmp_path,monkeypatch):
    ledger=target(tmp_path,monkeypatch);record=append(ledger)
    assert record["previous_hash"]==GENESIS_HASH
    assert record["scenario_results"]["BASE"]["terminal_status"]=="ELIGIBLE_OBSERVATION_FOUND"
    assert record["scenario_results"]["PESSIMISTIC"]["selected_observation"]["source_row_locator"]=="p1"
    assert record["provider_completeness_attested_not_independently_proven"] is True
    assert record["plan_coverage_adequacy_proven"] is False
    for field in ("costs_calculated","participation_applied","fills_generated","position_mutated","cash_mutated","performance_calculated","performance_claim_allowed","model_trained","learning_applied","network_allowed","paper_broker_submission_enabled","broker_connection_allowed","orders_submitted","live_trading_enabled"):assert record[field] is False
    assert ledger.verify()==[record]


def test_continuity_event_has_highest_precedence(tmp_path,monkeypatch):
    event={"ticker":"AAPL","event_id":"split-1","version":1,"event_type":"SPLIT","effective_at":THROUGH.isoformat(),"available_at":DECIDED.isoformat(),"record_status":"ACTIVE"}
    record=append(target(tmp_path,monkeypatch,bundle(actions=[event])))
    assert record["scenario_results"]["BASE"]["terminal_status"]=="ELIGIBLE_OBSERVATION_FOUND"
    assert record["scenario_results"]["PESSIMISTIC"]["terminal_status"]=="WINDOW_INCOMPLETE_CANNOT_DETERMINE"
    assert record["scenario_results"]["PESSIMISTIC"]["reason_code"]=="CORPORATE_ACTION_IN_WINDOW"
    assert record["scenario_results"]["PESSIMISTIC"]["selected_observation"] is None


def test_closed_window_is_conclusive_but_open_missing_price_is_not(tmp_path,monkeypatch):
    closed=[{"ticker":"AAPL","starts_at":(DECIDED-timedelta(days=1)).isoformat(),"ends_at":(THROUGH+timedelta(days=1)).isoformat(),"available_at":(DECIDED-timedelta(days=2)).isoformat(),"status":"CLOSED"}]
    first=append(target(tmp_path,monkeypatch,bundle(prices=[],calendar=closed)))
    assert first["scenario_results"]["BASE"]["terminal_status"]=="NO_ELIGIBLE_OBSERVATION_WITHIN_MAX_ORDER_AGE"
    second=append(target(tmp_path/"second",monkeypatch,bundle(prices=[])))
    assert second["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"


def test_aggregated_stale_crossed_or_zero_volume_rows_are_not_candidates(tmp_path,monkeypatch):
    row=bundle()["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    row={**row,"bid":"102","ask":"100","last":"101","volume":"0"}
    record=append(target(tmp_path,monkeypatch,bundle(prices=[row])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"


def test_base_can_expire_while_pessimistic_finds_later_evidence(tmp_path,monkeypatch):
    row=bundle()["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    late=DECIDED+timedelta(minutes=45)
    row={**row,"quote_observed_at":late.isoformat(),"trade_observed_at":late.isoformat(),"available_at":(late+timedelta(seconds=1)).isoformat()}
    record=append(target(tmp_path,monkeypatch,bundle(prices=[row])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"
    assert record["scenario_results"]["PESSIMISTIC"]["terminal_status"]=="ELIGIBLE_OBSERVATION_FOUND"


def test_later_retraction_does_not_erase_event_known_within_scenario(tmp_path,monkeypatch):
    event={"ticker":"AAPL","event_id":"split-1","version":1,"event_type":"SPLIT","effective_at":(DECIDED+timedelta(minutes=20)).isoformat(),"available_at":(DECIDED-timedelta(minutes=1)).isoformat(),"record_status":"ACTIVE"}
    retracted={**event,"version":2,"available_at":(THROUGH+timedelta(days=1)).isoformat(),"record_status":"RETRACTED"}
    record=append(target(tmp_path,monkeypatch,bundle(actions=[event,retracted])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="CORPORATE_ACTION_IN_WINDOW"
    assert record["scenario_results"]["PESSIMISTIC"]["reason_code"]=="CORPORATE_ACTION_IN_WINDOW"


def test_retraction_inside_scenario_does_not_erase_earlier_active_event(tmp_path,monkeypatch):
    event={"ticker":"AAPL","event_id":"split-1","version":1,"event_type":"SPLIT","effective_at":(DECIDED+timedelta(minutes=20)).isoformat(),"available_at":(DECIDED-timedelta(minutes=1)).isoformat(),"record_status":"ACTIVE"}
    retracted={**event,"version":2,"available_at":(DECIDED+timedelta(minutes=10)).isoformat(),"record_status":"RETRACTED"}
    record=append(target(tmp_path,monkeypatch,bundle(actions=[event,retracted])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="CORPORATE_ACTION_IN_WINDOW"


def test_calendar_state_unavailable_by_expiry_is_incomplete(tmp_path,monkeypatch):
    rows=bundle()["artifacts"]["MARKET_CALENDARS_AND_HALTS"]["rows"]
    rows=[{**rows[0],"available_at":(DECIDED+timedelta(minutes=45)).isoformat()}]
    record=append(target(tmp_path,monkeypatch,bundle(calendar=rows)))
    assert record["scenario_results"]["BASE"]["reason_code"]=="CALENDAR_STATE_NOT_KNOWN_WITHIN_WINDOW"


def test_calendar_state_must_be_known_when_price_becomes_available(tmp_path,monkeypatch):
    rows=bundle()["artifacts"]["MARKET_CALENDARS_AND_HALTS"]["rows"]
    rows=[{**rows[0],"available_at":(DECIDED+timedelta(minutes=20)).isoformat()}]
    record=append(target(tmp_path,monkeypatch,bundle(calendar=rows)))
    assert record["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"


def test_physically_effective_late_reported_event_is_incomplete(tmp_path,monkeypatch):
    event={"ticker":"AAPL","event_id":"split-1","version":1,"event_type":"SPLIT","effective_at":(DECIDED+timedelta(minutes=20)).isoformat(),"available_at":(THROUGH+timedelta(days=1)).isoformat(),"record_status":"ACTIVE"}
    record=append(target(tmp_path,monkeypatch,bundle(actions=[event])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="CORPORATE_ACTION_IN_WINDOW"


def test_quote_and_trade_must_share_one_uninterrupted_open_interval(tmp_path,monkeypatch):
    row=bundle()["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    row={**row,"quote_observed_at":(DECIDED+timedelta(minutes=1)).isoformat(),"trade_observed_at":(DECIDED+timedelta(minutes=21)).isoformat(),"available_at":(DECIDED+timedelta(minutes=21,seconds=1)).isoformat()}
    intervals=[
        {"ticker":"AAPL","starts_at":(DECIDED-timedelta(minutes=1)).isoformat(),"ends_at":(DECIDED+timedelta(minutes=10)).isoformat(),"available_at":(DECIDED-timedelta(minutes=2)).isoformat(),"status":"OPEN"},
        {"ticker":"AAPL","starts_at":(DECIDED+timedelta(minutes=10)).isoformat(),"ends_at":(DECIDED+timedelta(minutes=20)).isoformat(),"available_at":(DECIDED+timedelta(minutes=9)).isoformat(),"status":"HALTED"},
        {"ticker":"AAPL","starts_at":(DECIDED+timedelta(minutes=20)).isoformat(),"ends_at":(THROUGH+timedelta(minutes=1)).isoformat(),"available_at":(DECIDED+timedelta(minutes=19)).isoformat(),"status":"OPEN"},
    ]
    record=append(target(tmp_path,monkeypatch,bundle(prices=[row],calendar=intervals)))
    assert record["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"


def test_quote_and_trade_must_be_same_point_in_time(tmp_path,monkeypatch):
    row=bundle()["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    row={**row,"quote_observed_at":(DECIDED+timedelta(minutes=2)).isoformat(),"trade_observed_at":(DECIDED+timedelta(minutes=1)).isoformat(),"available_at":(DECIDED+timedelta(minutes=2,seconds=1)).isoformat()}
    record=append(target(tmp_path,monkeypatch,bundle(prices=[row])))
    assert record["scenario_results"]["BASE"]["reason_code"]=="NO_ELIGIBLE_PRICE_DESPITE_OPEN_MARKET"


def test_pessimistic_coverage_overrun_does_not_discard_base_result(tmp_path,monkeypatch):
    source=bundle();source["through_at"]=(DECIDED+timedelta(minutes=45)).isoformat()
    record=append(target(tmp_path,monkeypatch,source))
    assert record["scenario_results"]["BASE"]["terminal_status"]=="ELIGIBLE_OBSERVATION_FOUND"
    assert record["scenario_results"]["PESSIMISTIC"]["reason_code"]=="EVIDENCE_WINDOW_EXCEEDS_BUNDLE_COVERAGE"


def test_first_market_observation_wins_even_if_provider_publishes_it_later(tmp_path,monkeypatch):
    first_time=DECIDED+timedelta(minutes=2);second_time=DECIDED+timedelta(minutes=5)
    template=bundle()["artifacts"]["TOTAL_RETURN_PRICES"]["rows"][0]
    first={**template,"quote_observed_at":first_time.isoformat(),"trade_observed_at":first_time.isoformat(),"available_at":(DECIDED+timedelta(minutes=10)).isoformat(),"source_row_locator":"p1"}
    second={**template,"quote_observed_at":second_time.isoformat(),"trade_observed_at":second_time.isoformat(),"available_at":(DECIDED+timedelta(minutes=6)).isoformat(),"source_row_locator":"p2"}
    record=append(target(tmp_path,monkeypatch,bundle(prices=[first,second])))
    assert record["scenario_results"]["BASE"]["selected_observation"]["source_row_locator"]=="p1"


def test_corporate_and_terminal_events_with_same_id_do_not_collide(tmp_path,monkeypatch):
    effective=(DECIDED+timedelta(minutes=20)).isoformat()
    action={"ticker":"AAPL","event_id":"shared","version":1,"event_type":"SPLIT","effective_at":effective,"available_at":DECIDED.isoformat(),"record_status":"ACTIVE"}
    terminal={"ticker":"AAPL","event_id":"shared","version":1,"terminal_type":"ACQUIRED","effective_at":effective,"available_at":DECIDED.isoformat(),"record_status":"ACTIVE"}
    record=append(target(tmp_path,monkeypatch,bundle(actions=[action],outcomes=[terminal])))
    result=record["scenario_results"]["BASE"]
    assert result["reason_code"]=="CONTINUITY_AND_TERMINAL_EVENTS_IN_WINDOW"
    assert {row["artifact_role"] for row in result["matching_continuity_events"]}=={"CORPORATE_ACTIONS","DELISTING_OUTCOMES"}


@pytest.mark.parametrize("decision",["HOLD","UNAVAILABLE"])
def test_only_buy_or_sell_decisions_are_eligible(tmp_path,monkeypatch,decision):
    with pytest.raises(ValueError,match="BUY or SELL"):append(target(tmp_path,monkeypatch,decision=decision))


def test_decision_inputs_cannot_extend_beyond_decision_time(tmp_path,monkeypatch):
    ledger=target(tmp_path,monkeypatch)
    ledger.decision_ledger.records[0]["data_as_of"]=(DECIDED+timedelta(minutes=45)).isoformat()
    with pytest.raises(ValueError,match="data_as_of"):
        append(ledger)


def test_terminal_or_ambiguous_instrument_fails_before_evidence(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match="ambiguous"):append(target(tmp_path,monkeypatch,bundle(ambiguous=True)))
    with pytest.raises(ValueError,match="terminal"):append(target(tmp_path/"terminal",monkeypatch,bundle(initial="TERMINAL")))


def test_exact_retry_is_idempotent_and_assessor_conflict_fails(tmp_path,monkeypatch):
    ledger=target(tmp_path,monkeypatch);first=append(ledger);assert append(ledger)==first
    with pytest.raises(LedgerIntegrityError,match="conflicting"):append(ledger,assessed_by="Claude")


def test_tampering_and_upstream_pin_change_are_detected(tmp_path,monkeypatch):
    ledger=target(tmp_path,monkeypatch);append(ledger);text=ledger.path.read_text();ledger.path.write_text(text.replace('"fills_generated":false','"fills_generated":true'))
    with pytest.raises(LedgerIntegrityError,match="modified"):ledger.verify()
