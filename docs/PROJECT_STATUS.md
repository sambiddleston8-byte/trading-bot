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

Cross-phase foundation implementation with Phase 5 evidence acquisition as the
critical path. Safe boundaries now exist through Phase 10, but later phases are
not operationally complete. Alpaca, AWS, Hermes, Capitol Trades, Obsidian and
Buzz are not connected or activated, and results are not eligible for learning
or track-record claims. The requirement-by-requirement distinction is recorded
in `docs/MASTER_ROADMAP_COMPLETION_AUDIT.md`.

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
- The authoritative paper-submission methodology pre-flight is now immutable
  and evidence-backed. Each of its seven gates pins a source hash, exact source
  location and observation time; simple yes/no assertions remain only a legacy
  diagnostic. Complete evidence still cannot connect an account, permit
  credentials, make a network request, submit an order or enable live trading.
  Reassessment preserves history through explicit forward-only supersession.
- The first underlying methodology repair adds an immutable point-in-time input
  manifest for the active research-to-decision route. It pins the canonical
  research snapshot and seven raw input families with effective, public and
  retrieval times plus source hashes and exact locations. Late evidence is
  rejected as an input and missing families remain explicit. This proves
  provenance only; it does not validate forecasts or clear broker readiness.
- The active pipeline now separates estimate consistency from forecast
  accuracy. Revenue/EPS estimate agreement remains a visible input-quality
  diagnostic but can no longer multiply the investment score. Forecast accuracy
  is explicitly uncalibrated until complete realised outcomes support the
  preregistered evaluation policy, and the canonical contract carries both
  meanings separately.
- The active route now has one authoritative opportunity-ranking score and a
  versioned factor-lineage declaration. Synthesis owns the ranking factors;
  master decision is gate/explanation only, portfolio ranking is pass-through,
  and the former blended decision rating is display-only. Risk and evidence
  factors may still affect position sizing, but cannot create extra votes in
  candidate ordering.
- Historical S&P 500/Nasdaq-100 membership now has an immutable point-in-time
  event foundation for additions, removals and delistings. Snapshots respect
  both effective and public-availability cutoffs and retain exclusions. They
  remain explicitly partial and cannot claim survivorship-safe replay until
  full coverage and actual terminal-outcome evidence are separately proven.
- Delisted members now have a separate evidence-backed terminal-outcome ledger.
  It pins the precise delisting event, source content and availability time,
  distinguishes acquisition proceeds, bankruptcy/liquidation recovery and last
  tradable value, and cannot calculate performance or declare replay readiness.
  The historical source population is not yet complete.
- A bounded historical-universe coverage certificate now records a pinned
  source's completeness attestation for one exact interval and internally
  reconciles its starting population, every event and every delisting outcome.
  Successive intervals must be contiguous and carry the prior ending population
  forward. Source attestation is not independent proof, so completeness and
  replay readiness remain false until a later source-approval control is met.
  It cannot claim global coverage or calculate performance, and no real
  certificate has yet been populated from an authoritative historical dataset.
- A faithful active-pipeline replay can now be preregistered without running it.
  The inert plan freezes code and component versions, source hashes,
  dependencies, runner, sealed dataset commitment, untouched evaluation dates,
  point-in-time requirements, two-sided commission/spread/slippage/latency/
  impact costs, pessimistic stress and fixed success/failure metrics. It cannot
  open the test data, execute a replay, claim performance or connect to a
  broker. Plans cannot be backdated or shopped: a frozen Git revision gets one
  binding plan, with minimum non-commission costs, a separate 0.10% baseline
  slippage floor and at least 2x pessimistic stress. A real plan awaits an
  approved historical-universe source and a
  genuinely sealed dataset.
- Deterministic purged and embargoed forward folds now prevent long-horizon
  outcomes from leaking across training/test boundaries. Each observation pins
  its decision and label-end time; overlapping training labels are purged,
  post-test records are embargoed, windows cannot overlap and test observations
  cannot be reused. Fold construction executes no model or replay and makes no
  performance or promotion claim.
