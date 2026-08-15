from __future__ import annotations

"""Explicitly exempt, non-evidentiary replay of one fixed Massive campaign.

This module never upgrades quarantined bytes into authenticated provider
evidence.  It records a human research assumption in an append-only ledger,
copies only TRAIN/VALIDATION rows into a separate content-addressed research
store, keeps UNTOUCHED_TEST one-shot and in memory, and fixes all broker and
performance-claim authorities false.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, current_git_revision
from core.guardrailed_backtest import (
    GuardrailedBacktestEngine,
    MarketBar,
    ResearchExemptionDataAttestation,
    UniverseEvent,
)
from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)
from core.orchestration.massive_historical_adapter import (
    parse_massive_unadjusted_daily_bars,
)
from core.orchestration.massive_historical_quarantine import (
    MAX_PAYLOAD_BYTES,
    MassiveHistoricalQuarantineStore,
)
from core.research.conservative_baseline_campaign import (
    ACQUISITION_END,
    ACQUISITION_START,
    CAMPAIGN_POLICY_VERSION,
    SPLITS,
    approved_execution_policy_sha256,
    approved_execution_profile,
)
from core.research.conservative_baseline_strategy import (
    ConservativeBaselineStrategy,
    conservative_baseline_parameters,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-research-index-availability-exemption-v1"
TARGET_BASKET = ("AAPL", "MSFT", "SPY")
EVALUATED_SYMBOLS = ("AAPL", "MSFT")
BENCHMARK_SYMBOL = "SPY"
RESEARCH_ROLES = ("TRAIN", "VALIDATION")
TEST_ROLE = "UNTOUCHED_TEST"
NY = ZoneInfo("America/New_York")
MAX_LEDGER_BYTES = 4 * 1024 * 1024

INDEX_ASSUMPTION = (
    "Human-authorized research assumption: AAPL, MSFT and SPY satisfied the "
    "campaign's historical index-membership requirements continuously from "
    "2025-08-01 through 2026-07-31."
)
AVAILABILITY_ASSUMPTION = (
    "Human-authorized research assumption: every retained AAPL, MSFT and SPY "
    "daily bar was point-in-time available at its assumed 16:00 America/New_York "
    "session close throughout 2025-08-01 through 2026-07-31."
)
SESSION_ASSUMPTION = (
    "Massive aggregate window t is used only to derive the America/New_York "
    "session date; research session open/close are assumed as 09:30/16:00 local."
)
LIMITATIONS = (
    "NOT_PROVIDER_EVIDENCE",
    "NOT_AUTHENTICATED_REPLAY_EVIDENCE",
    "NO_HISTORICAL_AVAILABILITY_PROOF",
    "NO_INDEX_MEMBERSHIP_PROOF",
    "NO_CORPORATE_ACTION_OR_TOTAL_RETURN_EVIDENCE",
    "NO_ENTITLEMENT_OR_REPLAY_PERMISSION_PROOF",
    "NO_PERFORMANCE_OR_TRACK_RECORD_CLAIM",
)


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError(f"unsupported canonical value: {type(item)!r}")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=default,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if len(resolved) != 64 or any(c not in "0123456789abcdef" for c in resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("research exemption directory must be owner-only")


def _read_private(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LedgerIntegrityError("research exemption file is missing or unsafe")
    details = path.stat()
    if stat.S_IMODE(details.st_mode) not in {0o400, 0o600} or details.st_size > maximum:
        raise LedgerIntegrityError("research exemption file permissions or size are invalid")
    return path.read_bytes()


class ResearchExemptionLedger:
    """Owner-only hash chain for exemption, admission, consumption and results."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = _read_private(self.path, maximum=MAX_LEDGER_BYTES)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("research exemption ledger has an incomplete line")
        rows: list[dict[str, Any]] = []
        prior = GENESIS_HASH
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
                if set(row) != {
                    "schema_version", "policy_version", "event_type", "recorded_at",
                    "payload", "previous_hash", "record_hash",
                }:
                    raise ValueError("unsupported envelope")
                material = {k: v for k, v in row.items() if k != "record_hash"}
                if (
                    row["schema_version"] != SCHEMA_VERSION
                    or row["policy_version"] != POLICY_VERSION
                    or row["previous_hash"] != prior
                    or row["record_hash"] != _hash(material)
                    or not isinstance(row["payload"], dict)
                    or row["event_type"] not in {
                        "RESEARCH_EXEMPTION_REGISTERED",
                        "TRAIN_VALIDATION_RESEARCH_ADMITTED",
                        "UNTOUCHED_TEST_CONSUMPTION_STARTED",
                        "UNTOUCHED_TEST_RESULT_RECORDED",
                    }
                ):
                    raise ValueError("boundary mismatch")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"research exemption ledger line {line_number} is invalid"
                ) from error
            rows.append(row)
            prior = row["record_hash"]
        return rows

    def append(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        _private_directory(self.path.parent)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            existing = self.records()
            envelope = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "event_type": event_type,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "payload": json.loads(_canonical_json(dict(payload))),
                "previous_hash": existing[-1]["record_hash"] if existing else GENESIS_HASH,
            }
            envelope["record_hash"] = _hash(envelope)
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            target = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(target, 0o600)
                os.write(target, (_canonical_json(envelope) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return envelope
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class ResearchBar:
    market_bar: MarketBar
    split_role: str
    capture_id: str
    capture_record_hash: str
    payload_sha256: str
    provider_window_start_at: str
    effective_at: str
    available_at: str
    retrieved_at: str
    exemption_id: str

    def material(self) -> dict[str, Any]:
        bar = self.market_bar
        return {
            "symbol": bar.symbol,
            "split_role": self.split_role,
            "open_at": bar.open_at.isoformat(),
            "close_at": bar.close_at.isoformat(),
            "effective_at": self.effective_at,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "open": format(bar.open, "f"),
            "high": format(bar.high, "f"),
            "low": format(bar.low, "f"),
            "close": format(bar.close, "f"),
            "volume": format(bar.volume, "f"),
            "capture_id": self.capture_id,
            "capture_record_hash": self.capture_record_hash,
            "payload_sha256": self.payload_sha256,
            "provider_window_start_at": self.provider_window_start_at,
            "exemption_id": self.exemption_id,
            "availability_basis": "EXPLICIT_HUMAN_RESEARCH_ASSUMPTION_NOT_PROVIDER_EVIDENCE",
        }


def register_exemption(
    *,
    ledger: ResearchExemptionLedger,
    preregistration: Mapping[str, Any],
    captures: Sequence[Mapping[str, Any]],
    authorized_by: str,
) -> dict[str, Any]:
    if tuple(preregistration.get("target_basket", ())) != TARGET_BASKET:
        raise ValueError("exemption requires the exact AAPL/MSFT/SPY preregistration")
    if (
        preregistration.get("acquisition_start") != ACQUISITION_START
        or preregistration.get("acquisition_end") != ACQUISITION_END
        or tuple(preregistration.get("splits", ())) != SPLITS
    ):
        raise ValueError("exemption requires the exact approved campaign window and splits")
    if len(captures) != 36 or not captures:
        raise ValueError("exemption requires the complete 36-capture chain")
    existing = [
        row for row in ledger.records()
        if row["event_type"] == "RESEARCH_EXEMPTION_REGISTERED"
    ]
    if existing:
        return existing[0]
    payload = {
        "authorized_by": str(authorized_by).strip(),
        "authorization_basis": "EXPLICIT_USER_INSTRUCTION_IN_CODEX_TASK",
        "preregistration_id": preregistration["preregistration_id"],
        "preregistration_record_hash": preregistration["record_hash"],
        "capture_chain_final_hash": captures[-1]["record_hash"],
        "target_basket": list(TARGET_BASKET),
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "assumptions": [INDEX_ASSUMPTION, AVAILABILITY_ASSUMPTION, SESSION_ASSUMPTION],
        "limitations": list(LIMITATIONS),
        "research_exemption": True,
        "provider_evidence": False,
        "authenticated_replay_evidence": False,
        "canonical_dataset_admitted": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    if not payload["authorized_by"]:
        raise ValueError("authorized_by is required")
    payload["exemption_id"] = "RIXA-" + _hash(payload)[:32].upper()
    return ledger.append("RESEARCH_EXEMPTION_REGISTERED", payload)


def _exemption(ledger: ResearchExemptionLedger) -> dict[str, Any]:
    rows = [r for r in ledger.records() if r["event_type"] == "RESEARCH_EXEMPTION_REGISTERED"]
    if len(rows) != 1:
        raise ValueError("exactly one research exemption must be registered")
    return rows[0]


def _session_times(window_start: datetime) -> tuple[datetime, datetime]:
    session_date = window_start.astimezone(NY).date()
    opened = datetime.combine(session_date, time(9, 30), NY).astimezone(timezone.utc)
    closed = datetime.combine(session_date, time(16, 0), NY).astimezone(timezone.utc)
    return opened, closed


def _bars_from_captures(
    *,
    captures: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, bytes],
    exemption_id: str,
) -> tuple[ResearchBar, ...]:
    output: list[ResearchBar] = []
    for capture in captures:
        raw = payloads[capture["quarantine_capture_id"]]
        if hashlib.sha256(raw).hexdigest() != capture["payload_sha256"]:
            raise LedgerIntegrityError("research payload no longer matches capture hash")
        for parsed in parse_massive_unadjusted_daily_bars(raw):
            payload = parsed["payload"]
            if payload["ticker"] != capture["symbol"]:
                raise LedgerIntegrityError("research payload symbol violates capture binding")
            opened, closed = _session_times(parsed["window_start"])
            if not date.fromisoformat(capture["request_start"]) <= opened.date() <= date.fromisoformat(capture["request_end"]):
                raise LedgerIntegrityError("research bar falls outside capture slice")
            market = MarketBar(
                symbol=payload["ticker"],
                open_at=opened,
                close_at=closed,
                available_at=closed,
                open=Decimal(str(payload["open"])),
                high=Decimal(str(payload["high"])),
                low=Decimal(str(payload["low"])),
                close=Decimal(str(payload["close"])),
                volume=Decimal(str(payload["volume"])),
            )
            output.append(
                ResearchBar(
                    market_bar=market,
                    split_role=capture["split_role"],
                    capture_id=capture["quarantine_capture_id"],
                    capture_record_hash=capture["record_hash"],
                    payload_sha256=capture["payload_sha256"],
                    provider_window_start_at=payload["bar_window_start_at"],
                    effective_at=closed.isoformat(),
                    available_at=closed.isoformat(),
                    retrieved_at=capture["retrieved_at"],
                    exemption_id=exemption_id,
                )
            )
    output.sort(key=lambda row: (row.market_bar.symbol, row.market_bar.open_at))
    identities = [(r.market_bar.symbol, r.market_bar.open_at) for r in output]
    if len(identities) != len(set(identities)):
        raise LedgerIntegrityError("research conversion produced duplicate sessions")
    return tuple(output)


def _capture_payloads(
    store: MassiveHistoricalQuarantineStore,
    captures: Sequence[Mapping[str, Any]],
    *,
    allow_untouched: bool,
) -> dict[str, bytes]:
    output: dict[str, bytes] = {}
    root = store.blob_directory.resolve()
    for capture in captures:
        if capture["split_role"] == TEST_ROLE and not allow_untouched:
            raise ValueError("untouched bytes require one-shot consumption authority")
        relative = Path(capture["blob_relative_path"])
        path = store.blob_directory / relative
        if path.resolve() != root / relative or path.is_symlink():
            raise LedgerIntegrityError("quarantine blob escaped its store")
        output[capture["quarantine_capture_id"]] = _read_private(
            path, maximum=MAX_PAYLOAD_BYTES
        )
    return output


def admit_train_validation(
    *,
    ledger: ResearchExemptionLedger,
    store: MassiveHistoricalQuarantineStore,
    admitted_root: str | Path,
) -> tuple[ResearchBar, ...]:
    exemption = _exemption(ledger)
    captures = [r for r in store.verify() if r["split_role"] in RESEARCH_ROLES]
    payloads = _capture_payloads(store, captures, allow_untouched=False)
    bars = _bars_from_captures(
        captures=captures,
        payloads=payloads,
        exemption_id=exemption["payload"]["exemption_id"],
    )
    material = [row.material() for row in bars]
    content = (_canonical_json(material) + "\n").encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    destination_root = Path(admitted_root)
    _private_directory(destination_root)
    destination = destination_root / f"{content_hash}.json"
    if destination.exists():
        if _read_private(destination, maximum=50 * 1024 * 1024) != content:
            raise LedgerIntegrityError("research admitted blob hash collision")
    else:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        try:
            os.write(descriptor, content)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o400)
        finally:
            os.close(descriptor)
    existing = [
        r for r in ledger.records()
        if r["event_type"] == "TRAIN_VALIDATION_RESEARCH_ADMITTED"
    ]
    payload = {
        "exemption_id": exemption["payload"]["exemption_id"],
        "exemption_record_hash": exemption["record_hash"],
        "roles": list(RESEARCH_ROLES),
        "record_count": len(bars),
        "content_sha256": content_hash,
        "blob_name": destination.name,
        "source_capture_ids": [r["quarantine_capture_id"] for r in captures],
        "research_only_admitted": True,
        "canonical_dataset_admitted": False,
        "provider_evidence": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    if existing:
        if existing[0]["payload"] != payload:
            raise LedgerIntegrityError("research admission differs from its prior record")
    else:
        ledger.append("TRAIN_VALIDATION_RESEARCH_ADMITTED", payload)
    return bars


def _strategy_signals(
    rows: Sequence[ResearchBar], *, evaluation_role: str
) -> tuple[list[bool], list[bool]]:
    strategy = ConservativeBaselineStrategy()
    parameters = conservative_baseline_parameters()
    market = [r.market_bar for r in rows]
    entries = [False] * len(rows)
    exits = [False] * len(rows)
    role_indices = [index for index, row in enumerate(rows) if row.split_role == evaluation_role]
    if len(role_indices) < 2:
        raise ValueError("screen role requires an evaluation session and liquidation buffer")
    final_index = role_indices[-1]
    for index, row in enumerate(rows[:-1]):
        if row.split_role != evaluation_role or index + 1 >= final_index:
            continue
        action = strategy.decide(row.market_bar.symbol, market[: index + 1], parameters)
        if action == "ENTER_LONG":
            entries[index + 1] = True
        elif action == "EXIT_LONG":
            exits[index + 1] = True
    # Reserve the role's final session open for causal liquidation.  This keeps
    # the diagnostic screen from marking an open trade through the role end.
    entries[final_index] = False
    exits[final_index] = True
    return entries, exits


def vectorbt_fixed_parameter_screen(bars: Sequence[ResearchBar]) -> dict[str, Any]:
    """Evaluate the sole preregistered parameter set; this is not optimization."""
    import math
    import pandas as pd
    import vectorbt

    if vectorbt.__version__ != "1.1.0":
        raise ValueError("research screen is pinned to VectorBT 1.1.0")
    results: list[dict[str, Any]] = []
    for symbol in EVALUATED_SYMBOLS:
        symbol_rows = [r for r in bars if r.market_bar.symbol == symbol]
        for role in RESEARCH_ROLES:
            role_indices = [i for i, r in enumerate(symbol_rows) if r.split_role == role]
            if not role_indices:
                raise ValueError(f"{symbol} lacks {role} rows")
            entries, exits = _strategy_signals(symbol_rows, evaluation_role=role)
            index = pd.DatetimeIndex([r.market_bar.open_at for r in symbol_rows])
            close = pd.Series([float(r.market_bar.close) for r in symbol_rows], index=index)
            opened = pd.Series([float(r.market_bar.open) for r in symbol_rows], index=index)
            entry_series = pd.Series(entries, index=index)
            exit_series = pd.Series(exits, index=index)
            portfolio = vectorbt.Portfolio.from_signals(
                close=close,
                entries=entry_series,
                exits=exit_series,
                price=opened,
                val_price=close.shift(1),
                ffill_val_price=True,
                update_value=False,
                size=0.25,
                size_type="percent",
                direction="longonly",
                accumulate=False,
                fees=0.0001,
                slippage=0.0017,
                init_cash=100000.0,
                lock_cash=True,
                allow_partial=False,
                raise_reject=True,
                freq="1D",
            )
            first, last = role_indices[0], role_indices[-1]
            values = portfolio.value().iloc[first : last + 1]
            daily = values.pct_change().dropna()
            total_return = values.iloc[-1] / values.iloc[0] - 1
            drawdown = (1 - values / values.cummax()).max()
            std = daily.std(ddof=1)
            sharpe = None if not len(daily) or std == 0 or math.isnan(std) else daily.mean() / std * math.sqrt(252)
            orders = portfolio.orders.records
            turnover = float((orders["size"] * orders["price"]).sum() / 100000.0)
            trades = portfolio.trades.records
            closed = trades[trades["status"] == 1]
            win_rate = None if not len(closed) else float((closed["pnl"] > 0).sum() / len(closed))
            benchmark = symbol_rows[last - 1].market_bar.close / symbol_rows[first].market_bar.close - Decimal("1")
            results.append(
                {
                    "symbol": symbol,
                    "split_role": role,
                    "session_count": len(role_indices),
                    "last_session_reserved_as_next_open_liquidation_buffer": True,
                    "parameter_sha256": _hash(conservative_baseline_parameters()),
                    "total_return": format(Decimal(str(total_return)), "f"),
                    "benchmark_buy_hold_return": format(benchmark, "f"),
                    "sharpe_ratio_diagnostic": None if sharpe is None else format(Decimal(str(sharpe)), "f"),
                    "maximum_drawdown": format(Decimal(str(drawdown)), "f"),
                    "win_rate": None if win_rate is None else format(Decimal(str(win_rate)), "f"),
                    "turnover": format(Decimal(str(turnover)), "f"),
                    "trade_count": int(len(closed)),
                }
            )
    return {
        "screen_type": "SINGLE_PREREGISTERED_PARAMETER_EVALUATION_NOT_OPTIMIZATION",
        "parameter_search_allowed": False,
        "candidate_parameter_count": 1,
        "strategy_parameters": conservative_baseline_parameters(),
        "vectorbt_version": "1.1.0",
        "static_fee_bps_per_side": "1",
        "static_adverse_slippage_bps_per_side": "17",
        "cash_settlement_modelled": False,
        "hard_atr_risk_modelled": False,
        "each_role_final_session_reserved_as_next_open_liquidation_buffer": True,
        "diagnostic_only": True,
        "performance_claim_allowed": False,
        "results": results,
    }


def _result_material(result: Any) -> dict[str, Any]:
    return {
        "strategy_version": result.strategy_version,
        "parameter_hash": result.parameter_hash,
        "source_id": result.source_id,
        "validation_receipt_sha256": result.validation_receipt_sha256,
        "fee_schedule_id": result.fee_schedule_id,
        "execution_scenario": result.execution_scenario,
        "starting_equity": format(result.starting_equity, "f"),
        "ending_equity": format(result.ending_equity, "f"),
        "total_return": format(result.total_return, "f"),
        "maximum_drawdown": format(result.maximum_drawdown, "f"),
        "execution_count": len(result.executions),
        "completed_trade_count": len(result.completed_trades),
        "engine_config_sha256": result.engine_config_sha256,
        "strategy_source_sha256": result.strategy_source_sha256,
        "mechanical_simulation_only": True,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }


def execute_untouched_once(
    *,
    ledger: ResearchExemptionLedger,
    store: MassiveHistoricalQuarantineStore,
    admitted_bars: Sequence[ResearchBar],
    vectorbt_screen: Mapping[str, Any],
) -> dict[str, Any]:
    exemption = _exemption(ledger)
    rows = ledger.records()
    if any(r["event_type"] == "UNTOUCHED_TEST_CONSUMPTION_STARTED" for r in rows):
        raise ValueError("the single untouched-test evaluation has already been consumed")
    captures = [r for r in store.verify() if r["split_role"] == TEST_ROLE]
    repository_root = Path(__file__).resolve().parents[2]
    runner_source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    git_revision = current_git_revision(repository_root)
    start_payload = {
        "exemption_id": exemption["payload"]["exemption_id"],
        "exemption_record_hash": exemption["record_hash"],
        "source_capture_ids": [r["quarantine_capture_id"] for r in captures],
        "source_payload_hashes": [r["payload_sha256"] for r in captures],
        "maximum_untouched_test_evaluations": 1,
        "evaluation_number": 1,
        "selection_source": "SOLE_PREREGISTERED_PARAMETER_SET",
        "vectorbt_screen_sha256": _hash(vectorbt_screen),
        "git_revision": git_revision,
        "research_runner_source_sha256": runner_source_sha256,
        "consumption_is_irreversible": True,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    consumption = ledger.append("UNTOUCHED_TEST_CONSUMPTION_STARTED", start_payload)
    payloads = _capture_payloads(store, captures, allow_untouched=True)
    test_bars = _bars_from_captures(
        captures=captures,
        payloads=payloads,
        exemption_id=exemption["payload"]["exemption_id"],
    )
    all_material = [r.material() for r in (*admitted_bars, *test_bars)]
    content_hash = _hash(all_material)
    assumption_hashes = tuple(sorted((
        ("ASSUMED_HISTORICAL_AVAILABILITY", _hash(AVAILABILITY_ASSUMPTION)),
        ("ASSUMED_INDEX_MEMBERSHIP", _hash(INDEX_ASSUMPTION)),
        ("ASSUMED_SESSION_TIMES", _hash(SESSION_ASSUMPTION)),
    )))
    attestation = ResearchExemptionDataAttestation._from_explicit_research_exemption(
        source_id=f"RESEARCH_EXEMPTION:{exemption['payload']['exemption_id']}",
        source_content_sha256=content_hash,
        validation_receipt_sha256=consumption["record_hash"],
        derivation_policy_version=POLICY_VERSION,
        evidence_role_hashes=assumption_hashes,
        exemption_id=exemption["payload"]["exemption_id"],
        exemption_record_sha256=exemption["record_hash"],
    )
    engine_results: list[dict[str, Any]] = []
    for symbol in EVALUATED_SYMBOLS:
        warmup = [r for r in admitted_bars if r.market_bar.symbol == symbol][-50:]
        test = [r for r in test_bars if r.market_bar.symbol == symbol]
        if len(warmup) != 50 or len(test) < 3:
            raise ValueError(f"{symbol} lacks the preregistered warmup or test rows")
        engine_bars = [r.market_bar for r in (*warmup, *test)]
        evaluation_start = test[0].market_bar.close_at
        # The penultimate session is reserved for a causal next-open exit and
        # the final session releases one-session-unsettled proceeds.  Claiming
        # either as an evaluated signal session would require unavailable later
        # bars or an invented settlement event.
        evaluation_end = test[-2].market_bar.open_at
        membership_at = datetime.combine(
            date.fromisoformat(ACQUISITION_START), time(0, 0), timezone.utc
        )
        universe = [
            UniverseEvent(
                symbol,
                "ADD",
                membership_at,
                membership_at,
                f"RESEARCH_EXEMPTION:{exemption['payload']['exemption_id']}",
            )
        ]
        for scenario in ("BASE", "PESSIMISTIC"):
            profile = approved_execution_profile(scenario)
            result = GuardrailedBacktestEngine(
                config=profile.config,
                fee_schedule=profile.fee_schedule,
                data_attestation=attestation,
            ).run(
                bars=engine_bars,
                universe_events=universe,
                terminal_outcomes=[],
                corporate_actions=[],
                prices_are_unadjusted=True,
                strategy=ConservativeBaselineStrategy(),
                parameters=conservative_baseline_parameters(),
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
            )
            engine_results.append({"symbol": symbol, **_result_material(result)})
    benchmark = [r for r in test_bars if r.market_bar.symbol == BENCHMARK_SYMBOL]
    benchmark_return = benchmark[-3].market_bar.close / benchmark[0].market_bar.close - Decimal("1")
    report = {
        "status": "COMPLETED_RESEARCH_EXEMPTION_NOT_EVIDENTIARY",
        "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
        "execution_policy_sha256": approved_execution_policy_sha256(),
        "exemption_id": exemption["payload"]["exemption_id"],
        "exemption_record_hash": exemption["record_hash"],
        "consumption_record_hash": consumption["record_hash"],
        "git_revision": git_revision,
        "research_runner_source_sha256": runner_source_sha256,
        "untouched_evaluation_number": 1,
        "untouched_test_reusable": False,
        "evaluation_liquidation_policy": (
            "PENULTIMATE_CAPTURED_SESSION_OPEN_RESERVED_AS_NEXT_OPEN_LIQUIDATION;"
            "FINAL_CAPTURED_SESSION_OPEN_RESERVED_FOR_ONE_SESSION_CASH_SETTLEMENT"
        ),
        "spy_benchmark_return_through_last_evaluated_session": format(benchmark_return, "f"),
        "engine_results": engine_results,
        "limitations": list(LIMITATIONS),
        "research_exemption": True,
        "provider_evidence": False,
        "authenticated_replay_evidence": False,
        "canonical_dataset_admitted": False,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
    report["result_sha256"] = _hash(report)
    ledger.append("UNTOUCHED_TEST_RESULT_RECORDED", report)
    return report


def load_campaign_store(
    *,
    repository_root: str | Path,
    campaign_root: str | Path,
    admitted_store_root: str | Path,
) -> tuple[HistoricalQuarantinePreregistrationLedger, MassiveHistoricalQuarantineStore]:
    root = Path(repository_root)
    campaign = Path(campaign_root)
    prereg = HistoricalQuarantinePreregistrationLedger(
        campaign / "preregistration.jsonl", repository_root=root
    )
    store = MassiveHistoricalQuarantineStore(
        campaign / "raw",
        preregistration_ledger=prereg,
        admitted_store_roots=[Path(admitted_store_root)],
    )
    return prereg, store
