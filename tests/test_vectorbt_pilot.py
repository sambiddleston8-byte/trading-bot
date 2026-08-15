"""Deterministic tests for the synthetic-only VectorBT research pilot.

These tests run the real VectorBT library against fixed synthetic bars.  They
prove the pilot's causal, cost and authority boundaries.  They deliberately do
NOT prove parity with `GuardrailedBacktestEngine`; the parity scenario asserts
the opposite.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ast
import json
from pathlib import Path

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.guardrailed_backtest import MarketBar, ReplayDataAttestation
from core.research.vectorbt_pilot import (
    ASSUMPTIONS_VERSION,
    BENCHMARK_DEFINITION,
    DIAGNOSTIC_ANNUALIZATION_FACTOR,
    DIAGNOSTIC_LABEL,
    DIAGNOSTIC_RETURN_FREQUENCY,
    DIAGNOSTIC_RISK_FREE_RATE,
    FIXED_FALSE,
    FORBIDDEN_VECTORBT_ATTRIBUTES,
    HARD_STOP_ACTIVE_ON_ENTRY_BAR,
    HARD_STOP_EXIT_PRICE_POLICY,
    POLICY_VERSION,
    QUANTIZE_DECIMAL_PLACES,
    TURNOVER_DEFINITION,
    WIN_RATE_DEFINITION,
    VECTORBT_LICENCE,
    VECTORBT_PINNED_VERSION,
    SyntheticPilotAttestation,
    SyntheticPilotBar,
    SyntheticPilotSignal,
    VectorBTPilotAdapter,
    VectorBTPilotAssumptions,
    VectorBTPilotResult,
    quantize,
    synthetic_bar_content_sha256,
)
from core.orchestration.vectorbt_pilot_audit import (
    RECORD_TYPE,
    STATUS,
    VectorBTPilotAuditLedger,
)


UTC = timezone.utc
FIRST_OPEN = datetime(2024, 1, 1, 14, 30, tzinfo=UTC)
SESSION_LENGTH = timedelta(hours=6, minutes=30)
PILOT_MODULE = Path(__file__).resolve().parents[1] / "core" / "research" / "vectorbt_pilot.py"


def bar(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000000",
    symbol: str = "SYN",
    available_after: timedelta = timedelta(0),
) -> SyntheticPilotBar:
    opens_at = FIRST_OPEN + timedelta(days=index)
    closes_at = opens_at + SESSION_LENGTH
    return SyntheticPilotBar(
        symbol=symbol,
        open_at=opens_at,
        close_at=closes_at,
        available_at=closes_at + available_after,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def flat_bars(count: int = 8, *, symbol: str = "SYN") -> list[SyntheticPilotBar]:
    """A deterministic, gently rising ramp with no stop-triggering gaps."""
    rows = []
    for index in range(count):
        base = 100 + index
        rows.append(
            bar(
                index,
                open_=str(base),
                high=str(base + 2),
                low=str(base - 1),
                close=str(base + 1),
                symbol=symbol,
            )
        )
    return rows


def content_digest(bars) -> str:
    """Digest of the supplied bars, falling back to a placeholder for negative tests."""
    try:
        return synthetic_bar_content_sha256(bars or ())
    except ValueError:
        return "a" * 64


def attestation(bars=None, **overrides) -> SyntheticPilotAttestation:
    values = {
        "data_version": "synthetic-ramp-v1",
        "content_sha256": content_digest(bars),
        "generator_description": "deterministic linear ramp fixture",
        "synthetic_data_declared": True,
        "point_in_time_verified": True,
        "timestamps_verified": True,
        "no_future_data_verified": True,
    }
    values.update(overrides)
    return SyntheticPilotAttestation(**values)


def assumptions(**overrides) -> VectorBTPilotAssumptions:
    values = {
        "initial_cash": Decimal("100000"),
        "fees_bps_per_side": Decimal("5"),
        "slippage_bps_per_side": Decimal("10"),
        "maximum_position_fraction": Decimal("0.25"),
        "hard_stop_loss_fraction": Decimal("0.10"),
    }
    values.update(overrides)
    return VectorBTPilotAssumptions(**values)


def run(bars, signals, *, adapter=None, strategy_version="pilot-strategy-v1", **kwargs):
    engine = adapter or VectorBTPilotAdapter(assumptions=assumptions())
    return engine.run(
        bars=bars,
        signals=signals,
        attestation=kwargs.pop("attestation_value", attestation(bars)),
        strategy_version=strategy_version,
    )


# ---------------------------------------------------------------- attestation


def test_pilot_refuses_a_replay_data_attestation():
    """The authenticated attestation must never be usable as pilot input."""
    real = ReplayDataAttestation._from_authenticated_artifacts(
        source_id="ADMISSION-1",
        source_content_sha256="b" * 64,
        validation_receipt_sha256="c" * 64,
        derivation_policy_version="policy-v1",
        evidence_role_hashes=tuple(
            sorted(
                (role, "d" * 64)
                for role in (
                    "CORPORATE_ACTIONS",
                    "DELISTING_OUTCOMES",
                    "MARKET_CALENDARS_AND_HALTS",
                    "RAW_DAILY_SESSION_BARS",
                    "TOTAL_RETURN_PRICES",
                    "UNIVERSE_MEMBERSHIP",
                )
            )
        ),
    )
    bars = flat_bars()
    signals = [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)]
    with pytest.raises(ValueError, match="SyntheticPilotAttestation"):
        VectorBTPilotAdapter(assumptions=assumptions()).run(
            bars=bars, signals=signals, attestation=real, strategy_version="s"
        )


def test_synthetic_attestation_cannot_be_built_from_replay_fields():
    with pytest.raises(TypeError):
        SyntheticPilotAttestation(  # type: ignore[call-arg]
            source_id="ADMISSION-1",
            source_content_sha256="b" * 64,
            validation_receipt_sha256="c" * 64,
            derivation_policy_version="p",
            evidence_role_hashes=(),
        )


@pytest.mark.parametrize(
    "field",
    [
        "synthetic_data_declared",
        "point_in_time_verified",
        "timestamps_verified",
        "no_future_data_verified",
    ],
)
def test_attestation_requires_every_check_flag_true(field):
    with pytest.raises(ValueError, match=field):
        attestation(**{field: False})


def test_attestation_requires_a_real_digest_and_version():
    with pytest.raises(ValueError, match="content_sha256"):
        attestation(content_sha256="not-a-digest")
    with pytest.raises(ValueError, match="data_version"):
        attestation(data_version="  ")


def test_attestation_digest_must_match_the_supplied_bars():
    """A declared data version cannot be attached to different content."""
    bars = flat_bars()
    signals = [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)]
    wrong = attestation(bars, content_sha256="b" * 64)
    with pytest.raises(ValueError, match="does not match the supplied synthetic bars"):
        VectorBTPilotAdapter(assumptions=assumptions()).run(
            bars=bars, signals=signals, attestation=wrong, strategy_version="s"
        )


def test_attestation_digest_is_content_addressed_and_order_sensitive():
    bars = flat_bars()
    assert synthetic_bar_content_sha256(bars) == synthetic_bar_content_sha256(flat_bars())
    mutated = [*bars[:-1], bar(len(bars) - 1, open_="200", high="205", low="199", close="204")]
    assert synthetic_bar_content_sha256(mutated) != synthetic_bar_content_sha256(bars)
    # An attestation minted for one dataset is refused by another.
    signals = [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)]
    with pytest.raises(ValueError, match="does not match the supplied synthetic bars"):
        VectorBTPilotAdapter(assumptions=assumptions()).run(
            bars=mutated,
            signals=signals,
            attestation=attestation(bars),
            strategy_version="s",
        )


def test_pilot_refuses_engine_market_bars():
    bars = flat_bars()
    engine_bar = MarketBar(
        symbol="SYN",
        open_at=FIRST_OPEN,
        close_at=FIRST_OPEN + SESSION_LENGTH,
        available_at=FIRST_OPEN + SESSION_LENGTH,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
    )
    with pytest.raises(ValueError, match="SyntheticPilotBar"):
        run([engine_bar, *bars[1:]], [])


# ------------------------------------------------------------ causal ordering


def test_signal_at_prior_close_executes_at_exactly_the_next_bar_open():
    """T-1 close signal fills at the T open price, never the T-1 close."""
    bars = flat_bars()
    signal_bar, execution_bar = bars[2], bars[3]
    result = run(
        bars,
        [SyntheticPilotSignal("SYN", "ENTER_LONG", signal_bar.close_at, signal_bar.close_at)],
    )
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    portfolio = adapter._simulate(
        tuple(bars),
        {3: SyntheticPilotSignal("SYN", "ENTER_LONG", signal_bar.close_at, signal_bar.close_at)},
    )
    records = portfolio.orders.records_readable
    assert len(records) == 1
    fill_price = Decimal(str(float(records["Price"].iloc[0])))
    expected = execution_bar.open * (Decimal("1") + Decimal("10") / Decimal("10000"))
    assert quantize(fill_price, "p") == quantize(expected, "p")
    # It is emphatically not the signal bar's close, nor the signal bar's open.
    assert quantize(fill_price, "p") != quantize(signal_bar.close, "p")
    assert quantize(fill_price, "p") != quantize(signal_bar.open, "p")
    assert result.order_count == 1


def test_signal_bound_to_final_close_is_rejected_for_lack_of_a_next_open():
    bars = flat_bars()
    with pytest.raises(ValueError, match="no next bar open"):
        run(bars, [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[-1].close_at, bars[-1].close_at)])


def test_signal_must_bind_to_an_exact_bar_close():
    bars = flat_bars()
    with pytest.raises(ValueError, match="exact synthetic bar close"):
        run(
            bars,
            [
                SyntheticPilotSignal(
                    "SYN", "ENTER_LONG", bars[2].close_at + timedelta(minutes=1), bars[2].close_at
                )
            ],
        )


def test_signal_available_before_its_bound_close_is_future_data():
    bars = flat_bars()
    with pytest.raises(ValueError, match="future data"):
        run(
            bars,
            [
                SyntheticPilotSignal(
                    "SYN",
                    "ENTER_LONG",
                    bars[2].close_at,
                    bars[2].close_at - timedelta(seconds=1),
                )
            ],
        )


def test_signal_available_at_or_after_the_next_open_cannot_execute():
    bars = flat_bars()
    with pytest.raises(ValueError, match="strictly before the next bar open"):
        run(
            bars,
            [
                SyntheticPilotSignal(
                    "SYN", "ENTER_LONG", bars[2].close_at, bars[3].open_at
                )
            ],
        )


def test_duplicate_signals_on_one_close_are_rejected():
    bars = flat_bars()
    signal = SyntheticPilotSignal("SYN", "ENTER_LONG", bars[2].close_at, bars[2].close_at)
    with pytest.raises(ValueError, match="duplicate"):
        run(bars, [signal, signal])


def test_signals_must_be_supplied_in_strict_chronological_close_order():
    bars = flat_bars()
    later = SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at)
    earlier = SyntheticPilotSignal("SYN", "ENTER_LONG", bars[2].close_at, bars[2].close_at)
    with pytest.raises(ValueError, match="strict chronological bar-close order"):
        run(bars, [later, earlier])
    # The same pair in order is accepted.
    assert run(bars, [earlier, later]).order_count == 2


def test_signal_available_before_its_bound_bar_became_available_is_rejected():
    """A bar published after its close cannot be used before it was published."""
    rows = [
        bar(index, open_=str(100 + index), high=str(102 + index), low=str(99 + index),
            close=str(101 + index), available_after=timedelta(minutes=30))
        for index in range(6)
    ]
    signal = SyntheticPilotSignal(
        "SYN",
        "ENTER_LONG",
        rows[2].close_at,
        rows[2].close_at + timedelta(minutes=5),
    )
    assert signal.available_at >= rows[2].close_at
    assert signal.available_at < rows[2].available_at
    with pytest.raises(ValueError, match="before its bound bar became available"):
        run(rows, [signal])
    # Availability at or after publication is accepted.
    allowed = SyntheticPilotSignal(
        "SYN", "ENTER_LONG", rows[2].close_at, rows[2].available_at
    )
    assert run(rows, [allowed]).order_count == 1


def test_out_of_order_bars_are_rejected():
    bars = flat_bars()
    swapped = [bars[1], bars[0], *bars[2:]]
    with pytest.raises(ValueError, match="strict chronological order"):
        run(swapped, [])


def test_duplicate_bars_are_rejected():
    bars = flat_bars()
    with pytest.raises(ValueError, match="strict chronological order"):
        run([bars[0], bars[0], *bars[1:]], [])


def test_skipped_session_is_rejected_as_ambiguous_frequency():
    bars = [*flat_bars(4), bar(9, open_="120", high="122", low="119", close="121")]
    with pytest.raises(ValueError, match="skipped or ambiguous"):
        run(bars, [])


def test_uniform_non_daily_sessions_are_rejected_before_sharpe_is_computed():
    """Uniform spacing alone must not be mislabeled as daily in the audit record."""
    rows = []
    for index, original in enumerate(flat_bars(4)):
        opens_at = FIRST_OPEN + timedelta(hours=index)
        closes_at = opens_at + timedelta(minutes=30)
        rows.append(
            replace(
                original,
                open_at=opens_at,
                close_at=closes_at,
                available_at=closes_at,
            )
        )
    with pytest.raises(ValueError, match="exactly one day apart"):
        run(rows, [])


def test_bar_not_available_before_the_next_open_is_rejected():
    rows = flat_bars(4)
    late = SyntheticPilotBar(
        symbol=rows[1].symbol,
        open_at=rows[1].open_at,
        close_at=rows[1].close_at,
        available_at=rows[2].open_at + timedelta(seconds=1),
        open=rows[1].open,
        high=rows[1].high,
        low=rows[1].low,
        close=rows[1].close,
        volume=rows[1].volume,
    )
    with pytest.raises(ValueError, match="available strictly before"):
        run([rows[0], late, *rows[2:]], [])


def test_multiple_instruments_are_rejected():
    rows = [*flat_bars(4), *flat_bars(4, symbol="OTHER")]
    with pytest.raises(ValueError, match="exactly one instrument"):
        run(rows, [])


# ------------------------------------------------------------------ economics


def test_fees_and_slippage_are_adverse_and_applied_exactly_once():
    """Buy fills above the open, sell below it, each with one fee charge."""
    bars = flat_bars()
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    portfolio = adapter._simulate(
        tuple(bars),
        {
            2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
            5: SyntheticPilotSignal("SYN", "EXIT_LONG", bars[4].close_at, bars[4].close_at),
        },
    )
    records = portfolio.orders.records_readable
    assert list(records["Side"]) == ["Buy", "Sell"]
    slip = Decimal("10") / Decimal("10000")
    fee_rate = Decimal("5") / Decimal("10000")

    buy_price = Decimal(str(float(records["Price"].iloc[0])))
    sell_price = Decimal(str(float(records["Price"].iloc[1])))
    assert quantize(buy_price, "p") == quantize(bars[2].open * (Decimal("1") + slip), "p")
    assert quantize(sell_price, "p") == quantize(bars[5].open * (Decimal("1") - slip), "p")
    assert buy_price > bars[2].open and sell_price < bars[5].open

    for row in range(2):
        size = Decimal(str(float(records["Size"].iloc[row])))
        price = Decimal(str(float(records["Price"].iloc[row])))
        fee = Decimal(str(float(records["Fees"].iloc[row])))
        assert quantize(fee, "f") == quantize(size * price * fee_rate, "f")


def test_position_never_exceeds_the_maximum_fraction_and_never_levers():
    bars = flat_bars()
    fraction = Decimal("0.25")
    adapter = VectorBTPilotAdapter(
        assumptions=assumptions(maximum_position_fraction=fraction)
    )
    portfolio = adapter._simulate(
        tuple(bars),
        {2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)},
    )
    records = portfolio.orders.records_readable
    notional = Decimal(str(float(records["Size"].iloc[0]))) * Decimal(
        str(float(records["Price"].iloc[0]))
    )
    initial = Decimal("100000")
    assert notional <= initial * fraction
    # No leverage: cash never goes negative at any point in the run.
    assert float(portfolio.cash().min()) >= 0.0
    assert float(portfolio.value().min()) > 0.0


def test_accumulation_is_disabled_so_repeated_entries_add_nothing():
    bars = flat_bars(10)
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    portfolio = adapter._simulate(
        tuple(bars),
        {
            2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
            4: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[3].close_at, bars[3].close_at),
            6: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[5].close_at, bars[5].close_at),
        },
    )
    assert int(portfolio.orders.count()) == 1


def test_hard_stop_exits_a_collapsing_instrument():
    """A deep gap down must trigger the pilot's hard stop, not ride the loss."""
    rows = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
        bar(2, open_="100", high="101", low="99", close="100"),
        bar(3, open_="60", high="61", low="55", close="56"),
        bar(4, open_="55", high="56", low="50", close="51"),
    ]
    adapter = VectorBTPilotAdapter(
        assumptions=assumptions(hard_stop_loss_fraction=Decimal("0.10"))
    )
    portfolio = adapter._simulate(
        tuple(rows),
        {2: SyntheticPilotSignal("SYN", "ENTER_LONG", rows[1].close_at, rows[1].close_at)},
    )
    records = portfolio.orders.records_readable
    assert list(records["Side"]) == ["Buy", "Sell"], "hard stop did not liquidate"
    expected_gap_fill = rows[3].open * (Decimal("1") - Decimal("10") / Decimal("10000"))
    assert quantize(records["Price"].iloc[1], "gap stop fill") == quantize(
        expected_gap_fill, "expected gap stop fill"
    )
    assert int(portfolio.trades.count()) == 1
    assert float(portfolio.trades.records_readable["PnL"].iloc[0]) < 0.0
    # Without the stop the position would still be open at the end of the run.
    unprotected = VectorBTPilotAdapter(
        assumptions=assumptions(hard_stop_loss_fraction=Decimal("0.99"))
    )._simulate(
        tuple(rows),
        {2: SyntheticPilotSignal("SYN", "ENTER_LONG", rows[1].close_at, rows[1].close_at)},
    )
    assert int(unprotected.orders.count()) == 1


