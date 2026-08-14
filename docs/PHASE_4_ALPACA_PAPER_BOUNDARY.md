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

## Inactive risk policy and account-scoped stop foundation

Phase 4 now also has a human-preregistered paper-risk policy and a one-way local
stop ledger. A policy is bound to one hashed Alpaca paper-account reference, one
portfolio and strategy version, explicit order/position/gross-exposure/daily-loss
limits, account-snapshot freshness and a named stop identity. Monetary values
have no defaults and must be exact positive decimal inputs. Registration remains
`PREREGISTERED_INACTIVE` and cannot activate broker access or order submission.

A stop is pinned to the verified policy and follows the account or stop identity
across replacement policies. It has no clear or resume method. Unknown policies,
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
