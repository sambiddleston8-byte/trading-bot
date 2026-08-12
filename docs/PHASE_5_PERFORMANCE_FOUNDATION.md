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

## Initial-funded simulated portfolio valuation

The next bounded slice establishes the portfolio accounting base required by
the Master Roadmap before any portfolio return or risk statistic can be
reported. It records one immutable amount of simulated initial USD funding
before the portfolio's proposed orders. The funding record locks the complete
proposal set plus its strategy, model and Git identities. Contributions and
withdrawals after that point are deliberately unsupported until their timing
and time-weighting rules are defined.

A portfolio valuation is appended only when every proposal for that portfolio
version is a long `BUY`, every proposal has exactly one verified local
simulated fill, and every fill has a verified total-return result for the same
horizon. All positions must use the exact same asset and benchmark effective
times. Missing holdings, duplicate tickers, misaligned timestamps, target
weights above 100% and insufficient starting cash fail closed.

The fixed cash and valuation formulas are:

- `remaining cash = initial funding - recorded entry costs + gross cash dividends`;
- `position market value = sum of outcome position values`; and
- `total equity = remaining cash + position market value`.

The result retains exact rational values for funding, entry costs, dividends,
cash, each position, total equity, target weights and actual weights. Recorded
entry fees and adverse/favourable slippage amounts are also aggregated for
later transaction-cost attribution; fill-price slippage is disclosed but not
subtracted a second time. Position
weights use current market value divided by total equity; cash is a separate
actual and target weight, and the exact actual weights must sum to one.

This is one aligned simulated valuation, not yet a performance series. It does
not calculate portfolio return, benchmark-relative portfolio return, alpha,
CAGR, volatility, Sharpe, Sortino, drawdown or hit rate. It invents no exit,
accepts no external cash flow after initial funding, remains ineligible for
learning and cannot be presented as a track record. No broker, real-money,
worker, market-data-download, AWS or autonomous-learning capability is enabled.

## Boundary cash flows and time-weighted portfolio return

The next slice turns verified portfolio valuations into a time-ordered
simulated return series without mistaking deposits or withdrawals for
investment performance. An external contribution or withdrawal can be recorded
only immediately **after** a verified market valuation. The flow inherits that
valuation's exact effective time and retains its hash, portfolio, strategy,
model and Git identities. Mid-period cash flows are not approximated: they
require a new valuation at the exact flow boundary and otherwise fail closed.

For each period the calculation separates:

- the prior period's post-flow equity;
- the current base portfolio valuation plus all earlier external cash flows;
- the investment return before the current boundary flow; and
- the post-flow equity used to start the next period.

The fixed subperiod formula is `pre-flow equity / previous post-flow equity -
1`. Subperiod growth factors are multiplied to produce the cash-flow-neutral
time-weighted return. A contribution or withdrawal after the current valuation
therefore cannot alter the return through that boundary. Contributions remain
as simulated cash until a separately recorded trade exists; withdrawals cannot
exceed available simulated cash. Exact rational values and readable 34-digit
decimals are retained for every subperiod and linked result.

This is a non-annualized simulated portfolio return, not benchmark-relative
portfolio return, risk-adjusted alpha, CAGR, volatility, Sharpe, Sortino,
drawdown or a live track record. It remains ineligible for learning. No broker,
real-money, worker, market-data-download, AWS or autonomous-learning capability
is enabled.

## Matched-capital S&P 500 portfolio benchmark valuation

The portfolio benchmark begins with a counterfactual valuation before any
portfolio-relative return is calculated. Every verified simulated asset
position is matched to its S&P 500 gross-cash total return for the exact same
fill, horizon, price observations, strategy, model and Git identities. Missing
or mismatched benchmark evidence blocks the entire portfolio result rather than
silently dropping a holding.

For each position, the counterfactual exposes the asset position's exact
`recorded_entry_cost` to the matched S&P return. This amount includes the
asset's recorded entry-fee basis, so the benchmark does not receive extra
starting capital. No separate benchmark transaction cost is invented. Initial
funding not used by the asset position set remains zero-return cash in the
counterfactual. The fixed formulas are:

