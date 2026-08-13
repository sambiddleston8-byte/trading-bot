# Cross-cutting sandbox governance

Status: governing roadmap requirement; local controls active, cloud and agent
sandboxes not yet deployed

## Purpose

Every unapproved idea, code change, investment rule and agent action must remain
inside a bounded environment until evidence and a human-authorised promotion
move it forward. A sandbox is not one product or server. It is the combination
of isolation, restricted permissions, fixed limits, immutable evidence and a
controlled exit gate.

This policy applies across all ten phases of the Master Roadmap. Real-money
autonomous trading remains prohibited. A sandbox cannot weaken that invariant.

## Five required layers

### 1. Code sandbox

AI-generated changes use a short-lived branch and pull request. Tests run with
disposable data and cannot change authoritative histories. Codex builds bounded
changes; Claude may review high-value diffs read-only. Neither agent merges or
deploys its own work. Only a reviewed Git commit can leave this sandbox.

### 2. Data sandbox

Tests, backfills and experiments use synthetic, copied or explicitly classified
datasets. They cannot write to the authoritative decision, execution,
observation or outcome histories. Point-in-time availability and retrieval time
remain distinct, and backfilled data cannot masquerade as information available
when a decision was made.

Sensitive provider or broker credentials are never copied into test fixtures,
logs, prompts or model context. Sandbox outputs have a retention rule and are
not silently promoted into the evidence base.

### 3. Investment-strategy sandbox

New signals, models and rules first run as historical experiments, then
walk-forward/out-of-sample tests and finally shadow or paper portfolios. They
cannot change the approved portfolio or incumbent strategy. Success and failure
criteria are fixed before results are observed.

Sandbox performance is labelled as simulated, backtested, shadow or paper. It
cannot be presented as a live track record. Transaction costs, data timing,
survivorship, leakage and benchmark choice remain explicit.

### 4. Agent sandbox

Hermes and future agents receive named identities, least-privilege permissions,
allow-listed tools and bounded work queues. Every run has a UTC run ID, input
cutoff, model/prompt versions, maximum duration, call/cost ceiling, output log
and terminal status. Network access, file writes and secrets are granted only
where the job requires them.

Agents may research, monitor, simulate, diagnose and propose improvements. They
cannot alter production investment rules, approve experiments, merge code,
deploy releases, increase their own permissions or remove audit evidence.

### 5. Cloud-staging sandbox

Before an AWS release reaches the future 24/7 environment, the exact reviewed
image runs in a separate staging environment with separate secrets, database,
network policy and resource tags. Staging has no real-money broker credential
and cannot reach a live-order endpoint.

Deployment requires a pinned Git revision, backup/restore rehearsal, health and
cost alerts, a tested rollback and explicit user approval. AWS infrastructure
creation and spending remain separately authorised actions.

## Mandatory limits and emergency controls

Every unattended sandbox job must define before activation:

- maximum runtime and concurrency;
- API/model call ceiling and approved cost budget;
- permitted files, databases, endpoints and credentials;
- maximum simulated order/portfolio exposure where relevant;
- failure, stale-heartbeat and repeated-error thresholds;
- an append-only activity record; and
- a human-accessible stop control that prevents new work and preserves evidence.

Reaching a limit stops or quarantines the job. An agent cannot raise its own
limit. Cost alerts are not treated as hard safety caps; the job itself must have
deterministic ceilings where a provider supports them.

## Promotion path

No sandbox result promotes itself. The required path is:

```text
bounded hypothesis
→ sandbox experiment
→ predeclared evaluation
→ out-of-sample and robustness checks
→ paper/shadow evidence where appropriate
→ GitHub issue and bounded implementation
→ deterministic tests
→ independent challenge where valuable
→ human approval
→ staged deployment
→ monitored release with rollback
```

A failure at any gate leaves the incumbent unchanged. Production and paper
histories are immutable; rollback changes the active version rather than
rewriting what happened.

## Current controls already present

- short-lived branches, pull requests and offline GitHub tests;
- generated research outputs kept outside source-code pull requests;
- `RECORD_ONLY`, `LOCAL_SIMULATION_ONLY` and `PAPER_ONLY` execution boundaries;
- rejection of Alpaca live and arbitrary endpoints;
- no broker credentials, HTTP submission or real-money execution path;
- Docker services bound locally with workers manually activated;
- the disposable container experiment runner snapshots the exact verified input
  bytes into its private workspace before mounting them read-only, limits local
  execution to one container experiment at a time and rejects result decimals
  whose exponents could cause unbounded host-side expansion;
- append-only decisions, proposals, simulated fills, observations, outcomes and
  corporate-action evidence; and
- explicit user approval before merge, AWS spend or future broker activation.

## Requirements still to implement

- execution of the real active-pipeline experiment image and real sealed data
  through the implemented container/disposable-workspace runner;
- a real-Docker integration rehearsal of the exact generated isolation command,
  cleanup path and unprivileged read-only input mount before the runner is used
  with any admitted dataset;
- integration of the implemented activation, admission and immutable
  lifecycle/usage controls into a future actual sandbox worker, without
  weakening their daily-use, concurrency, heartbeat or failure enforcement;
- a separate AWS staging environment and least-privilege identities;
- use of the implemented promotion bundle and human decision boundary only
  after real complete evidence exists, followed by a separate reviewed
  implementation and activation step; and
- tests demonstrating that sandbox jobs cannot reach production data, live
  endpoints or promotion permissions.

These are introduced incrementally at the phase where the corresponding
capability first becomes executable. Documentation alone must never be treated
as proof that an unimplemented control exists.

## Roadmap placement

- Phases 1–2 establish code, data and review isolation.
- Phase 3 must implement cloud staging before unattended AWS operation.
- Phase 4 remains paper-only and adds broker-specific exposure/kill controls.
- Phase 5 supplies immutable measurement evidence for sandbox evaluation.
- Phase 6 must implement agent permissions, budgets and emergency stops before
  Hermes can schedule work.
- Phases 7–9 inherit the same data, agent and permission boundaries.
- Phase 10 may automate experiments but never promotion or production rule
  changes.

Every future issue and pull request must state which sandbox layers it affects,
which controls are active, and which capabilities deliberately remain blocked.