def test_hard_stop_recovery_fills_at_stop_market_not_recovered_close():
    """An intrabar breach cannot become an artificial winner at the close."""
    rows = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
        bar(2, open_="100", high="101", low="99", close="100"),
        bar(3, open_="100", high="105", low="80", close="103"),
        bar(4, open_="103", high="104", low="102", close="103"),
    ]
    adapter = VectorBTPilotAdapter(
        assumptions=assumptions(hard_stop_loss_fraction=Decimal("0.10"))
    )
    portfolio = adapter._simulate(
        tuple(rows),
        {2: SyntheticPilotSignal("SYN", "ENTER_LONG", rows[1].close_at, rows[1].close_at)},
    )
    records = portfolio.orders.records_readable
    assert list(records["Side"]) == ["Buy", "Sell"]
    stop_market_fill = (
        rows[2].open * (Decimal("1") + Decimal("10") / Decimal("10000"))
        * (Decimal("1") - Decimal("0.10"))
        * (Decimal("1") - Decimal("10") / Decimal("10000"))
    )
    assert quantize(records["Price"].iloc[1], "stop fill") == quantize(
        stop_market_fill, "expected stop fill"
    )
    assert Decimal(str(records["Price"].iloc[1])) < rows[3].close
    assert float(portfolio.trades.closed.records_readable["PnL"].iloc[0]) < 0.0