- `benchmark position ending value = matched entry capital × (1 + S&P gross-cash return)`;
- `benchmark cash reserve = initial funding - total matched entry capital`; and
- `benchmark total equity = cash reserve + benchmark position ending values`.

Exact rational capital, ending values, cash, total equity and weights are
retained alongside readable 34-digit decimals. Weights must reconcile exactly
to one. The existing S&P distribution and changing-composition acceptance
remain inherited from each verified benchmark-return result.

This is only a simulated benchmark valuation. It does not yet link benchmark
subperiods, calculate benchmark portfolio return, subtract the asset portfolio
return, calculate alpha, annualize results, enable learning or create a live
track record. No broker, real-money, worker, market-data-download, AWS or
autonomous-learning capability is enabled.

## Cash-flow-neutral S&P 500 portfolio benchmark return

The matched benchmark valuations can now be linked across the exact same
verified boundaries used by the simulated asset portfolio. Every benchmark
valuation must retain its precise asset-valuation ID and hash, and the asset and
benchmark effective timestamps must be identical. A missing, modified or
misaligned side blocks the entire result.

External contributions and withdrawals reuse the verified asset portfolio cash
flow. Prior flows are held as zero-return cash in the counterfactual; a flow at
the current boundary is applied only after measuring return through that
boundary. The fixed formulas are:

- `benchmark pre-flow equity = base benchmark equity + cumulative prior flows`;
- `benchmark subperiod return = benchmark pre-flow equity / previous benchmark post-flow equity - 1`;
- `benchmark post-flow equity = benchmark pre-flow equity + current boundary flow`; and
- `linked benchmark return = product(1 + benchmark subperiod return) - 1`.

Exact rational values and 34-digit decimal presentations are retained for each
boundary and the linked result. Mid-period flows, duplicate effective times,
non-positive equity, identity changes and lost supporting evidence fail closed.

This is the simulated S&P counterfactual's own non-annualized time-weighted
return. It does not yet subtract the asset portfolio return, calculate alpha or
risk statistics, enable learning or form a live track record. No broker,
real-money, worker, market-data-download, AWS or autonomous-learning capability
is enabled.

## Portfolio benchmark-relative time-weighted return

The verified asset and matched-S&P portfolio returns can now be combined only
when they retain the exact same portfolio version, through-horizon, funding,
asset-valuation boundaries, external-cash-flow records, strategy, model and Git
identities. The fixed formula is the arithmetic difference:

`asset time-weighted portfolio return - matched S&P time-weighted portfolio return`

Both return inputs and their difference are retained as exact rational values
plus readable 34-digit decimal presentations. The comparison record immutably
links both supporting return IDs and hashes and carries the asset and benchmark
valuation boundaries used by those returns. Missing, modified or misaligned
evidence fails closed.

This is an explicitly labelled simulated portfolio benchmark-relative return.
It is non-annualized and is **not** risk-adjusted alpha, learning evidence or a
live track record. CAGR, volatility, drawdown, Sharpe, Sortino, hit rate,
turnover and transaction-cost attribution remain separate later calculations.
No broker, real-money, worker, market-data-download, AWS or autonomous-learning
capability is enabled.

## Post-flow portfolio concentration

Issue #53 measures concentration from one verified simulated portfolio
valuation after applying every external cash flow through that exact boundary.
Contributions and withdrawals remain cash; position values are not silently
rescaled. Post-flow cash, total equity and all allocation weights must reconcile
exactly.

The result retains two distinct views:

- cash-inclusive allocation HHI, which treats cash as an allocation; and
- invested-position HHI, which normalizes only the invested positions to 100%.

It also records the effective number of invested positions, largest and top-five
weights in both views, and the full ordered position-weight evidence. All inputs
and outputs remain exact rational values with readable 34-digit decimals, linked
immutably to the valuation and every included cash-flow record.

No concentration threshold, risk label or trade recommendation is inferred.
This is a point-in-time simulated allocation statistic, not a return, alpha,
learning result or live track record. Volatility, Sharpe, Sortino and genuine
maximum drawdown remain blocked until sufficiently frequent and regular
portfolio-value history exists; sparse milestone observations must not be used
to make paper performance look more reliable than it is.

