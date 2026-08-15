# Master Roadmap completion audit

Audited: 15 August 2026

This is the evidence-based distinction between implemented foundations and an
operationally complete platform. A closed issue or passing unit test proves only
the boundary that it covers. It does not prove that an external account is
connected, a real dataset is complete, a worker is running, or an investment
method works out of sample.

## Phase evidence matrix

| Phase | Proven current state | Evidence still required for operational completion | Current gate |
|---|---|---|---|
| 1 — foundation | Immutable decision/model-version records, protected Git workflow, transactional local change boundary and architecture/risk audit are implemented and tested. The legacy executable pickle market cache is replaced by immutable, integrity-checked Parquet snapshots that remain explicitly non-authoritative. Optional provider reads share secret-safe pacing, narrow retry and circuit-breaking controls. All five identified direct SEC JSON readers now share an official-host-only, redirect-rejecting, declared-user-agent boundary paced below the published SEC ceiling; strict locally bounded parsing and secret-free failures are covered with fakes. Remaining direct non-broker current-universe, public macro and RSS reads now use separate fixed-endpoint strict-text boundaries with post-receipt parsing limits; a deterministic `core/` scan confines three common direct-GET call shapes to reviewed shared or PAPER-read boundaries. The Yahoo cache and six legacy price loaders now share an injected yfinance caller boundary with one opaque SDK invocation, secret-free failures, validated positive-close frames and explicit false replay/survivorship flags. The legacy learning, expected-return, universe-ranking and portfolio market-exposure readers now use that same boundary with unchanged request shapes, horizons, date exclusivity, adjustment choices and safe no-data behaviour; three of those modules no longer import yfinance and the deterministic direct-import inventory records the reduced list. The three direct `fast_info` current-price readers — portfolio monitoring, legacy learning and universe symbol validation — now use a second boundary on the same provider key that validates the symbol before SDK construction, invokes the SDK once, retains only a validated positive finite scalar and redacts failures to a stable reason code. Per-symbol unavailable prices fail closed without consuming shared circuit credit; those modules no longer import yfinance and the inventory records the reduced list. The legacy research and multi-factor `.info` readers now use a third boundary on that provider key; it performs one opaque property read, retains only explicitly allowlisted validated scalars in a copied immutable observation, preserves exact integers and removes those two direct yfinance imports. Three further direct `.info` readers — competitor peer analysis, the Yahoo source fetch and the valuation engine — now use that boundary through an injected client, with its allowlist extended only by the four scalars those three callers already read. Formulas, configured peer lists, rankings, weights, defaults and result schema are unchanged; peers with no usable allowlisted profile field are omitted rather than populated with invented values, and subject ranking inputs receive the same strict numeric validation as peer inputs. Each read returns a fresh plain dict, failures log one fixed symbol-free message, and `CompetitorAnalyser` drops its yfinance import while the other two retain yfinance for untouched statement and estimate APIs; a deterministic receiver inventory pins that their profile reads no longer use it. A deterministic check covers the valuation engine's direct and indirect profile dependency. The formerly deferred `FinancialDataEngine.get_company_info` aggregator now reads through that same injected boundary, keeping yfinance only for the untouched statement and price-history APIs. A deterministic AST inventory first enumerated every constant profile field its broad `CompanyContext` consumers, direct fundamental consumers and engine helpers read, and the allowlist was extended only by those 22 justified fields, with `longBusinessSummary` under a field-specific bounded rule that still rejects ASCII control characters and never truncates. Symbols are validated before SDK construction, only validated plain dictionaries are cached, and each call returns a fresh caller-owned copy so no caller can mutate the cache or another analyser's inputs. The nested `companyOfficers` field is deliberately not admitted: its only consumer assigned the raw list whole to a `"CEO"` key, so no smaller deterministic sanitized contract could be derived, and that key is now present but empty rather than carrying raw nested provider data or an invented name. The inventory tests separate boundary receivers from unrelated mappings and prove every consumed field is either admitted or explicitly recorded as omitted. | Continued migration of remaining legacy and provider paths and authoritative PostgreSQL cutover before unattended operation. Transport controls do not authenticate source contents or prove point-in-time completeness; current-universe CSVs, current FRED graph reads, RSS and Yahoo observations are not replay evidence. Yahoo `fast_info` prices and `.info` profile values, including `currentPrice`, `regularMarketPrice` and `previousClose`, are unqualified late numbers, not official quotes, tradeable prices, prior closes, settlement evidence or account-bound values; admitting `previousClose` to the profile allowlist adds it no authority and does not resolve the Phase 4 previous-close question. Yahoo profile text, including `website`, is descriptive only and is never dereferenced or requested. No officer, executive or CEO identity is available from this boundary. The Yahoo boundaries cannot constrain or count yfinance's internal HTTP requests. | Safe local foundation substantially complete. |
| 2 — Codex + Claude | Repeatable issue/branch/test/PR workflow and usage-allocation policy exist. Targeted Claude CLI design challenges and final reviews are working again; accounting changes in PRs #193 and #194 were reviewed before merge. | Keep reviews narrowly scoped and use deterministic tests for routine proof; reserve duplicated frontier review for high-risk architecture, investment, security and accounting changes. | No product activation needed. |
| 3 — AWS | Docker/PostgreSQL local foundation, backup/restore, comparison mode, a reviewed Lightsail design and a fail-closed cutover-readiness gate exist. The gate proves a trailing run from contiguous, strictly time-ordered comparison evidence and measures restore freshness from the system clock. | Thirty real consecutive parity observations, a fresh populated restore certificate, explicit PostgreSQL-authority decision, AWS-spend approval, staging resources, least-privilege identities, alerts and deployment rehearsal. | Deliberately deferred until software/evidence foundations are ready; the readiness gate cannot perform a cutover. |
| 4 — Alpaca paper | Immutable paper-order proposals, deterministic local fills, methodology pre-flight, a live-prohibition checklist and a paper-host-only read-only account adapter exist. Exact operator-supplied Alpaca PAPER account bytes can now be retained content-addressed under a strict, owner-only, tamper-evident but explicitly unauthenticated admission boundary; no real payload has been admitted. A separate local reconciliation re-reads those bytes and proves only exact account/time/status/currency/cash/buying-power/equity consistency with a verified cash snapshot, without storing the account ID or amounts; its cash-snapshot dependency now rejects float money, unsafe files and backward writes. A new collector foundation constrains a future PAPER observation to four fixed `GET` requests—account, long positions, open orders, account—retains the exact responses privately, brackets the account identity within 30 seconds and records only local attestation; fake sessions are used in tests and no real request or admission has occurred. Its separate staging normalizer re-verifies the retained bytes and supports only exact, bounded account/long-position and price-bounded simple-order semantics. It writes no downstream ledger and remains blocked for settlement, prior-close and position/order-to-account binding evidence; invalid candidates retain reason codes but no monetary values. An inactive human-preregistered risk-policy ledger records exact order, position, gross-exposure, daily-loss and separate account/risk freshness limits without defaults; a one-way local stop follows the hashed account/stop identity and distinguishes trigger time from append time. Normalized snapshots and exact position/open-order quantity evidence conservatively account for long positions and pending BUY/SELL orders. Shadow risk, SELL quantity and BASE/PESSIMISTIC execution-cost calculations remain inactive. A combined assessment now reserves settled cash for all pessimistically stressed pending and proposed BUYs, rechecks exact SELL availability, independently bounds source/derivation ages, pins the complete stop prefix and records only blocked outcomes. | A separately authorized real read-only PAPER collection using securely stored credentials; authenticated semantic reconciliation of settlement, positions, open orders and previous-close evidence; human-selected real risk and execution-cost values; authenticated reference/market/volume evidence; complete applicable fee/borrow treatment; an authenticated enforcement assessment; paper submission/cancel/fill/reconciliation adapter; and externally anchored stop evidence before operational reliance. | Even an internal pass is `BLOCKED_EXTERNAL_EVIDENCE_REQUIRED`; local byte retention, collector attestation and exact normalization do not prove broker origin or account binding, the current settled-cash input remains operator-supplied, all durable evidence is synthetic/unreconciled/unauthenticated, limits remain unenforced, and order routing, paper submission and live trading remain disabled. |
| 5 — performance | Fixed-horizon outcomes, portfolio/benchmark returns, costs, exposures, turnover, volatility, drawdown, CAGR and Sharpe evidence boundaries exist. Immutable event-aware paper position state processes exact FIFO partial/full sales, stock splits and paid gross USD dividends chronologically; daily valuation reconciles adjusted quantities, closing prices, trades, dividends and cash. The authenticated replay emits exact sizing and portfolio-state traces, accounts for partial sales as one complete round trip and stores completed runs in a separately verified append-only audit ledger tied to source, engine, strategy and Git identities. A pre-access strategy specification binds the exact parameters, source file, entry point, Git revision and untouched evaluation window. A policy-derived execution profile fixes the BASE/PESSIMISTIC fees, spread, 0.10% minimum baseline slippage, latency, liquidity impact, participation and order-age assumptions; audit policy v3 rejects substitutions and requires the matching scenario pair for completeness. Valid no-trade runs remain mechanical simulation evidence, not a track record. Zero daily return is selected for future Sortino windows and has a fail-closed readiness gate. FMP and EODHD were capability-probed without exposing their keys. A paper-broker snapshot separates settled/unsettled cash from gross-pre-tax performance. A real conservative Massive campaign was preregistered before access and 36/36 fixed slices are retained in quarantine, but none is qualified or admitted. | A genuine future portfolio/window registration; unsupported or ambiguous corporate-action and FX policies where justified; future hit-rate/calibration and alpha policies; qualified point-in-time historical provider/data; sufficient real forward observations; a qualified campaign bound into a versioned authenticated replay profile, an admitted dataset and actual paired audited replay; qualified intraday evidence when a maximum-order-age window is shorter than the next daily bar; actual broker reconciliation, investor-specific taxes and remaining execution-realism limitations. | Current FMP and EODHD access do not provide the combined S&P 500/Nasdaq-100 membership evidence. The latest EODHD capability probe returned 403 for historical membership and did not qualify delisted-price schema; its published S&P/Dow add-on remains incomplete for this mandate. Public Databento security-master and corporate-action samples prove only partial schemas: neither required index-membership history nor a historical availability clock is present. The failed-assessment path now preserves empty, unproven evidence categories without inventing positives. Norgate covers both universes but is Windows-only. The Massive campaign preregistration is real, but no authenticated replay-plan strategy specification, dataset admission, replay audit record or track record exists. |
| 6 — controlled learning | Lesson, experiment, run, robustness, shadow, review, human-decision, activation, lifecycle/budget and emergency-stop boundaries exist. | Real eligible outcomes, actual sandbox worker/Hermes installation, a human-approved activation window and end-to-end execution using the implemented controls. | No Hermes agent is installed, scheduled or running. |
| 7 — political trading | Immutable point-in-time disclosure and aggregate-snapshot models account for publication and disclosure delay. A fail-closed activation preflight requires pinned terms evidence, affirmative use/access findings and a provider licence where applicable. Point-in-time issuer and committee-membership foundations reject hindsight, tampering and ambiguous identities without inferring committee relevance. | Qualified official-source determination or express licensed feed, separately reviewed ingestion connector, real mapping evidence and snapshot integration, preregistered committee-relevance/reliability methodology and out-of-sample signal testing. | Legal/terms and source decision required; passing the preflight cannot implement a scraper or connector. |
| 8 — Obsidian | Safe deterministic non-authoritative Markdown exporter exists. | User-selected vault and first controlled export; optional knowledge workflows only after authoritative records exist. | Local path/configuration decision required. |
| 9 — Buzz | Fail-closed local manifest defines channels, identities and denied powers. | User-approved local installation, key handling, relay/storage configuration and a security-tested pilot. | Intentionally deferred; Buzz is not installed or connected. |
| 10 — continuous improvement | Preregistered experiments, purged/embargoed folds, robustness enforcement, disposable container runner, a fail-closed active-pipeline image-approval ledger, a canonical sealed experiment-control contract, effectiveness cohorts and human promotion decisions exist. The runner requires one verified approval matching the preregistered image digest, Git revision and dependency lock, then separately snapshots and mounts the exact whitelisted experiment instructions. | Qualified sealed real historical data, an actual digest-pinned image built from the active pipeline, independent review evidence, controlled integration rehearsal, actual out-of-sample/robustness/shadow evidence, and later human decisions. | The image and control boundaries are tested, but no real image is built or approved and no experiment has run. Improvement cannot be measured until Phase 5 evidence and provider gates clear. |