def test_stop_policy_records_vectorbt_entry_bar_limitation():
    material = assumptions().material()
    assert material["hard_stop_exit_price_policy"] == HARD_STOP_EXIT_PRICE_POLICY
    assert material["hard_stop_active_on_entry_bar"] is HARD_STOP_ACTIVE_ON_ENTRY_BAR is False
    assert material["signal_same_bar_execution_allowed"] is False


def test_assumptions_enforce_the_slippage_floor_and_no_leverage():
    with pytest.raises(ValueError, match="10 bps"):
        assumptions(slippage_bps_per_side=Decimal("9"))
    with pytest.raises(ValueError, match="no leverage"):
        assumptions(maximum_position_fraction=Decimal("1.5"))
    with pytest.raises(ValueError, match="below 1"):
        assumptions(hard_stop_loss_fraction=Decimal("1"))


# -------------------------------------------------------------------- metrics


def test_every_requested_metric_is_present_and_quantized():
    bars = flat_bars()
    result = run(
        bars,
        [
            SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
            SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
        ],
    )
    for name in (
        "total_return",
        "benchmark_total_return",
        "excess_return_vs_benchmark",
        "sharpe_ratio",
        "maximum_drawdown",
        "win_rate",
        "turnover",
    ):
        value = getattr(result, name)
        assert isinstance(value, Decimal), name
        assert -value.as_tuple().exponent == QUANTIZE_DECIMAL_PLACES, name
    assert isinstance(result.trade_count, int) and result.trade_count == 1
    assert result.open_trade_count == 0
    assert isinstance(result.order_count, int) and result.order_count == 2
    assert result.maximum_drawdown >= 0
    assert result.win_rate == Decimal("1.000000000")