- The future replay now has a deterministic execution-realism policy. It
  forbids same-close fills and requires the next tradable observation, market
  calendars/halts, volume participation caps, partial fills, rejections,
  cancellations, cash/position/settlement/fractional-share constraints and
  applicable regulatory or borrowing costs. Pessimistic per-side costs are at
  least twice base and must pass. The policy generates no fill, broker request,
  performance result or trading authority.
- DCF expected returns now require point-in-time sourced evidence for the full
  discount-rate and terminal-growth calculation, including risk-free rate,
  equity premium, beta, debt cost, tax and market-value capital weights. The
  reported WACC must reproduce the evidence-derived calculation and a WACC/
  growth sensitivity matrix is mandatory. Existing coarse 9%/3% valuations
  remain readable but are review-only and cannot make a new active research
  result portfolio-eligible; realised forecast accuracy remains uncalibrated.
  A URL and well-formed hash are not accepted as proof of content: eligibility
  remains fail-closed until a separate ledger authenticates the retrieved bytes.
  Rate/beta sanity bounds and a substantive base-bracketing sensitivity grid are
  also enforced.
- Authenticated source-content storage now preserves the actual retrieved bytes
  behind a content-addressed SHA-256 path and re-hashes them whenever its
  append-only ledger is verified. Missing, changed, symlinked or path-escaping
  blobs fail closed. A DCF assumption must match the exact authenticated source
  ID, content hash, URL and retrieval time; byte authentication does not itself
  claim the source's economic interpretation is correct. The active research
  pipeline reads this canonical local ledger directly; absence of authenticated
  content fails closed and performs no network access.
- Historical replay providers now require an immutable human approval record
  that pins the product, terms hash/review, permitted use, index/date coverage
  and explicit removed/delisted/corporate-action/terminal-outcome support. The
  record cannot subscribe, store credentials, fetch data, certify completeness,
  run a replay or grant performance/trading authority. No provider has been
  approved yet.
- Historical-data providers can now be qualified against a provider-neutral,
  fail-closed checklist before approval. Authenticated terms, documentation and
  a representative sample are mandatory, and every required replay capability
  must link to the exact documentation and sample that support it. Both roadmap
  universes, internal-use terms, removals/delistings, terminal outcomes,
  corporate actions, total-return prices, point-in-time fields, corrections,
  calendars/halts and reproducible versioned exports are checked. A pass only
  remains possible when every known limitation has a recorded mitigation and
  none blocks a required capability. A pass only
  makes the provider eligible for a separate human approval; qualification
  cannot approve, purchase, store credentials, fetch data, open evaluation
  observations, replay, claim performance or enable trading. FMP has not yet
  been assessed.
- A sealed replay-dataset admission boundary now links one preregistered replay
  plan to the exact human-approved provider and authenticated terms, an
  authenticated content-addressed dataset manifest, all required replay-data
  roles and contiguous S&P 500/Nasdaq-100 coverage records. The manifest hash
  must equal the commitment frozen before access, and neither the manifest nor
  its artifacts may be opened before the plan's declared access time. Exact
  authenticated terms must predate approval, approval must predate data access,
  and all manifest, artifact and coverage hosts must be expressly approved.
  The access-time gate runs before any source blob is authenticated or read,
  and admission cannot be backdated. Admission authenticates and reconciles
  metadata only: it does not interpret evaluation
  observations, train a model, execute a replay, calculate performance or
  enable any broker connection. No real dataset has been admitted.
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
  distinguished from future AWS controls and the later Hermes controls, which
  are now implemented as inactive governance boundaries rather than a running
  worker.
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
- The user has selected zero daily return as the future Sortino downside target.
  It is distinct from S&P 500 market benchmarking and SOFR-based Sharpe/CAPM.
  A fail-closed readiness gate now binds the future calculation to the exact
  preregistered policy and evaluation window, matching strategy/model identity,
  at least 252 daily observations and at least 30 true downside observations.
  The gated immutable calculator pins the policy, readiness fingerprint and
  exact return path, uses the preregistered total-count denominator and fixed
  252-session annualization, and fails closed on zero downside deviation. No
  live portfolio/window has been invented or registered, so no actual Sortino
  has yet been calculated.
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
- Phase 5 now pairs one verified local simulated BUY entry with one later
  verified SELL exit for the same decision, security, exact quantity and
  strategy/model/Git identity. Exact net profit/loss and return include both
  recorded fees; fill prices already embed recorded simulated slippage. Fills
  cannot be reused, and deterministic FIFO matching prevents favourable pair
  selection. Spread, market impact, latency, tax and borrow costs remain
  explicitly unmodelled, and the component cannot create or submit an order.