Phase 4 now refines the row above with a provider-semantic resolution ledger.
Agreeing bracketed `last_equity` proves a stable provider value but not its exact
previous-trading-day date; undocumented caller-added date fields are ignored and
the monetary value is not stored. Alpaca's documented account `cash` does not
prove a settled/unsettled split, and credential presence plus
account-scoped endpoint semantics do not prove exact position/order account
binding without response identity or a signed transport receipt. Settlement
therefore remains `UNRESOLVED_PROVIDER_SEMANTICS`, previous close remains
`PROVIDER_VALUE_SUPPORTED_EFFECTIVE_TIME_UNRESOLVED`, account binding remains
unproven and the row's operational evidence requirements remain in force.

Phase 5 also has a fixed-mapping causal adapter for verified immutable
decisions. It derives the earliest eligible close from a canonical schedule,
pins the replay-source attestation, rechecks both against the exact
authenticated engine bars and requires every registered signal to be consumed
inside the evaluation window. It emits each recorded instruction once at that
bound close, so the guardrailed engine can execute no earlier than the following
bar.
This is useful execution evidence for decisions that already exist, but it does
not historically regenerate the research process, qualify source data, prove
investment value or create a track record.

A separate VectorBT 1.1.0 pilot now evaluates only caller-declared synthetic,
exactly daily, single-instrument long-only scenarios. It preserves prior-close
signal causality and next-open strategy execution, pins fees, at least 0.10%
adverse slippage, position limits and stop-market risk behavior, and writes only
to a non-admissible pilot ledger. Its stop is inactive on the entry bar, cash
settlement is unmodelled, floating-point results are not Decimal replay parity,
and all benchmark and Sharpe outputs are diagnostic. It replaces no authenticated
replay, evidence, audit, risk or broker boundary, and no existing simulator was
removed. Adding the pinned pilot dependency changes the dependency lock input;
because no Phase 10 image exists or is approved, any future image approval must
use the then-current lock rather than an earlier digest.

