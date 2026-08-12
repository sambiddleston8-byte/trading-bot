# AI investment platform project status

This file keeps implementation continuity against the user-approved Master
Roadmap for the AI-Driven Investment Platform. The roadmap is the governing
architecture: work proceeds one phase at a time, GitHub remains the source of
truth, deterministic software is preferred for repeatable tasks, and autonomous
real-money trading remains disabled.

AI resource allocation is also a governing cross-phase roadmap requirement.
Deterministic tools or local models handle suitable routine work; Codex or
Claude Code owns each bounded complex task; and high-risk changes may use one
to build and the other to challenge. Included capacity from both paid
subscriptions must be considered before recommending upgrades or API credits.
The complete policy is recorded in `docs/AI_COLLABORATION.md`.
Its controlling objective is maximum software quality per unit of AI usage and
cost, with explicit context limits, Claude handoffs, review stop conditions and
future usage/cost observability.

## Current phase

Phase 5 performance and attribution foundations. The Alpaca boundary remains
local; nothing is connected or submitted, and results are not eligible for
learning.

## Completed

- Repository, architecture, Git state and automated tests inspected.
- Protected development branch created: `codex/phase1-immutable-ledger`.
- Baseline confirmed at 115 passing tests before Phase 1 additions.
- Append-only, SHA-256 hash-chained investment decision ledger implemented.
- Git, portfolio, component, model and prompt version metadata supported.
- Ledger integrity tests added.
- Strict roadmap decision schema connected to the successful, risk-approved
  portfolio-construction boundary.
- Unique portfolio snapshot IDs and duplicate-decision protection added.
- Research data cutoffs and pipeline/policy/portfolio versions flow into each
  selected holding's decision record.
- Codex-builder / Claude-challenger branch and review policy documented.
- Test collection side effects isolated from live research and valuation data.
- Phase 1 architecture, data-source, deployment and investment-methodology risk
  register completed.
- Claude Code completed a strict read-only adversarial review of commit
  `dbe3720`; its confirmed high-priority findings were repaired and covered by
  tests (writer locking, interrupted-tail recovery, strict data cutoffs, and
  thesis/catalyst field propagation).
- Recoverable portfolio/ledger transaction journaling and idempotent batch
  appends implemented with failure-injection coverage.
- New cached research records capture their source Git revision; legacy records
  remain explicitly `UNKNOWN` rather than inheriting a later revision.
- Claude's focused transaction review identified three actionable recovery and
  idempotence gaps; all were repaired with targeted regression coverage.
- Local Docker Desktop now runs the Streamlit web app and PostgreSQL on
  localhost only; research and monitoring workers remain manual and disabled.
- The initial PostgreSQL schema and ten audit/persistence tables were verified.
- Issue #7 adds a non-authoritative transactional PostgreSQL repository with
  cross-backend hash parity, concurrent retry safety, explicit UTC timestamps,
  exact replay checks and live-database rollback tests.
- Claude's issue #7 challenge review found hash, concurrency, replay and audit
  metadata gaps; all High/Medium findings were repaired and regression-tested.
- Issue #9 gives construction and reallocation one audited `PortfolioChange`
  format and one local snapshot-plus-ledger transaction boundary.
- Opt-in comparison mode shadow-writes and verifies portfolio, holdings,
  decision IDs and hashes against PostgreSQL while `local-files` remains the
  explicit default. Comparison mismatches/failures are surfaced by worker CLI
  output; workers themselves remain disabled unless manually invoked.
- Issue #11 adds a manual PostgreSQL custom-format backup and isolated restore
  rehearsal. A local rehearsal verified all ten tables, schema version, audit
  row counts and ledger head, then removed the disposable restore database.
  Backup files use owner-only permissions and remain outside Git.
- Issue #13 validates non-empty persistence using disposable synthetic
  databases: construction and reallocation each matched PostgreSQL twice,
  producing a four-record ledger chain. The populated backup restored with
  identical hashes and row counts, and both disposable databases were removed.
- Issue #15 adds a read-only preview for 14 pre-ledger portfolio snapshots. It
  hashes and inventories the source files without modifying them or PostgreSQL,
  labels every entry `UNVALIDATED_LEGACY`, and blocks promotion when immutable
  decision evidence and audit metadata are absent.
