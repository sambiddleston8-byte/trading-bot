# Phase 6 controlled learning and Hermes governance

Phase 6 begins with governance, not autonomous learning. The legacy learning
scripts are not authoritative: they may use live provider data or propose
adaptive weights without the immutable Phase 5 evidence and promotion gates.

## Fail-closed Hermes permission and budget policy

Issue #109 adds an immutable human-approved policy boundary for a future Hermes
agent. Registration records explicit daily job, duration, LLM-call, subagent,
AI-cost and consecutive-failure limits plus a named emergency stop. The complete
budget is content-addressed, so even a correctly rehashed budget change is
detected as a different policy.

The default runtime state is stopped. Scheduling, model invocation, network,
broker, AWS and GitHub writes are disabled. Hermes cannot alter production
investment rules, promote an experiment, merge code, deploy, change its own
permissions/budget, delete evidence or enable live trading. Its future permitted
scope is limited to reading verified local evidence and writing sandbox lesson
or experiment proposals for human review.

Preregistering a policy does not activate Hermes, schedule work or invoke any
model. Separate future implementation and human activation gates are required.

## Evidence-backed sandbox lesson proposals

Issue #111 adds an append-only proposal record for controlled-learning lessons.
Every proposal pins between one and 100 verified complete fixed-horizon outcomes
by ID and hash. It must separate an observed result from a suspected cause,
state uncertainty, provide a falsifiable hypothesis, propose an experiment and
name the result that would disconfirm the hypothesis.

The proposal begins unapproved. It cannot claim causality, validate itself,
execute an experiment, change code, weights, investment rules, permissions,
deployment or trading, or apply a lesson. Human, Codex and Claude Code provenance
is supported. Hermes provenance is explicitly rejected while Hermes remains
inactive; a future verified activation boundary is required before it can author
proposals. Recording a proposal invokes no model and performs no experiment.

## Preregistered sandbox experiment specifications

Issue #113 turns an evidence-backed lesson into a testable but still inert
experiment specification. It pins the lesson and content-addresses the complete
specification: baseline and candidate versions, one candidate change, the
point-in-time development-data cutoff, a future out-of-sample window of at least
30 days, a later shadow-test boundary, one primary metric, minimum improvement,
maximum drawdown and turnover degradation, bounded trial count, fixed seed and
explicit acceptance/rejection rules.

Random train/test splitting, out-of-sample reuse, metric substitution and
optional stopping are forbidden. Registration runs no backtest or model, starts
no shadow test and cannot approve promotion, change code or production rules,
deploy or trade. A separate future result record must prove the specification
was followed before any shadow test can be considered.

## Reproducible sandbox run manifests

Issue #115 adds an inert run manifest between preregistration and future
execution. It pins the exact experiment, full Git revision, dataset manifest,
dependency lock and runner hashes plus the isolated no-network environment,
trial count and CPU, memory and duration ceilings. The trial count cannot exceed
the preregistered budget.

The manifest is only a plan: it cannot run code, access a provider or broker,
record a result, start shadow testing, promote a strategy, deploy or trade. A
future runner must prove it used this exact manifest, and a separate immutable
result record must mechanically evaluate the preregistered rules.

## Mechanical sandbox experiment results

Issue #117 adds an append-only result boundary pinned to the exact run manifest,
experiment and runner-output artifact. It requires every planned trial and the
complete preregistered out-of-sample window, then computes the metric improvement,
drawdown degradation and turnover increase using decimal arithmetic. Direction
is fixed by metric, and pass or rejection follows only the preregistered limits.

Meeting the criteria is evidence, not permission. The result cannot start a
shadow test, approve or apply a strategy, change production rules, deploy or
trade. Separate future shadow evidence and human promotion gates remain required.

## Candidate strategy disposition

Issue #119 adds the first fail-closed strategy-registry boundary. Every verified
experiment result receives one immutable disposition: rejected when any
preregistered criterion failed, or eligible for a future shadow test when all
criteria passed. The entry pins the result and exact baseline/candidate versions.

Eligibility is not activation. This boundary cannot start a shadow test, make a
candidate the incumbent, approve promotion, change code, deploy or trade. Those
require separate evidence and human-authorised transitions.