The faithful active-pipeline route now has a ledger-issued sealed invocation
boundary. Its preregistered context binds the verified replay plan and approved
no-network image to every research-engine dependency, all active-route
components, a strictly ordered in-window as-of schedule, Git/dependency
identities and immutable learning/source-evidence digests. After the real data-
access embargo, the ledger—not a caller-supplied context—selects one exact
schedule index and issues an inert invocation only after re-hashing the 22
injected engine sources, six active-route component sources, authenticated
source ledger/blob manifest and learning-state file. The pipeline revalidates
those identities and bytes at consumption, uses the frozen timestamp and sealed
paths, forbids saving and ambient engine fallback, and makes all five formerly
degraded stage failures fatal in replay mode. Ordinary research behaviour is
preserved. This boundary does not itself stop a leaf engine opening a socket or
file; the approved no-network/read-only container must enforce that process
boundary. No real image or dataset has been admitted, no production invocation
has been issued, and no replay or performance claim exists.

An inert input-coverage contract now states, for each of the 22 sealed engine
keys, which dependency kinds it combines — admitted dataset artifact roles,
earlier stage output, the sealed learning state, sealed source evidence — and
compares those needs with the authenticated roles actually present in one
verified dataset admission. Historical v1 admissions retain their original
role alphabet. New v2 admissions may additionally carry six point-in-time roles
for analyst/earnings estimates, corporate events, news, macro series,
specialist research and supplemental-provider evidence. This is a vocabulary
change, not a data claim: an admission containing only the six required roles
still covers only market regime and leaves eight of nine artifact-backed engines
uncovered; market signals additionally needs optional raw daily session bars.
The coverage record is version-aware and permission alone never counts as
presence. Until genuine authenticated artifacts are admitted or the
preregistered pipeline scope is formally narrowed, a faithful replay of the
whole research route is not achievable. The contract reads no dataset bytes,
admits nothing and executes nothing.

