# Phase 4 Alpaca paper boundary

Status: local proposal and deterministic simulated-fill records only; no Alpaca
account connected

The first Phase 4 boundary records what the platform would propose sending to a
paper broker. It does not submit, cancel or replace an order and performs no
network request.

Each proposal is linked to its investment decision and portfolio version. It
records the quantity, reference price, target weight, strategy/model/Git
versions, UTC creation time and a deterministic Alpaca client order ID. Records
are append-only and SHA-256 hash chained so later edits are detectable. A retry
of the same decision/portfolio/ticker/side combination resolves to the same
order identity, and an explicit repair preserves any incomplete final bytes
before restoring a valid ledger after an interrupted write.

Only `PAPER_ONLY` proposals are accepted. The configuration recognizes only
`https://paper-api.alpaca.markets`; Alpaca's live domain and arbitrary endpoints
are rejected. Generic Alpaca endpoint environment variables are intentionally
ignored so a live setting elsewhere on a machine cannot redirect this boundary.

Paper credentials, read-only account verification, order submission, fills,
slippage and position reconciliation are separate later gates. Real-money
support is not implemented and is not a configurable option.

## Local simulated execution foundation

Issue #23 adds a separate append-only ledger for deterministic local fill
simulations. A simulation can only be derived from an existing proposal whose
hash chain verifies. It inherits the proposal's decision, portfolio, strategy,
model and Git identities rather than accepting replacement identities.

The record contains requested and simulated filled quantity, reference and fill
prices, fees, gross value and slippage. Slippage uses an explicit convention:
positive values are adverse to the portfolio for both buys and sells. This
creates suitable raw records for later Phase 5 performance and attribution work.
An input price more than 50% from the proposal reference price is rejected as a
probable unit or data-entry error, and fees cannot exceed gross value.

The deterministic fill identity makes an identical retry resolve to the
existing record by default. A retry that changes price, fees or any linked
identity fails closed instead of producing a second fill.

`LOCAL_SIMULATION_ONLY` is deliberately distinct from `PAPER_ONLY`. A simulated
fill is not evidence that Alpaca accepted or executed an order. The component
has no network client, credentials, broker fill identifier, submission, cancel
or replace operation.

The older standalone `PaperExecutionEngine` remains legacy experimental code.
It maintains positions in process memory and does not link its trade history to
the immutable decision, proposal, model and portfolio records. It is not the
authoritative execution record introduced here.

## Paper-submission pre-flight

The legacy in-memory pre-flight reports seven trust-critical methodology and
execution gates recorded in the research-methodology audit. It remains a simple
diagnostic for compatibility, but self-asserted booleans are not authoritative
readiness evidence.

The authoritative pre-flight is now an append-only, hash-chained assessment.
Every gate requires `PASSED` or `FAILED`, a concise evidence summary, observation
time, HTTPS source, source SHA-256 and an exact source locator. Missing evidence,
ambiguous booleans, reused evidence locations and future-dated observations fail
closed. Reassessments must explicitly supersede the current assessment for the
same strategy and move forward in time, preserving the earlier result rather
than rewriting it.

Even a complete evidence-backed assessment is labelled
`EVIDENCE_COMPLETE_AWAITING_HUMAN_DECISION`. It keeps credentials, network
requests, account connection, order submission and live trading false. It does
not create a broker route and cannot authorize one.

The two execution additions require realistic cost, latency and liquidity
assumptions and consistent research/execution data policies. Paper fills must
never be treated as proof that equivalent live fills would have occurred.

## Future live-promotion boundary

Real-money trading remains outside the implemented platform. A separate
read-only readiness checklist records ten future requirements covering forward
paper evidence, simulation-to-paper reconciliation, market-data parity, broker
state reconciliation, pessimistic execution stress, outage recovery, exposure
and kill controls, independent review, security/regulatory/tax review and a
human-set initial capital limit.

The present boolean checklist is explicitly
`UNVERIFIED_DIAGNOSTIC_ONLY`: its values are caller assertions, not evidence.
Even if all ten are asserted complete, it remains evidence-unverified and cannot
claim review eligibility or enable live submission. Phase 10 requires a future
append-only, hash-chained evidence gate before live readiness can be reviewed.
This repository still supports no live endpoint. A future live capability would
require a separate human decision, security design, implementation and review
project.

