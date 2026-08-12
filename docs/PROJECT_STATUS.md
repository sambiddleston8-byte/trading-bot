# AI investment platform project status

This file keeps implementation continuity against the user-approved Master
Roadmap for the AI-Driven Investment Platform. The roadmap is the governing
architecture: work proceeds one phase at a time, GitHub remains the source of
truth, deterministic software is preferred for repeatable tasks, and autonomous
real-money trading remains disabled.

## Current phase

Research-methodology quality gate brought forward before Phase 4 paper-broker
connection. The Alpaca boundary remains local; nothing is connected or
submitted.

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

## Phase 2 work

- Repeatable GitHub issue and pull-request promotion templates established.
- Codex remains the bounded-change builder; Claude remains the read-only
  adversarial challenger for high-value reviews.
- Frontier-model review is explicitly skipped for routine mechanical work.
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

## Next action

Begin the Phase 5 performance and attribution foundation using the immutable
decision, proposal and local simulated-fill identities. Define timestamped
price observations and outcome horizons without yet claiming investment
performance or connecting a broker. Issue #21 remains the ongoing
research-quality backlog. PostgreSQL remains non-authoritative and Lightsail
remains only the planned future destination.
