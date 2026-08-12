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