- Phase 5 now binds a raw prediction pair to its complete simulated round trip
  only when the exit exactly matches the authoritative fixed-horizon timestamp
  and observed price. Entry cost and split-adjusted quantity reconcile; net
  outcome adds gross paid dividends and deducts both execution fees. Exact net
  return and prediction error are pinned, but no success rule, cohort, hit rate,
  calibration, learning or track-record claim is applied.
- Phase 5 now subtracts the exact matched S&P 500 gross cash total return from
  that complete fixed-horizon asset return. Fill, horizon, outcome observation
  and full strategy/model/Git identity must match and both results are pinned.
  The asset includes both recorded simulated fees; no unevidenced benchmark
  execution cost is invented. This is relative return, not alpha or a success
  score, and remains ineligible for learning or track-record claims.
- Phase 5 now has an immutable human-preregistered risk-adjusted-alpha policy
  boundary. It supports CAPM with matched S&P/SOFR, official Ken French US
  three-factor or official five-factor models without selecting one. A fixed
  future start/end window, consistent risk-free basis, 756-observation floor,
  complete date intersection, OLS intercept and Newey-West HAC inference are
  locked before results. Model shopping, optional stopping, imputation and
  cross-version pooling are forbidden. No alpha or factor download occurs yet.
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

## Phase 6 work

- Phase 6 now starts with an immutable human-approved Hermes permission and
  resource-budget boundary. Its default state is stopped; scheduling, models,
  network, broker, AWS, GitHub and production-rule writes are disabled. Hermes
  cannot promote experiments, merge, deploy, change its own authority or enable
  trading. Only future sandbox lesson/experiment proposals are permitted, and
  registering a policy activates nothing. The legacy adaptive learning scripts
  remain non-authoritative.
- Phase 6 now records immutable evidence-backed sandbox lesson proposals. Each
  pins verified complete outcomes and requires a suspected cause, uncertainty,
  falsifiable hypothesis, experiment and disconfirming result. A proposal cannot
  validate or apply itself, alter rules/weights/code/permissions, promote,
  deploy or trade. Hermes provenance is blocked while Hermes remains inactive;
  human, Codex or Claude Code may author traceable proposals without invoking a
  model as part of the record operation.
- Phase 6 now preregisters immutable sandbox experiment specifications pinned to
  lessons. The full baseline/candidate change, point-in-time cutoff, future
  out-of-sample and shadow boundaries, single primary metric, improvement and
  risk limits, trial budget, seed and acceptance/rejection rules are
  content-addressed. Random splits, test reuse, metric switching, optional
  stopping, automatic execution, promotion, code/rule changes, deployment and
  trading are forbidden. Registration executes nothing.
- Phase 6 now records inert reproducible run manifests pinned to a verified
  experiment, exact code, dataset, dependencies and runner hashes. Isolated
  no-network execution, resource ceilings and the preregistered trial budget
  are fixed in advance. Planning cannot execute, record a result, promote,
  deploy, connect to a broker or trade.
- Phase 6 now records immutable sandbox results pinned to the exact experiment,
  run manifest and runner-output hash. It forbids incomplete trials and early
  completion, fixes metric direction, and mechanically evaluates primary,
  drawdown and turnover thresholds using decimal arithmetic. Passing is not
  promotion and cannot change a rule, deploy or trade.
- Phase 6 now gives each verified experiment candidate one immutable strategy
  disposition: rejected, or eligible for a future shadow test. Eligibility
  cannot start shadow testing, replace the incumbent, approve promotion,
  change code, deploy or trade.
- Phase 6 now preregisters immutable future paper/shadow plans for eligible
  candidates. The window, minimum complete decisions, cadence, metric and risk
  limits are fixed before observations begin. Planning cannot start the test,
  connect to a broker, promote, replace the incumbent, deploy or trade.
