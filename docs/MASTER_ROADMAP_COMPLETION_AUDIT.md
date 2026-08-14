# Master Roadmap completion audit

Audited: 14 August 2026

This is the evidence-based distinction between implemented foundations and an
operationally complete platform. A closed issue or passing unit test proves only
the boundary that it covers. It does not prove that an external account is
connected, a real dataset is complete, a worker is running, or an investment
method works out of sample.

## Phase evidence matrix

| Phase | Proven current state | Evidence still required for operational completion | Current gate |
|---|---|---|---|
| 1 — foundation | Immutable decision/model-version records, protected Git workflow, transactional local change boundary and architecture/risk audit are implemented and tested. The legacy executable pickle market cache is replaced by immutable, integrity-checked Parquet snapshots that remain explicitly non-authoritative. Optional provider reads share secret-safe pacing, narrow retry and circuit-breaking controls. | Continued migration of remaining legacy and provider paths and authoritative PostgreSQL cutover before unattended operation. | Safe local foundation substantially complete. |
| 2 — Codex + Claude | Repeatable issue/branch/test/PR workflow and usage-allocation policy exist. Targeted Claude CLI design challenges and final reviews are working again; accounting changes in PRs #193 and #194 were reviewed before merge. | Keep reviews narrowly scoped and use deterministic tests for routine proof; reserve duplicated frontier review for high-risk architecture, investment, security and accounting changes. | No product activation needed. |
| 3 — AWS | Docker/PostgreSQL local foundation, backup/restore, comparison mode, a reviewed Lightsail design and a fail-closed cutover-readiness gate exist. The gate proves a trailing run from contiguous, strictly time-ordered comparison evidence and measures restore freshness from the system clock. | Thirty real consecutive parity observations, a fresh populated restore certificate, explicit PostgreSQL-authority decision, AWS-spend approval, staging resources, least-privilege identities, alerts and deployment rehearsal. | Deliberately deferred until software/evidence foundations are ready; the readiness gate cannot perform a cutover. |
| 4 — Alpaca paper | Immutable paper-order proposals, deterministic local fills, methodology pre-flight, a live-prohibition checklist and a paper-host-only read-only account adapter exist. | User-created paper account, securely stored credentials, successful account check, paper submission/cancel/fill/reconciliation adapter and broker-specific kill/exposure controls. | Explicit broker-account connection decision required; live endpoint and order submission remain unsupported. |
| 5 — performance | Fixed-horizon outcomes, portfolio/benchmark returns, costs, exposures, turnover, volatility, drawdown, CAGR and Sharpe evidence boundaries exist. Immutable event-aware paper position state processes exact FIFO partial/full sales, stock splits and paid gross USD dividends chronologically; daily valuation reconciles adjusted quantities, closing prices, trades, dividends and cash. The authenticated replay emits exact sizing and portfolio-state traces, accounts for partial sales as one complete round trip and stores completed runs in a separately verified append-only audit ledger tied to source, engine, strategy and Git identities. A pre-access strategy specification binds the exact parameters, source file, entry point, Git revision and untouched evaluation window. A policy-derived execution profile fixes the BASE/PESSIMISTIC fees, spread, 0.10% minimum baseline slippage, latency, liquidity impact, participation and order-age assumptions; audit policy v3 rejects substitutions and requires the matching scenario pair for completeness. Valid no-trade runs remain mechanical simulation evidence, not a track record. Zero daily return is selected for future Sortino windows and has a fail-closed readiness gate. FMP and EODHD were capability-probed without exposing their keys. A paper-broker snapshot separates settled/unsettled cash from gross-pre-tax performance. | A genuine future portfolio/window registration; unsupported or ambiguous corporate-action and FX policies where justified; future hit-rate/calibration and alpha policies; qualified point-in-time historical provider/data; sufficient real forward observations; a strategy preregistered before access, an admitted dataset and actual paired audited replay; qualified intraday evidence when a maximum-order-age window is shorter than the next daily bar; actual broker reconciliation, investor-specific taxes and remaining execution-realism limitations. | Current FMP and EODHD access do not provide the combined S&P 500/Nasdaq-100 membership evidence. The latest EODHD capability probe returned 403 for historical membership and did not qualify delisted-price schema; its published S&P/Dow add-on remains incomplete for this mandate. Norgate covers both but is Windows-only. No real strategy specification, replay audit record or track record yet. |
| 6 — controlled learning | Lesson, experiment, run, robustness, shadow, review, human-decision, activation, lifecycle/budget and emergency-stop boundaries exist. | Real eligible outcomes, actual sandbox worker/Hermes installation, a human-approved activation window and end-to-end execution using the implemented controls. | No Hermes agent is installed, scheduled or running. |
| 7 — political trading | Immutable point-in-time disclosure and aggregate-snapshot models account for publication and disclosure delay. A fail-closed activation preflight requires pinned terms evidence, affirmative use/access findings and a provider licence where applicable. Point-in-time issuer and committee-membership foundations reject hindsight, tampering and ambiguous identities without inferring committee relevance. | Qualified official-source determination or express licensed feed, separately reviewed ingestion connector, real mapping evidence and snapshot integration, preregistered committee-relevance/reliability methodology and out-of-sample signal testing. | Legal/terms and source decision required; passing the preflight cannot implement a scraper or connector. |
| 8 — Obsidian | Safe deterministic non-authoritative Markdown exporter exists. | User-selected vault and first controlled export; optional knowledge workflows only after authoritative records exist. | Local path/configuration decision required. |
| 9 — Buzz | Fail-closed local manifest defines channels, identities and denied powers. | User-approved local installation, key handling, relay/storage configuration and a security-tested pilot. | Intentionally deferred; Buzz is not installed or connected. |
| 10 — continuous improvement | Preregistered experiments, purged/embargoed folds, robustness enforcement, disposable container runner, a fail-closed active-pipeline image-approval ledger, a canonical sealed experiment-control contract, effectiveness cohorts and human promotion decisions exist. The runner requires one verified approval matching the preregistered image digest, Git revision and dependency lock, then separately snapshots and mounts the exact whitelisted experiment instructions. | Qualified sealed real historical data, an actual digest-pinned image built from the active pipeline, independent review evidence, controlled integration rehearsal, actual out-of-sample/robustness/shadow evidence, and later human decisions. | The image and control boundaries are tested, but no real image is built or approved and no experiment has run. Improvement cannot be measured until Phase 5 evidence and provider gates clear. |

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
2. Obtain exact entitlement/sample confirmation for a Mac/Linux-compatible
   provider covering both index histories, symbol lineage, delisted prices and
   terminal outcomes; compare it with Norgate plus a Windows extraction layer.
   Do not buy an incomplete S&P-only product for the combined mandate. See
   `docs/HISTORICAL_PROVIDER_OPTIONS.md`. Credentials must not enter chat, Git,
   tests or model prompts.
3. Qualify and, only after separate human approval, admit a genuinely sealed
   point-in-time dataset covering membership removals, delistings, terminal
   outcomes, corporate actions, total return, corrections and market calendars.
4. Use the recorded-decision adapter for bounded causal execution diagnostics,
   implement provider-neutral point-in-time engine-adapter boundaries without
   inventing provider evidence, then supply the ledger-issued active-pipeline
   invocation with qualified historical adapters and run it through the approved no-
   network/read-only container under its frozen preregistration. Record only
   mechanical out-of-sample and robustness results. Do not treat the adapter or
   the sealed invocation boundary as a substitute for replaying research
   generation.
5. Accumulate the fixed future shadow/paper evidence before any strategy
   promotion decision or performance claim.

AWS deployment, an actual Hermes worker and broker paper submission remain
downstream of these software and evidence foundations. They must not be used to
create the appearance of progress before the investment process can be tested
faithfully.