A separate provider-neutral access boundary can now open the exact
content-addressed whole-dataset bytes for one fully covered artifact-backed
engine, but only from a chain-verified admission, from the same authenticated
content store that admission verified, and only when the artifact was public by
an explicit as-of time. Without a sealed invocation that time is caller-asserted
and suitable only for inert inspection; with one it must equal the pinned clock
and use the pinned paths. This is authentication, not a
point-in-time semantic claim: every returned artifact remains explicitly not
row-validated and not engine-ready, and the boundary is not connected to the
research pipeline. A provider-neutral canonical envelope now covers every
active-pipeline dataset role with explicit effective, availability, retrieval,
provider-identity, raw/normalized hash and per-observation cutoff fields. Its
engine-scoped gate rejects an entire batch if any observation was unavailable
at the exact decision timestamp and can bind that timestamp to a sealed
invocation. It deliberately keeps provider-payload semantics, source
authentication, vintage selection, role completeness, dataset-commitment
binding and engine readiness false. Realized-data roles reject future-effective
observations; explicitly scheduled event/state/calendar roles may be known
before their future effective instant. Provider-specific normalization and
qualified role payload schemas therefore remain prerequisites to engine adapters
rather than being guessed from synthetic fixtures.
One provider-specific staging pilot now covers Massive's documented unadjusted
stock custom-bar JSON shape and an exact CSV projection. It validates a single
`RAW_DAILY_SESSION_BARS` role through the same canonical cutoff and hash checks
without weakening the complete-engine role-coverage gate. Because the public
contract does not establish historical publication timestamps or stable row
identities, the pilot dates both effectiveness and availability at local receipt
and labels the derived bases unqualified. A successful sample therefore remains
non-authenticated, semantics-unqualified, incomplete and not engine-ready.
The once-executed pre-access Massive boundary fixes the exact AAPL/MSFT/SPY
basket, acquisition window, ordered train/validation/untouched-test split,
strategy source/version, complete parameter space and exact evaluation/selection
protocol in an owner-only, one-campaign hash-chain ledger. The tool derives the
clean Git revision and strategy hash rather than accepting them as operator
claims. A separate content-addressed raw-response quarantine accepts only exact
split-contained 31-day slices and binds raw/header hashes, non-secret query,
HTTP status, request chronology, access counters, asserted-but-unauthenticated
public terms and preregistration provenance. Its storage must
be disjoint from admitted-source storage. The new modules have no direct
admission, simulation-engine or broker import/wiring, cannot issue source,
replay or synthetic-pilot attestations, and their wrapper fails the synthetic
pilot's exact-type boundary. Deterministic
fake responses prove the boundary. The one permitted real plan has since
captured all 36/36 fixed multi-ticker response slices owner-only in quarantine;
none was admitted. These local chains are not externally anchored and do not
claim to detect deletion of every local artifact.
The user has selected a fixed conservative 20/50/20 trend/momentum baseline for
the next campaign. Its new self-contained Decimal adapter binds the complete
parameters, disables search, maps only scores 80/90 to long entry and scores at
or below 60 to exit, holds on neutral or insufficient history and rejects
non-causal or mixed-symbol history. It does not replace the legacy strategy or
the authenticated engine and has no data, VectorBT, broker or network path.
AAPL and MSFT remain separate candidate instruments and SPY is benchmark-only.
The user subsequently approved the fixed 1 August 2025 through 31 July 2026
window and its exact contiguous splits, one-test protocol, BASE/PESSIMISTIC
costs, USD 100,000 simulated starting cash, 25% position-notional cap, 1%
per-trade/aggregate risk, 14-bar ATR/2x stop and per-candidate success gates.
The hashable inert campaign contract now represents those choices and builds an
engine-policy-v3 profile that enforces the position cap using the maximum
configured adverse entry price; replay audit revalidates that cap against actual
fills while retaining legacy v2 audit interpretation. The authenticated
execution-profile resolver now exposes an explicit campaign-v2 route that
accepts only the immutable preregistration ledger plus an ID, re-verifies that
ledger internally and requires the exact approved strategy, parameters, windows,
policy hash, cost-policy identity and scenarios before deriving the 25% cap and
1% aggregate risk limit.
The absent-campaign route preserves the exact legacy-v1 profile, and campaign-v2
is never selected implicitly. The assumptions are uncalibrated and authorize no
performance claim. After PR #252 merged, clean main produced the one permitted owner-only
Massive preregistration, `HQP-DCE89C243F16E9C659B3B339A08DE350` (record hash
`d20899e47335d6fa4ee2a5773fcd1cf12f1b921018a775a4525e7bc166a4dc6d`).
The retained official public terms-page bytes are hashed, but entitlement and
historical replay permission remain unresolved. The authorized collection
captured all 36/36 fixed slices with zero missing; the final capture-chain record
hash is `3ccee45a3cb33a42e1d13adbf8f86b5e187c004b0950a8b62bb0fee7a1a20b98`.
Twenty-seven TRAIN/VALIDATION captures normalize to 145 TRAIN and 43 VALIDATION
sessions per symbol with no duplicate IDs or out-of-slice observations.
All staged availability/effectiveness bases are local-retrieval-only and
unqualified as of 15 August 2026, too late for the registered 2025–2026 decision
window. A new store-issued wrapper closes the local normalizer-substitution gap:
it re-verifies the capture and blob, derives retrieval time, symbol, split and
request window only from the immutable record, checks every normalized bar
against them and records a binding hash. Direct adapter output remains explicitly
unbound. This proves no provider origin, semantics or historical availability.
The user then authorized a baseline-only Research Index & Availability
Exemption. Its separate owner-only hash chain identifies the exact
preregistration/capture parents and labels index membership, 16:00 New York bar
availability and 09:30/16:00 session times as human assumptions, never provider
evidence. Exemption record hash
`8eaf1d499213f0abc324b3265b1c6a184b4a083621a01ed0b2808ef89b624513`
and consumption record hash
`dbc49943fe6b1ac922a59191e21145ef2949cb2887825917d81e81700de23534`
make those local chain points explicit. It copied only TRAIN/VALIDATION into a disjoint local research store;
canonical dataset admission, authentication and every performance/broker/order/
trading authority stayed false. VectorBT evaluated only the one preregistered
20/50/20 configuration, not a newly invented search space. The single untouched
package was consumed at Git `5ca19d64e45340ea577400b5ca8658d37d41cc38` and
cannot be rerun. The GuardrailedBacktestEngine result hash is
`05adfe5236ea7bd50c57b2e04326ec421253365c4b51e9cb83ceb86b86fe4ac1`:
AAPL returned 0.5341% BASE/0.3971% PESSIMISTIC with two trades; MSFT returned
-1.0886%/-1.2162% with one; SPY's matching price-return diagnostic was 1.2225%.
MSFT's negative result and single trade independently fail absolute and trade-
count gates. Final review found the runner omitted the preregistered one-
observation purge and embargo, and the SPY price diagnostic was timing-
misaligned rather than the registered complete relative-return measure. The
consumed result is permanently protocol-nonconformant: it cannot establish a
valid preregistered or relative test and cannot be rerun. Future execution now
derives warm-up, purge, embargo and one-shot enforcement from the campaign
contract. Missing corporate-action/total-return evidence, entitlement, replay
permission, provider semantics and external anchoring remain unresolved, so
this failed diagnostic is not a track record or promotion evidence. The final
focused Claude Opus correction review returned PASS with no release blockers.
Campaign v1 is now formally archived as `CLOSED_PROTOCOL_NONCONFORMANT` in the
same append-only chain. Closure record
`bc245c30a4dc744f30ccd2bba9381a213dc18d0fe867434ad1c225bd7c0d44d5`
binds all four reviewed v1 records, both protocol defects and the remediation
scope of PRs #255 and #257. It prohibits any further v1 admission, screening,
evaluation or untouched reuse. The original-scope AAPL/MSFT/SPY Research Index
& Availability Exemption remains a recorded human assumption only; no scope
extension or provider, performance, broker, order or trading authority was
granted.