## Preregistered shadow-test plans

Issue #121 adds an inert future paper/shadow observation plan for candidates
that passed every experiment criterion. It fixes the future window before it
begins, requires at least 30 days and a minimum number of complete decisions,
and inherits the experiment's metric and risk thresholds. Retrospective window
selection, later extension and metric switching are forbidden.

Planning starts nothing. Broker access, shadow observations, promotion,
incumbent changes, deployment and trading remain disabled. A separate evidence
record must prove the complete fixed window before human review is possible.

## Complete shadow-test results

Issue #123 adds an append-only paper/shadow result pinned to the exact plan and
evidence-bundle hash. It cannot be written before the fixed window ends or below
the minimum complete-decision count. Decimal calculations mechanically apply
the fixed metric direction, improvement, drawdown and turnover thresholds.

A passing record is labelled as awaiting human review, never promoted. It
cannot replace the incumbent, activate production, deploy or trade. Failed
criteria leave the incumbent unchanged.

## Human promotion-review bundle

Issue #125 adds the immutable evidence package required before asking for a
human promotion decision. It pins a passing shadow result, exact candidate Git
revision, GitHub issue and pull request, implementation manifest, deterministic
test evidence, independent-review evidence and rollback plan. Builder and
reviewer must be different identities.

Completing the package records no decision. Promotion, incumbent or production
changes, deployment and trading remain false and cannot be set by this ledger.
An explicit future human decision requires a separate boundary.

That separate immutable decision boundary now exists. After reviewing one
verified complete bundle, a named human may either reject the candidate or
approve it only for a separate implementation change. One bundle receives one
final decision; concurrent retries are idempotent and a later contradictory
decision is blocked. Approval does not alter the incumbent, change code or a
production rule, activate or deploy anything, connect a broker, submit an order
or enable live trading. Those remain separate implementation, review and
activation steps; an agent cannot promote itself.

## Durable Hermes emergency stop

Issue #127 adds a persistent, one-way stop record pinned to an immutable Hermes
policy and its named emergency-stop identifier. Human, safety-monitor and
system-failure sources may latch it. Once triggered, new work is denied, running
work must terminate and evidence remains preserved.

This component deliberately has no clear, resume or activation operation.
Unknown policies and inactive policies also evaluate as stopped. A separate
future human-controlled activation design is required before Hermes may run.

## Human-controlled activation and start-time admission

The first activation boundary now exists without activating Hermes. A verified,
unstopped permission policy may receive only an explicit human-approved future
window of at most 31 days. The approval pins the exact Git object, named local
sandbox job and action, concurrency, duration, model-call, per-job cost,
simulated-exposure and stale-heartbeat limits, plus canonical read/write roots.
This initial boundary permits no endpoint or credential at all.

A deterministic admission check rejects unknown, stopped, early, expired or
nearly expired windows, unapproved jobs, code drift and invalid or excessive
resource requests. A passing check returns the fixed deadline and local
permissions but still creates no scheduler and starts no job. Network, broker,
AWS, GitHub-write, promotion, order and live-trading authority remain false.
An actual scheduled-worker lifecycle and consumption ledger must enforce daily,
concurrency, heartbeat, failure and cumulative-cost state before any work may
run.

## Immutable worker lifecycle and usage enforcement

That lifecycle boundary now exists without installing or starting a scheduler.
A future admitted request must atomically reserve its daily job, concurrency,
model-call, AI-cost, duration and simulated-exposure capacity before a worker
may start. Request IDs are idempotent but cannot be reused for different work.
Start, heartbeat, success, failure and quarantine events form one append-only,
hash-chained history pinned to the exact activation and Git revision. Reported
model use and cost cannot exceed the reservation.

The guard detects expired deadlines, stale heartbeats and the approved number
of consecutive failures. It latches the independent durable emergency stop
before attempting to record quarantine, so a logging failure cannot leave work
authorised. A stopped policy cannot win a race between admission and
reservation, and a stale worker cannot report success to evade stopping. The
ledger itself runs no task, creates no scheduler and grants no network, broker,
AWS, GitHub-write, promotion, order or live-trading permission. Integration
with a future actual sandbox worker remains separately human controlled.