Phase 10 now has that immutable evidence boundary in
`core/broker/live_readiness_gate.py`. Each of the ten fixed requirements binds
an exact HTTPS source, content hash, locator, observation time and independent
reviewer, with forward-only reassessment history. Complete passing evidence can
produce only `HUMAN_REVIEW_ELIGIBLE_ONLY`; credentials, network, broker, order,
deployment, automatic-promotion and live-trading authority remain false.
## Read-only paper-account boundary

The first network-capable Alpaca component is deliberately read-only. It accepts
only `https://paper-api.alpaca.markets`, reads `/v2/account` with dedicated
`ALPACA_PAPER_*` environment credentials and exposes no create, replace, cancel
or order-submission method. It returns a hash of the account reference and raw
payload rather than either value.

Alpaca's account response does not provide a universal settled/unsettled cash
decomposition. The adapter therefore refuses to invent one and will not append
the Phase 5 broker-cash snapshot until separate exact settlement evidence is
provided. Missing credentials return `NOT_CONFIGURED`; unsupported status,
currency, schema or any non-paper endpoint fails closed. This adds no broker
connection by itself and enables no trading.

## Unauthenticated exact-byte capture admission

The first broker-evidence admission stage can retain exact operator-supplied
bytes representing an Alpaca PAPER `/v2/account` response. It performs no
network request, reads no credential and accepts no other broker, environment,
endpoint shape or capture method. The payload must be strict JSON with one exact
non-empty string account ID matching the caller's hashed account reference.

The raw payload is content-addressed and stored in an owner-only, read-only blob;
the append-only ledger stores its hash, byte length and non-identifying local
attestor rather than the account ID. File links, ambiguous JSON, weak permissions,
oversized content, conflicting observation times and later tampering fail closed.

This proves only that particular local bytes were retained unchanged. It does
not prove that Alpaca produced them, that transport was authenticated, that the
account is reconciled or that cash is settled. Every authenticity, settlement,
reconciliation, recommendation, review and trading-authority flag remains
false. No real broker payload has been admitted. A later, separately reviewed
stage must reconcile authenticated account, position, order and settlement
evidence. Until then, the operator-supplied settled-cash input used by the
combined assessment remains unauthenticated and the assessment must stay blocked.

## Unauthenticated account-field reconciliation

A separate ledger now compares one verified retained capture with one verified
paper-account cash snapshot. It re-reads the exact captured bytes and requires
the account identity, observation time, ACTIVE status, USD currency, total cash,
buying power and equity to match. The captured object's canonical hash must also
match the snapshot source hash, while the distinct raw-byte hash remains pinned.

The reconciliation record stores no account ID or financial amounts. It stores
only source identities and hashes, timestamps and narrowly derived match flags.
The cash-snapshot dependency now rejects binary floating-point money, unsafe
file links and permissions, oversized files and backward observations before
append.

The result is always `ACCOUNT_FIELDS_RECONCILED_UNAUTHENTICATED`. It proves local
semantic consistency, not broker origin or transport authentication. Settled
versus unsettled cash, positions, open orders and previous-close evidence remain
unreconciled. No real capture or reconciliation has been recorded, and the
component cannot access credentials, make a network request, recommend, route or
submit any paper or live order.

## Read-only paper-state collection bundle

The next network-capable foundation can collect a single, tightly bounded local
view of the Alpaca PAPER account using an explicitly injected session. It makes
only four fixed HTTPS `GET` requests to Alpaca's official paper host: account,
long positions, open orders and account again. The opening and closing account
responses must identify the same ACTIVE USD paper account within one 30-second
wall-clock and monotonic window. Redirects, pagination ambiguity, short
positions, duplicate identities, unsupported open-order states, malformed JSON
and non-paper endpoints fail closed.

All four observed responses are retained as private, content-addressed bytes;
identical opening and closing account responses share one blob. The immutable
bundle binds their hashes, byte lengths, fixed request paths, local observation
times and matching account reference. Admission is internal to the collector,
and tests use injected fake sessions only. No real provider request or evidence
admission was performed while implementing this boundary.

The record is deliberately labelled local attestation only. HTTPS and API
credentials allow a future request but do not supply a broker signature or
non-repudiable transport receipt. The bundle does not yet semantically reconcile
settled cash, positions, orders, prior-close values or fills. Raw bytes are
returned only by an explicit verified read. Credentials are neither stored nor
returned, and the collector exposes no submit, cancel, replace, position-close
or other order-mutation method. Paper submission and live trading remain off.