Campaign v2 proposal
`ff4ca9a0919e43044337e609140197bb5869681e47e226720b12df64075cc82b`
was explicitly approved and registered through the separate v2-only schema.
Approval record
`09a2acfc37629e9c7287c8a875de0b52209f65459efc4478ca3d0ed93ffc06df`,
exemption-extension record
`8a25bf21c4f5f945bac32181d1556503f2f58bcd44e3b220909c06b3d350388d`
and preregistration `HQP2-074A7002D313E7C24C64EADE9DCDF4BB` / record
`cc626aa7c4aedab63bce51cae5dd9e90a1f5d81f00bd46a49fb956cfe4dcf210`
form one immutable chain pinned to clean Git `d5476c6`, the exact v1 closure,
preregistration and entitlement evidence. The extension is authorized but not
applied to any capture, and no future assumption has been asserted. The empty
owner-only v2 raw root is disjoint from v1 and known admitted research storage.
Provider access, capture activation, data opening and evaluation remained false.
On 15 August 2026 the package was found calendar-invalid for the requested
activation: VALIDATION had not ended and UNTOUCHED_TEST was wholly future. It is
therefore retired by code and closed by terminal supersession record
`3f8f1c3e9ddc85f3e34ed484d7162b565525d6eb73488bb1c99c73ab01f845e5`
at clean Git `8cd3cff`; it was never activated and is not reusable.