def test_benchmark_is_the_same_window_buy_and_hold_and_excess_reconciles():
    bars = flat_bars()
    result = run(
        bars,
        [
            SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
            SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
        ],
    )
    expected = quantize(bars[-1].close / bars[0].close - Decimal("1"), "b")
    assert result.benchmark_total_return == expected
    assert (
        result.excess_return_vs_benchmark
        == result.total_return - result.benchmark_total_return
    )
    assert result.evaluation_start == bars[0].open_at
    assert result.evaluation_end == bars[-1].close_at


def test_turnover_matches_its_declared_definition():
    bars = flat_bars()
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    signals = {
        2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
        5: SyntheticPilotSignal("SYN", "EXIT_LONG", bars[4].close_at, bars[4].close_at),
    }
    result = adapter._metrics(
        adapter._simulate(tuple(bars), signals),
        bars=tuple(bars),
        attestation=attestation(),
        strategy_version="s",
        input_sha256="e" * 64,
    )
    records = adapter._simulate(tuple(bars), signals).orders.records_readable
    manual = sum(
        Decimal(str(float(records["Size"].iloc[row])))
        * Decimal(str(float(records["Price"].iloc[row])))
        for row in range(len(records))
    )
    assert result.turnover == quantize(manual / Decimal("100000"), "t")