## Verified bundle normalization staging

A separate local staging ledger can now re-verify one retained collection and
translate only exact, supported Alpaca fields into candidate account, long-
position and open-order values. Decimal input is bounded and converted through
exact fractions rather than binary floating point. Position quantity multiplied
by current price must equal market value exactly. Quantity-based limit and
stop-limit orders use only their remaining quantity and explicit limit price;
market, bare-stop, complex, unsupported-asset and ambiguous orders fail closed.
Notional orders remain quantity-incomplete, and SELL candidates cannot refer to
an unheld symbol or exceed its verified position quantity/value boundary.

Successful normalization is labelled
`NORMALIZED_AWAITING_EXTERNAL_EVIDENCE`, not approved or reconciled. It always
records that settled/unsettled cash, prior-close evidence and cryptographic
binding of the position/order responses to the bracketed account are still
unproven. It writes none of the account, risk or quantity ledgers. Semantically
invalid bundles receive a `NORMALIZATION_BLOCKED` audit record containing reason
codes but no monetary values. Verification re-reads and re-hashes the original
private blobs, so later source or normalization changes fail closed.

All tests use synthetic retained bytes. No real bundle has been normalized, no
downstream evidence has been adopted and no network, recommendation, paper-order
or live-trading authority is introduced.

## Inactive risk policy and account-scoped stop foundation

Phase 4 now also has a human-preregistered paper-risk policy and a one-way local
stop ledger. A policy is bound to one hashed Alpaca paper-account reference, one
portfolio and strategy version, explicit order/position/gross-exposure/daily-loss
limits, separate account- and risk-snapshot freshness bounds and a named stop identity. Monetary values
have no defaults and must be exact positive decimal inputs. Registration remains
`PREREGISTERED_INACTIVE` and cannot activate broker access or order submission.

A stop is pinned to the verified policy and follows the account or stop identity
across replacement policies. It separately records the claimed trigger time and
the system append time, so a later backdated stop blocks future work without
rewriting valid earlier assessments. It has no clear or resume method. Unknown policies,
inactive policies and latched policies all report that work is not allowed.

These local hash chains are tamper-evident under the repository's append-only
workflow, not externally anchored or cryptographically authenticated. The
records therefore state explicitly that the limits are not enforced, no order
route exists, and no external head anchor or authentication is present. Future
paper submission still requires human-selected limits, independently verified
account/position/loss evidence, an enforcement assessment and a separately
reviewed paper-only adapter.

## Normalized paper-risk evidence

A separate read-only evidence ledger now pins one verified paper-account cash
snapshot to normalized long-position and open-order payload hashes. It records
current long exposure, pending BUY exposure and their conservative sum; pending
SELL orders do not reduce risk before a fill. Daily loss is calculated from the
pinned current equity and a separately hashed previous-close equity observation
from an earlier UTC date within seven days.

The collection must occur within sixty seconds of the account observation,
move strictly forward for that account and use exact decimal/fraction values.
Conflicting evidence at the same time is rejected before writing. Nested
position/order shapes, identities, sorting and source hashes are verified.

This record still states that broker reconciliation, policy assessment, limit
enforcement, order routing, external anchoring and cryptographic authentication
are absent. It cannot approve or submit an order.

## Shadow-only limit comparison

A pinned shadow assessment can now compare one verified paper proposal with the
inactive policy, normalized risk snapshot and an exact stop-ledger prefix. BUY
orders add their reference-price notional to both ticker and gross exposure.
SELL orders never reduce exposure before fill and remain incomplete because the
risk snapshot does not yet prove share-quantity sufficiency.

The calculation reports separate order, position, gross, daily-loss and real
recording-time freshness comparisons. Any permanent account/stop-identity latch
blocks the result regardless of its stated trigger time. Assessment creation and
stop creation share the same lock, closing the read-before-stop/write-after-stop
race while preserving the exact historical prefix for older assessments.

Even a mathematically within-limits result is labelled inactive and
unreconciled. It explicitly has no stressed execution price, fees, fill-price
certainty, authentication, recommendation, human-review eligibility, broker
access or submission authority.

## Exact position-quantity evidence