- Phase 6 now records complete immutable paper/shadow evidence after the fixed
  window and minimum decision count. Metric and risk thresholds are evaluated
  mechanically. Passing only permits future human review; it cannot promote,
  replace the incumbent, deploy or trade.
- Phase 6 now assembles immutable human-review packages containing the passing
  shadow result, exact code and implementation identities, GitHub records,
  deterministic tests, independent review and rollback evidence. A complete
  package still records no human decision and cannot promote, activate, deploy
  or trade.
- Phase 6 now records the explicit human decision after a complete review
  package: reject the candidate, or approve it only for a separate implementation
  change. Each bundle receives one final, immutable decision. Even approval
  leaves the incumbent unchanged and cannot change code/rules, activate,
  deploy, connect a broker, submit an order or enable live trading. Promotion
  cannot be self-approved and still requires a distinct reviewed implementation
  and later activation boundary.
- Phase 6 now has a durable latched Hermes emergency stop. It denies new jobs,
  requires running jobs to terminate, preserves evidence and exposes no resume
  or activation operation. Unknown and inactive policies fail closed as stopped.
- Phase 6 now has the separate human-controlled activation boundary anticipated
  by that stop design. A future window is short-lived, pins exact code, local
  job/action, canonical read/write roots and strict duration, concurrency,
  model-call, AI-cost, simulated-exposure and heartbeat limits. The initial
  boundary forbids all endpoints and credentials. Start-time admission fails
  closed on policy stops, time-window expiry, code drift or excessive requests,
  but passing still creates no scheduler and starts no job. Runtime lifecycle,
  daily-consumption, concurrency, repeated-failure and heartbeat enforcement
  remains a separate prerequisite before any unattended worker can run.
- Phase 6 now implements that immutable lifecycle/accounting prerequisite.
  Capacity for each admitted future job is atomically reserved before start,
  including daily count/cost, concurrency, duration, model calls and simulated
  exposure. Start, heartbeat and terminal events are hash chained; actual use
  cannot exceed the reservation. Deadline, stale-heartbeat and consecutive-
  failure violations latch the durable stop before quarantine logging. Stop-
  admission races and stale success reports fail closed. No scheduler or task
  is installed or run, and no external, broker, AWS, GitHub-write, promotion,
  order or trading permission is granted.

## Phase 7 work

- Phase 7 now begins with an immutable source-neutral congressional trade
  disclosure ledger. It separates transaction, filing, evidenced public
  availability and system-observation times so disclosure delay cannot become
  look-ahead bias. Capitol Trades requires a licensed feed; official-source use
  requires a recorded terms review. No scraper, connector, automatic signal,
  broker path or trading capability exists.
- Phase 7 now creates immutable point-in-time issuer activity snapshots from
  verified disclosures. They count buy/sell/repeat activity, preserve uncertain
  value ranges, calculate conservative net bounds and expose disclosure delay.
  Future evidence is excluded and the result is never a standalone recommendation
  or executable/copy-trading signal.
- Phase 7 now has a fail-closed source-activation preflight. Official House or
  Senate automation requires pinned terms evidence and affirmative documented
  findings for the exact use and access method. Capitol Trades or another
  commercial provider additionally requires an in-force licensed-feed contract.
  Passing permits only a separate connector review; no connector, scraper,
  download, signal or trading authority is created.
  A retrospective Claude review found that the original official-source
  commercial-use condition duplicated the general legal flag and could be
  bypassed by caller-supplied text. Policy v2 now blocks official House/Senate
  commercial investment use unconditionally and also hardens evidence URLs.
- Phase 7 now has a point-in-time issuer-mapping boundary for future connector
  work. It requires a stable reviewed issuer identifier, exact asset-name match,
  effective period, knowledge timestamp and hashed evidence. Hindsight,
  conflicting and tampered mappings fail closed. It is not connected to an
  external source or current snapshot path and grants no recommendation or
  trading authority.
- Phase 7 now has point-in-time committee-membership evidence. It pins chamber,
  committee/subcommittee, role, effective dates, knowledge time and hashed
  source review, then rejects hindsight or mismatched disclosures. It explicitly
  makes no committee-relevance or investment-advantage claim and is not yet
  integrated into activity snapshots.

## Phase 8 work