def test_benchmark_and_sharpe_are_labelled_diagnostic_only():
    bars = flat_bars()
    result = run(
        bars,
        [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)],
    )
    assert result.benchmark_label == DIAGNOSTIC_LABEL
    assert result.sharpe_label == DIAGNOSTIC_LABEL
    assert result.sharpe_risk_free_rate == DIAGNOSTIC_RISK_FREE_RATE == Decimal("0")
    assert result.sharpe_annualization_factor == DIAGNOSTIC_ANNUALIZATION_FACTOR == 252
    assert result.sharpe_return_frequency == DIAGNOSTIC_RETURN_FREQUENCY
    assert result.benchmark_definition == BENCHMARK_DEFINITION
    assert result.turnover_definition == TURNOVER_DEFINITION


def test_open_positions_are_not_counted_as_wins_or_closed_trades():
    bars = flat_bars()
    result = run(
        bars,
        [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)],
    )
    assert result.trade_count == 0
    assert result.open_trade_count == 1
    assert result.win_rate is None
    assert result.material()["win_rate"] is None
    assert result.win_rate_definition == WIN_RATE_DEFINITION
    assert result.material()["win_rate_definition"] == WIN_RATE_DEFINITION


def test_undefined_sharpe_is_recorded_as_null_not_fabricated_zero():
    bars = flat_bars()
    result = run(bars, [])
    assert result.sharpe_ratio is None
    assert result.material()["sharpe_ratio"] is None

    material = result.material()
    assert material["satisfies_sharpe_metric_readiness_gate"] is False
    assert material["satisfies_benchmark_evidence_rules"] is False
    assert "SharpeMetricReadinessGate" in material[
        "diagnostic_metrics_do_not_satisfy_existing_gates"
    ]


def test_sharpe_uses_the_recorded_definition_not_library_defaults():
    """VectorBT defaults to 365-day annualization; the pilot must pin 252."""
    import math

    bars = flat_bars(10)
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    signals = {
        2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
        6: SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
    }
    portfolio = adapter._simulate(tuple(bars), signals)
    result = adapter._metrics(
        portfolio,
        bars=tuple(bars),
        attestation=attestation(bars),
        strategy_version="s",
        input_sha256="e" * 64,
    )
    returns = portfolio.returns()
    manual = (
        math.sqrt(DIAGNOSTIC_ANNUALIZATION_FACTOR)
        * (returns.mean() - float(DIAGNOSTIC_RISK_FREE_RATE))
        / returns.std(ddof=1)
    )
    assert result.sharpe_ratio == quantize(manual, "s")
    # The library default would annualize by 365 days and must not be recorded.
    library_default = portfolio.sharpe_ratio()
    assert quantize(library_default, "s") != result.sharpe_ratio
    assert quantize(
        library_default / math.sqrt(365 / DIAGNOSTIC_ANNUALIZATION_FACTOR), "s"
    ) == result.sharpe_ratio

    material = result.material()
    assert material["sharpe_annualization_factor"] == 252
    assert material["sharpe_year_frequency"] == "252 days"
    assert material["sharpe_sample_ddof"] == 1
    assert material["sharpe_risk_free_rate"] == "0"
    assert "sqrt(252)" in material["sharpe_formula"]