## Point-in-time sector-classification evidence

Sector exposure must not be calculated from convenient present-day labels or
from inconsistent free-text sector names. This slice therefore records the
classification evidence for each position before any exposure statistic is
allowed. Every append-only record is linked to one verified portfolio
valuation and position and retains the provider, provider taxonomy and version,
classification effective time, retrieval time, HTTPS source and source-input
hash.

Provider sector and optional industry codes and labels are preserved exactly;
this ledger does not silently translate `Tech`, `Technology` and `Information
Technology` into one category. `Unknown`, `Unclassified` and equivalent
placeholders are rejected. Evidence is either `COMPLETE`, with a sector code and
name, or `UNCERTAIN`, with explicit reasons and no asserted classification. An
uncertain observation may later be resolved by a separately identified complete
record, but complete evidence cannot regress to uncertain. Evidence retrieved
after the valuation boundary is explicitly labelled as backfilled.

Classification identity is content-addressed to the source hash, effective
date and exact provider labels, in addition to the append-only ledger hash
chain. The component deliberately calculates no exposure, concentration,
return, alpha or recommendation and remains ineligible for learning and
track-record claims. The next exposure calculation must require complete
evidence for every invested ticker under one exact provider taxonomy/version;
missing or mixed classifications must fail closed, while cash remains a
separate allocation.

## Exact post-flow sector exposure

Issue #57 converts complete sector evidence into a portfolio statistic only
when every invested position has exactly one complete classification from the
same provider, taxonomy and taxonomy version. Missing, uncertain, duplicated or
mixed-taxonomy evidence blocks the entire calculation. Provider codes and
labels remain unchanged, and conflicting code/name mappings within one
taxonomy also fail closed.

Verified position values are grouped by sector using exact rational arithmetic.
For each sector the record reports both its weight in total post-flow portfolio
equity and its normalized weight among invested positions. Contributions and
withdrawals through the valuation boundary are applied to cash, exactly as in
the concentration calculation. Cash is reported as a distinct allocation and
is never disguised as a sector. All sector and cash weights must reconcile
exactly to one.

The append-only result immutably links its valuation, every included cash flow
and every classification record. If any classification was retrieved only
after the valuation boundary, the complete exposure result explicitly reports
that it contains backfilled evidence. Verification resolves these exact pinned
IDs and hashes, so evidence appended later cannot rewrite or falsely invalidate
an older result. This is a simulated point-in-time
allocation statistic, not a diversification judgment or recommendation. It
calculates no return or alpha, is not risk-adjusted or annualized, remains
ineligible for learning, and cannot be presented as a track record. No broker,
real-money, worker, market-data-download, AWS or autonomous-learning capability
is enabled.

## Historical support pinning across portfolio statistics

A targeted Claude catch-up review found that portfolio return, matched S&P
portfolio return and concentration records stored their exact supporting IDs
and hashes but originally re-selected the current ledger contents during later
verification. A legitimate valuation or boundary cash flow appended after a
calculation could therefore make an untouched historical result appear
corrupted.

New calculations still discover all verified evidence available at calculation
time. Once appended, however, each historical record is verified only from its
exact stored valuation and cash-flow IDs and hashes. Pinned evidence must still
exist, remain unmodified, preserve chronological and portfolio boundaries,
retain compatible strategy/model/Git identity and reproduce the exact recorded
economics. Unrelated evidence appended later can neither rewrite nor falsely
invalidate the older result. This integrity change does not promote any result
to learning evidence or a track record and enables no live capability.

## Recorded entry transaction-cost attribution

Issue #63 attributes only the transaction-cost evidence already present in a
verified simulated portfolio valuation. For every position it reconciles the
proposal reference notional, simulated fill notional, recorded entry fee,
signed fill-price slippage and fee-inclusive recorded entry cost using exact
rational arithmetic. Adverse slippage cost and favourable slippage benefit are
reported separately, alongside signed net cost and basis points relative to
reference notional. Portfolio totals must reconcile exactly to the valuation.

