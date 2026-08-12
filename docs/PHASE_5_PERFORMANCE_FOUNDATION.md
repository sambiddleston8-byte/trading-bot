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

## Simulated long holding-period total return

Issue #33 adds that bounded calculation without changing its supporting
evidence. A result requires a verified simulated long `BUY` fill, entry and due
unadjusted-price observations, and complete corporate-action coverage from the
fill through the outcome timestamp. Uncertainty and relevant unsupported events
fail closed.

The first policy is deliberately narrow:

- USD cash dividends only, recorded gross before withholding or tax;
- no dividend reinvestment;
- dividend entitlement only when the simulated fill strictly precedes the
  ex-time;
- dividend cash included only when the recorded payment time is no later than
  the outcome timestamp, preventing a later calculation from importing future
  cash into an earlier outcome;
- sequential split quantity adjustment using the recorded numerator and
  denominator;
- exact rational quantities and monetary results retained alongside readable
  34-digit decimal presentations;
- exact simulated fractional shares retained, with no invented cash-in-lieu;
- the recorded entry fee included and entry slippage not counted twice; and
- no exit fee, tax, currency conversion or unrecorded exit execution invented.

Simultaneous split/distribution events are blocked because the evidence does
not define a safe ordering rule. A payment after the outcome is also blocked;
the calculator does not value a dividend receivable without a separately
approved valuation policy.

The S&P 500 observation currently supplies only an unadjusted **price** return.
It is retained as clearly labelled context, not subtracted from the asset total
return. This slice therefore calculates neither relative total return nor
alpha. Results remain simulated, position-level, ineligible for learning and
not a portfolio or live track-record claim.

## Like-for-like S&P 500 distribution evidence

Issue #35 records the missing benchmark distribution evidence without yet
calculating a benchmark return. S&P DJI's standard Total Return Index reinvests
dividends, while the first asset policy above holds dividends as cash. The
platform therefore does not treat that reinvested series as automatically
like-for-like.

Instead, each due horizon can receive immutable S&P 500 gross ordinary cash
dividend **points** covering exactly the open-start, closed-end benchmark price
interval `(entry, outcome]`. The fixed policy is USD, no withholding deduction
and no reinvestment. A future cash benchmark return can combine these points
with the already verified unadjusted S&P price levels under an explicit formula.

Published dividend points use the index membership, weights and divisor in
effect on each ex-date; they do not represent a basket frozen at the entry
date. That composition drift can matter over longer horizons. The evidence
therefore records `CURRENT_INDEX_WEIGHTS_NOT_FROZEN_AT_ENTRY` explicitly, and a
future relative-return calculation must deliberately accept or reject that
benchmark approximation before producing a result.

Every evidence record retains:

- links and hashes for its verified entry and outcome observations;
- exact period endpoints and same-UTC-market-date alignment to the asset at
  both entry and outcome;
- exact decimal dividend points, including valid zero-point intervals;
- complete or explicitly uncertain status;
- provider, provider version, retrieval/backfill status and source-input hash;
  and
- official S&P methodology name, version, HTTPS URI and document hash.

The methodology definition itself receives a deterministic hash. Adjusted
prices, mismatched market dates, incomplete horizons, negative points,
unofficial methodology URLs and conflicting retries fail closed. This component
does not download data and makes no performance, relative-return, alpha,
learning or track-record claim.

Uncertainty does not overwrite history or permanently block later resolution.
One immutable `UNCERTAIN` record may be followed by one separately identified
`COMPLETE` record for the same fill and horizon. A conflicting second complete
value still fails closed, and complete evidence can never regress to uncertain.

This is an attestation-style evidence boundary: it retains the provider's
declared aggregate points and a hash of the provider input, but not individual
S&P constituent dividend events. The ledger can prove what was recorded and
detect later inconsistency; by itself it cannot independently reconstruct or
cross-check every ex-date inside the provider's declared `(entry, outcome]`
window. That limitation must remain visible to the future calculator.

Official S&P methodology states that total-return indices reflect price changes
plus reinvested dividend income, while dividend-points indices track dividend
payments separately. That distinction is why this evidence boundary is kept
separate from the reinvested total-return index.

Governing references checked 2026-08-12:

- [S&P DJI Index Mathematics Methodology](https://www.spglobal.com/spdji/en/methodology/article/index-mathematics-methodology/)
- [S&P DJI explanation of price, total-return and dividend-points indices](https://www.spglobal.com/spdji/en/education/article/faq-sp-sdg-indices/)

## S&P 500 gross cash total return

Issue #37 adds the deterministic calculation that consumes the verified price
observations and complete dividend-point evidence above. Its fixed formula is:

`(outcome unadjusted price + gross dividend points - entry unadjusted price) /
entry unadjusted price`

The dividend points are treated as gross cash: no withholding deduction and no
reinvestment. The result separately retains the price-return context and the
distribution-return component, plus exact rational values and readable
34-digit decimal presentations.

The calculation refuses to append a result unless the caller explicitly
accepts that the published dividend-point series uses the index membership,
weights and divisor in effect on each ex-date, rather than a basket frozen at
entry. That acceptance is recorded in the immutable result; it is not silently
inferred. Uncertain or missing distribution evidence, adjusted prices,
mismatched supporting hashes, an early calculation time and tampering all fail
closed.

This remains benchmark-only, simulated and ineligible for learning. It does
not subtract the benchmark result from an asset return, calculate alpha, claim
portfolio performance or create a track record. No market-data download,
worker, broker or cloud path is enabled.

## Position-level relative total return

Issue #39 combines the two verified like-for-like results for the exact same
simulated fill and horizon. The fixed formula is the arithmetic difference:

`asset gross total return after recorded entry fee, excluding exit costs -
S&P 500 gross cash total return`

The calculation retains the exact rational inputs and difference, readable
34-digit decimal presentations, and immutable links to both supporting result
records. It fails closed if either result is missing, modified, calculated
later than the comparison, or does not share the same fill, decision,
portfolio, ticker, horizon, observations, strategy, model and Git identities.

This result is deliberately position-level and simulated. It includes the
asset's recorded entry fee, while the benchmark has no equivalent transaction
cost, and it excludes any unrecorded exit execution or exit cost. It is useful
for a transparent benchmark comparison but is **not** risk-adjusted alpha,
portfolio performance, learning evidence or a live track record. Those claims
remain blocked. No market-data download, worker, broker, AWS or autonomous
learning path is enabled.