- Issue #17 prepares manual research and monitoring jobs for later unattended
  operation. Every run receives an append-only local lifecycle history, only
  `RECORD_ONLY` or `PAPER_ONLY` modes are accepted, and a fixed global-then-job
  lock order prevents concurrent portfolio mutations. No schedule is enabled.
- Issue #19 starts Phase 4 with append-only proposed paper-order records linked
  to their decisions and portfolio versions. The configuration accepts only
  Alpaca's official paper endpoint; it has no credentials, HTTP submission or
  live-money mode.
- Issue #21 reviews the complete investment-research method before broker
  submission. Local and read-only Claude reviews find that the active pipeline
  has strong evidence and safety gates, but that existing backtests do not
  faithfully replay that pipeline. It prioritises immutable point-in-time
  evidence, corrected forecast confidence, removal of correlated score reuse,
  current-pipeline historical replay and calibrated valuation assumptions.
- Issue #23 adds deterministic local simulated-fill records derived only from
  verified paper-order proposals. Records are append-only, hash-chained,
  idempotent, linked to decision/portfolio/model/Git identities and explicit
  about fees and adverse slippage. A fail-closed pre-flight reports the five
  methodology gates but cannot enable broker submission even when they clear.
  Claude's focused review found no functional safety defect; its boundary,
  linkage, input-sanity and retry recommendations were added to the tests and
  implementation.
- Issue #25 starts Phase 5 with immutable raw asset/S&P 500 price observations
  at entry, 1-day, 1-week, 1-, 3-, 6-, 12- and 24-month horizons. Each record is
  linked to a verified simulated fill and preserves separate asset/benchmark
  effective times, retrieval timing, source/version, price basis and a source
  input hash. Later horizons require entry, cannot be recorded early and cannot
  change price basis. Claude's statistical review prompted tighter benchmark
  alignment, cross-horizon consistency and explicit retrieval/backfill labels.
  The component calculates no return and makes no performance claim.
- Issue #27 adds immutable deterministic `PRICE_RETURN_ONLY` results for
  verified long simulated buys. It calculates decimal asset, S&P 500 and
  benchmark-relative price returns, plus a return after the recorded entry fee.
  Entry slippage is disclosed but not double-counted and no exit fee is
  invented. Missing evidence, adjusted-price bases, SELL fills and any split,
  dividend or uncertain corporate action fail closed. Results remain explicitly
  ineligible for learning and cannot be presented as total return, alpha or a
  track record.
  Claude's focused accounting review confirmed the formulas and no-double-count
  treatment, then prompted full entry-to-outcome corporate-action coverage,
  self-contained benchmark identity and an explicit
  `entry_fee_adjusted_long_return_excl_exit` field name.
- Issue #29 adds immutable sourced corporate-action evidence linked to verified
  simulated fills. It distinguishes complete no-event coverage, supported cash
  dividends/splits, unsupported events and explicit uncertainty. Dividend
  amounts and split terms remain exact decimal strings; overlapping complete
  evidence must agree on the economic events. It deliberately performs no
  entitlement decision, currency conversion, share adjustment, return
  calculation or learning update.
- Issue #31 makes sandboxing a governing requirement across all roadmap phases.
  It defines separate code, data, investment-strategy, agent and cloud-staging
  layers; fixed permissions/resource limits; emergency stops; and a promotion
  path that no agent can approve for itself. GitHub issues and pull requests now
  require sandbox impact and escape risks to be stated. Existing controls are
  distinguished from future AWS/Hermes controls that are not yet implemented.
- Issue #33 adds an immutable simulated long holding-period total return from
  verified unadjusted prices and complete corporate-action evidence. It applies
  USD gross cash dividends without reinvestment and exact split ratios, retains
  rational values alongside 34-digit decimal presentations, includes the
  recorded entry fee and does not invent cash-in-lieu or an exit. Later-paid
  dividends, uncertain/unsupported actions and ambiguous simultaneous events
  fail closed. The S&P price return is labelled non-like-for-like context, so no
  relative total return or alpha is claimed; results remain ineligible for
  learning and are not a portfolio or live track record.
- Issue #35 adds immutable S&P 500 gross ordinary cash dividend-point evidence
  over the exact `(entry, outcome]` benchmark interval. It matches the asset's
  gross-cash/no-reinvestment policy rather than silently comparing it with the
  standard reinvested S&P total-return index. Records require unadjusted prices,
  same UTC market dates for asset and benchmark endpoints, exact points,
  complete/uncertain status, provider input hashes and a hashed official S&P
  methodology identity. No benchmark return, relative return or alpha is yet
  calculated. An immutable uncertain record may later resolve through a
  separately identified complete record; complete evidence cannot regress or
  be replaced by a conflicting value.
