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

## Trust-critical backlog before paper submission or learning

1. Give every decision input `effective_at`, `available_at`, `retrieved_at`,
   provider/upstream identity and an immutable payload hash.
2. Archive historical index membership, removals and delisted outcomes to
   prevent survivorship bias.
3. Build one faithful replay of `InvestmentResearchPipeline` through
   `MasterPortfolioDecisionEngine` and `PortfolioEngine`.
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

1. Profile complete research runs and record provider latency, retry counts,
   cache hits and per-engine duration.
2. Centralise provider clients with bounded concurrency, rate limiting,
   retry/backoff, freshness rules and circuit breaking.
3. Replace the legacy pickle market cache with immutable, versioned Parquet
   snapshots before it becomes part of the faithful replay harness.
4. Consider Polars for large replay tables only after a representative
   benchmark. Prefer NumPy vectorisation before Numba for small metric loops.

## Promotion rule

No result from the legacy backtests or learning files is eligible to promote a
strategy. Promotion remains: pre-registered hypothesis, point-in-time replay,
walk-forward/out-of-sample validation, robustness checks, forward paper
evidence, Codex implementation, Claude challenge and human approval.
