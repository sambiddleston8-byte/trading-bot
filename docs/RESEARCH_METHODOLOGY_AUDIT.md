# Investment research methodology audit

**Issue:** #21

**Audit scope:** the active end-to-end research and portfolio process, plus the
historical tests that might be mistaken for validation of that process.

**Status:** local audit and independent Claude challenge complete; corrective
implementation not yet started.

**Trading boundary:** research and paper records only. No broker connection,
order submission, AWS schedule or real-money path is enabled.

## Plain-English conclusion

The platform has a promising, unusually transparent research structure. It
collects several kinds of evidence, challenges the positive thesis, blocks
incomplete records, checks valuation quality, records decisions immutably and
keeps portfolio constraints separate from research.

It is **not yet ready to submit even paper orders to Alpaca**. The reason is not
that the software cannot construct an order. The reason is that the current
tests do not yet demonstrate that the full, current research method would have
made sound decisions using only information genuinely available at each past
decision date. Several confidence and ranking formulas are also based on
reasonable-looking but uncalibrated rules, and correlated inputs are counted
more than once.

The research method should improve throughout the project because it is the
platform's backbone. This audit must not stop essential infrastructure work.
The roadmap can continue with record-only execution, performance-data and
operational foundations in parallel. Actual paper-order submission stays
disabled until the trust-critical controls identified below are complete.

## Current process map

1. **Choose companies:** load the present S&P 500 and Nasdaq-100 lists, with a
   hand-selected fallback list.
2. **Collect evidence:** obtain company financials, analyst estimates, market
   data, news, catalysts, market context and specialist research.
3. **Analyse:** score fundamentals, build bear/base/bull DCF valuations and
   generate market and risk signals.
4. **Challenge:** test catalysts, run an adversarial thesis review and audit
   evidence completeness and consistency.
5. **Synthesise:** combine the component results into an investment-case score
   and recommendation.
6. **Make a master decision:** apply hard safety gates, confidence rules and a
   further opportunity score.
7. **Construct a portfolio:** admit only eligible records, rank candidates,
   apply risk/sector/position constraints and record the resulting decision.
8. **Monitor and learn:** retain decision and portfolio history for later outcome
   attribution. Automated learning is deliberately inactive without sufficient
   closed paper evidence.

## What is already good and should be preserved

- Incomplete research, failed evidence audits, fatal thesis findings and failed
  valuations are blocked rather than silently accepted.
- The thesis challenger and catalyst validation make the positive case face
  contrary evidence.
- Research confidence is explicitly described as **evidence quality**, not the
  probability that a share price will rise.
- The DCF exposes assumptions and checks whether terminal value dominates the
  result.
- Deterministic rules, component versions, Git revisions, UTC times and data
  cutoffs make decisions inspectable.
- The immutable decision ledger and portfolio versioning create a sound base
  for later outcome attribution.
- Learning adjustments remain inactive when there is insufficient evidence.
- Paper-only, worker and AWS boundaries prevent premature autonomy.

## Ranked findings

### Critical — the current strategy has no faithful point-in-time validation

The available backtests do not recreate the current research pipeline. The
point-in-time backtest uses a separate simplified score with valuation fixed at
50. The walk-forward backtest uses a historical moving-average signal. Other
backtests accept precomputed scores or use older signal engines. Passing these
tests therefore does not prove that the current fundamental, DCF, catalyst,
thesis, evidence, master-decision and portfolio rules work together.

The historical-fundamentals cache selects records by a general `Date`, not by a
separate timestamp showing when the filing or data became publicly available.
That cannot rule out look-ahead leakage. The current universe sources provide
today's index members; using those names in older periods would omit companies
that failed, were acquired or left the index, creating survivorship bias.

**Required repair:** build one historical replay harness for the *current*
pipeline. Each input needs an `available_at` time, source identity, retrieval
time and immutable payload reference. Use the constituents actually eligible at
each date, retain delisted securities, use adjusted returns consistently and
include transaction costs. Keep a final untouched out-of-sample period.