- Phase 8 now has a deterministic non-authoritative Obsidian Markdown exporter
  covering all roadmap knowledge folders. Generated pages pin authoritative
  records and hashes, are integrity-marked and cannot overwrite manual edits,
  unmanaged pages or symlinks. No vault location, plugin or paid AI is required
  or activated yet.

## Phase 9 work

- Phase 9 now identifies Block's open-source Buzz as the intended collaboration
  workspace and records an immutable local-only manifest for all roadmap channels
  plus distinct inactive Codex, Claude Code and Hermes identities. GitHub and the
  ledgers/database remain authoritative. Buzz, its relay, dependencies, keys,
  workflows and agents are not installed, started or connected; no identity can
  merge, deploy, promote, access AWS/brokers or trade.

## Phase 10 work

- The Phase 10 completion audit identified and closed the planning half of a
  missing robustness gate. Passing out-of-sample results must now preregister at
  least two historical-period, sector and market-regime slices plus a fixed pass
  fraction. Costs, point-in-time data, survivorship safety and no leakage are
  mandatory. Planning cannot execute, grant shadow eligibility, promote, deploy
  or trade. The following completed item records the result enforcement that
  closed this gap.
- Phase 10 now enforces every preregistered robustness slice with pinned evidence
  and the required costs, point-in-time, survivorship and leakage controls. The
  fixed pass fraction and every robustness dimension must pass. The strategy
  registry can no longer advance a plain out-of-sample result; only verified
  robustness evidence can make a candidate eligible for future shadow testing.
- Phase 10 now implements real local disposable experiment storage: an exact
  marker-authorized root, private manifest and SQLite database, bounded retention
  and safe expiry. Path escapes, symlinks, tampering, early deletion and unknown
  files fail closed. A container runner now links this storage to a verified
  preregistered run manifest and sealed marked-root input. It enforces a pinned
  image digest, no network, read-only filesystem, no capabilities/privilege
  escalation, no automatic image pull, an unprivileged user,
  CPU/memory-plus-swap/process/time limits, forced container-ID cleanup and exactly
  one attempt. The experiment gets no writable production/workspace mount; a
  bounded mechanical JSON result is host-captured into SQLite. No real active-
  pipeline image or sealed dataset has been run, and capture grants no
  promotion, deployment, broker or trading authority.
- The container runner no longer trusts a caller-supplied image digest alone.
  A separate append-only image-approval ledger pins the digest-qualified image,
  full Git revision, dependency lock, entrypoint source, Dockerfile, build
  provenance and independent security-review evidence. The runner fails before
  reserving its single attempt unless the verified approval matches the run
  manifest's image, code and dependencies. Recording approval performs no
  build, pull, push, inspection or execution. No real active-pipeline image is
  built or approved, no sealed experiment has run and no deployment, broker or
  trading authority is granted.
- Phase 10 now derives a canonical sealed experiment-control manifest from the
  verified run, its exactly pinned experiment and the approved image before an
  attempt is reserved. Only the baseline/candidate versions, point-in-time
  cutoff, fixed future window, metric, seed, trial count and committed hashes
  enter it; free-form descriptions/rules, credentials, provider settings, URLs
  and ledger paths are excluded. The private workspace snapshots and rehashes
  the control separately from the dataset, mounts both read-only and binds the
  control hash to the attempt and result. This is an input contract only: no
  real image is built or approved and no experiment, deployment or trade runs.
- Claude's retrospective container-isolation review found that adversarial
  decimal exponents could exhaust host memory and that an approved input could
  change between hashing and its Docker mount. Policy v2 bounds decimal
  exponents, snapshots and rehashes the exact verified bytes in the private
  disposable workspace, mounts only that read-only snapshot and permits one
  local experiment container at a time. The real Docker command path still
  requires a controlled integration rehearsal before any admitted dataset run.
- Phase 10 now assigns complete simulated benchmark-relative outcomes to every
  roadmap effectiveness dimension using pinned point-in-time evidence. Labels
  observed after the decision remain explicit hindsight backfills and cannot
  support effectiveness claims.
- A deterministic read-only summary covers regime, sector, company size,
  horizon, valuation, volatility, strategy, signal and model version. It
  excludes backfilled labels, enforces a minimum sample floor of 30 and uses
  exact arithmetic. Results remain descriptive: no independence, significance
  or causal claim is inferred, and no recommendation, learning, promotion or
  trading authority is produced.
