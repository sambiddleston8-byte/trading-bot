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

The pre-flight reports the five trust-critical methodology gates recorded in
the research-methodology audit. Missing or false gates produce `BLOCKED`. Even
when every gate is reported as cleared, the result keeps
`broker_submission_enabled` false and requires separate explicit human
authorization. The checker reports readiness; it has no ability to create a
broker route.