- Issue #37 calculates a deterministic S&P 500 gross cash total return only
  from verified unadjusted entry/outcome prices and complete dividend-point
  evidence. The fixed formula adds dividend points as cash without reinvestment
  before dividing by the entry index level. Exact rational values, source
  hashes and the separate price/distribution components are retained. A result
  requires explicit acceptance that published dividend points follow changing
  index membership and weights rather than a basket frozen at entry. Relative
  return, alpha, learning, portfolio-performance and track-record claims remain
  blocked.
- Issue #39 calculates an immutable position-level benchmark-relative total
  return only when verified asset and S&P gross-cash total-return results share
  the exact fill, horizon and evidence identity. The arithmetic difference and
  both inputs are retained as exact rational values plus readable decimals.
  The asset includes its recorded entry fee while neither side invents an exit;
  this limitation remains explicit. The result is simulated and cannot claim
  risk-adjusted alpha, portfolio performance, learning eligibility or a track
  record.
- The next Phase 5 accounting slice records immutable simulated initial funding
  before a portfolio's proposals, then values the complete long buy-and-hold
  proposal/fill set at one exactly aligned horizon. Remaining cash subtracts
  recorded entry costs and adds verified gross cash dividends; positions, cash,
  total equity, target weights and actual weights retain exact rational values.
  Recorded entry fees and slippage are separately aggregated without counting
  fill-price slippage twice.
  Missing positions, timestamp misalignment, funding shortfalls and target
  weights above 100% fail closed. External contributions/withdrawals, exits,
  portfolio returns, alpha, learning and track-record claims remain blocked.
- Phase 5 now supports immutable simulated contributions or withdrawals only
  immediately after a verified portfolio valuation. Mid-period flows require a
  new exact boundary valuation and otherwise fail closed; withdrawals cannot
  exceed simulated cash. A linked exact-rational time-weighted return removes
  each boundary flow before measuring its subperiod and multiplies subperiod
  growth factors. The result is non-annualized, not benchmark-relative, not
  risk-adjusted alpha, ineligible for learning and not a live track record.
- Phase 5 now also values a matched-capital S&P 500 portfolio counterfactual at
  each verified asset-portfolio horizon. Every asset position must have a
  benchmark gross-cash result with the exact same fill and evidence identity.
  Its recorded fee-inclusive entry-cost capital earns that benchmark return,
  while initially uninvested funding remains cash; no benchmark cost is
  invented. Exact position values, cash, total equity and weights reconcile,
  but no benchmark portfolio return, relative return or alpha is yet claimed.
- Phase 5 now links those matched S&P portfolio valuations across the exact
  asset-portfolio and external-cash-flow boundaries. Prior flows remain
  zero-return counterfactual cash and each current flow is applied only after
  measuring its boundary, producing an exact non-annualized time-weighted S&P
  portfolio return. Missing paired valuations, mismatched timestamps or
  identities and non-positive equity fail closed. Asset-minus-benchmark
  portfolio return, alpha, risk statistics, learning and track-record claims
  remain blocked.
- Phase 5 now calculates the explicitly labelled arithmetic difference between
  verified asset and matched-S&P time-weighted portfolio returns. Both exact
  rational inputs and the difference are retained with immutable links to the
  same funding, asset valuations, benchmark valuations and external cash-flow
  evidence. Missing or misaligned evidence fails closed. The result is
  simulated, non-annualized and not risk-adjusted alpha; learning and
  track-record claims remain blocked.
- Phase 5 now measures portfolio concentration from a verified valuation after
  applying all external cash flows through its exact boundary as cash. Separate
  exact cash-inclusive and invested-position HHIs prevent cash holdings or new
  deposits from being confused with invested diversification. Largest, top-five
  and effective-position measures retain full valuation and cash-flow lineage.
  No threshold, recommendation, return, alpha, learning or track-record claim is
  made. Sparse milestone valuations remain ineligible for volatility, Sharpe,
  Sortino or genuine maximum-drawdown claims.
