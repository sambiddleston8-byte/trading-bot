"""TRAIN-only conformance run for the portfolio-wide Executive engine path."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

from core.guardrailed_backtest import (
    BacktestResult,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    UniverseEvent,
)
from core.research.executive_intent_signal_adapter import (
    ExecutiveIntentSignalAdapter,
    executive_intent_signal_parameters,
)
from core.research.pit_feature_signal_adapter import PITFeatureConsumer, SYMBOLS
from core.research.sec_form4_insider_specialist import SECForm4InsiderSpecialistBot
from core.research.stage3_feature_strategy_evaluation import (
    ADMITTED_MATRIX_SHA256,
    ROOT,
    _action,
    _attestation,
    _bar,
    _canonical,
    _configuration,
    _hash,
    _write_private,
)
from core.research.stage3_train_rolling_diagnostic import (
    _session_values,
    _unique_session_value,
)
from core.research.stage4_train_insider_ensemble_evaluation import (
    ADMITTED_FORM4_ARTIFACT_SHA256,
    FORM4_ARTIFACT,
)


OUTPUT = ROOT / "stage3/train_executive_portfolio_conformance_v1.json"
STATUS = "TRAIN_ONLY_EXECUTIVE_PORTFOLIO_CONFORMANCE_COMPLETE"


def _projection(result: BacktestResult) -> dict[str, Any]:
    def normalized(value: Any) -> Any:
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, tuple):
            return [normalized(item) for item in value]
        if isinstance(value, list):
            return [normalized(item) for item in value]
        if isinstance(value, Mapping):
            return {key: normalized(item) for key, item in value.items()}
        return value

    return normalized({
        "ending_equity": result.ending_equity,
        "executions": [asdict(item) for item in result.executions],
        "executive_intents": [asdict(item) for item in result.executive_intents],
        "cash_reservations": [asdict(item) for item in result.cash_reservations],
        "completed_trades": [asdict(item) for item in result.completed_trades],
    })


def _validate(result: BacktestResult) -> None:
    if not result.executive_intents:
        raise ValueError("portfolio conformance lacks Executive intent traces")
    traces = {
        (item.decision_at, item.symbol, item.intent_sha256)
        for item in result.executive_intents
    }
    for execution in result.executions:
        if execution.reason != "EXECUTIVE_PORTFOLIO_TARGET":
            continue
        matching = [
            item for item in result.executive_intents
            if item.decision_at == execution.signal_at
            and item.symbol == execution.symbol
        ]
        if len(matching) != 1:
            raise ValueError("portfolio execution lacks one originating intent trace")
    for reservation in result.cash_reservations:
        if (
            reservation.decision_at,
            reservation.symbol,
            reservation.intent_sha256,
        ) not in traces:
            raise ValueError("cash reservation lacks its originating intent trace")
        if reservation.consumed_cash + reservation.released_cash != reservation.reserved_cash:
            raise ValueError("cash reservation does not reconcile")
    for batch in {item.batch_sequence for item in result.cash_reservations}:
        rows = [
            item for item in result.cash_reservations
            if item.batch_sequence == batch
        ]
        sizing = [
            item for item in result.sizing_decisions
            if item.action == "BUY"
            and item.signal_at == rows[0].decision_at
            and item.evaluated_at == rows[0].execution_at
        ]
        if sum(item.reserved_cash for item in rows) > sizing[0].settled_cash_before:
            raise ValueError("portfolio batch reserved more than shared settled cash")
    if result.portfolio_states[-1].position_quantity != 0:
        raise ValueError("portfolio conformance ends with an open position")
    if result.portfolio_states[-1].unsettled_cash != 0:
        raise ValueError("portfolio conformance ends with unsettled cash")


def evaluate_train_portfolio_conformance(
    repository_root: Path,
    *,
    write_output: bool = True,
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
    if train.get("role") != "TRAIN" or train.get("quarantine_only") is not False:
        raise ValueError("portfolio conformance accepts only admitted TRAIN data")

    matrix_path = repository_root / ROOT / "stage3/technical_features/train_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    if matrix.get("matrix_sha256") != ADMITTED_MATRIX_SHA256["TRAIN"]:
        raise ValueError("TRAIN feature matrix differs from its admitted pin")
    feature_sessions = sorted({row["effective_at"][:10] for row in matrix["rows"]})
    if len(feature_sessions) < 5:
        raise ValueError("portfolio conformance lacks enough TRAIN feature sessions")
    first_day, last_day = feature_sessions[0], feature_sessions[-1]
    train_bars = sorted(
        (
            row for row in train["bars"]
            if row["session_date"] <= last_day
        ),
        key=lambda row: (row["session_date"], row["symbol"]),
    )
    measurement_rows = [
        row for row in train_bars
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
    liquidation_signal_at = _unique_session_value(
        measurement_rows,
        session_day=feature_sessions[-3],
        session_field="session_date",
        value_field="close_at",
    )
    suppressed = (
        *_session_values(
            matrix["rows"], session_day=first_day,
            session_field="effective_at", value_field="effective_at",
        ),
        *_session_values(
            matrix["rows"], session_day=last_day,
            session_field="effective_at", value_field="effective_at",
        ),
    )
    form4 = json.loads((repository_root / FORM4_ARTIFACT).read_bytes())
    source_sha256 = _hash(_canonical(train_bars))
    actions = tuple(
        _action(item) for item in train["corporate_actions"]
        if item["symbol"] in SYMBOLS
        and train_bars[0]["session_date"] <= item["effective_date"] <= last_day
    )
    universe_events = tuple(
        UniverseEvent(
            symbol=symbol, action="ADD",
            effective_at=min(
                _bar(row).open_at for row in train_bars if row["symbol"] == symbol
            ),
            available_at=min(
                _bar(row).open_at for row in train_bars if row["symbol"] == symbol
            ),
            source_locator="fixed-campaign-basket",
        )
        for symbol in SYMBOLS
    )
    reports: dict[str, Any] = {}
    for scenario in ("BASE", "PESSIMISTIC"):
        config = _configuration(scenario)
        fee_schedule = ExchangeFeeSchedule(
            "STAGE3-PORTFOLIO-CONFORMANCE-FEES-v1",
            (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),),
        )

        def run(order_reversed: bool) -> BacktestResult:
            consumer = PITFeatureConsumer(
                matrix,
                expected_matrix_sha256=ADMITTED_MATRIX_SHA256["TRAIN"],
                suppressed_decision_ats=suppressed,
            )
            strategy = ExecutiveIntentSignalAdapter(
                consumer,
                insider_specialist=SECForm4InsiderSpecialistBot(
                    form4, expected_sha256=ADMITTED_FORM4_ARTIFACT_SHA256
                ),
                liquidation_signal_at=liquidation_signal_at,
            )
            engine = GuardrailedBacktestEngine(
                config=config,
                fee_schedule=fee_schedule,
                data_attestation=_attestation(
                    role="TRAIN-PORTFOLIO-CONFORMANCE",
                    symbol="AAPL-MSFT-SPY",
                    source_sha256=source_sha256,
                    feature_sha256=ADMITTED_MATRIX_SHA256["TRAIN"],
                    qualification_sha256=qualification_sha256,
                    receipt_sha256=qualification["qualification_sha256"],
                ),
            )
            return engine.run(
                bars=tuple(
                    _bar(row) for row in (
                        reversed(train_bars) if order_reversed else train_bars
                    )
                ),
                universe_events=(
                    tuple(reversed(universe_events))
                    if order_reversed else universe_events
                ),
                terminal_outcomes=(),
                corporate_actions=(
                    tuple(reversed(actions)) if order_reversed else actions
                ),
                prices_are_unadjusted=True,
                strategy=strategy,
                parameters=executive_intent_signal_parameters(),
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
            )

        canonical_result = run(False)
        reversed_result = run(True)
        _validate(canonical_result)
        _validate(reversed_result)
        canonical_projection = _projection(canonical_result)
        reversed_projection = _projection(reversed_result)
        if canonical_projection != reversed_projection:
            raise ValueError("portfolio execution changes with input symbol order")
        reports[scenario] = {
            "result_sha256": _hash(_canonical(canonical_projection)),
            "ending_equity": format(canonical_result.ending_equity, "f"),
            "completed_trade_count": len(canonical_result.completed_trades),
            "execution_count": len(canonical_result.executions),
            "intent_trace_count": len(canonical_result.executive_intents),
            "cash_reservation_count": len(canonical_result.cash_reservations),
            "input_order_conformance": True,
        }

    report = {
        "status": STATUS,
        "partition_role": "TRAIN",
        "symbols": list(SYMBOLS),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "one_session_embargo_and_purge_suppression": True,
        "portfolio_wide_batching_complete": True,
        "shared_cash_reservation_complete": True,
        "cross_symbol_order_conformance_complete": True,
        "validation_data_read": False,
        "untouched_test_included": False,
        "promotion_allowed": False,
        "scenarios": reports,
    }
    report["report_sha256"] = _hash(_canonical(report))
    if write_output:
        _write_private(OUTPUT, report)
    return report


if __name__ == "__main__":
    print(json.dumps(evaluate_train_portfolio_conformance(Path.cwd()), indent=2))