def test_cash_settlement_is_recorded_as_unmodelled():
    """The pilot is more permissive than the engine and must say so."""
    bars = flat_bars()
    result = run(
        bars,
        [
            SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
            SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
        ],
    )
    assert result.cash_settlement_modelled is False
    material = result.material()
    assert material["cash_settlement_modelled"] is False
    assert "more permissive" in material["cash_settlement_note"]


def test_every_authority_flag_is_fixed_false():
    bars = flat_bars()
    result = run(
        bars,
        [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)],
    )
    material = result.material()
    for field in FIXED_FALSE:
        assert material[field] is False, field
    assert material["simulation_only"] is True
    assert material["synthetic_data_only"] is True


def test_result_authority_and_metric_definitions_cannot_be_forged():
    result = ledger_result()
    with pytest.raises(ValueError, match="simulation_only"):
        replace(result, simulation_only=False)
    with pytest.raises(ValueError, match="benchmark_label"):
        replace(result, benchmark_label="AUTHORITATIVE")
    with pytest.raises(ValueError, match="cash_settlement_modelled"):
        replace(result, cash_settlement_modelled=True)


def test_quantize_rejects_non_finite_values():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            quantize(value, "metric")
    with pytest.raises(ValueError, match="real number"):
        quantize("0.5", "metric")


# ---------------------------------------------------------- deterministic IDs


def test_identical_inputs_produce_identical_hashes():
    bars = flat_bars()
    signals = [
        SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
        SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
    ]
    first = run(bars, signals)
    second = run(flat_bars(), list(signals))
    assert first.input_sha256 == second.input_sha256
    assert first.result_sha256() == second.result_sha256()
    assert first.attestation_sha256 == second.attestation_sha256
    assert first.assumptions_sha256 == second.assumptions_sha256


def test_changed_assumptions_change_the_input_and_result_hashes():
    bars = flat_bars()
    signals = [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)]
    baseline = run(bars, signals)
    altered = run(
        bars,
        signals,
        adapter=VectorBTPilotAdapter(
            assumptions=assumptions(slippage_bps_per_side=Decimal("25"))
        ),
    )
    assert altered.assumptions_sha256 != baseline.assumptions_sha256
    assert altered.input_sha256 != baseline.input_sha256
    assert altered.result_sha256() != baseline.result_sha256()


def test_result_and_ledger_material_record_exact_assumptions_not_only_a_hash():
    result = ledger_result()
    expected = assumptions().material()
    assert result.material()["assumptions"] == expected
    assert result.assumptions_sha256 == assumptions().identity_sha256()
    with pytest.raises(ValueError, match="do not match assumptions_sha256"):
        replace(
            result,
            assumptions=tuple(
                sorted({**expected, "fees_bps_per_side": "999"}.items())
            ),
        )


def test_changed_data_changes_the_input_hash():
    bars = flat_bars()
    signals = [SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)]
    baseline = run(bars, signals)
    moved = [*bars[:-1], bar(len(bars) - 1, open_="200", high="205", low="199", close="204")]
    altered = run(moved, signals)
    assert altered.input_sha256 != baseline.input_sha256


# --------------------------------------------------------------------- ledger


def ledger_result(bars=None, signals=None, **kwargs):
    bars = bars or flat_bars()
    signals = signals if signals is not None else [
        SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at),
        SyntheticPilotSignal("SYN", "EXIT_LONG", bars[5].close_at, bars[5].close_at),
    ]
    return run(bars, signals, **kwargs)


def test_ledger_appends_a_pilot_only_record(tmp_path):
    ledger = VectorBTPilotAuditLedger(tmp_path / "pilot.jsonl")
    record = ledger.append(result=ledger_result(), recorded_by="tester")
    assert record["record_type"] == RECORD_TYPE == "VECTORBT_SYNTHETIC_PILOT_ONLY"
    assert record["status"] == STATUS
    assert record["previous_hash"] == GENESIS_HASH
    assert record["result"]["vectorbt_version"] == VECTORBT_PINNED_VERSION
    assert record["result"]["vectorbt_licence"] == VECTORBT_LICENCE
    assert record["result"]["assumptions_version"] == ASSUMPTIONS_VERSION
    assert record["result"]["assumptions"] == assumptions().material()
    assert record["result"]["policy_version"] == POLICY_VERSION
    assert record["result"]["parity_with_guardrailed_engine_proven"] is False
    assert ledger.verify() == [record]


def test_ledger_append_is_idempotent(tmp_path):
    ledger = VectorBTPilotAuditLedger(tmp_path / "pilot.jsonl")
    result = ledger_result()
    first = ledger.append(result=result, recorded_by="tester")
    second = ledger.append(result=result, recorded_by="tester")
    assert first["record_hash"] == second["record_hash"]
    assert len(ledger.verify()) == 1