- Phase 5 now records immutable point-in-time sector-classification evidence
  for each verified invested position before sector exposure is permitted.
  Provider labels are preserved with their exact taxonomy/version, effective
  date, retrieval time, source and source-input hash. Missing or placeholder
  classifications become explicit `UNCERTAIN` records rather than an invented
  sector; later resolution is a separate content-addressed record and complete
  evidence cannot regress. Backfilled evidence is labelled, no exposure or
  recommendation is calculated, and learning and track-record claims remain
  blocked.
- Phase 5 now calculates exact post-flow sector exposure only when every
  invested position has exactly one complete classification under the same
  provider taxonomy/version. Provider labels are not translated; missing,
  uncertain, duplicated, mixed-taxonomy or conflicting code/name evidence
  blocks the result. Portfolio and invested-only sector weights reconcile with
  cash kept separate, and use of classification evidence retrieved after the
  valuation boundary is explicitly disclosed. Historical results verify their
  exact pinned support, so later evidence cannot rewrite or falsely invalidate
  them. No diversification judgment, recommendation, return, alpha, learning
  update or track-record claim is made.
- A targeted Claude catch-up review identified a High historical-integrity
  pattern in asset portfolio return, matched-S&P portfolio return and portfolio
  concentration verification. Those ledgers now resolve an existing result
  from the exact valuation and cash-flow IDs/hashes it originally recorded,
  instead of re-selecting current time-range membership. Later legitimate
  evidence can no longer rewrite or falsely invalidate history; missing,
  modified, misordered or identity-incompatible pinned support still fails
  closed. No performance claim, learning eligibility or live capability changes.
- Phase 5 now attributes the recorded simulated entry costs already embedded in
  verified portfolio valuations. Exact position and portfolio reconciliation
  separates fees, adverse slippage, favourable slippage, signed net cost and
  basis points against proposal reference notional. The result explicitly
  prevents re-deduction and does not invent exits, spreads, market impact or
  latency. It is not full round-trip/live-realism costing and makes no turnover,
  return, alpha, recommendation, learning or track-record claim.
- Phase 5 now records immutable point-in-time security factor-exposure evidence
  linked to verified invested positions. Provider model/version, hashed
  methodology, effective/retrieval times, source hashes and exact decimal factor
  values are retained without translating factor definitions or units. Missing
  evidence becomes explicit uncertainty, later resolution is append-only and
  backfill is labelled. Competing complete records for one valuation/security/
  model version are rejected. No portfolio factor aggregation, recommendation,
  performance, alpha, learning or track-record claim is yet produced.
- Phase 5 now calculates exact portfolio factor exposure only when every
  invested position has one complete observation under an identical provider,
  factor-model version, hashed methodology, effective timestamp, definitions
  and units. Exact position weighting is reported both among invested assets
  and as contribution scaled to total post-flow equity. Cash remains separate
  with no invented factor exposure; mixed or missing evidence fails closed and
  all supporting records are pinned by ID/hash. No risk label, recommendation,
  return, alpha, learning, track-record or live capability is produced.
- Phase 5 now has a versioned, content-addressed metric-readiness gate. CAGR
  requires at least one year and a verified return pinned to identical
  valuations; daily volatility and maximum drawdown require at least 253 unique
  observations over one year with no gap above four calendar days. Sharpe,
  Sortino, hit rate, turnover and prediction calibration disclose their missing
  risk-free, downside-policy, outcome, execution or cohort prerequisites rather
  than manufacturing results. The gate calculates no metric and makes no
  recommendation, learning, track-record or live-trading claim.
- The metric-readiness gate is now v2 and uses only the authoritative daily
  valuation/return ledgers for daily-series eligibility. It requires at least
  253 valuations and 252 exactly pinned consecutive returns over one year;
  milestone density, missing pairs, extra returns or altered hashes cannot make
  volatility or drawdown appear ready. CAGR retains its separate verified TWR
  requirement.
- Phase 5 now calculates sample daily volatility, fixed-252 annualized
  volatility and maximum drawdown only when the v2 gate approves the exact
  daily evidence fingerprint. Drawdown comes from a compounded cash-flow-neutral
  wealth index rather than raw balances. Exact mean/variance/drawdown evidence
  and the full wealth path are pinned; square roots use a declared 34-digit
  decimal context. Sharpe, Sortino, alpha, learning and track-record claims stay
  blocked.
