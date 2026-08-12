# Phase 5 performance and attribution foundation

Status: immutable raw observations only; no performance claim or learning input

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