A separate quantity ledger now pins the exact positions payload already used by
the normalized risk snapshot. Every long ticker must be present exactly once,
and exact fractional quantity multiplied by exact mark price must equal that
ticker's pinned market value. The aggregate must also equal the pinned current
gross position exposure. Empty portfolios remain valid, while partial coverage,
duplicate tickers, floats and alternate factorizations at the same observation
time fail closed.

This proves internal quantity/price/value identity for future SELL sufficiency
checks. It does not prove broker reconciliation, payload authenticity, execution
price, risk-policy compliance or permission to route an order.

## Exact open-order quantity evidence

A companion ledger now pins every open order from the same normalized risk
snapshot. Each order reference must appear exactly once with the same ticker and
side, and exact remaining quantity multiplied by exact risk mark price must
equal its pinned remaining notional. The canonical order and exact BUY and SELL
totals are independently recomputed; empty evidence is accepted only when the
pinned snapshot has no open orders.

This closes the data-shape gap needed to deduct already-reserved SELL quantities
before assessing a new simulated sale. It remains synthetic, unreconciled and
unauthenticated, and cannot assess a risk policy, route an order or enable paper
or live submission.

## Quantity-bound SELL shadow

A composite SELL-only shadow now pins the earlier limit comparison, proposal,
position quantities and open-order quantities to the exact same risk snapshot.
Available shares are calculated exactly as held long quantity minus pending SELL
quantity for the ticker; pending BUY orders never increase availability. A
proposed sale larger than the non-negative remainder fails closed.

The composite rechecks snapshot freshness at its own real recording time and
shares the permanent-stop lock. Stops matched by either account or replacement-
policy stop identity remain latched, and the stored stop prefix is causally
checked so a known stop cannot be rewritten out while genuinely later stops do
not rewrite history.

The result is explicitly internal arithmetic only. The quantities are not yet
broker-reconciled or cryptographically authenticated, broker quantity
sufficiency is not proven, and risk enforcement, stressed prices, fees, routing,
submission, recommendation and live trading all remain disabled.

## Pessimistic execution-price and configured-fee evidence

An inactive human-preregistered paper execution policy now reuses the same exact
BASE/PESSIMISTIC contract as the guardrailed replay engine. It accepts no hidden
cost defaults: both scenarios must supply commission, spread, slippage, latency,
market impact, participation and order-age assumptions. Baseline slippage cannot
fall below 0.10%, and every pessimistic per-side cost must be at least twice its
base value.

A separate shadow calculator applies only the pinned PESSIMISTIC scenario to a
verified paper proposal. BUY prices move adversely upward and SELL prices move
adversely downward; configured commission is calculated once on stressed gross
notional. SELL conservative risk notional uses the greater of reference and
stressed gross value before adding the fee, so worse expected proceeds cannot
make the risk measure look smaller.

This proves only exact internal stress arithmetic. The proposal reference price
is not authenticated, no volume evidence or calibrated impact model exists, and
regulatory fees, borrow costs and other applicable charges remain incomplete.
The policy is inactive and no cash/position sufficiency, risk enforcement,
broker access, route, submission, recommendation or live trading is enabled.

## Combined paper-operational assessment

The final local Phase 4 calculation now combines the pinned risk comparison,
exact SELL quantity evidence where applicable, pessimistic execution evidence,
settled cash, every account-wide pending BUY commitment and the permanent-stop
prefix. Existing pending BUYs receive the same adverse-price and configured-fee
uplift as the new proposal. A BUY must fit within settled cash after those
commitments; a SELL must fit within held shares after pending sells, including
recording an explicit block when the account is already over-reserved.

Account evidence, risk evidence, the shadow calculation, execution-stress
evidence and the proposal reference are all age-bounded by pinned policy values.
The assessment uses its real append time, rather than a caller-supplied earlier
time, when testing freshness. Stop prefixes are complete as of that append time:
an already-recorded stop cannot be removed, while a genuinely later stop does
not corrupt the historical result.

This is still a blocking calculation, not an operational broker gate. Even when
all internal arithmetic passes, the result is
`BLOCKED_EXTERNAL_EVIDENCE_REQUIRED`. Authenticated prices, positions, orders
and cash; broker reconciliation; volume and market-impact evidence; complete
fees and borrow costs; cryptographic/external anchoring; and a separate future
human activation decision are mandatory. Network access, credentials, order
routing, paper submission, recommendations and live trading remain false.