- A retrospective Claude review of the complete Hermes controlled-learning
  chain found a builder/reviewer self-approval gap, duplicate results per run
  and cross-policy emergency-stop masking. The remediated boundaries require a
  distinct decision-maker, one immutable result per run and policy-scoped stop
  enforcement. Human names remain unauthenticated assertions and result metrics
  remain hash-pinned rather than deterministically derived from artifacts, so
  the chain stays unwired and cannot operationally promote, deploy or trade.
- The authenticated guardrailed replay engine now emits an exact sizing trace
  for every accepted or rejected simulated order and an ordered portfolio-state
  trace after each cash, corporate-action, fill, terminal and session-close
  event. Cash, unsettled proceeds, equity, position cost, ATR stop, liquidity,
  fee and risk-budget constraints are preserved rather than reconstructed from
  summary returns. Partial exits remain individual execution records, while
  profit is calculated once for the complete entry-to-final-exit round trip so
  purchase cost cannot be redistributed between partial sales.
- Completed mechanical replay runs can now be written to a private append-only,
  SHA-256 hash-chained audit ledger. Each run is bound to the authenticated
  source admission, source and role hashes, validation receipt, strategy and
  parameter versions, engine policy/configuration, fee schedule, scenario,
  evaluation window and Git commit. Candidate arithmetic is checked before the
  durable append and again during verification; execution/sizing, purchase
  cost, all exit proceeds, portfolio state, return and drawdown must reconcile.
  Valid no-trade runs remain recordable and all broker, order, paper-promotion,
  performance-claim and live-trading permissions remain false. No real sealed
  dataset has been replayed and no track record has been created.
- Claude's focused red-team reviews found a missing rejected-exit trace and two
  progressively subtler partial-exit cost-allocation weaknesses. The engine was
  changed to whole-round-trip accounting, regression coverage was added, and
  Claude returned PASS on authentication, append integrity and the final
  financial reconciliation design.
- A replay plan can now bind exactly one strategy and one canonical parameter
  set before sealed evaluation-data access. The append-only strategy
  specification also pins the plan record, untouched evaluation window, Git
  revision, Python entry point and strategy source-file hash; strategy or
  parameter search after access remains explicitly prohibited. The backtest
  engine derives the entry point and hashes the actual strategy source file it
  executes instead of accepting those values from the caller. Replay-run audit
  policy v2 revalidates the authenticated admission and strategy parent and
  rejects any different strategy version, parameters, code file, entry point,
  Git commit or evaluation dates. This closes a post-access cherry-picking
  route, but no real strategy has been preregistered and no sealed replay has
  run. Claude passed the ledger and binding reviews after retracting a proposed
  date-type defect that the canonical conversion and regression tests disproved.
- Verified immutable investment decisions can now be converted into one
  tamper-evident, simulation-only signal per ticker and market close. The
  mapping is fixed (`BUY`/`STRONG_BUY` enter, `AVOID` exit, and
  `HOLD`/`WATCHLIST` do nothing), preserves the original data/decision/model/
  portfolio/Git identities and cannot carry broker, order, promotion or live-
  trading authority. The strategy adapter accepts only a verified signal
  ledger, derives the earliest eligible close from a complete canonical market
  schedule, pins the replay-source attestation and revalidates both against the
  exact authenticated bars received by the engine. It emits each instruction
  once at that bound close, and the run fails if any registered instruction is
  skipped or falls outside the evaluation window. The existing guardrailed
  engine therefore executes no earlier than the following bar's open with its
  configured fees, spread, slippage, latency and risk controls.
  This is a causal execution diagnostic for decisions that already exist. It
  does not recreate how the research decision was generated, qualify historical
  data, prove investment value or create a track record.
