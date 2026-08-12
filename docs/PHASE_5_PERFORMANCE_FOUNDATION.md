# Phase 5 performance and attribution foundation

Status: immutable observations, price returns and corporate-action evidence;
no total-return track record or learning input

Issue #25 introduces the authoritative raw-data boundary for later performance
and attribution. It does not calculate returns, alpha, hit rate or a track
record. First it ensures the prices used by those future calculations are
timestamped, reproducible and linked to the exact decision and local simulated
fill that created the measurement obligation.

## Fixed observation horizons

Every fill can have at most one observation at each predeclared horizon:

- entry;
- 1 day;
- 1 week;
- 1 month;
- 3 months;
- 6 months;
- 12 months; and
- 24 months.

Months use calendar-month arithmetic rather than assuming every month has 30
days. A market observation must fall on or within seven days after its target,
allowing weekends and market holidays without silently moving the horizon.

## Traceability and timing

Each observation inherits the fill, proposal order, decision, portfolio,
strategy, model and Git identities from a verified simulated-fill ledger. Entry
asset price is always the simulated fill price and cannot be supplied by the
caller. The S&P 500 (`^GSPC`) is the initial benchmark required by the Master
Roadmap.

The record separately stores the asset price time, benchmark price time and data
retrieval time, so it cannot imply that differently timed prices were sampled
together. An entry benchmark must be at or within four days before the simulated
fill. Later asset and benchmark prices must each fall within the declared
seven-day horizon window.

`retrieval_mode` describes retrieval speed only: retrieval more than 72 hours
after the older price timestamp is labelled `BACKFILLED`. It does not mean the
price landed on the exact target; `target_alignment` separately confirms only
that both timestamps are within the declared window. Prices, source, source
version and adjusted/unadjusted basis are mandatory and the basis must remain
consistent across the full observation series.

A later horizon cannot be recorded until the entry observation exists. This
prevents isolated outcome prices from accumulating without the baseline needed
for a future reproducible comparison.

## Safety boundary

Records are append-only, hash-chained and safe for identical retries. A
conflicting record for the same fill/horizon fails closed. Interrupted final
writes require an explicit repair that preserves the incomplete bytes.

This component deliberately performs no market-data download, broker action,
return calculation or learning adjustment. `performance_claim` is always
`false`. The older `OutcomeEngine` and `DecisionTracker` remain legacy
experiments because they overwrite mutable JSON, use sequential IDs/local time
and are not linked to immutable execution records.

The next Phase 5 slice can calculate deterministic returns only from a verified
entry observation and a verified due-horizon observation. Until then the
platform must continue to say that there is not enough authoritative data to
claim performance.

That future calculator must also handle splits, dividends and other corporate
actions explicitly. It must not combine a raw simulated fill with an adjusted
later price as though the two were automatically comparable.

## Verified price-return outcomes

Issue #27 adds the first deterministic calculation layer, deliberately limited
to `PRICE_RETURN_ONLY`. A result is appended only when it has:

- a verified long `BUY` simulated fill;
- verified entry and due-horizon observations;
- `UNADJUSTED_CLOSE` asset and benchmark price bases;
- an explicitly sourced `NO_EVENTS` corporate-action check covering the full
  period from entry through the outcome date; and
- a calculation time at or after every supporting record.

The result records the benchmark ticker plus decimal asset price return, S&P 500 price return and their
difference. It also shows a long return after the **recorded entry fee only**.
The simulated fill price already contains entry slippage, so slippage is
disclosed but never added a second time. The fee-adjusted field is explicitly
named `entry_fee_adjusted_long_return_excl_exit`; no hypothetical exit fee is
invented.

The formulas, source record hashes, calculation version, return unit and all
decision/portfolio/model/Git identities are retained in an append-only result
ledger. Identical retries are safe; conflicting results fail closed.

This is not total return, alpha or a track record. A dividend, split or other
corporate action blocks the calculation until a later total-return component
can apply the event correctly. SELL fills are also blocked because measuring
the benefit of selling requires a separately defined avoided-loss or portfolio
counterfactual, not the long-return formula used here. Results remain ineligible
for automated learning.

## Corporate-action evidence

Issue #29 adds the separate immutable evidence needed to handle those blocked
cases safely. Each evidence record is linked to a verified simulated fill and
declares an inclusive interval from the fill through a later observation. It
retains the data provider and version, retrieval time, a SHA-256 digest of the
provider input and the exact events found.

Supported event evidence preserves:

- cash-dividend ex-date, optional payment date, exact per-share amount and
  three-letter currency; and
- stock-split effective date and exact numerator/denominator terms.

Exact decimal strings are used for amounts and split terms so future
calculations do not inherit binary floating-point rounding. Provider event IDs
are retained for auditability, while overlapping complete coverage is compared
using the economic event terms. Contradictory complete intervals fail closed;
consistent longer coverage can extend an earlier record without replacing it.

`NO_EVENTS`, `SUPPORTED_EVENTS_PRESENT`, `UNSUPPORTED_EVENTS_PRESENT` and
`UNCERTAIN` are distinct states. Uncertainty must have an explicit reason, and
unknown corporate actions are preserved as `OTHER` rather than silently
discarded. Only complete evidence can support a future calculation, and an
unsupported event must remain a blocker until an explicit treatment exists.

This ledger does not decide dividend entitlement, reinvest dividends, adjust
share quantities, convert currencies or calculate a return. Those rules belong
in the next deterministic total-return slice and must consume the verified
evidence without changing either the raw observations, the existing
price-return result or the corporate-action record.