def test_ledger_chains_distinct_runs(tmp_path):
    ledger = VectorBTPilotAuditLedger(tmp_path / "pilot.jsonl")
    first = ledger.append(result=ledger_result(), recorded_by="tester")
    other = ledger_result(
        signals=[SyntheticPilotSignal("SYN", "ENTER_LONG", flat_bars()[2].close_at, flat_bars()[2].close_at)]
    )
    second = ledger.append(result=other, recorded_by="tester")
    assert second["previous_hash"] == first["record_hash"]
    assert len(ledger.verify()) == 2


def test_ledger_detects_a_tampered_metric(tmp_path):
    path = tmp_path / "pilot.jsonl"
    ledger = VectorBTPilotAuditLedger(path)
    ledger.append(result=ledger_result(), recorded_by="tester")
    record = json.loads(path.read_text().strip())
    record["result"]["total_return"] = "9.000000000"
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_ledger_detects_a_flipped_authority_flag(tmp_path):
    path = tmp_path / "pilot.jsonl"
    ledger = VectorBTPilotAuditLedger(path)
    ledger.append(result=ledger_result(), recorded_by="tester")
    record = json.loads(path.read_text().strip())
    record["result"]["parity_with_guardrailed_engine_proven"] = True
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_ledger_detects_a_truncated_line(tmp_path):
    path = tmp_path / "pilot.jsonl"
    ledger = VectorBTPilotAuditLedger(path)
    ledger.append(result=ledger_result(), recorded_by="tester")
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_ledger_rejects_a_non_pilot_result(tmp_path):
    ledger = VectorBTPilotAuditLedger(tmp_path / "pilot.jsonl")
    with pytest.raises(ValueError, match="VectorBTPilotResult"):
        ledger.append(result=object(), recorded_by="tester")


def test_ledger_rejects_a_result_claiming_authority(tmp_path):
    ledger = VectorBTPilotAuditLedger(tmp_path / "pilot.jsonl")
    base = ledger_result()

    class Escalated(VectorBTPilotResult):
        pass

    forged = Escalated(**{key: getattr(base, key) for key in base.__dataclass_fields__})
    with pytest.raises(ValueError, match="VectorBTPilotResult"):
        ledger.append(result=forged, recorded_by="tester")


# -------------------------------------------------------- no broker/network


def test_pilot_module_never_imports_vectorbt_at_module_scope():
    tree = ast.parse(PILOT_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        assert not isinstance(node, (ast.Import, ast.ImportFrom)) or "vectorbt" not in (
            ast.unparse(node)
        ), "vectorbt must be imported lazily inside a function"


BANNED_MODULE_ROOTS = frozenset(
    {
        "requests",
        "urllib",
        "urllib3",
        "http",
        "socket",
        "ssl",
        "asyncio",
        "websockets",
        "schedule",
        "yfinance",
        "alpaca",
        "alpaca_trade_api",
        "ccxt",
        "binance",
        "telegram",
        "smtplib",
        "ftplib",
        "subprocess",
    }
)
# Transport verbs only. Container verbs such as `get` are excluded because the
# pilot legitimately calls `dict.get`.
BANNED_CALL_NAMES = frozenset(
    {"urlopen", "request", "connect", "recv", "download", "fetch", "sendall"}
)


def pilot_ast() -> ast.Module:
    return ast.parse(PILOT_MODULE.read_text(encoding="utf-8"))


def imported_module_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_pilot_module_imports_no_broker_or_network_package():
    """An AST import inventory, so explanatory prose cannot trigger or mask it."""
    roots = imported_module_roots(pilot_ast())
    assert roots & BANNED_MODULE_ROOTS == set(), sorted(roots & BANNED_MODULE_ROOTS)
    assert "vectorbt" in roots, "the pilot is expected to import vectorbt lazily"


def test_pilot_module_accesses_no_broker_or_data_attribute():
    """Attribute and call inventory: string literals are never inspected."""
    tree = pilot_ast()
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in FORBIDDEN_VECTORBT_ATTRIBUTES:
        assert forbidden not in attributes, f"pilot must not touch vectorbt.{forbidden}"

    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called.add(target.attr)
            elif isinstance(target, ast.Name):
                called.add(target.id)
    assert called & BANNED_CALL_NAMES == set(), sorted(called & BANNED_CALL_NAMES)

    # The banned roots must not appear as bare names either (e.g. `requests.get`).
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert names & BANNED_MODULE_ROOTS == set(), sorted(names & BANNED_MODULE_ROOTS)


def test_banned_call_inventory_covers_the_documented_verbs():
    """Guard the guard: the banned-verb list must stay non-empty and meaningful."""
    assert BANNED_CALL_NAMES and BANNED_MODULE_ROOTS
    assert "requests" in BANNED_MODULE_ROOTS and "alpaca" in BANNED_MODULE_ROOTS


def test_pilot_uses_only_portfolio_from_signals():
    tree = ast.parse(PILOT_MODULE.read_text(encoding="utf-8"))
    chains = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if isinstance(node.value.value, ast.Name) and node.value.value.id == "vectorbt":
                chains.add(f"{node.value.attr}.{node.attr}")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "vectorbt":
                chains.add(node.attr)
    assert chains <= {"Portfolio.from_signals", "Portfolio", "__version__"}, chains


def test_pilot_pins_the_verified_dependency_version(monkeypatch):
    import core.research.vectorbt_pilot as module

    class Fake:
        __version__ = "0.9.9"

    monkeypatch.setitem(__import__("sys").modules, "vectorbt", Fake())
    with pytest.raises(ValueError, match="pinned to VectorBT"):
        module._load_from_signals()


# ------------------------------------------------------------ leakage attempts


def test_leakage_attempt_signal_from_the_future_close_is_rejected():
    """A signal claiming to know a later bar's close cannot be bound to it."""
    bars = flat_bars()
    with pytest.raises(ValueError, match="future data"):
        run(
            bars,
            [
                SyntheticPilotSignal(
                    "SYN", "ENTER_LONG", bars[5].close_at, bars[2].close_at
                )
            ],
        )


def test_leakage_attempt_same_bar_execution_is_structurally_impossible():
    """Even a signal available exactly at the close fills only at the next open."""
    bars = flat_bars()
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    portfolio = adapter._simulate(
        tuple(bars),
        {3: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[2].close_at, bars[2].close_at)},
    )
    timestamp = portfolio.orders.records_readable["Timestamp"].iloc[0]
    assert timestamp.to_pydatetime() == bars[3].open_at
    assert timestamp.to_pydatetime() != bars[2].open_at


