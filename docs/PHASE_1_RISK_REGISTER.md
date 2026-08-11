# Phase 1 architecture and investment-system risk register

## High priority

### Multiple overlapping decision and portfolio paths

The repository contains legacy and current decision, portfolio, research and
learning engines. This creates a risk that a screen, page or scheduled process
uses a stale path with different rules. The current portfolio-first application
path and `PortfolioConstructionService` must remain authoritative. Legacy paths
should be mapped and deprecated incrementally rather than deleted blindly.

### Live-data side effects in tests

Test collection previously executed manual analysis scripts and wrote into live
research history. Phase 1 isolates the identified paths and verifies the full
suite produces no working-tree change. This invariant should be enforced in CI
when CI is introduced.

### Point-in-time data integrity

The platform has point-in-time and walk-forward components, but current research
also depends on present-day provider responses. Historical claims are not valid
unless every input is tied to its availability date. Every ledger decision now
requires a `data_as_of` value; future attribution must reject records without a
credible data cutoff.

### Ledger durability

The local JSONL ledger is append-only and hash-chained, making modification and
reordering detectable. Phase 1 now serialises writers with a file lock, fsyncs
each append, and provides an explicit recovery operation for a malformed final
partial line while preserving that tail as evidence. A filesystem owner can
still truncate valid final records. Before always-on AWS operation, move the
ledger to a transactional database and/or immutable object retention, with an
externally anchored chain head and one controlled writer.

## Medium priority

### External data reliability

Core analysis relies substantially on Yahoo Finance and network calls. Optional
Alpha Vantage, FMP, Polygon and FRED providers use environment-based credentials.
Provider outages, schema changes, throttling and revised data can change results.
Raw responses, provenance, timestamps and reconciliation outcomes should be
retained for reproducibility.

### Model and policy version fragmentation

Some historical research records contain older master-decision versions. The
current application re-evaluates stale policy from preserved inputs, but the
ledger must distinguish the original research-pipeline version from the current
decision and portfolio-policy versions. Phase 1 now records these separately.

### Non-atomic portfolio and ledger persistence

Phase 2 adds a durable local transaction journal and an idempotent, locked ledger
batch. A failure between the ledger batch and snapshot rename leaves a pending
journal that the next construction attempt completes without duplicating
decisions. Failure-injection tests cover this recovery path. This provides
recoverable local consistency, but it is not a substitute for a database
transaction. Before unattended AWS operation, migrate the journal/outbox and
portfolio/decision writes to transactional managed storage.

### Cached-research source provenance

New research artifacts now record the Git revision that generated them, and the
portfolio ledger preserves that revision separately from the later decision
revision. Historical artifacts created before this change remain explicitly
`UNKNOWN`; the system does not falsely attribute them to the current checkout.

### Dependency reproducibility

`requirements.txt` contains both pinned and unpinned duplicates for some
packages and currently uses Python 3.14 locally. A clean, locked runtime has not
yet been demonstrated. Dependency locking and a supported container runtime
belong in Phase 3, after application boundaries are stable.

## Current deployment state

The application is a local Streamlit/Python prototype. No Dockerfile, GitHub
Actions workflow, or AWS deployment configuration is present. There is no broker
integration in the authoritative portfolio-construction path, and Phase 1 ledger
records are explicitly `RECORD_ONLY`.

## Promotion gates before Phase 2

- Full suite passes without changing live data.
- Phase 1 source changes are reviewed and committed separately from generated
  research data.
- Ledger schema, hash integrity, model versions, portfolio IDs, successful-write
  behavior and blocked-portfolio behavior are tested.
- The non-atomic persistence limitation is documented and must be resolved
  before unattended AWS execution.