This is attribution, not another deduction: fill-price slippage is already
embedded in the simulated fill and fees are already included in recorded entry
cost. The result explicitly blocks double counting. It does not invent an exit,
exit fee, separately observed bid-ask spread, market impact or latency cost.
Those omissions mean it is not yet a full round-trip or live-realism cost model.
It calculates no turnover, return, alpha or recommendation, remains ineligible
for learning and cannot be presented as a track record. No broker, real-money,
worker, market-data-download, AWS or autonomous-learning capability is enabled.

## Point-in-time security factor-exposure evidence

Issue #65 establishes the evidence boundary required before portfolio factor
exposure can be calculated. Each append-only observation links one invested
ticker to a verified portfolio valuation and retains the provider, factor-model
name and version, hashed methodology, factor effective time, retrieval time,
HTTPS source and source-payload hash. Provider factor codes, names, units and
finite decimal exposure values are preserved without translation, with exact
rational representations for later deterministic aggregation.

Complete evidence must contain a non-empty set of uniquely coded and named
factors and no uncertainty. Unusable or missing evidence is recorded as
`UNCERTAIN` with reasons and no asserted exposures. An uncertain observation
may resolve through a separately identified complete record; complete evidence
cannot regress to uncertain, and conflicting complete observations cannot
coexist for the same valuation, ticker and factor-model version. A correction
requires an explicit new model version or a future supersession mechanism.
Evidence retrieved after the valuation boundary is explicitly labelled as
backfilled.

This component records security-level source evidence only. It does not combine
different factor models or units, calculate portfolio factor exposure, label a
portfolio as risky, recommend a trade, calculate performance or alpha, update
learning, or create a track record. No provider download, broker, real-money,
worker, AWS or autonomous-learning capability is enabled.

## Exact post-flow portfolio factor exposure

Issue #67 aggregates security-level factor evidence only when every invested
position has exactly one complete observation from the selected provider and
factor-model version. All positions must also share the exact methodology URI
and hash, factor effective timestamp, and ordered provider factor definitions
and units. Missing, uncertain, duplicated or mixed evidence blocks the whole
calculation instead of silently dropping positions or combining incomparable
numbers.

Exact position values weight each provider exposure in two deliberately named
ways: normalized among invested positions, and scaled as a contribution to
total post-flow portfolio equity. Contributions and withdrawals through the
valuation boundary remain cash. Cash weight is reported separately and no cash
factor exposure is invented, so the result is explicitly an invested-position
exposure statistic rather than a claim about an unmodelled whole portfolio.

The append-only result pins the valuation, every included cash flow and every
factor-evidence record by ID and hash. Later legitimate evidence cannot rewrite
or falsely invalidate history. Backfilled evidence is disclosed. This is a
simulated descriptive statistic only: it supplies no risk label, recommendation,
return, alpha, learning eligibility or track-record claim and enables no broker,
real-money, worker, provider-download, AWS or autonomous-learning capability.

## Performance metric readiness and observation cadence

Issue #69 adds a deterministic gate that must be consulted before CAGR,
volatility, Sharpe, Sortino, maximum drawdown, hit rate, turnover or prediction
calibration is implemented. It calculates none of those metrics. Instead, it
content-addresses the verified funding, valuation and time-weighted-return
evidence and reports whether each metric's prerequisites exist.

CAGR requires at least 365 elapsed calendar days and exactly one verified
time-weighted return pinned to the same valuations. Daily-series volatility and
maximum drawdown require at least 253 unique valuations spanning at least 365
calendar days, with the first observation within four days of funding and no
consecutive calendar gap above four days. This conservative policy permits
weekends and ordinary long weekends but rejects the current sparse milestone
series. It is versioned so future market-calendar refinement cannot silently
rewrite the rule.

Sharpe remains blocked until a point-in-time risk-free series is matched to
each return period. Sortino remains blocked until its minimum acceptable return
and adequate downside sample are predeclared. Hit rate, turnover and prediction
calibration remain blocked until their independent outcome, execution and
predeclared cohort evidence exists. An `EVIDENCE_READY` result is permission to
implement a separately tested calculation, not a performance claim. The gate
never annualizes a result, recommends a trade, enables learning, creates a track
record or enables live trading.