- Phase 5 now calculates compound annual growth rate only after the v2 gate
  confirms a full-year horizon and one exact verified time-weighted return.
  The immutable result pins funding, valuation, return and readiness evidence,
  uses a declared 365.2425-day tropical-year basis and fixed 34-digit decimal
  power context, and remains simulated, gross pre-tax and non-risk-adjusted.
- Phase 5 now records immutable final New York Fed SOFR Index evidence for the
  future risk-free return series. It parses and retains the official raw JSON,
  binds the endpoint query to the returned date, computes the payload hash
  internally, rejects empty/nonpublication responses, and waits until after a
  conservative 15:00 New York revision cutoff. It calculates no Sharpe or other
  metric and enables no provider download.
- Phase 5 now derives an exact risk-free return for one authoritative daily
  portfolio-return period only by pinning final SOFR Index observations on both
  matching session dates. Index-ratio arithmetic correctly retains weekend and
  holiday accrual, source backfills are disclosed, and no excess return, Sharpe,
  Sortino or other risk-adjusted metric is yet calculated.
- Phase 5 now has a separate fail-closed Sharpe readiness gate that requires the
  full authoritative daily series and exactly one ID/hash/date/identity-matched
  SOFR risk-free return for every selected daily return. Missing, duplicate,
  extra or substituted evidence blocks readiness; later evidence beyond the
  assessed horizon does not destabilize history. The gate calculates no metric.
- Phase 5 now calculates annualized Sharpe only after that complete paired gate
  approves the evidence. Daily excess returns and sample variance retain exact
  fractions; square roots and the fixed-252 annualized ratio use a declared
  34-digit decimal context. Zero variance fails closed, both full evidence
  chains are pinned, and Sortino, alpha, learning and track-record claims remain
  disabled.
- Phase 5 now has an immutable human-preregistered Sortino target boundary. It
  supports matched daily SOFR or zero daily return without selecting either,
  requires the choice before a future evaluation window, forbids retrospective
  application or same-window replacement, and fixes conservative floors of 252
  total and 30 downside observations. No Sortino is yet calculated.
- Phase 5 now pairs each preregistered timestamped prediction to one verified
  simulated total-return result only at its exact declared fixed horizon.
  Confidence and return units are explicit, the decision must predate the fill,
  prediction error remains exact, and every decision/result/fill is pinned.
  The raw outcome excludes an unrecorded exit execution, and no success rule,
  bucket, hit rate, aggregate calibration, learning or track record is applied.
- Phase 5 now has an immutable human-preregistered prediction-evaluation policy
  boundary. It fixes the future decision cohort, success rule, confidence and
  expected-return buckets, calibration measures and conservative sample floors
  before results are eligible. Horizons and model versions cannot be pooled,
  all eligible outcomes must be included, and complete round-trip cost evidence
  is required. Policy selection must predate every eligible decision, preventing
  outcome-aware rule or bucket selection. Current entry-only outcomes therefore remain ineligible. The
  system does not choose a rule or calculate hit rate/calibration yet.
- Phase 5 now records immutable official unadjusted daily closing-price evidence
  per verified simulated fill and US market session. Exact prices, New York
  effective dates, provider/version, HTTPS sources, source hashes and retrieval
  timing are retained; late backfills are labelled and conflicting same-session
  evidence is rejected. V1 accepts only 4:00 p.m. New York regular-session
  closes; early closes remain blocked pending historical calendar evidence. It
  does not yet calculate a daily portfolio value or
  metric because corporate actions, complete open-position coverage, cash flows
  and execution history must first reconcile at the same boundary.
- Phase 5 now converts daily close evidence into exact per-fill position values
  only with complete corporate-action coverage through the same close. Splits
  adjust quantity exactly and only paid gross USD dividends are included;
  uncertain/unsupported actions, ambiguous ordering, unpaid distributions and
  FX needs fail closed. Fill, close and action evidence are pinned by ID/hash,
  and the result remains a position accounting value rather than a portfolio
  metric, recommendation, learning input or track record.
- Phase 5 now aggregates complete same-close position values into an exact daily
  portfolio valuation. Initial funding, recorded entry costs and fees, gross
  pre-tax USD dividends, external flows, cash, position values and weights reconcile
  exactly. Missing holdings, mixed identity, negative cash and unsupported sell/
  rebalance state fail closed. All funding, fill, value and cash-flow support is
  pinned by ID/hash. Broker net cash, withholding and investor tax are explicitly
  not modelled; no return, metric, learning or track-record claim is made.
