from __future__ import annotations

"""Strict, bitemporal parsing of authenticated historical-replay artifacts."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Mapping, Sequence

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import canonical_timestamp
from core.orchestration.replay_dataset_admission import ReplayDatasetAdmissionLedger


SCHEMA_VERSION = "1.1"
ROLES = (
    "TOTAL_RETURN_PRICES", "MARKET_CALENDARS_AND_HALTS",
    "CORPORATE_ACTIONS", "DELISTING_OUTCOMES",
)
IDENTIFIER = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
COMMON_COVERAGE_KEYS = {
    "tickers", "covers_from_at", "through_at", "knowledge_as_of_at",
    "provider_declared_completeness",
}
PRICE_KEYS = {
    "ticker", "observation_kind", "quote_observed_at", "trade_observed_at",
    "available_at", "bid", "ask", "last", "volume", "source_row_locator",
}
CALENDAR_KEYS = {
    "ticker", "starts_at", "ends_at", "available_at", "status", "source_row_locator"
}
VERSIONED_COMMON = {
    "ticker", "event_id", "version", "effective_at", "available_at",
    "record_status", "supersedes_version", "source_row_locator",
}
CORPORATE_KEYS = VERSIONED_COMMON | {"event_type"}
DELISTING_KEYS = VERSIONED_COMMON | {"terminal_type"}
INITIAL_STATE_KEYS = {
    "ticker", "state", "terminal_type", "as_of_at", "available_at",
    "source_row_locator",
}
AMBIGUITY_PRECEDENCE = {
    "NONE": 0,
    "ACTIVE_AT_EFFECTIVE_THEN_REVISED": 1,
    "MULTIPLE_ACTIVE_TERMINAL_OUTCOMES": 2,
    "CONFLICTING_TERMINAL_TYPES_AT_SAME_EFFECTIVE_TIME": 3,
}


def _text(value: Any, name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    result = value.strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return result


def _ticker(value: Any) -> str:
    result = _text(value, "ticker", 32).upper()
    if not IDENTIFIER.fullmatch(result):
        raise ValueError("ticker must use the normalized replay identifier format")
    return result


def _time(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _decimal(value: Any, name: str, *, positive: bool) -> str:
    if not isinstance(value, str) or not DECIMAL_TEXT.fullmatch(value):
        raise ValueError(f"{name} must be a plain exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be finite") from error
    digits = len(result.as_tuple().digits)
    places = max(0, -result.as_tuple().exponent)
    if (
        not result.is_finite() or digits > 30 or places > 12 or result < 0
        or (positive and result <= 0)
    ):
        raise ValueError(f"{name} is outside the supported finite decimal boundary")
    return "0" if result == 0 else format(result.normalize(), "f")


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_integer(value: Any, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _object(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} has missing or unsupported fields")
    return value


def _enum(value: Any, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an exact enum literal")
    result = value
    if result not in allowed:
        raise ValueError(f"{name} is unsupported")
    return result


def _coverage(value: Any, role: str) -> tuple[dict[str, Any], datetime, datetime, datetime]:
    extra = (
        {"observation_representation", "adjustment_basis"}
        if role == "TOTAL_RETURN_PRICES" else set()
    )
    source = _object(value, COMMON_COVERAGE_KEYS | extra, "coverage")
    tickers = [_ticker(item) for item in _sequence(source["tickers"], "tickers")]
    if not tickers or tickers != sorted(tickers) or len(tickers) != len(set(tickers)):
        raise ValueError("coverage tickers must be nonempty, unique, and sorted")
    start = _time(source["covers_from_at"], "covers_from_at")
    end = _time(source["through_at"], "through_at")
    knowledge = _time(source["knowledge_as_of_at"], "knowledge_as_of_at")
    if start >= end or knowledge < end:
        raise ValueError("coverage and shared knowledge cutoff are invalid")
    if source["provider_declared_completeness"] != "COMPLETE":
        raise ValueError("provider must explicitly attest COMPLETE coverage")
    result = {
        "tickers": tickers,
        "covers_from_at": start.isoformat(timespec="microseconds"),
        "through_at": end.isoformat(timespec="microseconds"),
        "knowledge_as_of_at": knowledge.isoformat(timespec="microseconds"),
        "provider_declared_completeness": "COMPLETE",
    }
    if role == "TOTAL_RETURN_PRICES":
        if (
            source["observation_representation"] != "POINT_IN_TIME_QUOTE_AND_TRADE"
            or source["adjustment_basis"] != "RAW_UNADJUSTED"
        ):
            raise ValueError("price observation representation or adjustment basis is invalid")
        result.update({
            "observation_representation": "POINT_IN_TIME_QUOTE_AND_TRADE",
            "adjustment_basis": "RAW_UNADJUSTED",
        })
    return result, start, end, knowledge


def _within(moment: datetime, start: datetime, end: datetime, name: str) -> None:
    if not start <= moment < end:
        raise ValueError(f"{name} is outside half-open effective coverage")


def _locator(value: Any, seen: set[str]) -> str:
    result = _text(value, "source_row_locator")
    if result in seen:
        raise ValueError("source row locators must be globally unique within an artifact")
    seen.add(result)
    return result


def _price_rows(
    values: Any, tickers: set[str], start: datetime, end: datetime, knowledge: datetime
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []; locators: set[str] = set()
    for value in _sequence(values, "rows"):
        row = _object(value, PRICE_KEYS, "price row")
        ticker = _ticker(row["ticker"])
        quote = _time(row["quote_observed_at"], "quote_observed_at")
        trade = _time(row["trade_observed_at"], "trade_observed_at")
        available = _time(row["available_at"], "available_at")
        if ticker not in tickers or quote > available or trade > available or available > knowledge:
            raise ValueError("price row chronology or ticker is invalid")
        _within(quote, start, end, "quote_observed_at"); _within(trade, start, end, "trade_observed_at")
        if row["observation_kind"] != "POINT_IN_TIME_QUOTE_AND_TRADE":
            raise ValueError("aggregated price rows cannot support execution evidence")
        rows.append({
            "ticker": ticker, "observation_kind": "POINT_IN_TIME_QUOTE_AND_TRADE",
            "quote_observed_at": quote.isoformat(timespec="microseconds"),
            "trade_observed_at": trade.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "bid": _decimal(row["bid"], "bid", positive=True),
            "ask": _decimal(row["ask"], "ask", positive=True),
            "last": _decimal(row["last"], "last", positive=True),
            "volume": _decimal(row["volume"], "volume", positive=False),
            "source_row_locator": _locator(row["source_row_locator"], locators),
        })
    key = lambda item: (
        item["ticker"], item["quote_observed_at"], item["trade_observed_at"],
        item["available_at"], item["source_row_locator"],
    )
    semantic = [item[:4] for item in map(key, rows)]
    if len(semantic) != len(set(semantic)) or rows != sorted(rows, key=key):
        raise ValueError("price rows must be semantically unique and canonically sorted")
    return rows


def _calendar_rows(
    values: Any, tickers: set[str], start: datetime, end: datetime, knowledge: datetime
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []; locators: set[str] = set()
    for value in _sequence(values, "rows"):
        row = _object(value, CALENDAR_KEYS, "calendar row")
        ticker = _ticker(row["ticker"]); begins = _time(row["starts_at"], "starts_at")
        ends = _time(row["ends_at"], "ends_at")
        available = _time(row["available_at"], "available_at")
        if ticker not in tickers or not start <= begins < ends <= end or available > knowledge:
            raise ValueError("calendar interval is outside coverage")
        rows.append({
            "ticker": ticker, "starts_at": begins.isoformat(timespec="microseconds"),
            "ends_at": ends.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "status": _enum(row["status"], "status", {"OPEN", "CLOSED", "HALTED"}),
            "source_row_locator": _locator(row["source_row_locator"], locators),
        })
    key = lambda item: (
        item["ticker"], item["starts_at"], item["ends_at"], item["available_at"], item["status"],
        item["source_row_locator"],
    )
    if rows != sorted(rows, key=key):
        raise ValueError("calendar rows must be canonically sorted")
    for ticker in sorted(tickers):
        intervals = [item for item in rows if item["ticker"] == ticker]
        if not intervals: raise ValueError("every ticker requires a total calendar partition")
        cursor = start; prior_status = None
        for item in intervals:
            begins = _time(item["starts_at"], "starts_at"); ends = _time(item["ends_at"], "ends_at")
            if begins != cursor or item["status"] == prior_status:
                raise ValueError("calendar must tile coverage and adjacent equal states are forbidden")
            cursor = ends; prior_status = item["status"]
        if cursor != end: raise ValueError("calendar must exactly reach through_at")
    return rows


def _versioned_rows(
    values: Any, role: str, tickers: set[str], start: datetime, end: datetime,
    knowledge: datetime,
) -> list[dict[str, Any]]:
    keys = CORPORATE_KEYS if role == "CORPORATE_ACTIONS" else DELISTING_KEYS
    rows: list[dict[str, Any]] = []; locators: set[str] = set()
    for value in _sequence(values, "rows"):
        row = _object(value, keys, "versioned event row")
        ticker = _ticker(row["ticker"]); effective = _time(row["effective_at"], "effective_at")
        available = _time(row["available_at"], "available_at")
        if ticker not in tickers or available > knowledge:
            raise ValueError("event ticker or knowledge chronology is invalid")
        _within(effective, start, end, "effective_at")
        version = _integer(row["version"], "version")
        status = _enum(row["record_status"], "record_status", {"ACTIVE", "RETRACTED"})
        result = {
            "ticker": ticker, "event_id": _text(row["event_id"], "event_id", 200),
            "version": version, "effective_at": effective.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "record_status": status,
            "supersedes_version": _optional_integer(row["supersedes_version"], "supersedes_version"),
            "source_row_locator": _locator(row["source_row_locator"], locators),
        }
        if role == "CORPORATE_ACTIONS":
            result["event_type"] = _enum(
                row["event_type"], "event_type", {"SPLIT", "SYMBOL_CHANGE", "OTHER"}
            )
        else:
            terminal = row["terminal_type"]
            if status == "ACTIVE":
                result["terminal_type"] = _enum(
                    terminal, "terminal_type", {"DELISTED", "ACQUIRED", "BANKRUPT", "OTHER_TERMINAL"}
                )
            elif terminal is not None:
                raise ValueError("retracted terminal outcomes must have null terminal_type")
            else: result["terminal_type"] = None
        rows.append(result)
    chain_key = lambda item: (item["ticker"], item["event_id"], item["version"])
    ordered = sorted(rows, key=chain_key)
    if len({chain_key(item) for item in rows}) != len(rows):
        raise ValueError("event versions must be unique")
    chains: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in ordered: chains.setdefault((item["ticker"], item["event_id"]), []).append(item)
    for chain in chains.values():
        prior_available = None
        for index, item in enumerate(chain, start=1):
            if (
                item["version"] != index
                or item["supersedes_version"] != (None if index == 1 else index - 1)
                or (prior_available is not None and item["available_at"] <= prior_available)
                or (index < len(chain) and item["record_status"] == "RETRACTED")
            ):
                raise ValueError("event version chain is invalid")
            prior_available = item["available_at"]
    output_key = lambda item: (
        item["ticker"], item["effective_at"], item["available_at"], item["event_id"],
        item["version"], item["source_row_locator"],
    )
    if rows != sorted(rows, key=output_key):
        raise ValueError("event rows must use canonical output ordering")
    return rows


def _initial_states(
    values: Any, tickers: set[str], start: datetime, knowledge: datetime,
    reserved_locators: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows=[]; locators=set(reserved_locators or set())
    for value in _sequence(values, "initial_instrument_states"):
        row=_object(value, INITIAL_STATE_KEYS, "initial instrument state")
        ticker=_ticker(row["ticker"]); state=_enum(row["state"],"state",{"ACTIVE","TERMINAL"})
        as_of=_time(row["as_of_at"],"as_of_at"); available=_time(row["available_at"],"available_at")
        if ticker not in tickers or as_of != start or available > start or available > knowledge:
            raise ValueError("initial instrument state boundary is invalid")
        terminal=row["terminal_type"]
        if state == "ACTIVE" and terminal is not None: raise ValueError("ACTIVE initial state requires null terminal_type")
        if state == "TERMINAL": terminal=_enum(terminal,"terminal_type",{"DELISTED","ACQUIRED","BANKRUPT","OTHER_TERMINAL"})
        rows.append({"ticker":ticker,"state":state,"terminal_type":terminal,"as_of_at":as_of.isoformat(timespec="microseconds"),"available_at":available.isoformat(timespec="microseconds"),"source_row_locator":_locator(row["source_row_locator"],locators)})
    if [item["ticker"] for item in rows] != sorted(tickers):
        raise ValueError("initial states must contain every ticker exactly once in order")
    return rows


def _delisting_ambiguity(
    rows: list[dict[str, Any]], states: list[dict[str, Any]], start: datetime
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    state_by_ticker={item["ticker"]:item for item in states}; chains={}
    for row in rows: chains.setdefault((row["ticker"],row["event_id"]),[]).append(row)
    for chain in chains.values(): chain.sort(key=lambda item:item["version"])
    reasons={key:"NONE" for key in chains}
    finals={key:chain[-1] for key,chain in chains.items()}
    for key,chain in chains.items():
        if state_by_ticker[key[0]]["state"] == "TERMINAL": raise ValueError("initial TERMINAL state forbids outcome chains")
        if any(_time(item["effective_at"],"effective_at") <= start for item in chain):
            raise ValueError("every outcome version must be strictly after coverage start")
        if any(
            current["record_status"] == "ACTIVE"
            and _time(successor["available_at"], "available_at")
            > max(
                _time(current["available_at"], "available_at"),
                _time(current["effective_at"], "effective_at"),
            )
            and (
                successor["record_status"] == "RETRACTED"
                or successor["effective_at"] != current["effective_at"]
                or successor["terminal_type"] != current["terminal_type"]
            )
            for current, successor in zip(chain, chain[1:])
        ):
            reasons[key]=max(
                (reasons[key],"ACTIVE_AT_EFFECTIVE_THEN_REVISED"),
                key=AMBIGUITY_PRECEDENCE.__getitem__,
            )
    active_by_ticker={ticker:[] for ticker in state_by_ticker}
    for key,final in finals.items():
        if final["record_status"] == "ACTIVE": active_by_ticker[key[0]].append((key,final))
    for candidates in active_by_ticker.values():
        if not candidates: continue
        if len(candidates) > 1:
            for key,_ in candidates:
                reasons[key]=max(
                    (reasons[key],"MULTIPLE_ACTIVE_TERMINAL_OUTCOMES"),
                    key=AMBIGUITY_PRECEDENCE.__getitem__,
                )
        groups: dict[str, list[tuple[tuple[str, str], dict[str, Any]]]] = {}
        for candidate in candidates:
            groups.setdefault(candidate[1]["effective_at"], []).append(candidate)
        for tied in groups.values():
            if len({item[1]["terminal_type"] for item in tied}) > 1:
                for key,_ in tied:
                    reasons[key]=max(
                        (reasons[key],"CONFLICTING_TERMINAL_TYPES_AT_SAME_EFFECTIVE_TIME"),
                        key=AMBIGUITY_PRECEDENCE.__getitem__,
                    )
    normalized=[]
    for row in rows:
        reason=reasons[(row["ticker"],row["event_id"])]
        normalized.append({**row,"actual_state_ambiguous":reason!="NONE","ambiguity_reason":reason})
    ticker_state={}
    for ticker,state in state_by_ticker.items():
        candidates=[final for (name,_),final in finals.items() if name==ticker and final["record_status"]=="ACTIVE"]
        chosen=min(candidates,key=lambda item:(item["effective_at"],item["available_at"],item["event_id"])) if candidates else None
        ambiguous=any(reason!="NONE" for (name,_),reason in reasons.items() if name==ticker)
        resolved_terminal=state["terminal_type"] if state["state"]=="TERMINAL" else (chosen["terminal_type"] if chosen else None)
        ticker_state[ticker]={"initial_state":state,"canonical_terminal_outcome":chosen,"resolved_state":"TERMINAL" if resolved_terminal else "ACTIVE","resolved_terminal_type":resolved_terminal,"actual_state_ambiguous":ambiguous}
    return normalized,ticker_state


def _load_source(admission_ledger, content_ledger, admission_id, role):
    try:
        admission=next((item for item in admission_ledger.verify() if item.get("admission_id")==_text(admission_id,"admission_id")),None)
        if admission is None: raise ValueError("a verified replay dataset admission is required")
        reference=next((item for item in admission["artifacts"] if item["role"]==role),None)
        if reference is None: raise ValueError("verified admission lacks the requested role")
        content,payload=content_ledger.read_verified(reference["content_evidence_id"])
        if content["content_evidence_id"] != reference["content_evidence_id"]: raise ValueError("content does not match admission")
        if not isinstance(payload,bytes): raise ValueError("authenticated artifact payload must be bytes")
        source=json.loads(payload)
        for field in ("record_hash","replay_plan_id","replay_plan_record_hash","dataset_commitment_sha256"):
            if field not in admission: raise ValueError("verified admission is missing required pins")
        for field in ("record_hash","source_input_sha256"):
            if field not in content: raise ValueError("authenticated content is missing required pins")
    except (AttributeError,KeyError,OSError,TypeError,UnicodeDecodeError,json.JSONDecodeError) as error:
        raise ValueError("verified replay artifact dependencies are malformed") from error
    return admission,content,source


def load_authenticated_replay_artifact(*,admission_ledger:ReplayDatasetAdmissionLedger,content_ledger:AuthenticatedSourceContentLedger,admission_id:str,role:str)->dict[str,Any]:
    resolved=_text(role,"role").upper()
    if resolved not in ROLES: raise ValueError("role is outside the execution artifact boundary")
    admission,content,source=_load_source(admission_ledger,content_ledger,admission_id,resolved)
    root_keys={"schema_version","artifact_role","coverage","rows"}|({"initial_instrument_states"} if resolved=="DELISTING_OUTCOMES" else set())
    root=_object(source,root_keys,"artifact")
    if root["schema_version"]!=SCHEMA_VERSION or root["artifact_role"]!=resolved: raise ValueError("artifact schema or role is unsupported")
    coverage,start,end,knowledge=_coverage(root["coverage"],resolved); tickers=set(coverage["tickers"])
    states=[]; ticker_state={}
    if resolved=="TOTAL_RETURN_PRICES": rows=_price_rows(root["rows"],tickers,start,end,knowledge)
    elif resolved=="MARKET_CALENDARS_AND_HALTS": rows=_calendar_rows(root["rows"],tickers,start,end,knowledge)
    else:
        rows=_versioned_rows(root["rows"],resolved,tickers,start,end,knowledge)
        if resolved=="DELISTING_OUTCOMES":
            states=_initial_states(
                root["initial_instrument_states"],tickers,start,knowledge,
                {item["source_row_locator"] for item in rows},
            )
            rows,ticker_state=_delisting_ambiguity(rows,states,start)
    return {"schema_version":SCHEMA_VERSION,"artifact_role":resolved,"admission_id":admission["admission_id"],"admission_record_hash":admission["record_hash"],"replay_plan_id":admission["replay_plan_id"],"replay_plan_record_hash":admission["replay_plan_record_hash"],"dataset_commitment_sha256":admission["dataset_commitment_sha256"],"content_evidence_id":content["content_evidence_id"],"content_record_hash":content["record_hash"],"source_input_sha256":content["source_input_sha256"],"coverage":coverage,"rows":rows,"initial_instrument_states":states,"resolved_ticker_states":ticker_state,"source_bytes_authenticated":True,"artifact_structure_validated":True,"provider_completeness_attested_not_independently_proven":True,"plan_coverage_adequacy_proven":False,"observation_selected":False,"replay_executed":False,"costs_calculated":False,"fills_generated":False,"performance_calculated":False,"network_allowed":False,"broker_connection_allowed":False,"orders_submitted":False,"live_trading_enabled":False}


def load_authenticated_replay_artifact_bundle(*,admission_ledger:ReplayDatasetAdmissionLedger,content_ledger:AuthenticatedSourceContentLedger,admission_id:str)->dict[str,Any]:
    artifacts={role:load_authenticated_replay_artifact(admission_ledger=admission_ledger,content_ledger=content_ledger,admission_id=admission_id,role=role) for role in ROLES}
    coverages=[item["coverage"] for item in artifacts.values()]; first=coverages[0]
    for coverage in coverages[1:]:
        if any(coverage[field]!=first[field] for field in ("tickers","covers_from_at","through_at","knowledge_as_of_at")):
            raise ValueError("execution artifacts must share exact ticker, coverage, and knowledge boundaries")
    return {"schema_version":SCHEMA_VERSION,"record_type":"AUTHENTICATED_BITEMPORAL_REPLAY_ARTIFACT_BUNDLE","admission_id":next(iter(artifacts.values()))["admission_id"],"replay_plan_id":next(iter(artifacts.values()))["replay_plan_id"],"tickers":first["tickers"],"covers_from_at":first["covers_from_at"],"through_at":first["through_at"],"shared_knowledge_as_of_at":first["knowledge_as_of_at"],"artifacts":artifacts,"cross_role_header_boundaries_reconciled":True,"cross_role_financial_coherence_proven":False,"plan_coverage_adequacy_proven":False,"provider_completeness_attested_not_independently_proven":True,"observation_selected":False,"replay_executed":False,"costs_calculated":False,"fills_generated":False,"performance_calculated":False,"network_allowed":False,"broker_connection_allowed":False,"orders_submitted":False,"live_trading_enabled":False}
