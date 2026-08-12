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