- Phase 5 now links consecutive verified daily valuations into exact one-period
  cash-flow-neutral returns. New boundary contributions/withdrawals are removed
  before measuring investment return and then remain in ending capital, so cash
  movements cannot masquerade as gains or losses. Flows must occur exactly at
  the current close; intraday flows are blocked. Only adjacent weekday regular
  sessions (with weekends skipped) qualify, while holiday intervals await
  historical exchange-calendar evidence. Valuations and boundary flows
  are pinned by ID/hash. Results remain gross-pre-tax, non-annualized,
  non-risk-adjusted and ineligible for learning or track-record claims.
- Phase 5 now calculates exact non-annualized gross two-way turnover between
  consecutive verified daily valuations. Interval fills must exactly reconcile
  with newly supported fill IDs; this excludes the original deployment by
  construction. BUY and SELL notional, counts and recorded fees are separate,
  and the denominator is average boundary equity. Both valuations and every
  fill are pinned by ID/hash. It is not a complete round-trip, broker-cash, tax,
  recommendation, learning or track-record claim and cannot submit an order.
- A single-pass Codex/Claude quant-validity and performance audit was filtered
  against the active architecture. Exact-horizon legacy outcomes, fail-closed
  price valuation, same-day market-regime caching, one-snapshot walk-forward
  data access and incomplete-universe rejection were hardened with regression
  tests. TA-Lib, Polars, Numba, boosted-tree models and Optuna remain deferred
  until profiling and faithful point-in-time validation justify them. The
  remaining trust-critical and performance sequence is recorded in
  `docs/QUANT_VALIDITY_AND_PERFORMANCE_AUDIT.md`.
- Simulation-to-live integrity is now a governing cross-phase roadmap
  requirement. Paper-submission readiness adds execution-realism and data-parity
  gates, while a separate ten-part live-promotion checklist remains incapable of
  enabling live trading even when all evidence is reported complete. The full
  measurement ladder and conservative execution requirements are recorded in
  `docs/MASTER_ROADMAP_AMENDMENT_SIMULATION_TO_LIVE.md`.

## Phase 2 work

- Repeatable GitHub issue and pull-request promotion templates established.
- Codex and Claude Code may each be the bounded-change builder or independent
  reviewer according to task fit, useful existing context and remaining
  included subscription capacity. They do not duplicate full-repository work.
- Frontier-model review is explicitly skipped for routine mechanical work.
- Anthropic and OpenAI pay-as-you-go APIs are not substitutes for included
  Claude Code or Codex capacity. Additional usage is recommended only after
  deterministic/local options and suitable capacity in the other paid
  subscription have been considered; reliability and safety remain mandatory.
- Mature platform operations should move into deterministic software and
  conventional infrastructure. Runtime AI usage will be metered by model, task,
  tokens, cost, duration, retries and outcome where provider data permits, so
  future allocation can be improved using evidence.
- Phase 3 AWS architecture issue #3 documented a low-cost, paper-only pilot;
  Claude challenged its persistence, scheduling, access and cost boundaries, and
  all High/Medium findings were resolved in the design.
- Keep AWS execution blocked until transactional persistence replaces the local
  two-file snapshot/ledger sequence.

## Safety invariants

- Execution remains `RECORD_ONLY` or paper-only.
- No broker credential or live-order path may be introduced in Phase 1.
- Tests must not write into the repository's live `data/` history.
- Existing user data changes must not be staged with Phase 1 source changes.
- Unapproved code, data, strategies and agent actions remain inside the sandbox
  boundary described in `docs/SANDBOX_GOVERNANCE.md`.
- No agent may promote its own experiment, merge, deploy or increase its own
  permissions.

## Next action

Obtain the user's explicit Sortino target choice, then preregister it for a
future evaluation window before calculating Sortino. Continue objective Phase 5
prerequisites that do not depend on that choice meanwhile. The hit-rate and
calibration policy boundary now exists but requires the user's future explicit
choices before registration or aggregation. Turnover is a distinct verified
calculation. Risk-adjusted
alpha remains a separate model; learning and track-record claims remain blocked.
Issue #21 remains the research-quality backlog. PostgreSQL remains
non-authoritative and Lightsail remains only the planned future destination.