### High — “forecast confidence” does not validate forecast accuracy

`ForecastValidator` compares analyst revenue growth with analyst EPS growth and
calls close values `HIGH` confidence. Agreement between two related estimates
from the same provider is not independent validation, and revenue and EPS can
properly grow at different rates as margins change. The result feeds valuation
quality and portfolio confidence, so the wording overstates what has been
established.

**Required repair:** rename this check to analyst-estimate consistency and stop
using it as evidence of accuracy. Forecast confidence must come from historical
forecast errors, source independence, estimate dispersion, age and coverage.

### High — correlated evidence is counted repeatedly

The synthesis score includes fundamentals, valuation, catalysts, thesis and
data quality. The master decision starts with that score and adds expected
return, catalysts, thesis effects, technical/risk signals and evidence quality
again. Portfolio ranking combines master conviction with research confidence.
It then calculates a third `decision_rating` from many of the same inputs and
uses that rating to sort candidates, so this is not merely a display problem.
The position-sizing calculation reuses several inputs again.

This can make one underlying assumption appear to be several independent votes
and gives the final decimals more precision than the evidence supports.

**Required repair:** create a factor lineage table and one authoritative
opportunity score. Classify every later calculation as a gate, rank adjustment,
risk constraint or display-only explanation. A factor may affect each economic
question once unless an explicitly tested interaction justifies reuse.

### High — DCF assumptions are transparent but not sufficiently grounded

The discount rate starts at a fixed 9% and is adjusted in coarse beta and
debt-to-equity bands. It is not a full cost-of-capital calculation. Terminal
growth defaults to 3%. Bear and bull cases use fixed growth and margin offsets,
without observed error distributions or probabilities. Growth heavily weights
analyst and Yahoo fields that may not be independent, while missing growth
falls back to 8%.

The DCF is useful as a transparent scenario tool, but its base expected return
must not yet be treated as a calibrated forecast.

**Required repair:** version and date all assumptions; calculate or document the
risk-free rate, equity premium, beta, debt cost, tax and capital weights; show a
sensitivity matrix; derive scenario ranges from historical forecast errors and
sector economics; and distinguish price upside from probability-weighted total
return.

### Medium — evidence audits focus more on presence than timing and independence

The audit is effective at blocking missing or internally inconsistent records,
but a completed source is not automatically timely, independent or accurate.
Source breadth can increase confidence even when providers ultimately depend on
the same upstream data.

**Required repair:** attach `effective_at`, `available_at`, `retrieved_at`,
provider, upstream origin and content hash to decision-relevant evidence. Score
independent upstream origins and past reliability, not just completed source
counts.

### Medium — latest research files are mutable operational views

The pipeline saves one `data/research/pipeline/{ticker}.json` file, replacing the
previous latest view. The canonical contract can also rebuild an old pipeline
record under the current policy. These behaviours are convenient for the user
interface but unsafe as the only historical truth.

**Required repair:** retain the latest view, but also write a content-addressed,
append-only research artifact for every run. A historical decision must always
refer to the exact original artifact and original policy; policy migration must
create a new derived record rather than reinterpret history.

### Medium — the chosen universe needs an explicit mandate and historical record

The live universe uses useful but unofficial GitHub-hosted index files and has a
large-cap fallback list. The sources have retrieval timestamps but no saved
revision or content hash. The process also lacks an explicit statement of
eligible markets, liquidity, security types, exclusions and rebalance rules.

**Required repair:** define the investable-universe policy, archive every
constituent snapshot with provenance and hashes, validate source changes and
build historical membership before performance claims.

## Active versus older code

The active decision route is `InvestmentResearchPipeline` → canonical research
contract → `MasterPortfolioDecisionEngine` → `PortfolioEngine`. The standalone
`ExpectedReturnEngine`, `WalkForwardBacktest`, `PointInTimeBacktest` and several
other backtest classes are useful experiments or older components, but they are
not evidence that the active route is validated. They should be labelled
clearly until either integrated into the current replay harness or retired.

