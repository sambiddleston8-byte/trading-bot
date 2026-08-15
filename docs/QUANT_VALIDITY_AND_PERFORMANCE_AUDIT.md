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
- The cache and six legacy price loaders now share an injected yfinance caller
  boundary. It paces entry across client instances, invokes the opaque SDK once
  per call without speculative retries, redacts failures, validates finite
  positive closes before fresh use or cache reuse and carries false point-in-
  time, survivorship-safety and replay-admission flags at the boundary. Legacy
  callers retain only the validated frame and gain no evidence authority. It
  cannot count or constrain yfinance's internal HTTP
  operations, authenticate returned content or establish historical
  availability. Remaining yfinance research and learning callers are outside
  this batch.
- The legacy learning, expected-return, universe-ranking and portfolio market-
  exposure price reads now cross the same boundary with unchanged request
  shapes and unchanged safe no-data behaviour. Horizons, exclusive end dates,
  adjusted/unadjusted choices and return formulas are untouched. Valid frames
  therefore retain the same calculations, while malformed frames or an open
  shared circuit now fail closed instead of producing an outcome. These reads
  stay current, back-adjusted or unqualified, non-point-in-time, non-
  survivorship-safe and inadmissible as replay evidence; the boundary's false
  point-in-time, survivorship and replay-admission flags are visible truth and
  confer no authority downstream. Remaining research and multi-factor
  yfinance callers are outside this batch.
- The three direct `fast_info` current-price readers — portfolio monitoring,
  legacy learning and universe symbol validation — now cross a second bounded
  yfinance boundary sharing the same provider key, pacing and circuit. It
  validates the symbol before SDK construction, invokes the SDK once, retains
  only a positive finite scalar from either real `fast_info` access shape, and
  redacts every failure to a fixed reason code with counters-only metadata.
  A missing or unusable per-symbol price becomes `PRICE_UNAVAILABLE` without
  consuming shared circuit credit: monitoring returns `None`, symbol
  validation returns `False`, and learning uses its existing `1d` history fallback. The
  observation records false authenticated, point-in-time, survivorship-safe,
  tradeable-quote and replay-admission flags, so this number may not be used as
  an execution price, a prior close, a settlement input or replay evidence.
  Broad `.info`, statement and calendar yfinance callers are outside this
  batch.
- Optional FMP, EODHD, Alpha Vantage, FRED and Massive reads now share one
  secret-safe access coordinator. It applies process-wide provider pacing, at
  most one retry for connection failures or HTTP 502/503/504, capped backoff,
  and a consecutive-failure circuit breaker. Authentication, quota and other
  terminal responses are never retried. Safe attempt, wait and elapsed-time
  metadata is returned without URLs, parameters, headers or response bodies.
- Unauthenticated current-universe CSV, public FRED graph and Google News RSS
  reads now have fixed-endpoint strict-text controls and post-receipt parsing-
  size limits. These
  controls reduce request and parser risk only: they do not make current
  membership survivorship-safe, establish historical FRED availability, or
  qualify RSS as point-in-time catalyst evidence.
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

- An isolated VectorBT research pilot now exists for synthetic data only. It is
  single-instrument and long-only, binds every signal to an exact bar close with
  both close-level and bar-availability causality checks, and executes every
  strategy signal at exactly the next bar open. Same-bar strategy-signal
  execution is impossible. Its `SyntheticPilotAttestation` is content-addressed to the
  supplied bars and cannot be confused with `ReplayDataAttestation`; its bar type
  cannot reach `GuardrailedBacktestEngine`. VectorBT is imported lazily, only
  `Portfolio.from_signals` is called, and every execution semantic is pinned
  rather than defaulted. Closed and open trades are separated, and undefined
  win rate or Sharpe is recorded as `null` rather than fabricated as zero.
  Defined numeric results are quantized to nine decimal places and stored
  in a separate `VECTORBT_SYNTHETIC_PILOT_ONLY` ledger with every broker, paper,
  live, performance, promotion and track-record flag false,
  `cash_settlement_modelled` false and
  `parity_with_guardrailed_engine_proven` false. Its benchmark and Sharpe are
  labelled `DIAGNOSTIC_ONLY` with an explicit zero risk-free rate and 252-day
  annualization; exact daily spacing is required before either is computed. The
  stop uses the actual entry fill and adverse stop-market fills rather than a
  recovered bar close; partial fills are disabled and rejected orders fail the
  run. VectorBT cannot activate the stop on the entry bar; that limitation is
  recorded.
  These metrics satisfy neither `SharpeMetricReadinessGate` nor the
  benchmark evidence rules and never enter an existing performance ledger. This
  pilot replaces nothing, validates nothing and is not replay evidence. Note that
  `vectorbt==1.1.0` is Apache 2.0 **with Commons Clause**, not plain Apache 2.0.
  Total return includes open-position mark-to-market value; volume does not add
  liquidity modelling; settlement, corporate actions and terminal outcomes are
  unmodelled; and float arithmetic followed by nine-decimal quantization is not
  Decimal parity with the authenticated engine. The cost-free first-close
  benchmark is diagnostic and not like-for-like execution evidence.

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
