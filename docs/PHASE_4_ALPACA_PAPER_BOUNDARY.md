# Phase 4 Alpaca paper boundary

Status: local proposal records only; no Alpaca account connected

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
