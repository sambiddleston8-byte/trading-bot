# Quant validity and performance audit

## Roadmap alignment

This audit supports Phase 5 performance and attribution, protects the boundary
to Phase 6 controlled learning, and defines prerequisites for Phase 10
continuous improvement. It does not authorise broker submission, autonomous
learning, AWS scheduling or real-money trading.

## Immediate conclusions

- The active system is a deterministic evidence-and-rules pipeline, not a
  trained machine-learning model. LightGBM, XGBoost and Optuna are therefore
  deferred until a faithful point-in-time dataset and replay harness exist.
- Active technical and risk calculations already use vectorised pandas
  operations. TA-Lib, pandas-ta, Polars and Numba require a measured bottleneck
  and semantic-equivalence tests before adoption.
- Provider access and repeat downloads are a more material current performance
  constraint than numerical calculation.
- Historical tests do not faithfully replay the active research and portfolio
  route. Their results cannot validate or promote the current strategy.

## Fixes implemented in this change

- Outcome evaluators request the first trading close on or after each exact
  calendar horizon instead of using the job's eventual run-date price or a
  trading-row offset.
- Missing portfolio prices fail closed instead of silently using cost basis.
- Market-regime cache reuse is limited to the same UTC date.
- The legacy walk-forward test loads one bounded market-data snapshot and
  reuses point-in-time slices instead of repeatedly querying Yahoo.
- Market-data universe loads fail closed when symbols are missing unless a
  caller explicitly requests a partial diagnostic result.
- The executable pickle market cache has been replaced by immutable,
  content-hashed Parquet snapshots. Each exact provider/symbol/date request
  retains separate versions and an integrity-checked sidecar. Existing pickle
  files are left untouched and are never opened. The sidecar explicitly records
  that Yahoo's adjusted data is back-adjusted, non-point-in-time,
  non-survivorship-safe and inadmissible as replay evidence.
- Optional FMP, EODHD, Alpha Vantage, FRED and Massive reads now share one
  secret-safe access coordinator. It applies process-wide provider pacing, at
  most one retry for connection failures or HTTP 502/503/504, capped backoff,
  and a consecutive-failure circuit breaker. Authentication, quota and other
  terminal responses are never retried. Safe attempt, wait and elapsed-time
  metadata is returned without URLs, parameters, headers or response bodies.
- A verified recorded-decision adapter now provides an intermediate causal
  execution check: an immutable decision is bound to an exact later market
  close using a fixed recommendation-to-action mapping. The close must be the
  earliest eligible close in a canonical schedule whose replay-source
  attestation and timestamps are rechecked against the exact authenticated bars
  supplied to the guardrailed engine. Every registered signal must be consumed
  inside the evaluation window, and the engine can act only from the following
  bar. It is intentionally narrower than
  the required faithful pipeline replay because it tests execution economics
  for already-recorded decisions, not the historical validity of the research
  process that produced them.

## Trust-critical backlog before paper submission or learning

1. Give every decision input `effective_at`, `available_at`, `retrieved_at`,
   provider/upstream identity and an immutable payload hash.
2. Archive historical index membership, removals and delisted outcomes to
   prevent survivorship bias.
3. Build one faithful replay of `InvestmentResearchPipeline` through
   `MasterPortfolioDecisionEngine` and `PortfolioEngine`. The recorded-decision
   adapter is a causal execution foundation only and does not complete this
   item.
4. Model turnover, fees, bid-ask spread, adverse slippage, liquidity limits and
   next-tradable-price execution without inventing precision.
5. Reserve a final untouched out-of-sample period and pre-register success and
   failure thresholds before examining its results.
6. Add purging/embargo where overlapping forecast horizons could leak labels
   between training and validation periods.
7. Only then compare simple baselines with LightGBM/XGBoost and use Optuna
   inside training folds. Every trial must retain its data, feature, model,
   policy and Git versions.

## Performance backlog

1. The first immutable telemetry boundary records per-ticker research wall
   duration and COMPLETE/ERROR outcome separately from configured pacing.
   The active research pipeline now attaches optional-provider coordinated-
   access duration and retry counts to that ledger, using separate component
   namespaces for success and for failures that reached the secret-safe source
   boundary. These rows include local pacing and retry backoff, overlap the
   aggregate supplemental stage duration, and must not be summed with it or
   described as vendor latency. Unconfigured calls, malformed measurements and
   failures outside that boundary emit no fabricated row. Cache hits and per-
   engine duration remain unknown, and these observations prove neither data
   freshness nor provider authority.
2. Continue the provider-client migration after measured need. The optional
   provider family now shares rate limiting, narrow retry/backoff and circuit
   breaking. Bounded concurrency is deferred because those callers are still
   sequential. CDN `Age` is deliberately not treated as financial-data
   freshness or point-in-time proof; any future freshness rule must use actual
   source semantics and availability timestamps.
3. Keep the new immutable Parquet cache non-authoritative. It removes executable
   pickle loading and detects changed bytes, but its adjusted Yahoo snapshots
   must never enter the faithful replay harness.
4. Consider Polars for large replay tables only after a representative
   benchmark. Prefer NumPy vectorisation before Numba for small metric loops.

## Promotion rule

No result from the legacy backtests or learning files is eligible to promote a
strategy. Promotion remains: pre-registered hypothesis, point-in-time replay,
walk-forward/out-of-sample validation, robustness checks, forward paper
evidence, Codex implementation, Claude challenge and human approval.
