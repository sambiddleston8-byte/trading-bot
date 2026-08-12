from __future__ import annotations

"""Point-in-time congressional activity aggregation; research evidence only."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all
from core.political.disclosure_ledger import CongressionalTradeDisclosureLedger


SIGNAL_SCHEMA_VERSION = "1.0"
SIGNAL_POLICY_VERSION = "conservative-point-in-time-congressional-activity-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_AVAILABILITY_MODES = {"HISTORICAL_PUBLIC_EVIDENCED", "LIVE_SYSTEM_OBSERVED"}
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def _required(value: Any, name: str, maximum: int = 200) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _signal_id(ticker: str, as_of: str, mode: str, ids: list[str], hashes: list[str]) -> str:
    return "CTSIGNAL-" + hashlib.sha256(
        _canonical_json([ticker, as_of, mode, ids, hashes, SIGNAL_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class CongressionalActivitySignalLedger:
    """Aggregates immutable disclosures without producing an executable signal."""

    def __init__(self, path: str | Path, disclosure_ledger: CongressionalTradeDisclosureLedger) -> None:
        self.path = Path(path)
        self.disclosure_ledger = disclosure_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Congressional-signal ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank congressional-signal line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at congressional-signal line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Congressional-signal line {line_number} is not an object.")
                records.append(record)
        return records

    def aggregate(
        self, *, ticker: str, as_of: str | datetime, availability_mode: str,
        generated_by: str, generated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        symbol = _required(ticker, "ticker", 15).upper()
        if not _TICKER.fullmatch(symbol):
            raise ValueError("ticker format is invalid")
        mode = _required(availability_mode, "availability_mode", 50).upper()
        if mode not in ALLOWED_AVAILABILITY_MODES:
            raise ValueError("availability_mode is not supported")
        cutoff = _timestamp(as_of); generated = _timestamp(generated_at or datetime.now(timezone.utc))
        if cutoff > generated:
            raise ValueError("as_of cannot postdate generation")
        if generated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("generated_at cannot be in the future")
        availability_field = "historical_point_in_time_signal_at" if mode == "HISTORICAL_PUBLIC_EVIDENCED" else "live_system_signal_at"
        eligible = [
            record for record in self.disclosure_ledger.verify()
            if record.get("ticker") == symbol and _timestamp(record[availability_field]) <= cutoff
        ]
        eligible.sort(key=lambda item: (item[availability_field], item["disclosure_id"]))
        if not eligible:
            raise ValueError("No verified point-in-time disclosures are available for this ticker")
        buys = [item for item in eligible if item["transaction_type"] == "PURCHASE"]
        sales = [item for item in eligible if item["transaction_type"] == "SALE"]
        exchanges = [item for item in eligible if item["transaction_type"] == "EXCHANGE"]
        purchase_counts = Counter(item["politician_name"] for item in buys)
        buy_min = sum((Decimal(item["amount_min_usd"]) for item in buys), Decimal(0))
        buy_max = sum((Decimal(item["amount_max_usd"]) for item in buys), Decimal(0))
        sale_min = sum((Decimal(item["amount_min_usd"]) for item in sales), Decimal(0))
        sale_max = sum((Decimal(item["amount_max_usd"]) for item in sales), Decimal(0))
        net_lower = buy_min - sale_max
        net_upper = buy_max - sale_min
        direction = "POSITIVE_RANGE" if net_lower > 0 else "NEGATIVE_RANGE" if net_upper < 0 else "MIXED_OR_INDETERMINATE_RANGE"
        delay_days = [int(item["transaction_to_public_delay_days"]) for item in eligible]
        source_counts = Counter(item["source"] for item in eligible)
        ids = [item["disclosure_id"] for item in eligible]; hashes = [item["record_hash"] for item in eligible]
        result = {
            "schema_version": SIGNAL_SCHEMA_VERSION,
            "policy_version": SIGNAL_POLICY_VERSION,
            "signal_snapshot_id": _signal_id(symbol, cutoff.isoformat(), mode, ids, hashes),
            "record_type": "POINT_IN_TIME_CONGRESSIONAL_ACTIVITY_RESEARCH_SNAPSHOT",
            "status": "NON_EXECUTABLE_RESEARCH_FACTOR",
            "generated_at": generated.isoformat(),
            "generated_by": _required(generated_by, "generated_by", 100),
            "ticker": symbol,
            "as_of": cutoff.isoformat(),
            "availability_mode": mode,
            "availability_field": availability_field,
            "disclosure_ids": ids,
            "disclosure_record_hashes": hashes,
            "disclosure_count": len(eligible),
            "purchase_count": len(buys),
            "sale_count": len(sales),
            "exchange_count": len(exchanges),
            "unique_purchasers": len(purchase_counts),
            "unique_sellers": len({item["politician_name"] for item in sales}),
            "repeat_purchasers": sum(1 for count in purchase_counts.values() if count >= 2),
            "purchase_amount_min_usd": _decimal(buy_min),
            "purchase_amount_max_usd": _decimal(buy_max),
            "sale_amount_min_usd": _decimal(sale_min),
            "sale_amount_max_usd": _decimal(sale_max),
            "net_activity_lower_bound_usd": _decimal(net_lower),
            "net_activity_upper_bound_usd": _decimal(net_upper),
            "activity_direction": direction,
            "minimum_disclosure_delay_days": min(delay_days),
            "maximum_disclosure_delay_days": max(delay_days),
            "mean_disclosure_delay_days": _decimal(Decimal(sum(delay_days)) / Decimal(len(delay_days))),
            "source_counts": dict(sorted(source_counts.items())),
            "committee_relevance_available": False,
            "historical_signal_reliability_available": False,
            "subsequent_abnormal_return_available": False,
            "automatic_recommendation": False,
            "standalone_investment_decision_allowed": False,
            "copy_trade_allowed": False,
            "broker_connection_allowed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH; seen_ids = set(); records = self.records()
        for index, record in enumerate(records, start=1):
            material={key:value for key,value in record.items() if key!="record_hash"}
            if record.get("previous_hash")!=previous_hash or record.get("record_hash")!=_record_hash(material): raise LedgerIntegrityError(f"Congressional-signal record {index} has been modified.")
            resolved,reasons=resolve_pinned_records(self.disclosure_ledger.verify(),record.get("disclosure_ids") or [],record.get("disclosure_record_hashes") or [],id_field="disclosure_id",label="congressional disclosure")
            if reasons or not resolved: raise LedgerIntegrityError(f"Congressional-signal record {index} lost its disclosures.")
            try:
                symbol=_required(record.get("ticker"),"ticker",15).upper(); cutoff=_timestamp(record.get("as_of")); generated=_timestamp(record.get("generated_at")); mode=_required(record.get("availability_mode"),"mode",50).upper(); _required(record.get("generated_by"),"generated_by",100)
            except (TypeError,ValueError) as error: raise LedgerIntegrityError(f"Congressional-signal record {index} has invalid values.") from error
            field="historical_point_in_time_signal_at" if mode=="HISTORICAL_PUBLIC_EVIDENCED" else "live_system_signal_at" if mode=="LIVE_SYSTEM_OBSERVED" else None
            ordered=sorted(resolved,key=lambda item:(item[field],item["disclosure_id"])) if field else []
            if not field or any(item.get("ticker")!=symbol or _timestamp(item[field])>cutoff for item in ordered): raise LedgerIntegrityError(f"Congressional-signal record {index} includes unavailable evidence.")
            buys=[item for item in ordered if item["transaction_type"]=="PURCHASE"]; sales=[item for item in ordered if item["transaction_type"]=="SALE"]; exchanges=[item for item in ordered if item["transaction_type"]=="EXCHANGE"]
            purchase_counts=Counter(item["politician_name"] for item in buys); buy_min=sum((Decimal(item["amount_min_usd"]) for item in buys),Decimal(0)); buy_max=sum((Decimal(item["amount_max_usd"]) for item in buys),Decimal(0)); sale_min=sum((Decimal(item["amount_min_usd"]) for item in sales),Decimal(0)); sale_max=sum((Decimal(item["amount_max_usd"]) for item in sales),Decimal(0)); net_lower=buy_min-sale_max; net_upper=buy_max-sale_min
            direction="POSITIVE_RANGE" if net_lower>0 else "NEGATIVE_RANGE" if net_upper<0 else "MIXED_OR_INDETERMINATE_RANGE"; delays=[int(item["transaction_to_public_delay_days"]) for item in ordered]; source_counts=Counter(item["source"] for item in ordered)
            ids=[item["disclosure_id"] for item in ordered]; hashes=[item["record_hash"] for item in ordered]; expected_id=_signal_id(symbol,cutoff.isoformat(),mode,ids,hashes)
            boundary=(
                record.get("schema_version")==SIGNAL_SCHEMA_VERSION and record.get("policy_version")==SIGNAL_POLICY_VERSION and record.get("signal_snapshot_id")==expected_id and expected_id not in seen_ids
                and record.get("record_type")=="POINT_IN_TIME_CONGRESSIONAL_ACTIVITY_RESEARCH_SNAPSHOT" and record.get("status")=="NON_EXECUTABLE_RESEARCH_FACTOR" and record.get("availability_field")==field
                and record.get("disclosure_ids")==ids and record.get("disclosure_record_hashes")==hashes and generated>=cutoff and generated<=datetime.now(timezone.utc)+MAX_CLOCK_SKEW
                and record.get("disclosure_count")==len(ordered) and record.get("purchase_count")==len(buys) and record.get("sale_count")==len(sales) and record.get("exchange_count")==len(exchanges)
                and record.get("unique_purchasers")==len(purchase_counts) and record.get("unique_sellers")==len({item["politician_name"] for item in sales}) and record.get("repeat_purchasers")==sum(1 for count in purchase_counts.values() if count>=2)
                and record.get("purchase_amount_min_usd")==_decimal(buy_min) and record.get("purchase_amount_max_usd")==_decimal(buy_max) and record.get("sale_amount_min_usd")==_decimal(sale_min) and record.get("sale_amount_max_usd")==_decimal(sale_max)
                and record.get("net_activity_lower_bound_usd")==_decimal(net_lower) and record.get("net_activity_upper_bound_usd")==_decimal(net_upper) and record.get("activity_direction")==direction
                and record.get("minimum_disclosure_delay_days")==min(delays) and record.get("maximum_disclosure_delay_days")==max(delays) and record.get("mean_disclosure_delay_days")==_decimal(Decimal(sum(delays))/Decimal(len(delays))) and record.get("source_counts")==dict(sorted(source_counts.items()))
                and all(record.get(field_name) is False for field_name in ("committee_relevance_available","historical_signal_reliability_available","subsequent_abnormal_return_available","automatic_recommendation","standalone_investment_decision_allowed","copy_trade_allowed","broker_connection_allowed","order_submitted","live_trading_enabled"))
            )
            if not boundary: raise LedgerIntegrityError(f"Congressional-signal record {index} violates its boundary.")
            seen_ids.add(expected_id); previous_hash=record["record_hash"]
        return records

    def _append(self,result:dict[str,Any],*,allow_existing:bool):
        self.path.parent.mkdir(parents=True,exist_ok=True); descriptor=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX); records=self.verify(); existing=next((item for item in records if item["signal_snapshot_id"]==result["signal_snapshot_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash","generated_at"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Congressional signal snapshot already exists.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH}; record={**material,"record_hash":_record_hash(material)}; target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:_write_all(target,(_canonical_json(record)+"\n").encode());os.fsync(target)
            finally:os.close(target)
            return record
        finally:fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)