Revision-1 proposal
`7c43094e64f324d6987b67a25d03626eb4defe4096ae1b135e1c0319b60fc0d5`
is registered by that terminal record for approval only. Its completed,
v1-disjoint window is 1 August
2024-31 July 2025: TRAIN through 28 February, VALIDATION March-April and
UNTOUCHED_TEST-role May-July. Because every date was already historical at
proposal time, the final split is explicitly a `SEALED_RETROSPECTIVE_TEST`, not
genuinely future untouched evidence. The package makes no assertion that market
outcomes were unknown to researchers and grants no promotion or track-record
authority. It preserves the unchanged AAPL/MSFT 20/50/20 strategy, SPY
diagnostic, 25% position cap, conservative costs, 50-observation warm-up,
one-observation purge, one-observation embargo, one test execution and existing
success thresholds. Approval, a distinct revision preregistration, entitlement
revalidation and separate capture authorization remain mandatory before any
provider request. No API key is requested and no provider call or evaluation
is allowed by this proposal registration.
The older strict parser for execution-critical price, calendar, corporate-
action, delisting, universe-membership and daily-bar roles now enforces the
same content-store binding. Schemas for the six newer research roles will be
added one role at a time only after qualified provider samples establish stable
provider-neutral semantics; no speculative field contract is treated as
progress.