- The next active-pipeline replay boundary is now explicit and fail-closed. An
  inert canonical context must reconcile one verified replay plan with one
  approved no-network runner image, the exact Git revision and dependency lock,
  all 22 research-engine dependencies, the ambient factor-lineage policy, a
  strictly ordered preregistered in-window as-of schedule, the sealed learning-
  state digest and authenticated
  source-ledger/blob-manifest digests. The master-decision and factor-lineage
  identities must match their preregistered component identities. Network,
  provider fallback, filesystem writes, mutable learning, degraded-stage
  completion, broker/order/deployment/promotion and performance-claim authority
  are all fixed false. The full canonical context is preregistered once per plan
  in an append-only ledger before the plan's evaluation-data-access embargo;
  the verified context is not released until the real clock reaches that
  embargo. The ledger can now issue one unforgeable inert invocation for one
  exact preregistered schedule index only after re-hashing the 22 injected
  engine sources, all six active-route component sources, the authenticated
  source ledger/blob manifest and the immutable learning-state file, and after
  matching the checked-out Git revision. Those identities and bytes are
  revalidated again when the pipeline consumes the invocation, closing the
  post-issue mutation gap. Replay mode cannot save output, never falls back to
  ambient engine loading, uses the sealed source/learning paths and frozen
  as-of timestamp, and re-raises all five previously degraded stage failures.
  The existing ordinary research path retains its current graceful-degradation
  behaviour. This Python boundary does not itself block a leaf engine's socket
  or filesystem calls; that remains the approved no-network/read-only
  container's job. No real image or dataset has been admitted, no real
  invocation has been issued, and no replay or performance claim exists.
- A preregistered replay execution policy now derives the only permitted
  simulator configuration for each exact BASE or PESSIMISTIC scenario. The
  derived profile fixes risk limits, ATR sizing, commission, spread, at least
  0.10% baseline slippage, latency, nonlinear lagged-liquidity impact,
  participation, settlement, order age and stop-pierce treatment; it also pins
  the complete canonical configuration and fee schedule hash. A daily-bar run
  fails closed if the declared maximum order age expires before the next bar,
  because the missing intraday cancellation cannot be reconstructed safely.
  Replay-run audit policy v3 revalidates the execution-policy parent and exact
  per-fill economics, and a separate completeness gate requires one matching
  BASE/PESSIMISTIC pair before the replay is considered complete. This creates
  no performance claim, paper-trading approval, broker request or live-trading
  authority. No real replay has been run. Claude passed the bounded execution-
  policy review before merge.
- The legacy Yahoo market-data cache no longer deserializes executable pickle
  files. It now stores each exact request as immutable, versioned Parquet bytes
  with an integrity-checked sidecar, leaves all existing pickle files untouched
  and never opens them. The sidecar keeps the truth visible: adjusted Yahoo
  data is back-adjusted, non-point-in-time, non-survivorship-safe and forbidden
  as authenticated replay evidence. This improves local safety and repeat-use
  efficiency; it does not improve historical-data validity or create a track
  record.
- Optional FMP, EODHD, Alpha Vantage, FRED and Massive requests now pass through
  a shared process-local access coordinator. It gently spaces requests across
  separate client instances, retries only connection failures and HTTP
  502/503/504 once, caps provider-requested backoff, and temporarily pauses a
  provider after repeated failures. Authentication, entitlement, quota, HTTP
  429 and provider-declared errors are terminal and never retried, protecting
  limited included allowances. Returned access metadata contains counts and
  timings only—never URLs, query parameters, headers or response bodies. This
  does not activate a provider, run a worker, cache responses, prove freshness,
  purchase data, connect a broker or enable trading. Concurrency remains
  deferred until a real concurrent optional-provider caller exists.
- Successful supplementary-provider calls and failures that reach the safe
  optional-provider boundary now contribute separately named, secret-free
  access-duration and retry-count observations to the existing append-only
  research telemetry ledger. The provider rows use stable safe slugs and copy
  no raw payload, exception text or request detail. Their duration includes
  local pacing/backoff and overlaps the aggregate supplementary stage, so it is
  not vendor latency and must not be summed with the stage duration.
  Unconfigured calls, malformed measurements and failures outside this boundary
  add no fabricated row. This instrumentation makes no extra request and has no
  freshness, research, provider-selection, execution or trading authority.

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

