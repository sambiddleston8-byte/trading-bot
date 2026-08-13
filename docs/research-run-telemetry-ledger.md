# Research-run telemetry ledger

## Purpose

The research-run telemetry ledger records how long each ticker's research
attempt took and whether that attempt completed or raised an error. It provides
objective evidence before the project adopts faster libraries, increases
server size, or changes provider infrastructure.

It is observation-only. It cannot change a research conclusion, select a
provider, trigger a retry, approve a cache entry, create an order, or enable
paper or real-money trading.

## Record contract

Each JSONL record contains:

- a deterministic identity binding the batch, ticker and sequence position;
- the ticker-level wall duration around the research call;
- the configured provider-pacing delay as a separate field;
- `COMPLETE` or `ERROR`, with only a safe exception class name for errors;
- optional component measurements only when a caller explicitly supplies them;
- a UTC observation time, fixed no-authority policy, previous hash and record
  hash.

Unknown component fields are rejected. URLs, headers, provider payloads, API
keys and free-text error messages therefore have no supported storage field.
Retry count and cache status are never defaulted to zero: absent measurement is
stored as unknown (`component_observations: null`).

## Integrity and concurrency

Records use canonical JSON and a SHA-256 chain. Before appending, the entire
existing chain and the semantic record boundary are verified under an
exclusive filesystem lock. Writes use append-only mode followed by a durable
filesystem sync. Concurrent identical retries produce one record; conflicting
content for the same identity fails closed. The first observation time is
retained when an otherwise identical retry omits an explicit timestamp.

An explicit tail-repair operation can preserve and remove only a malformed
partial final line left by an interrupted write. It cannot revise a complete
record.

Hash chaining makes modification, reordering and deletion inside the retained
chain detectable. It is not an external timestamping service: a filesystem
owner could still truncate valid final records or replace the complete file and
its trust anchor. Backups and restricted operating-system permissions remain
necessary.

## Batch integration

`PortfolioResearchBatchService` accepts an optional telemetry sink. Existing
callers do not write telemetry by default. When a sink is supplied, the
authoritative research report is checkpointed before the measurement is sent.
Any telemetry exception is contained and cannot change the batch outcome.

The measured wall duration covers only `research_runner(ticker)`. It excludes
the deliberate sleep between tickers; that configured delay is stored
separately. This first boundary does not yet identify which engine or provider
is slow because those layers do not currently expose trustworthy timings,
retries or cache results.