def test_leakage_attempt_valuation_uses_the_prior_close_not_the_current_one():
    """VectorBT's default val_price would leak the current close; the pilot pins the prior one."""
    import inspect

    source = inspect.getsource(VectorBTPilotAdapter._simulate)
    assert "val_price=close.shift(1)" in source
    assert "update_value=False" in source
    # A run must not depend on the current bar close for sizing: shifting a
    # later close cannot change an earlier fill.
    bars = flat_bars()
    adapter = VectorBTPilotAdapter(assumptions=assumptions())
    signals = {2: SyntheticPilotSignal("SYN", "ENTER_LONG", bars[1].close_at, bars[1].close_at)}
    baseline = adapter._simulate(tuple(bars), signals).orders.records_readable
    mutated = [
        *bars[:6],
        bar(6, open_="106", high="900", low="105", close="880"),
        bars[7],
    ]
    after = adapter._simulate(tuple(mutated), signals).orders.records_readable
    assert float(baseline["Size"].iloc[0]) == float(after["Size"].iloc[0])
    assert float(baseline["Price"].iloc[0]) == float(after["Price"].iloc[0])


# ------------------------------------------------- manual parity scenario only


def test_fixed_manual_scenario_matches_hand_computed_arithmetic():
    """One fully hand-computed scenario, asserted against manual arithmetic.

    This is a self-consistency check of the pilot's own cost model.  It is NOT
    a parity check against GuardrailedBacktestEngine; see the test below.
    """
    rows = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
        bar(2, open_="100", high="101", low="99", close="100"),
        bar(3, open_="110", high="111", low="109", close="110"),
    ]
    adapter = VectorBTPilotAdapter(
        assumptions=assumptions(
            initial_cash=Decimal("100000"),
            fees_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("10"),
            maximum_position_fraction=Decimal("1"),
        )
    )
    portfolio = adapter._simulate(
        tuple(rows),
        {
            2: SyntheticPilotSignal("SYN", "ENTER_LONG", rows[1].close_at, rows[1].close_at),
            3: SyntheticPilotSignal("SYN", "EXIT_LONG", rows[2].close_at, rows[2].close_at),
        },
    )
    records = portfolio.orders.records_readable
    # Buy at bar 2 open 100 * 1.001 = 100.10; sell at bar 3 open 110 * 0.999 = 109.89.
    assert quantize(float(records["Price"].iloc[0]), "p") == Decimal("100.100000000")
    assert quantize(float(records["Price"].iloc[1]), "p") == Decimal("109.890000000")
    size = Decimal("100000") / Decimal("100.10")
    expected_end = size * Decimal("109.89")
    assert quantize(float(portfolio.value().iloc[-1]), "v") == quantize(expected_end, "v")


def test_parity_with_the_guardrailed_engine_is_explicitly_unproven():
    """The pilot must never claim, imply or record engine parity."""
    from core.guardrailed_backtest import BacktestResult

    result = ledger_result()
    assert result.material()["parity_with_guardrailed_engine_proven"] is False
    assert not isinstance(result, BacktestResult)
    # The pilot's own risk model differs from the engine's ATR-risk sizing, so
    # exact semantic equivalence is not established anywhere in this suite.
    assert "maximum_position_fraction" in VectorBTPilotAssumptions.__dataclass_fields__
    assert not hasattr(result, "sizing_decisions")
    assert not hasattr(result, "portfolio_states")


def test_pilot_result_cannot_enter_the_authenticated_replay_audit_ledger(tmp_path):
    from core.orchestration.replay_run_audit import ReplayRunAuditLedger

    authenticated = ReplayRunAuditLedger(
        tmp_path / "replay.jsonl",
        admission_ledger=None,
        content_ledger=None,
        strategy_ledger=None,
        execution_policy_ledger=None,
    )
    # The authenticated ledger requires engine-only traces the pilot never has.
    with pytest.raises(AttributeError, match="portfolio_states"):
        authenticated.append(
            result=ledger_result(), git_revision="a" * 40, recorded_by="tester"
        )
