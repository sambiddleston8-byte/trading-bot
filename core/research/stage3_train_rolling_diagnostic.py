"""Three-fold TRAIN-only execution diagnostic for fixed Stage 3 policies."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.guardrailed_backtest import (
    BacktestResult,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    UniverseEvent,
)
from core.research.pit_feature_signal_adapter import (
    SYMBOLS,
    DeterministicSignalAdapter,
    MomentumConfirmedSignalAdapter,
    PITFeatureConsumer,
    deterministic_signal_parameters,
    momentum_confirmed_signal_parameters,
)
from core.research.stage3_feature_strategy_evaluation import (
    ADMITTED_MATRIX_SHA256,
    INITIAL_CASH_PER_SYMBOL,
    ROOT,
    _action,
    _attestation,
    _bar,
    _canonical,
    _composite_metrics,
    _configuration,
    _decimal,
    _hash,
    _maximum_drawdown,
    _sharpe,
    _spy_buy_hold_total_return,
    _write_private,
)


OUTPUT = ROOT / "stage3/train_rolling_policy_diagnostic.json"
FOLD_COUNT = 3
FOLD_SIZE = 18
POLICIES: Mapping[str, tuple[type[DeterministicSignalAdapter], Mapping[str, Any]]] = {
    "BASELINE": (DeterministicSignalAdapter, deterministic_signal_parameters()),
    "MOMENTUM_CONFIRMED": (
        MomentumConfirmedSignalAdapter,
        momentum_confirmed_signal_parameters(),
    ),
}


def _session_values(
    rows: Sequence[Mapping[str, Any]],
    *,
    session_day: str,
    session_field: str,
    value_field: str,
) -> tuple[str, ...]:
    values = tuple(
        sorted(
            {
                str(row[value_field])
                for row in rows
                if str(row[session_field])[:10] == session_day
            }
        )
    )
    if not values:
        raise ValueError(
            f"{session_day} lacks required {value_field} in the rolling source"
        )
    return values


def _unique_session_value(
    rows: Sequence[Mapping[str, Any]],
    *,
    session_day: str,
    session_field: str,
    value_field: str,
) -> str:
    values = _session_values(
        rows,
        session_day=session_day,
        session_field=session_field,
        value_field=value_field,
    )
    if len(values) != 1:
        raise ValueError(
            f"{session_day} has ambiguous {value_field} in the rolling source"
        )
    return values[0]


def _assert_flat_and_settled(
    result: BacktestResult, *, evaluation_end: datetime
) -> None:
    if not result.portfolio_states or not result.equity_curve:
        raise ValueError("rolling fold lacks final portfolio evidence")
    final = result.portfolio_states[-1]
    if final.as_of_at != evaluation_end or result.equity_curve[-1][0] != evaluation_end:
        raise ValueError("rolling fold final state differs from evaluation end")
    if final.position_quantity != 0:
        raise ValueError("rolling fold retains an open position")
    if final.unsettled_cash != 0:
        raise ValueError("rolling fold retains unsettled cash")
    if result.ending_equity != final.settled_cash:
        raise ValueError("rolling fold ending equity is not fully settled cash")


def _aggregate_folds(
    runs: Sequence[tuple[str, Mapping[str, BacktestResult], Mapping[str, Any]]]
) -> dict[str, Any]:
    initial = INITIAL_CASH_PER_SYMBOL * Decimal(len(SYMBOLS))
    chained_equity = initial
    chained_curve: list[Decimal] = []
    strategy_growth = Decimal("1")
    benchmark_growth = Decimal("1")
    total_sessions = 0
    turnover_session_weight = Decimal("0")
    trades: list[dict[str, Any]] = []
    fees = Decimal("0")
    adverse = Decimal("0")
    fold_returns: list[dict[str, str]] = []

    for fold_id, results, composite in runs:
        curves = {
            symbol: dict(results[symbol].equity_curve) for symbol in SYMBOLS
        }
        sessions = sorted(set.intersection(*(set(curve) for curve in curves.values())))
        if any(set(curve) != set(sessions) for curve in curves.values()):
            raise ValueError("rolling fold equity curves are not aligned")
        previous = initial
        for moment in sessions:
            current = sum(
                (curves[symbol][moment] for symbol in SYMBOLS), Decimal("0")
            )
            chained_equity *= current / previous
            chained_curve.append(chained_equity)
            previous = current
        ending = sum(
            (results[symbol].ending_equity for symbol in SYMBOLS), Decimal("0")
        )
        if abs(ending - previous) > initial * Decimal("1e-24"):
            raise ValueError("daily fold curve differs from settled ending equity")

        evaluated_sessions = int(composite["evaluated_sessions"])
        total_sessions += evaluated_sessions
        turnover_session_weight += (
            Decimal(composite["annual_turnover"]) * Decimal(evaluated_sessions)
        )
        benchmark_growth *= Decimal("1") + Decimal(
            composite["spy_buy_hold_total_return"]
        )
        strategy_growth *= Decimal("1") + Decimal(composite["total_return"])
        fold_returns.append(
            {
                "fold_id": fold_id,
                "total_return": composite["total_return"],
                "spy_buy_hold_total_return": composite[
                    "spy_buy_hold_total_return"
                ],
            }
        )
        trades.extend(
            {"fold_id": fold_id, **trade} for trade in composite["trade_log"]
        )
        attribution = composite["execution_cost_attribution"]
        fees += Decimal(attribution["fees"])
        adverse += Decimal(attribution["adverse_execution_cost"])

    if not chained_curve or total_sessions == 0:
        raise ValueError("rolling diagnostic produced no evaluated sessions")
    if len(chained_curve) != total_sessions:
        raise ValueError("pooled daily observations differ from evaluated sessions")
    total_return = strategy_growth - Decimal("1")
    if abs(chained_equity / initial - strategy_growth) > Decimal("1e-24"):
        raise ValueError("daily and fold-reset return chains do not reconcile")
    benchmark_return = benchmark_growth - Decimal("1")
    winning = sum(Decimal(trade["net_profit_loss"]) > 0 for trade in trades)
    combined_cost = fees + adverse
    return {
        "fold_count": len(runs),
        "pooled_evaluated_sessions": total_sessions,
        "pooled_daily_observations": len(chained_curve),
        "starting_equity": _decimal(initial),
        "fold_reset_chained_ending_equity": _decimal(initial * strategy_growth),
        "fold_reset_chained_total_return": _decimal(total_return),
        "pooled_daily_sharpe_ratio": (
            _decimal(value)
            if (value := _sharpe(chained_curve, initial)) is not None
            else None
        ),
        "fold_reset_chained_maximum_drawdown": _decimal(
            _maximum_drawdown([initial, *chained_curve])
        ),
        "completed_trade_count": len(trades),
        "win_rate": (
            _decimal(Decimal(winning) / Decimal(len(trades))) if trades else None
        ),
        "session_weighted_annual_turnover": _decimal(
            turnover_session_weight / Decimal(total_sessions)
        ),
        "fold_reset_spy_buy_hold_total_return": _decimal(benchmark_return),
        "fold_reset_excess_return_vs_spy": _decimal(
            total_return - benchmark_return
        ),
        "execution_cost_attribution": {
            "fees": _decimal(fees),
            "adverse_execution_cost": _decimal(adverse),
            "combined_execution_cost": _decimal(combined_cost),
            "combined_execution_cost_fraction_of_deployed_fold_capital": _decimal(
                combined_cost / (initial * Decimal(len(runs)))
            ),
        },
        "fold_returns": fold_returns,
        "trade_log": sorted(
            trades,
            key=lambda trade: (
                datetime.fromisoformat(trade["opened_at"]),
                trade["symbol"],
                trade["fold_id"],
            ),
        ),
        "aggregation_definition": (
            "chronological daily fold returns chained with capital reset to equal "
            "AAPL/MSFT/SPY sleeves at each non-overlapping fold; excludes overnight "
            "returns across fold boundaries"
        ),
    }


def evaluate_train_rolling(
    repository_root: Path,
    *,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
) -> dict[str, Any]:
    stage2 = repository_root / ROOT / "stage2"
    qualification_bytes = (stage2 / "qualification_report.json").read_bytes()
    qualification = json.loads(qualification_bytes)
    qualification_sha256 = _hash(qualification_bytes)
    train_path = stage2 / "clean_feature_store/train.json"
    train_bytes = train_path.read_bytes()
    if _hash(train_bytes) != qualification["artifacts"]["TRAIN"]:
        raise ValueError("TRAIN partition differs from qualification")
    train = json.loads(train_bytes)
    matrix_path = (
        repository_root / ROOT / "stage3/technical_features/train_matrix.json"
    )
    matrix = json.loads(matrix_path.read_text())
    if matrix.get("matrix_sha256") != admitted_train_matrix_sha256:
        raise ValueError("TRAIN feature matrix differs from its admitted pin")
    if matrix.get("partition_role") != "TRAIN":
        raise ValueError("rolling diagnostic accepts only the TRAIN matrix")
    if train.get("role") != "TRAIN" or train.get("quarantine_only") is not False:
        raise ValueError("rolling diagnostic accepts only admitted clean TRAIN data")

    feature_sessions = sorted(
        {row["effective_at"][:10] for row in matrix["rows"]}
    )
    if len(feature_sessions) != FOLD_COUNT * FOLD_SIZE:
        raise ValueError("TRAIN feature sessions do not match the fixed fold contract")
    folds = [
        feature_sessions[index : index + FOLD_SIZE]
        for index in range(0, len(feature_sessions), FOLD_SIZE)
    ]
    train_bars = sorted(
        train["bars"], key=lambda row: (row["session_date"], row["symbol"])
    )
    all_actions = train["corporate_actions"]
    if any(action["action_type"] == "SPLIT" for action in all_actions):
        raise ValueError("rolling raw-feature diagnostic requires split-free TRAIN")

    fee_schedule = ExchangeFeeSchedule(
        "STAGE3-TRAIN-ROLLING-FEES-v1",
        (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),),
    )
    report: dict[str, Any] = {
        "status": "TRAIN_ONLY_FIXED_POLICY_ROLLING_DIAGNOSTIC_COMPLETE",
        "source_partition": "TRAIN",
        "fold_count": FOLD_COUNT,
        "sessions_per_fold": FOLD_SIZE,
        "one_session_fold_embargo": True,
        "one_session_fold_purge": True,
        "parameter_search_allowed": False,
        "validation_data_read": False,
        "validation_policy_selection_allowed": False,
        "untouched_test_included": False,
        "promotion_allowed": False,
        "source_artifact_sha256": qualification["artifacts"]["TRAIN"],
        "feature_matrix_sha256": admitted_train_matrix_sha256,
        "qualification_report_artifact_sha256": qualification_sha256,
        "policies": {},
    }
    runtime: dict[
        str, dict[str, list[tuple[str, Mapping[str, BacktestResult], Mapping[str, Any]]]]
    ] = {
        policy: {scenario: [] for scenario in ("BASE", "PESSIMISTIC")}
        for policy in POLICIES
    }

    for policy_name, (adapter_type, parameters) in POLICIES.items():
        policy_report: dict[str, Any] = {
            "policy_version": adapter_type.version,
            "parameters": dict(parameters),
            "folds": [],
            "aggregate": {},
        }
        for fold_number, fold_sessions in enumerate(folds, start=1):
            fold_id = f"TRAIN-FOLD-{fold_number}"
            first_day, last_day = fold_sessions[0], fold_sessions[-1]
            embargo_ats = _session_values(
                matrix["rows"],
                session_day=first_day,
                session_field="effective_at",
                value_field="effective_at",
            )
            purge_ats = _session_values(
                matrix["rows"],
                session_day=last_day,
                session_field="effective_at",
                value_field="effective_at",
            )
            consumer = PITFeatureConsumer(
                matrix,
                expected_matrix_sha256=admitted_train_matrix_sha256,
                suppressed_decision_ats=(*embargo_ats, *purge_ats),
            )
            fold_rows = [
                row for row in train_bars if row["session_date"] <= last_day
            ]
            measurement_rows = [
                row
                for row in train_bars
                if first_day <= row["session_date"] <= last_day
            ]
            evaluation_start = datetime.fromisoformat(
                _unique_session_value(
                    measurement_rows,
                    session_day=first_day,
                    session_field="session_date",
                    value_field="open_at",
                )
            )
            evaluation_end = datetime.fromisoformat(
                _unique_session_value(
                    measurement_rows,
                    session_day=last_day,
                    session_field="session_date",
                    value_field="close_at",
                )
            )
            liquidation_day = fold_sessions[-3]
            liquidation_signal_at = _unique_session_value(
                measurement_rows,
                session_day=liquidation_day,
                session_field="session_date",
                value_field="close_at",
            )
            spy_rows = [
                row for row in measurement_rows if row["symbol"] == "SPY"
            ]
            benchmark = _spy_buy_hold_total_return(
                spy_rows,
                [action for action in all_actions if action["symbol"] == "SPY"],
                start_day=first_day,
                end_day=last_day,
            )
            fold_report: dict[str, Any] = {
                "fold_id": fold_id,
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end": evaluation_end.isoformat(),
                "embargoed_decision_ats": list(embargo_ats),
                "purged_decision_ats": list(purge_ats),
                "liquidation_signal_at": liquidation_signal_at,
                "source_sessions": len(fold_sessions),
                "scenarios": {},
            }
            for scenario in ("BASE", "PESSIMISTIC"):
                scenario_config = _configuration(scenario)
                results: dict[str, BacktestResult] = {}
                for symbol in SYMBOLS:
                    source_rows = [
                        row for row in fold_rows if row["symbol"] == symbol
                    ]
                    bars = tuple(_bar(row) for row in source_rows)
                    source_sha256 = _hash(_canonical(source_rows))
                    actions = tuple(
                        _action(action)
                        for action in all_actions
                        if action["symbol"] == symbol
                        and bars[0].open_at.date().isoformat()
                        <= action["effective_date"]
                        <= bars[-1].close_at.date().isoformat()
                    )
                    engine = GuardrailedBacktestEngine(
                        config=scenario_config,
                        fee_schedule=fee_schedule,
                        data_attestation=_attestation(
                            role=fold_id,
                            symbol=symbol,
                            source_sha256=source_sha256,
                            feature_sha256=admitted_train_matrix_sha256,
                            qualification_sha256=qualification_sha256,
                            receipt_sha256=qualification["qualification_sha256"],
                        ),
                    )
                    strategy = adapter_type(
                        consumer,
                        liquidation_signal_at=liquidation_signal_at,
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
                        corporate_actions=actions,
                        prices_are_unadjusted=True,
                        strategy=strategy,
                        parameters=parameters,
                        evaluation_start=evaluation_start,
                        evaluation_end=evaluation_end,
                    )
                    _assert_flat_and_settled(
                        result, evaluation_end=evaluation_end
                    )
                    results[symbol] = result
                composite = _composite_metrics(
                    results, benchmark_return=benchmark
                )
                fold_report["scenarios"][scenario] = {
                    "cost_model_bps": {
                        "baseline_slippage": _decimal(
                            scenario_config.baseline_slippage_bps
                        ),
                        "bid_ask_half_spread": _decimal(
                            scenario_config.bid_ask_half_spread_bps
                        ),
                        "liquidity_impact_at_max_participation": _decimal(
                            scenario_config.liquidity_impact_bps_at_max_participation
                        ),
                        "latency_adverse": _decimal(
                            scenario_config.latency_adverse_bps
                        ),
                        "exchange_fee_variable_bps": _decimal(
                            fee_schedule.tiers[0].variable_bps
                        ),
                        "exchange_fee_minimum": _decimal(
                            fee_schedule.tiers[0].minimum_fee
                        ),
                    },
                    "composite": composite,
                }
                runtime[policy_name][scenario].append(
                    (fold_id, results, composite)
                )
            policy_report["folds"].append(fold_report)
        policy_report["aggregate"] = {
            scenario: _aggregate_folds(runtime[policy_name][scenario])
            for scenario in ("BASE", "PESSIMISTIC")
        }
        report["policies"][policy_name] = policy_report

    report["evaluation_sha256"] = _hash(_canonical(report))
    report["artifact_sha256"] = _write_private(repository_root / OUTPUT, report)
    return report
