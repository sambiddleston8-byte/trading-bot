# Phase 2: Codex-builder and Claude-challenger workflow

## Purpose

Use frontier models only where judgment adds value. Deterministic tools perform
formatting, version control and automated tests. Codex implements bounded change
sets; Claude challenges high-risk diffs independently.

## Roles

### Codex — builder and coordinator

- inspects the current source and roadmap before changing code;
- implements one bounded objective on a feature branch;
- adds or updates the smallest useful deterministic tests;
- records assumptions, versions and unresolved risks;
- never enables real-money execution or merges its own work autonomously.

### Claude — adversarial challenger

- reviews a specific commit or pull-request diff read-only;
- tries to identify integrity failures, investment-methodology errors, data
  leakage, look-ahead bias, missing tests and unsafe execution paths;
- reports findings by severity with evidence and concrete failure scenarios;
- does not edit the builder's files during the review pass.

## When Claude is worth using

Use Claude for architecture boundaries, financial-methodology changes, security
or persistence decisions, model/prompt changes and final promotion review. Do
not spend frontier-model allowance on mechanical formatting, routine test runs,
dependency installation or generated-data movement.

## Pull-request promotion gates

A draft pull request may be marked ready only when:

1. the bounded objective and safety impact are documented;
2. the deterministic offline GitHub check passes;
3. relevant live-provider tests pass when provider behavior changed;
4. Claude has challenged high-risk logic, or the author records why a frontier
   review adds no material value;
5. Critical and High findings are fixed; accepted Medium/Low findings are added
   to the risk register with an owner or future phase;
6. generated research data is excluded or preserved on a separately classified
   data branch;
7. no broker credential, order routing or real-money mode is introduced.

Only the user may authorise marking a pull request ready or merging it.