## How this work fits the phased roadmap

Infrastructure and research quality should now proceed as two controlled,
coordinated workstreams.

### Infrastructure work that can continue now

- finish record-only execution and performance-record foundations without an
  Alpaca network submission path;
- build the Phase 5 attribution data model and deterministic measurement jobs;
- retain the existing database, job-locking, backup, logging and future AWS
  boundaries;
- add an automated pre-flight guard so a future paper-submission path cannot be
  enabled while trust-critical methodology gates are open; and
- continue normal tests, GitHub reviews and immutable version tracking.

### Trust-critical work required before paper-order submission

1. Add genuine point-in-time availability metadata to decision inputs.
2. Correct the misleading forecast-confidence label and stop estimate agreement
   from acting as evidence of forecast accuracy.
3. Document factor lineage and remove repeated correlated inputs from the
   candidate-selection path.
4. Establish historical constituent membership, including removals and delisted
   outcomes.
5. Build a faithful replay of the active decision route, with an untouched
   out-of-sample period and realistic costs.

### Point-in-time input-provenance foundation

The first trust-critical repair now records an immutable source-input manifest
for each verified investment decision. It binds the exact canonical research
snapshot to financial statements, market price, analyst estimates,
news/catalysts, technical history, market-regime data and macro data. Each
available family preserves effective, public-availability and retrieval times,
plus provider, endpoint, HTTPS source, source SHA-256 and exact source locator.

Evidence retrieved after canonical generation is rejected rather than treated
as an original input. Missing families remain explicit and make the manifest
incomplete. A complete provenance manifest proves reproducibility only: it does
not validate forecasts, clear the methodology gate, recommend a position,
enable learning or authorize broker submission. Pipeline capture and the other
four trust-critical repairs remain separate work.

### Investment-method optimisation that can continue later

- archive content-addressed research runs and richer source provenance;
- refine WACC, terminal growth, scenario distributions and sensitivity tables;
- measure every forecast and decision at the Master Roadmap's defined horizons;
- test which engines add predictive value by regime, sector, size and horizon;
  and
- allow bounded learning only when a frozen baseline is beaten out of sample
  and the change passes Codex, Claude and human review.

## Success and failure rules for this workstream

The methodology is ready for paper-order submission only when all of the
following are true:

- the current pipeline can be replayed without future information;
- historical membership and delisted outcomes are included;
- every decision can reproduce its exact inputs, code and policy;
- forecast confidence is calibrated against realised errors;
- scoring factors have documented lineage and no unexplained double counting;
- a frozen out-of-sample evaluation passes predeclared return, drawdown,
  turnover and data-quality thresholds;
- a deliberately pessimistic cost and slippage test does not invalidate the
  result; and
- independent challenge review finds no unresolved Critical or High issue.

Failure of any item keeps broker submission disabled. A favourable backtest on
an altered test period is not permission to proceed; it becomes a new
hypothesis requiring another untouched evaluation period.

## Independent challenge result

Claude performed a read-only review of the audit against the active pipeline,
forecast, valuation, universe, portfolio and backtest code. It confirmed every
checked factual finding. It identified one understatement: the third-layer
decision rating changes candidate ordering, rather than being merely a display
score. It agreed that infrastructure can continue in parallel, while the five
trust-critical controls above should remain mandatory before order submission.

This document is a useful audit record, but a static document is not an ongoing
technical control. The future paper boundary therefore needs an automated
pre-flight gate that fails closed if these requirements are not satisfied.

## Roadmap effect

This audit brings forward the research-method review already distributed across
the Master Roadmap's validation, backtesting, learning and improvement phases.
It does not replace the roadmap, reorder it or demand that every research
improvement finish first. It supplies a maintained improvement backlog and the
quality gate needed before the existing Phase 4 paper-order boundary is allowed
to submit to Alpaca. Infrastructure and Phase 5 measurement foundations may
continue while those gates are repaired. AWS preparation remains documented;
actual always-on deployment still requires its separate roadmap decision.