Preregister the user-approved zero-return Sortino target when the next genuine
portfolio version and future evaluation window are declared. Continue objective Phase 5
prerequisites that do not depend on that choice meanwhile. The hit-rate and
calibration policy boundary now exists but requires the user's future explicit
choices before registration or aggregation. Turnover is a distinct verified
calculation. Complete fixed-horizon simulated outcomes now exist but remain
unaggregated until the human policy choices are registered; complete matched
benchmark-relative evidence is also ready for the recommended rule. Risk-adjusted
alpha now has a preregistration boundary but still requires the user's model and
future-window choice; learning and track-record claims remain blocked.
Issue #21's research-methodology audit is closed; its remaining trust-critical
actions are tracked by the provider/replay/evidence sequence and
`docs/QUANT_VALIDITY_AND_PERFORMANCE_AUDIT.md`. PostgreSQL remains
non-authoritative and Lightsail remains only the planned future destination.

Phase 3 now has a fail-closed PostgreSQL authority-cutover readiness gate. It
requires 30 consecutive exact comparison matches, local/database decision-ledger
count and tail-hash parity, applied migrations, a clean outbox/job state and a
fresh successful isolated restore rehearsal. Passing only creates fingerprinted
evidence for a later human decision; it cannot switch persistence, deploy AWS,
authorize spending or enable trading. No real cutover certificate has passed.

A retrospective Claude review found that the original gate could not prove an
unbroken chronological run and could measure restore age against a caller-
selected past assessment time. Policy v2 now requires contiguous sequence
numbers, strictly increasing non-future observation times, calculates only the
trailing exact-match run, uses the system clock for restore freshness and turns
malformed outbox/job counters into structured blocked evidence.

The configured FMP key has now been authenticated and capability-probed without
printing secrets or financial values. Delisted companies, as-reported
financials, dividend-adjusted prices and splits are available, while historical
S&P 500/Nasdaq membership changes and symbol changes return access-denied or
payment-required responses for the current account. FMP therefore remains supplementary and is not yet qualified
as the sealed replay provider; see `docs/FMP_PROVIDER_ASSESSMENT.md`.

Vendor support answers have now closed the immediate purchase question. FMP's
historical Nasdaq endpoint is not proven to be Nasdaq-100 membership, while
EODHD confirms point-in-time S&P 500 only, current-only Nasdaq-100, ticker
changes from 2022, no correction history and a one-month post-cancellation data
deletion duty. The governing decision is GBP 0 incremental recurring-data spend
until complete representative samples and terms support the combined mandate;
see `docs/HISTORICAL_PROVIDER_DECISION_2026-08-14.md`. Existing keys remain
supplementary and no purchase, provider approval or replay is authorized.

Phase 5 now has a separate immutable paper-broker cash snapshot boundary. It
distinguishes total, settled and unsettled USD cash, buying power and equity,
stores only hashes of the account reference and source payload, and requires an
explicitly confirmed paper environment. It neither relabels gross-pre-tax
performance nor estimates tax, submits orders, completes reconciliation or
enables live trading.

Daily simulated portfolio valuations now use an explicit as-of-close fill
boundary. Future BUY or SELL fills are excluded from earlier sessions, included
fills must come from the proposal set pinned by initial funding, and every
historical BUY fill requires one value at the exact shared close. This removes a
look-ahead path where later activity could previously block an earlier daily
valuation. Immutable paper-position-state ledgers consume exact FIFO lots across
decisions, support partial/full sales, reject short/oversell and pin the as-of
execution-chain prefix. The event-aware state applies supported stock splits
chronologically, calculates gross USD dividend entitlement at ex-time and
credits only payments evidenced by the exact close. Daily valuation pins that
state, reconciles BUY/SELL gross cash, recorded fees and paid dividends, and
values one current holding per ticker from adjusted shares and one official
close. Remaining historical BUY cost is disclosed proportionally but is not a
tax cost basis or realized-profit calculation. Uncertain/unsupported actions,
FX needs and ambiguous same-time events still fail closed. Simulated fill
timestamps remain controlled replay inputs, not independently reconciled broker
timestamps.

Phase 4 now also has a strictly read-only Alpaca paper-account adapter. It uses
dedicated paper-only environment variables, accepts only Alpaca's official paper
host, returns hashed account/payload references and has no order method. Because
the account response alone does not prove settled versus unsettled cash, it
requires separate exact settlement evidence before recording a cash snapshot.
