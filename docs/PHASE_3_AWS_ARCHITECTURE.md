# Phase 3 AWS architecture — paper-only pilot

Status: proposed architecture; no resources deployed

Governing issue: [#3](https://github.com/sambiddleston8-byte/trading-bot/issues/3)

Pricing checked: 11 August 2026; verify the selected AWS region before purchase

## Decision

Start with one small Amazon Lightsail Linux instance running the existing
Streamlit application and scheduled Python commands in Docker, plus a private
managed PostgreSQL database. This is the smallest credible always-on pilot that
removes dependence on the iMac while giving portfolio snapshots and decision
records a real transaction boundary.

Do not start with Kubernetes, an always-running LLM, or a distributed
microservice architecture. Do not deploy until the database adapter, migration,
container tests, authentication and rollback rehearsal pass in later issues.

## Current repository boundary

The repository currently contains:

- a Streamlit UI starting at `app.py`;
- scheduled one-shot commands in `scripts/run_portfolio_research_cycle.py` and
  `scripts/run_portfolio_monitor.py`;
- a macOS-only opt-in launchd installer;
- generated research and paper-portfolio JSON files under `data/`;
- a hash-chained JSONL decision ledger and a recoverable local two-file journal;
- a local Docker image and Compose environment with manual-only worker profiles;
- a non-authoritative PostgreSQL adapter, comparison mode and restore rehearsal;
- a read-only preview that keeps pre-ledger snapshots `UNVALIDATED_LEGACY`;
- no AWS infrastructure definition and no enabled unattended schedule.

The local journal is appropriate crash protection for Phase 1, but it is not the
storage mechanism for unattended cloud operation.

## Options considered

| Option | Shape | Cost character | Decision |
|---|---|---|---|
| Lightsail pilot | One Linux instance, Docker Compose, managed PostgreSQL, host timers | Predictable bundled compute; smallest operational surface | Recommended for the first paper-only pilot |
| ECS/Fargate | Streamlit service, scheduled Fargate tasks, EventBridge Scheduler, RDS PostgreSQL | Pay-per-task compute but more networking, load-balancer and database cost/complexity | Migration target if workload or availability justifies it |
| EC2 with PostgreSQL on the same host | One VM containing application and database | Lowest headline bill | Rejected for unattended use: one host failure couples compute, state and recovery |
| EKS/Kubernetes | Managed cluster and multiple services | Highest complexity and baseline overhead | Rejected as unnecessary |

AWS advertises bundled Lightsail Linux instance plans starting at USD 5/month
with public IPv4 (or USD 3.50 with IPv6), and a managed database bundle starting
at USD 15/month. A realistic pilot
planning allowance is **USD 30–50/month** for a modest instance, database,
snapshots, object storage, logs and normal transfer. This is a planning band, not
a quote; region, tax, IPv4, storage, logs and transfer can change it. AWS's own
Lightsail pricing also shows how optional load balancers and container services
add separate monthly charges, so the instance pilot avoids both initially. See
[Lightsail pricing](https://aws.amazon.com/lightsail/pricing/).

The scale-out option uses Fargate, which bills requested CPU and memory per
second with a one-minute Linux minimum, plus related services and public IPv4
where applicable. It is not automatically cheaper for an always-on UI. See
[Fargate pricing](https://aws.amazon.com/fargate/pricing/). EventBridge Scheduler
currently includes 14 million monthly invocations in its free tier, then charges
per million, so scheduler invocation cost is negligible relative to compute and
database cost; see [EventBridge pricing](https://aws.amazon.com/eventbridge/pricing/).

## Pilot topology

```text
Operator browser on localhost
        |
        v (encrypted SSH tunnel; UI is not publicly reachable)
Lightsail Linux instance
  Docker Compose
  - web: Streamlit app
  - worker image: one-shot research/monitor commands
        |
        v (private database connection)
Managed PostgreSQL
  - run metadata
  - portfolios and holdings
  - investment decisions and model versions
  - transactional outbox
        |
        +--> object storage: immutable-by-convention research artifacts/backups

Host systemd timers (disabled until explicitly enabled)
  --> docker compose run --rm worker <bounded command>
  --> one global portfolio-mutation lock plus a per-job advisory lock
```

The web and worker containers use the same versioned image and Git revision.
The web container does not run background research loops. Each scheduled job is
finite, retryable and identified by a unique run ID.

Co-locating the stateless UI and workers means a host failure pauses both, which
is acceptable for the pilot because no trade execution is involved and all
authoritative state is outside the host. It must not lose or rewrite database
history; recovery starts the same pinned image on a replacement host.

## PostgreSQL transaction boundary

One database transaction must atomically write:

1. the portfolio version;
2. its holdings;
3. every associated investment decision;
4. model/prompt/policy/Git versions and data cutoffs;
5. an outbox event describing the committed change.

The transaction commits all five or none. Object artifacts are written with a
content hash and temporary key first; the database transaction references only
the final key. Outbox delivery marks notification/export work complete without
changing the investment decision.

Minimum logical tables:

- `job_runs` — run ID, command, image/Git version, start/end, state and error;
- `research_artifacts` — ticker, data cutoff, source/model versions, object key
  and SHA-256 hash;
- `portfolio_versions` and `portfolio_holdings`;
- `investment_decisions` and `decision_model_versions`, including
  `previous_hash` and `record_hash`;
- `outbox_events` — append-only delivery work;
- `ledger_anchors` — independently retained chain-head anchors;
- `schema_migrations`.

Decision rows are insert-only to the application role. Corrections create a new
decision referencing the superseded ID; they never update the historical row.
Database constraints enforce unique decision IDs, UTC timestamps,
`data_as_of <= decided_at`, allowed record-only/paper modes and referential
integrity.

PostgreSQL must preserve—not weaken—the Phase 1 tamper-evidence property. Within
the transaction, the writer locks a single ledger-head row, calculates the same
canonical application-level `previous_hash`/`record_hash` chain, inserts the
decision batch, and advances the head. UPDATE and DELETE are revoked from the
application role. A deterministic verifier recomputes the complete chain, and a
scheduled job writes the dated chain head to separately versioned object storage
so privileged database mutation or a bad restore is detectable. Concurrent
writers cannot allocate competing successors because the head row is locked
until commit.

The current repository has two portfolio write paths. Initial construction uses
`PortfolioDecisionTransaction`, but scheduled monitoring/research calls
`PortfolioMonitorService.apply_reallocation`, which currently saves only a JSON
portfolio snapshot. Cloud migration is blocked until **every** portfolio-affecting
path—including reallocation—uses one repository method that creates a new
portfolio version, holding rows, associated per-ticker decisions/reasons and the
outbox event in the transaction above. No cloud timer may call the current
JSON-only reallocation path.

## Migration sequence

1. Add a repository interface around both current write paths: initial portfolio
   construction and monitor/research-cycle reallocation. Preserve behavior while
   making every portfolio mutation emit decisions and version metadata.
2. Add PostgreSQL schema migrations and integration tests using an ephemeral
   test database.
3. Import copies of legacy records with hashes and an import manifest; never
   label them as validated outcomes.
4. Run local and PostgreSQL adapters in comparison mode. PostgreSQL remains
   non-authoritative until record counts, hash-chain verification, reallocation
   decisions and failure injection agree.
5. Rehearse backup restore and rollback to the last known-good image.
6. Only after user approval, make PostgreSQL authoritative for the paper pilot.

Before step 6 can even be presented for approval, a pure cutover-readiness gate
must pass. It requires at least 30 consecutive, distinct portfolio changes with
exact local/PostgreSQL comparison matches; identical local and database decision
ledger counts and tail hashes; all required migrations; no undelivered outbox
events; no failed/running jobs; and an exact isolated restore rehearsal no more
than seven days old. The result is fingerprinted and only says
`EVIDENCE_READY_FOR_HUMAN_DECISION`. It cannot change `PERSISTENCE_MODE`, make
PostgreSQL authoritative, deploy AWS, authorize spend or enable trading.

There is no unattended AWS operation before steps 1–5 pass.

## Containers and scheduling

The later implementation issue should create one pinned Python image with three
entry points:

- `web`: `streamlit run app.py`;
- `research-cycle`: `python scripts/run_portfolio_research_cycle.py`;
- `portfolio-monitor`: `python scripts/run_portfolio_monitor.py`.

Docker Compose is a pilot deployment mechanism, not a scaling claim. Host
`systemd` timers replace the macOS launchd schedule. Timers remain disabled by
default. Any job capable of changing the portfolio first obtains one global
portfolio-mutation advisory lock, then its per-job lock; read-only jobs use only
their per-job lock. This prevents the research cycle and monitor racing on the
same portfolio. Lock order is fixed globally.

Every provider request has a client-side timeout. Each container has a hard
runtime limit enforced by `systemd` (with termination grace), a heartbeat and an
overdue-run alert. Process termination closes the database session and releases
advisory locks. A job records its UTC data cutoff and exits non-zero on partial
failure. Retries reuse the run ID/idempotency key and cannot duplicate a
decision.

If scaling becomes necessary, keep the same image and commands while moving the
web service to ECS and timed commands to EventBridge-scheduled Fargate tasks.

## Security and access gates

- The first pilot is private: the Streamlit port binds only to localhost and the
  operator reaches it through an encrypted SSH tunnel from an IP-allowlisted
  administrative connection. No public web port or domain is required.
- Public access remains a separate issue requiring application authentication,
  a domain, TLS termination and a web-specific firewall rule.
- Store database and provider secrets outside Git and outside the image in
  encrypted SSM Parameter Store parameters for the pilot; inject them at runtime
  with least-privilege access. Re-evaluate Secrets Manager only if automated
  rotation justifies its additional cost.
- The application role cannot alter historical decision rows or database schema.
- Do not store Codex, Claude or broker credentials on the pilot host.
- Do not install an Alpaca live-trading key. A later paper-trading issue must
  accept paper credentials only and reject live endpoints in code and tests.
- Pin the deployed image to a reviewed Git commit; never deploy `latest`.

## Backups, observability and cost controls

- Enable managed database automated backups and take a pre-migration snapshot.
- Export encrypted logical backups to versioned object storage, retain daily
  backups for 35 days and monthly backups for 12 months, and rehearse a restore
  before promotion. Revisit retention once real storage volume is measured.
- Delete abandoned temporary object keys after 24 hours and apply lifecycle
  rules to superseded non-audit artifacts. Ledger anchors and required audit
  evidence use a separately reviewed retention rule.
- Send application/job logs to a 30-day bounded-retention destination; never log
  secrets or full provider responses unnecessarily.
- Alert on failed or overdue jobs, stale heartbeats/research cutoffs, failed
  outbox delivery, database storage pressure and missing daily backups.
- Create AWS Budgets alerts before resources are launched. Suggested initial
  alerts are USD 35 forecast and USD 50 actual monthly spend; these are alerts,
  not automatic hard caps.
- Tag every resource with project, environment, owner and phase so costs can be
  attributed and removed together.
- Before enabling a timer, inventory every third-party provider's quota and paid
  plan, estimate calls per run, and set a daily call ceiling. The USD 30–50 band
  covers AWS infrastructure only; market-data/API subscriptions are separate.
  A timer stays disabled if its projected calls can exceed a free/approved plan.

## Investment-integrity gates

- Every job and decision uses UTC and records `data_as_of` separately from
  processing time.
- A scheduler retry cannot replace historical research or portfolio versions.
- Backfills are explicitly labelled and cannot masquerade as contemporaneous
  decisions.
- Production/paper records never use future data relative to their decision
  timestamp.
- Benchmarks and prices retain their source and availability timestamp.
- Learning remains proposal-and-test only; no self-modifying production code.

## Promotion and rollback gates

Deployment requires a separate user-authorised issue and all of the following:

- Docker build and offline test suite pass from a clean checkout;
- PostgreSQL integration, concurrency, idempotency and failure-injection tests
  pass;
- hash-chain verification and external-anchor checks pass after concurrent
  decision batches and a restore;
- construction and reallocation both use the same atomic repository boundary;
- cross-job exclusion, request timeouts and overdue-job termination pass;
- legacy import reconciliation and backup restore pass;
- authentication, TLS, secrets and budget alerts are configured;
- Claude challenges the persistence/security diff read-only;
- no Critical/High finding remains;
- paper-only/no-broker execution tests pass;
- rollback to the prior image and database snapshot is rehearsed.

Rollback stops scheduled jobs first, restores the last known-good application
image and, only when required, restores the pre-migration database snapshot.
Historical decision exports are retained as evidence; rollback never rewrites
them.

## Explicitly deferred

- creating an AWS account or any paid resource;
- Terraform/CDK and Docker implementation;
- database schema code and data migration;
- public access and custom domain;
- Hermes deployment;
- Capitol Trades ingestion;
- Alpaca paper execution;
- any real-money execution.
