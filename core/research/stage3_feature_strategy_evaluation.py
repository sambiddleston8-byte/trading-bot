"""Offline TRAIN/VALIDATION evaluation of the admitted PIT technical strategy."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from core.guardrailed_backtest import (
    BacktestConfig,
    BacktestResult,
    CorporateAction,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    ExecutionRecord,
    GuardrailedBacktestEngine,
    MarketBar,
    ResearchExemptionDataAttestation,
    UniverseEvent,
)
from core.research.conservative_baseline_campaign_v2_proposal import (
    PARENT_RESEARCH_EXEMPTION_ID,
    PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
)
from core.research.pit_feature_signal_adapter import (
    SYMBOLS,
    DeterministicSignalAdapter,
    PITFeatureConsumer,
    deterministic_signal_parameters,
)


NY = ZoneInfo("America/New_York")
ROOT = Path("data/research/massive_campaign_v2_revision_2")
OUTPUT = ROOT / "stage3/feature_strategy_evaluation.json"
INITIAL_CASH_PER_SYMBOL = Decimal("100000")
ANNUALIZATION_SESSIONS = Decimal("252")
ADMITTED_MATRIX_SHA256 = {
    "TRAIN": "c125d4da43072d874fa600989f4ddcff1dd0e2baf8a720b4d026bc12a29e1b0a",
    "VALIDATION": "5eb5a78470555695edb881e46ad39af52500a4cf164f2d4c82aad677b3d60c98",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _decimal(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 34
        result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_private(path: Path, report: Mapping[str, Any]) -> str:
    payload = _canonical(report) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("feature strategy evaluation conflicts with admitted output")
        return _hash(payload)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("evaluation write made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    return _hash(payload)


def _bar(row: Mapping[str, Any]) -> MarketBar:
    return MarketBar(
        symbol=row["symbol"],
        open_at=datetime.fromisoformat(row["open_at"]),
        close_at=datetime.fromisoformat(row["close_at"]),
        available_at=datetime.fromisoformat(row["available_at"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=Decimal(row["volume"]),
    )


def _action(row: Mapping[str, Any]) -> CorporateAction:
    effective = datetime.combine(
        date.fromisoformat(row["effective_date"]), time(9, 30), NY
    ).astimezone(timezone.utc)
    if row["action_type"] == "CASH_DIVIDEND":
        return CorporateAction(
            symbol=row["symbol"],
            action_type="CASH_DIVIDEND",
            effective_at=effective,
            available_at=datetime.fromisoformat(row["reported_at"]),
            source_locator=row["source_id"],
            cash_per_share=Decimal(row["cash_per_share"]),
            cash_paid_at=datetime.combine(
                date.fromisoformat(row["pay_date"]), time(16), NY
            ).astimezone(timezone.utc),
        )
    return CorporateAction(
        symbol=row["symbol"],
        action_type="SPLIT",
        effective_at=effective,
        available_at=datetime.fromisoformat(row["reported_at"]),
        source_locator=row["source_id"],
        split_ratio=Decimal(row["split_ratio"]),
    )


def _configuration(scenario: str) -> BacktestConfig:
    pessimistic = scenario == "PESSIMISTIC"
    return BacktestConfig(
        initial_cash=INITIAL_CASH_PER_SYMBOL,
        execution_scenario=scenario,
        baseline_slippage_bps=Decimal("20" if pessimistic else "10"),
        bid_ask_half_spread_bps=Decimal("10" if pessimistic else "5"),
        latency_adverse_bps=Decimal("0"),
        liquidity_impact_bps_at_max_participation=Decimal(
            "20" if pessimistic else "10"
        ),
        stop_pierce_fill_fraction=Decimal("1" if pessimistic else "0.5"),
    )


def _attestation(
    *,
    role: str,
    symbol: str,
    source_sha256: str,
    feature_sha256: str,
    qualification_sha256: str,
    receipt_sha256: str,
) -> ResearchExemptionDataAttestation:
    return ResearchExemptionDataAttestation._from_explicit_research_exemption(
        source_id=f"RESEARCH_EXEMPTION:{role}:{symbol}:PIT_TECHNICAL_EVALUATION",
        source_content_sha256=source_sha256,
        validation_receipt_sha256=receipt_sha256,
        derivation_policy_version="stage3-pit-feature-strategy-evaluation-v1",
        evidence_role_hashes=tuple(
            sorted(
                (
                    ("ASSUMED_ADMITTED_CLEAN_PARTITION", source_sha256),
                    ("ASSUMED_ADMITTED_FEATURE_MATRIX", feature_sha256),
                    ("ASSUMED_QUALIFICATION_REPORT", qualification_sha256),
                )
            )
        ),
        exemption_id=PARENT_RESEARCH_EXEMPTION_ID,
        exemption_record_sha256=PARENT_RESEARCH_EXEMPTION_RECORD_HASH,
    )


def _maximum_drawdown(curve: Sequence[Decimal]) -> Decimal:
    peak = curve[0]
    worst = Decimal("0")
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, Decimal("1") - value / peak)
    return worst


def _sharpe(curve: Sequence[Decimal], initial: Decimal) -> Decimal | None:
    returns = []
    previous = initial
    for value in curve:
        returns.append(value / previous - Decimal("1"))
        previous = value
    if len(returns) < 2:
        return None
    mean = sum(returns, Decimal("0")) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(
        len(returns) - 1
    )
    if variance == 0:
        return None
    return mean / variance.sqrt() * ANNUALIZATION_SESSIONS.sqrt()


def _is_filled_trade_execution(execution: ExecutionRecord) -> bool:
    return execution.filled_quantity > 0 and execution.action in {"BUY", "SELL"}


def _trade_and_cost_attribution(
    results: Mapping[str, BacktestResult], *, initial_equity: Decimal
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if set(results) != set(SYMBOLS):
        raise ValueError("trade attribution requires the exact campaign symbol set")

    def precise_sum(values: Iterable[Decimal]) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return sum(values, Decimal("0"))

    def precise_product(left: Decimal, right: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return left * right

    def precise_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 50
            return numerator / denominator

    def matches_engine_decimal(actual: Decimal, expected: Decimal) -> bool:
        tolerance = max(
            Decimal("1e-24"),
            precise_product(abs(expected), Decimal("1e-27")),
        )
        return abs(precise_sum((actual, -expected))) <= tolerance

    logs: list[dict[str, Any]] = []
    all_filled: list[tuple[str, int, ExecutionRecord]] = []
    attributed_execution_keys: set[tuple[str, int]] = set()
    for symbol in SYMBOLS:
        result = results[symbol]
        # GuardrailedBacktestEngine permits one non-scaling position per symbol
        # and refuses to return while a position remains open.  Bind the same
        # deterministic entry/window contract enforced by replay_run_audit to
        # stable execution indices instead of process-local object identities.
        filled = [
            (index, execution)
            for index, execution in enumerate(result.executions)
            if _is_filled_trade_execution(execution)
        ]
        all_filled.extend((symbol, index, execution) for index, execution in filled)
        trades = list(result.completed_trades)
        if trades != sorted(trades, key=lambda trade: (trade.opened_at, trade.closed_at)):
            raise ValueError("completed trades changed deterministic engine order")
        if any(
            left.closed_at >= right.opened_at
            for left, right in zip(trades, trades[1:])
        ):
            raise ValueError("completed trade windows overlap")
        for trade in trades:
            entries = [
                (index, execution)
                for index, execution in filled
                if execution.action == "BUY"
                and execution.executed_at == trade.opened_at
                and (symbol, index) not in attributed_execution_keys
            ]
            exits = [
                (index, execution)
                for index, execution in filled
                if execution.action == "SELL"
                and trade.opened_at <= execution.executed_at <= trade.closed_at
                and (symbol, index) not in attributed_execution_keys
            ]
            if len(entries) != 1 or not exits:
                raise ValueError("completed trade lacks exact filled execution support")
            if max(execution.executed_at for _, execution in exits) != trade.closed_at:
                raise ValueError("completed trade lacks its final filled exit")
            entry_index, entry = entries[0]
            final_exit = max(
                exits, key=lambda item: (item[1].executed_at, item[0])
            )[1]
            entry_total_cost = precise_sum(
                (
                    precise_product(entry.filled_quantity, entry.execution_price),
                    entry.fee,
                )
            )
            exit_net_proceeds = precise_sum(
                (
                    precise_product(
                        execution.filled_quantity, execution.execution_price
                    )
                    - execution.fee
                    for _, execution in exits
                )
            )
            if not matches_engine_decimal(entry_total_cost, trade.entry_total_cost):
                raise ValueError("completed trade cost differs from its filled entry")
            if not matches_engine_decimal(
                exit_net_proceeds, trade.exit_net_proceeds
            ):
                raise ValueError("completed trade proceeds differ from its filled exits")
            if final_exit.reason != trade.exit_reason:
                raise ValueError("completed trade reason differs from its final exit")
            supporting = entries + exits
            supporting_keys = {(symbol, index) for index, _ in supporting}
            attributed_execution_keys.update(supporting_keys)
            fees = precise_sum(execution.fee for _, execution in supporting)
            adverse_cost = precise_sum(
                precise_product(
                    (
                        execution.execution_price - execution.reference_price
                        if execution.action == "BUY"
                        else execution.reference_price - execution.execution_price
                    ),
                    execution.filled_quantity,
                )
                for _, execution in supporting
            )
            reference_notional = precise_sum(
                precise_product(
                    execution.reference_price, execution.filled_quantity
                )
                for _, execution in supporting
            )
            if reference_notional <= 0:
                raise ValueError("filled trade lacks positive reference notional")
            net_pnl = trade.exit_net_proceeds - trade.entry_total_cost
            identity = {
                "symbol": symbol,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
                "entry_total_cost": _decimal(trade.entry_total_cost),
                "exit_net_proceeds": _decimal(trade.exit_net_proceeds),
            }
            with localcontext() as context:
                context.prec = 50
                adverse_bps = (
                    adverse_cost / reference_notional * Decimal("10000")
                )
            logs.append(
                {
                    "trade_id": "PITTRADE-"
                    + hashlib.sha256(_canonical(identity)).hexdigest()[:32].upper(),
                    **identity,
                    "execution_indices": [index for index, _ in supporting],
                    "entry_filled_quantity": _decimal(entry.filled_quantity),
                    "exit_filled_quantity": _decimal(
                        precise_sum(
                            execution.filled_quantity for _, execution in exits
                        )
                    ),
                    "net_profit_loss": _decimal(net_pnl),
                    "net_return": _decimal(trade.return_rate),
                    "exit_reason": trade.exit_reason,
                    "entry_execution_index": entry_index,
                    "entry_executed_at": entry.executed_at.isoformat(),
                    "exit_execution_indices": [index for index, _ in exits],
                    "final_exit_executed_at": max(
                        execution.executed_at for _, execution in exits
                    ).isoformat(),
                    "entry_execution_count": len(entries),
                    "exit_execution_count": len(exits),
                    "fees": _decimal(fees),
                    "adverse_execution_cost": _decimal(adverse_cost),
                    "combined_execution_cost": _decimal(
                        precise_sum((fees, adverse_cost))
                    ),
                    "notional_weighted_adverse_execution_bps": _decimal(
                        adverse_bps
                    ),
                }
            )
    logs.sort(
        key=lambda row: (
            datetime.fromisoformat(row["opened_at"]),
            row["symbol"],
            row["trade_id"],
        )
    )
    expected_execution_keys = {
        (symbol, index) for symbol, index, _ in all_filled
    }
    if attributed_execution_keys != expected_execution_keys:
        raise ValueError("filled execution is not attributed to a completed trade")
    fees = precise_sum(execution.fee for _, _, execution in all_filled)
    adverse = precise_sum(
        precise_product(
            (
                execution.execution_price - execution.reference_price
                if execution.action == "BUY"
                else execution.reference_price - execution.execution_price
            ),
            execution.filled_quantity,
        )
        for _, _, execution in all_filled
    )
    if fees != precise_sum(Decimal(row["fees"]) for row in logs):
        raise ValueError("trade fees do not reconcile to engine executions")
    if adverse != precise_sum(
        Decimal(row["adverse_execution_cost"]) for row in logs
    ):
        raise ValueError("trade fill costs do not reconcile to engine executions")
    realized = precise_sum(Decimal(row["net_profit_loss"]) for row in logs)
    ending = precise_sum(results[symbol].ending_equity for symbol in SYMBOLS)
    residual = precise_sum((ending, -initial_equity, -realized))
    cash_reconciliation_tolerance = precise_product(
        initial_equity, Decimal("1e-24")
    )
    if abs(residual) > cash_reconciliation_tolerance:
        raise ValueError(
            "cash P&L residual requires an independently attributed cash-flow ledger"
        )
    attribution = {
        "fees": _decimal(fees),
        "adverse_execution_cost": _decimal(adverse),
        "combined_execution_cost": _decimal(precise_sum((fees, adverse))),
        "combined_execution_cost_fraction_of_initial_equity": _decimal(
            precise_ratio(precise_sum((fees, adverse)), initial_equity)
        ),
        "realized_net_trade_profit_loss": _decimal(realized),
        "cash_pnl_reconciliation_residual": _decimal(residual),
        "cash_reconciliation_tolerance": _decimal(cash_reconciliation_tolerance),
        "filled_execution_count": len(all_filled),
        "definition": "fees plus adverse BUY/SELL fill-price distance from the engine reference price; exact stable-index binding under the engine's one-position and mandatory-horizon-exit invariants; cash flows such as dividends are unsupported unless independently attributed; excludes opportunity cost and market movement",
    }
    return logs, attribution


def _composite_metrics(
    results: Mapping[str, BacktestResult],
    *,
    benchmark_return: Decimal,
) -> dict[str, Any]:
    curves = {symbol: dict(result.equity_curve) for symbol, result in results.items()}
    sessions = sorted(set.intersection(*(set(curve) for curve in curves.values())))
    if any(set(curve) != set(sessions) for curve in curves.values()):
        raise ValueError("authoritative symbol equity curves are not aligned")
    initial = INITIAL_CASH_PER_SYMBOL * Decimal(len(SYMBOLS))
    composite = [sum((curves[symbol][moment] for symbol in SYMBOLS), Decimal("0")) for moment in sessions]
    ending = sum((result.ending_equity for result in results.values()), Decimal("0"))
    trades = [trade for result in results.values() for trade in result.completed_trades]
    filled = [
        execution
        for result in results.values()
        for execution in result.executions
        if _is_filled_trade_execution(execution)
    ]
    turnover = (
        sum(
            (execution.execution_price * execution.filled_quantity for execution in filled),
            Decimal("0"),
        )
        / initial
        * ANNUALIZATION_SESSIONS
        / Decimal(len(sessions))
    )
    total_return = ending / initial - Decimal("1")
    sharpe = _sharpe(composite, initial)
    trade_logs, cost_attribution = _trade_and_cost_attribution(
        results, initial_equity=initial
    )
    if (
        len(trade_logs) != len(trades)
        or cost_attribution["filled_execution_count"] != len(filled)
    ):
        raise ValueError("trade and execution attribution does not reconcile")
    return {
        "evaluated_sessions": len(sessions),
        "starting_equity": _decimal(initial),
        "ending_equity": _decimal(ending),
        "total_return": _decimal(total_return),
        "sharpe_ratio": _decimal(sharpe) if sharpe is not None else None,
        "maximum_drawdown": _decimal(_maximum_drawdown([initial, *composite, ending])),
        "win_rate": (
            _decimal(
                Decimal(sum(trade.return_rate > 0 for trade in trades))
                / Decimal(len(trades))
            )
            if trades
            else None
        ),
        "annual_turnover": _decimal(turnover),
        "completed_trade_count": len(trades),
        "filled_order_count": len(filled),
        "trade_log": trade_logs,
        "execution_cost_attribution": cost_attribution,
        "spy_buy_hold_total_return": _decimal(benchmark_return),
        "excess_return_vs_spy": _decimal(total_return - benchmark_return),
        "sharpe_definition": "sqrt(252) * mean(daily composite return) / sample standard deviation(daily composite return); risk-free rate 0",
        "annual_turnover_definition": "gross filled BUY+SELL notional / initial equal-capital composite equity * 252 / evaluated sessions",
        "win_rate_definition": "profitable completed trades / all completed trades",
    }


def _spy_buy_hold_total_return(
    spy_rows: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    *,
    start_day: str,
    end_day: str,
) -> Decimal:
    shares = Decimal("1")
    cash = Decimal("0")
    eligible = [
        row
        for row in actions
        if row["symbol"] == "SPY"
        and start_day < row["effective_date"] <= end_day
    ]
    by_day: dict[str, set[str]] = {}
    for action in eligible:
        by_day.setdefault(action["effective_date"], set()).add(action["action_type"])
    if any("SPLIT" in kinds and len(kinds) > 1 for kinds in by_day.values()):
        raise ValueError("same-day SPY split and dividend ordering is ambiguous")
    for action in sorted(
        (
            row
            for row in eligible
        ),
        key=lambda row: (row["effective_date"], row["action_type"]),
    ):
        if action["action_type"] == "SPLIT":
            shares *= Decimal(action["split_ratio"])
        else:
            cash += shares * Decimal(action["cash_per_share"])
    initial = Decimal(spy_rows[0]["open"])
    ending = shares * Decimal(spy_rows[-1]["close"]) + cash
    return ending / initial - Decimal("1")


def evaluate(
    repository_root: Path,
    *,
    admitted_matrix_sha256: Mapping[str, str] = ADMITTED_MATRIX_SHA256,
) -> dict[str, Any]:
    stage2 = repository_root / ROOT / "stage2"
    qualification_bytes = (stage2 / "qualification_report.json").read_bytes()
    qualification = json.loads(qualification_bytes)
    qualification_sha256 = _hash(qualification_bytes)
    clean_paths = {
        "TRAIN": stage2 / "clean_feature_store/train.json",
        "VALIDATION": stage2 / "clean_feature_store/validation.json",
    }
    matrix_paths = {
        "TRAIN": repository_root / ROOT / "stage3/technical_features/train_matrix.json",
        "VALIDATION": repository_root / ROOT / "stage3/technical_features/validation_matrix.json",
    }
    clean_bytes = {role: path.read_bytes() for role, path in clean_paths.items()}
    for role, payload in clean_bytes.items():
        if _hash(payload) != qualification["artifacts"][role]:
            raise ValueError("clean partition differs from qualification")
    partitions = {role: json.loads(payload) for role, payload in clean_bytes.items()}
    matrices = {role: json.loads(path.read_text()) for role, path in matrix_paths.items()}
    matrix_hashes = dict(admitted_matrix_sha256)
    if set(matrix_hashes) != {"TRAIN", "VALIDATION"}:
        raise ValueError("both admitted matrix pins are required")
    if any(matrices[role].get("matrix_sha256") != matrix_hashes[role] for role in matrices):
        raise ValueError("feature matrix differs from the admitted matrix pin")
    train_purged_at = max(row["effective_at"] for row in matrices["TRAIN"]["rows"])
    validation_embargoed_at = min(row["effective_at"] for row in matrices["VALIDATION"]["rows"])
    consumers = {
        "TRAIN": PITFeatureConsumer(
            matrices["TRAIN"],
            expected_matrix_sha256=matrix_hashes["TRAIN"],
            suppressed_decision_ats=(
                train_purged_at,
            ),
        ),
        "VALIDATION": PITFeatureConsumer(
            matrices["VALIDATION"],
            expected_matrix_sha256=matrix_hashes["VALIDATION"],
            suppressed_decision_ats=(
                validation_embargoed_at,
            ),
        ),
    }
    fee_schedule = ExchangeFeeSchedule(
        "STAGE3-FEATURE-EVALUATION-FEES-v1",
        (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),),
    )
    report: dict[str, Any] = {
        "status": "TRAIN_VALIDATION_FEATURE_STRATEGY_EVALUATED",
        "strategy": "SMA20_GT_SMA50_AND_MOMENTUM20_GT_ZERO_WITH_ADMITTED_ATR14_SIZING",
        "symbols": list(SYMBOLS),
        "initial_cash_per_symbol": _decimal(INITIAL_CASH_PER_SYMBOL),
        "one_bar_train_purge": bool(train_purged_at),
        "train_purged_decision_at": train_purged_at,
        "one_bar_validation_embargo": bool(validation_embargoed_at),
        "validation_embargoed_decision_at": validation_embargoed_at,
        "untouched_test_included": False,
        "parameter_search_allowed": False,
        "performance_claim_allowed": False,
        "promotion_allowed": False,
        "source_artifact_sha256": dict(qualification["artifacts"]),
        "feature_matrix_sha256": matrix_hashes,
        "qualification_report_artifact_sha256": qualification_sha256,
        "partitions": {},
    }
    all_actions = partitions["TRAIN"]["corporate_actions"] + partitions["VALIDATION"]["corporate_actions"]
    if any(action["action_type"] == "SPLIT" for action in all_actions):
        raise ValueError(
            "this raw-feature evaluation requires a split-free admitted window"
        )
    for role in ("TRAIN", "VALIDATION"):
        role_bars = sorted(
            partitions[role]["bars"], key=lambda row: (row["session_date"], row["symbol"])
        )
        sessions = sorted({row["session_date"] for row in role_bars})
        if role == "TRAIN":
            warmup: list[Mapping[str, Any]] = []
        else:
            train = partitions["TRAIN"]["bars"]
            train_sessions = sorted({row["session_date"] for row in train})[-50:]
            warmup = [row for row in train if row["session_date"] in train_sessions]
        evaluation_start = datetime.fromisoformat(
            next(row["open_at"] for row in role_bars if row["session_date"] == sessions[0])
        )
        evaluation_end_day = sessions[-1]
        evaluation_end = datetime.fromisoformat(
            next(row["close_at"] for row in role_bars if row["session_date"] == evaluation_end_day)
        )
        liquidation_day = sessions[-3]
        liquidation_signal_at = next(
            row["close_at"] for row in role_bars if row["session_date"] == liquidation_day
        )
        spy_rows = [row for row in role_bars if row["symbol"] == "SPY" and row["session_date"] <= evaluation_end_day]
        benchmark = _spy_buy_hold_total_return(
            spy_rows,
            all_actions,
            start_day=sessions[0],
            end_day=evaluation_end_day,
        )
        feature_sessions = {
            row["effective_at"][:10] for row in matrices[role]["rows"]
        }
        required_feature_sessions = {
            day for day in sessions if day >= min(feature_sessions)
        }
        if feature_sessions != required_feature_sessions:
            raise ValueError("admitted feature matrix does not cover the evaluation window")
        if role == "TRAIN" and train_purged_at[:10] != evaluation_end_day:
            raise ValueError("TRAIN purge is not the final evaluation decision")
        if role == "VALIDATION" and validation_embargoed_at[:10] != sessions[0]:
            raise ValueError("VALIDATION embargo is not the first evaluation decision")
        role_report: dict[str, Any] = {
            "source_sessions": len(sessions),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end": evaluation_end.isoformat(),
            "boundary_session": sessions[-1],
            "scenarios": {},
        }
        for scenario in ("BASE", "PESSIMISTIC"):
            results: dict[str, BacktestResult] = {}
            symbol_report: dict[str, Any] = {}
            for symbol in SYMBOLS:
                source_rows = [row for row in warmup + role_bars if row["symbol"] == symbol]
                bars = tuple(_bar(row) for row in source_rows)
                source_sha256 = _hash(_canonical(source_rows))
                applicable_actions = tuple(
                    _action(action)
                    for action in all_actions
                    if action["symbol"] == symbol
                    and bars[0].open_at.date().isoformat()
                    <= action["effective_date"]
                    <= bars[-1].close_at.date().isoformat()
                )
                engine = GuardrailedBacktestEngine(
                    config=_configuration(scenario),
                    fee_schedule=fee_schedule,
                    data_attestation=_attestation(
                        role=role,
                        symbol=symbol,
                        source_sha256=source_sha256,
                        feature_sha256=matrix_hashes[role],
                        qualification_sha256=qualification_sha256,
                        receipt_sha256=qualification["qualification_sha256"],
                    ),
                )
                strategy = DeterministicSignalAdapter(
                    consumers[role], liquidation_signal_at=liquidation_signal_at
                )
                result = engine.run(
                    bars=bars,
                    universe_events=(
                        UniverseEvent(
                            symbol=symbol,
                            action="ADD",
                            effective_at=bars[0].open_at,
                            available_at=bars[0].open_at,
                            source_locator="fixed-campaign-basket",
                        ),
                    ),
                    terminal_outcomes=(),
                    corporate_actions=applicable_actions,
                    prices_are_unadjusted=True,
                    strategy=strategy,
                    parameters=deterministic_signal_parameters(),
                    evaluation_start=evaluation_start,
                    evaluation_end=evaluation_end,
                )
                results[symbol] = result
                symbol_report[symbol] = {
                    "total_return": _decimal(result.total_return),
                    "maximum_drawdown": _decimal(result.maximum_drawdown),
                    "completed_trade_count": len(result.completed_trades),
                    "filled_order_count": sum(
                        execution.filled_quantity > 0
                        and execution.action in {"BUY", "SELL"}
                        for execution in result.executions
                    ),
                    "ending_equity": _decimal(result.ending_equity),
                }
            role_report["scenarios"][scenario] = {
                "cost_model_bps": {
                    "baseline_slippage": "20" if scenario == "PESSIMISTIC" else "10",
                    "bid_ask_half_spread": "10" if scenario == "PESSIMISTIC" else "5",
                    "liquidity_impact_at_max_participation": "20" if scenario == "PESSIMISTIC" else "10",
                    "exchange_fee": "1",
                    "latency_adverse": "0",
                },
                "composite": _composite_metrics(results, benchmark_return=benchmark),
                "symbols": symbol_report,
            }
        report["partitions"][role] = role_report
    report["evaluation_sha256"] = _hash(_canonical(report))
    report["artifact_sha256"] = _write_private(repository_root / OUTPUT, report)
    return report
