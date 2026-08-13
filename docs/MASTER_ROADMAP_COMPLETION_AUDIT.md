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
| 2 — Codex + Claude | Repeatable issue/branch/test/PR workflow and usage-allocation policy exist. Targeted Claude review has worked historically, but recent CLI attempts returned no response. | Restore reliable Claude CLI review before relying on it for a future high-risk promotion; routine deterministic changes do not require duplicated frontier review. | No product activation needed. |
| 3 — AWS | Docker/PostgreSQL local foundation, backup/restore, comparison mode and a reviewed Lightsail design exist. | Explicit AWS-spend approval, staging resources, least-privilege identities, transactional PostgreSQL authority, alerts and deployment rehearsal. | Deliberately deferred until software/evidence foundations are ready. |
| 4 — Alpaca paper | Immutable paper-order proposals, deterministic local fills, methodology pre-flight and a live-prohibition checklist exist. | User-created paper account, securely stored credentials, read-only account check, paper submission/cancel/fill/reconciliation adapter and broker-specific kill/exposure controls. | Explicit broker-account connection decision required; live endpoint remains unsupported. |
| 5 — performance | Fixed-horizon outcomes, portfolio/benchmark returns, costs, exposures, turnover, volatility, drawdown, CAGR and Sharpe evidence boundaries exist. | Human Sortino target; future hit-rate/calibration and alpha policies; qualified point-in-time historical provider/data; sufficient real forward observations; taxes/net broker-cash and several execution-realism limitations. | User methodology decisions and FMP/provider assessment required. No track record yet. |
| 6 — controlled learning | Lesson, experiment, run, robustness, shadow, review, human-decision, activation, lifecycle/budget and emergency-stop boundaries exist. | Real eligible outcomes, actual sandbox worker/Hermes installation, a human-approved activation window and end-to-end execution using the implemented controls. | No Hermes agent is installed, scheduled or running. |
| 7 — political trading | Immutable point-in-time disclosure and aggregate-snapshot models account for publication and disclosure delay. | Licensed Capitol Trades feed or documented official-source terms review, ingestion, issuer/entity mapping, committee/reliability evidence and out-of-sample signal testing. | Legal/terms and source decision required; no scraper or connector exists. |
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

1. Record the human Sortino downside target before its future measurement
   window. The recommended basis is exact matched daily SOFR.
2. Assess a real historical provider. The planned first candidate is Financial
   Modeling Prep after the user creates an account; credentials must not enter
   chat, Git, tests or model prompts.
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
