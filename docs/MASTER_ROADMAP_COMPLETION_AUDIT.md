# Master Roadmap completion audit

Audited: 13 August 2026

This is the evidence-based distinction between implemented foundations and an
operationally complete platform. A closed issue or passing unit test proves only
the boundary that it covers. It does not prove that an external account is
connected, a real dataset is complete, a worker is running, or an investment
method works out of sample.

## Phase evidence matrix

| Phase | Proven current state | Evidence still required for operational completion | Current gate |
|---|---|---|---|
| 1 — foundation | Immutable decision/model-version records, protected Git workflow, transactional local change boundary and architecture/risk audit are implemented and tested. | Continued migration of remaining legacy paths and authoritative PostgreSQL cutover before unattended operation. | Safe local foundation substantially complete. |
| 2 — Codex + Claude | Repeatable issue/branch/test/PR workflow and usage-allocation policy exist. Targeted Claude CLI design challenges and final reviews are working again; accounting changes in PRs #193 and #194 were reviewed before merge. | Keep reviews narrowly scoped and use deterministic tests for routine proof; reserve duplicated frontier review for high-risk architecture, investment, security and accounting changes. | No product activation needed. |
| 3 — AWS | Docker/PostgreSQL local foundation, backup/restore, comparison mode, a reviewed Lightsail design and a fail-closed cutover-readiness gate exist. The gate proves a trailing run from contiguous, strictly time-ordered comparison evidence and measures restore freshness from the system clock. | Thirty real consecutive parity observations, a fresh populated restore certificate, explicit PostgreSQL-authority decision, AWS-spend approval, staging resources, least-privilege identities, alerts and deployment rehearsal. | Deliberately deferred until software/evidence foundations are ready; the readiness gate cannot perform a cutover. |
| 4 — Alpaca paper | Immutable paper-order proposals, deterministic local fills, methodology pre-flight, a live-prohibition checklist and a paper-host-only read-only account adapter exist. | User-created paper account, securely stored credentials, successful account check, paper submission/cancel/fill/reconciliation adapter and broker-specific kill/exposure controls. | Explicit broker-account connection decision required; live endpoint and order submission remain unsupported. |
| 5 — performance | Fixed-horizon outcomes, portfolio/benchmark returns, costs, exposures, turnover, volatility, drawdown, CAGR and Sharpe evidence boundaries exist. Immutable event-aware paper position state now processes exact FIFO partial/full sales, stock splits and paid gross USD dividends chronologically; daily valuation uses adjusted current quantities, one close per ticker and reconciled trade/dividend cash. Zero daily return is human-selected for future Sortino windows and has a fail-closed readiness gate. FMP and EODHD accounts have been capability-probed without exposing their keys. A paper-broker snapshot separates settled/unsettled cash evidence from gross-pre-tax performance. | A genuine future portfolio/window registration; unsupported/ambiguous corporate-action and FX policies where justified; future hit-rate/calibration and alpha policies; qualified point-in-time historical provider/data; sufficient real forward observations; actual broker reconciliation, investor-specific taxes and several execution-realism limitations. | Current FMP and EODHD access do not provide the combined S&P 500/Nasdaq-100 membership evidence. The latest EODHD capability probe returned 403 for historical membership and did not qualify delisted-price schema; its published S&P/Dow add-on remains incomplete for this mandate. Norgate covers both but is Windows-only. No track record yet. |
| 6 — controlled learning | Lesson, experiment, run, robustness, shadow, review, human-decision, activation, lifecycle/budget and emergency-stop boundaries exist. | Real eligible outcomes, actual sandbox worker/Hermes installation, a human-approved activation window and end-to-end execution using the implemented controls. | No Hermes agent is installed, scheduled or running. |
| 7 — political trading | Immutable point-in-time disclosure and aggregate-snapshot models account for publication and disclosure delay. A fail-closed activation preflight requires pinned terms evidence, affirmative use/access findings and a provider licence where applicable. Point-in-time issuer and committee-membership foundations reject hindsight, tampering and ambiguous identities without inferring committee relevance. | Qualified official-source determination or express licensed feed, separately reviewed ingestion connector, real mapping evidence and snapshot integration, preregistered committee-relevance/reliability methodology and out-of-sample signal testing. | Legal/terms and source decision required; passing the preflight cannot implement a scraper or connector. |
| 8 — Obsidian | Safe deterministic non-authoritative Markdown exporter exists. | User-selected vault and first controlled export; optional knowledge workflows only after authoritative records exist. | Local path/configuration decision required. |
| 9 — Buzz | Fail-closed local manifest defines channels, identities and denied powers. | User-approved local installation, key handling, relay/storage configuration and a security-tested pilot. | Intentionally deferred; Buzz is not installed or connected. |
| 10 — continuous improvement | Preregistered experiments, purged/embargoed folds, robustness enforcement, disposable container runner, effectiveness cohorts and human promotion decisions exist. | Qualified sealed real historical data, reviewed active-pipeline image, actual out-of-sample/robustness/shadow evidence, and later human decisions. | Cannot truthfully measure improvement until Phase 5 evidence and provider gates clear. |

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
4. Run the active pipeline through the no-network container under its frozen
   preregistration, then record mechanical out-of-sample and robustness results.
5. Accumulate the fixed future shadow/paper evidence before any strategy
   promotion decision or performance claim.

AWS deployment, an actual Hermes worker and broker paper submission remain
downstream of these software and evidence foundations. They must not be used to
create the appearance of progress before the investment process can be tested
faithfully.