## Current provider-spending decision

FMP and EODHD support answers now confirm that neither currently proves the
combined S&P 500/Nasdaq-100 point-in-time mandate. EODHD additionally confirmed
limited ticker history, no correction history and post-cancellation deletion
duties. The incremental recurring-data budget is therefore **GBP 0** until
complete representative evidence supports a specific purchase. This decision
is recorded in `docs/HISTORICAL_PROVIDER_DECISION_2026-08-14.md`; it does not
prevent continued use of current/free entitlements for supplementary research.

## Cross-cutting claims that remain prohibited

- The platform has not demonstrated repeatable out-of-sample investment value.
- Local simulation, backtests and broker paper fills are not a live track record.
- No phase may infer unavailable historical data or silently backfill a decision.
- No agent may promote itself, change its limits or activate external systems.
- AWS, Alpaca, Capitol Trades, Obsidian, Buzz and Hermes are not operationally
  connected merely because their safe boundaries or plans exist.
- Autonomous real-money trading remains unsupported and disabled.

## Current critical path

1. Register the selected zero-return Sortino target only when a genuine future
   portfolio version and measurement window exist; do not invent either.
2. **Campaign v2 revision awaits approval; provider access remains blocked:**
   the original v2 controls are retired by code and terminally superseded for
   their incomplete and future dates. Review and explicitly approve revision-1
   hash `7c43094e...`
   before a distinct revision preregistration is appended. Only then revalidate
   entitlement and separately authorize bounded quarantine capture. Treat its
   last split as a one-shot sealed retrospective test, never as genuinely future
   evidence or promotion authority. Historical availability, stable identity,
   replay permission and required provider roles remain unresolved.
3. Obtain exact entitlement/sample confirmation for a cloud-native Mac/Linux-
   compatible HTTPS REST or versioned flat-file provider covering both index
   histories, symbol lineage, delisted prices and terminal outcomes. Windows-
   only local updater paths, including Norgate plus a Windows extraction layer,
   are excluded from the selected architecture. Do not buy an incomplete S&P-
   only product for the combined mandate. See
   `docs/HISTORICAL_PROVIDER_OPTIONS.md` and use the exact evidence request in
   `docs/HISTORICAL_PROVIDER_SAMPLE_REQUEST.md`. Credentials must not enter
   chat, Git, tests or model prompts.
4. Qualify and, only after separate human approval, admit a genuinely sealed
   point-in-time dataset covering membership removals, delistings, terminal
   outcomes, corporate actions, total return, corrections and market calendars.
5. Use the recorded-decision adapter for bounded causal execution diagnostics,
   define canonical role schemas and observation-level cutoff filters without
   inventing provider evidence, then supply the ledger-issued active-pipeline
   invocation with qualified historical adapters and run it through the approved no-
   network/read-only container under its frozen preregistration. Record only
   mechanical out-of-sample and robustness results. Do not treat the adapter or
   the sealed invocation boundary as a substitute for replaying research
   generation.
6. Accumulate the fixed future shadow/paper evidence before any strategy
   promotion decision or performance claim.

AWS deployment, an actual Hermes worker and broker paper submission remain
downstream of these software and evidence foundations. They must not be used to
create the appearance of progress before the investment process can be tested
faithfully.
