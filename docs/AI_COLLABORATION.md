# Governing AI collaboration and resource-allocation policy

## Purpose

This policy is a cross-phase amendment to the user-approved Master Roadmap. It
governs every substantial development task, not only Phase 2.

The controlling objective is **maximum software quality per unit of AI usage
and cost**. This means extracting the maximum useful development work from the
user's included ChatGPT/Codex and Claude Pro/Claude Code subscriptions before
recommending additional usage or an upgrade. It is not to minimise either
service in isolation. Code quality, testing, security and investment-system
reliability must never be weakened to save allowance.

Use frontier models only where judgment adds value. Deterministic tools perform
repeatable work such as formatting, version control and automated tests. Local
models may perform simple, repetitive and low-risk work when practical. Codex
or Claude Code performs bounded complex work; for important or high-risk work,
one may build while the other independently challenges the result.

## Default allocation

| Work | Default resource |
| --- | --- |
| Tests, calculations, formatting, file checks and repeatable automation | Deterministic code and local tools |
| Simple, repetitive or low-risk language tasks | Local AI when practical |
| Complex implementation, debugging or architecture | Codex **or** Claude Code, selected for fit and available included capacity |
| Investment-engine, security, persistence, execution or other high-risk changes | One frontier model builds; the other reviews the bounded diff |

The coordinator must avoid duplicate full-repository analysis. Each model
receives only the relevant issue, architecture note, files and diff. Existing
context may favour the model already working on a bounded change, but work may
be alternated or redistributed when the other subscription has useful included
capacity remaining.

Unused allowance is preferable to unnecessary work. Neither model is invoked
merely to consume remaining capacity.

## Roles

### Codex — coordinator, builder or reviewer

- inspects the current source and roadmap before changing code;
- selects the cheapest reliable resource for each bounded objective;
- implements one bounded objective on a feature branch when Codex is the best
  fit or its existing context avoids rediscovery;
- adds or updates the smallest useful deterministic tests;
- records assumptions, versions and unresolved risks;
- can review a Claude Code change independently when risk justifies a second
  frontier-model opinion;
- never enables real-money execution or merges its own work autonomously.

### Claude Code — builder or adversarial challenger

- may implement a bounded coding, architecture or debugging objective when it
  is the best fit or preserves useful included Codex allowance;
- when acting as challenger, reviews a specific commit or pull-request diff
  read-only;
- tries to identify integrity failures, investment-methodology errors, data
  leakage, look-ahead bias, missing tests and unsafe execution paths;
- reports findings by severity with evidence and concrete failure scenarios;
- does not edit the builder's files during an independent review pass.

### Claude handoff when direct invocation is unavailable

The Codex environment must not assume it can invoke Claude Code. When Claude is
the better owner but cannot be called directly, Codex must clearly tell the user
`CLAUDE HANDOFF` and provide one concise, ready-to-paste instruction containing
only the objective, relevant architecture, files or diff, constraints, risks,
tests and expected output. Codex then continues from Claude's result without
repeating repository discovery unnecessarily.

## When a second frontier model is worth using

Use an independent Codex/Claude challenge for architecture boundaries,
financial-methodology changes, security or persistence decisions, broker and
execution logic, model/prompt changes and final high-risk promotion review. Do
not spend frontier-model allowance on mechanical formatting, routine test runs,
dependency installation or generated-data movement.

Cross-review is normally justified by major architecture or complex code,
unresolved uncertainty, difficult defects, security/authentication/credential
changes, external trading APIs, execution, material portfolio construction,
valuation or risk changes, backtesting validity, autonomous or learning
behaviour, possible leakage/look-ahead/survivorship bias, silent corruption of
investment conclusions, or limits in deterministic verification.

It is normally not justified by formatting, renaming, basic configuration,
simple files, routine documentation or UI changes, deterministic calculations,
routine tests or other low-risk mechanical changes.

The reviewer receives a targeted question, relevant architecture, bounded diff,
affected interfaces, tests and named uncertainty. It independently searches for
bugs, assumptions, edge cases, security weaknesses, needless complexity,
missing tests, performance/data-quality/statistical problems and unsafe agent
behaviour. It does not receive an unbounded instruction to "review everything."

## Subscription and spending boundaries

- Claude Code should authenticate through the user's Claude subscription when
  that included capacity can perform the task. Claude Pro does not include
  Anthropic API billing, so Anthropic API usage must not be assumed or enabled.
- Avoid pay-as-you-go model APIs when work can reasonably be completed with
  deterministic tools, local AI or included subscription capacity.
- Before recommending more ChatGPT/Codex usage, a Claude upgrade, or API
  credits, consider whether the other paid subscription has suitable unused
  capacity.
- Warn the user before an unnecessarily expensive model choice and recommend a
  cheaper reliable route.
- Paid frontier usage remains justified when necessary for quality or safety;
  cost control must not bypass tests, reviews or promotion gates.

## Per-task allocation check

Before substantial work, the coordinator records or decides:

1. whether deterministic code or local AI can complete it reliably;
2. if frontier reasoning is needed, whether Codex or Claude Code is the better
   single owner based on task fit, current context and remaining included
   capacity;
3. whether the risk requires the other model to review only the bounded diff;
4. the smallest relevant context and deterministic verification required; and
5. whether any proposed paid overage can first be avoided using the other
   subscription.

Concise architecture, status, issue and pull-request documentation is part of
the cost-control system: it lets both agents resume work without repeatedly
rediscovering the repository.

## Context, modularity and project memory

Context is treated as an expensive resource. Repository search, relevant module
boundaries, interfaces and tests are inspected first; unrelated files are not
loaded, and stable files are not repeatedly reread without reason.

The platform must retain clear module responsibilities, interfaces, APIs,
schemas, dependencies and tests so an agent can work on one component without
understanding the whole repository. Concise architecture, design-decision,
assumption, test-strategy, known-problem and current-status documentation must
remain current without becoming a second oversized source tree.

## Review resolution and stop conditions

The default workflow is:

`ONE MODEL -> DETERMINISTIC TESTS AND CHECKS -> ACCEPT`

When a justified reviewer finds a legitimate issue, the original builder fixes
that specific issue and deterministic checks verify it. Another frontier review
occurs only when the fix materially changes the design, uncertainty or
disagreement remains, deterministic evidence is insufficient, or the risk
itself warrants it. Models must not debate or rereview indefinitely.

Work stops when the bounded objective has sufficient evidence of correctness,
such as targeted/integration/regression tests, static analysis, type checking,
data or financial-model validation, expected-output reconciliation or a
relevant benchmark. Every autonomous loop requires measurable objectives and
explicit stopping conditions.

Testing is a token-saving mechanism. Strong deterministic unit, integration,
regression, data-validation, financial-validity, backtesting-integrity, API and
security checks reduce the need for second-model judgment. Use targeted tests
during development; do not run expensive full-system checks after every trivial
change.

## Investment-system scrutiny

Additional independent reasoning is appropriate where it materially improves
the reliability of decisions, valuation, expected returns, portfolio
construction, risk, backtesting, market/alternative/politician data, catalysts,
Alpaca paper execution, autonomous trading, adaptation, learning or performance
attribution. Plausible-looking but incorrect results are a primary risk. Cost
control must not reduce justified scrutiny in these areas.

## Development AI is not production AI

Codex and Claude Code are development resources, not default runtime components.
Mature functions should become deterministic Python, APIs, databases, scheduled
processes, rules engines, statistical models, tests and conventional
infrastructure wherever practical. The finished platform must not require
premium coding agents for routine operation.

## Usage and cost observability

As runtime AI is introduced, its auditable records should capture, where the
provider exposes them: model, task, input/output/cached tokens, estimated cost,
duration, success/failure, retries and measurable outcome quality. Allocation
decisions should increasingly use this evidence rather than assumptions.

Periodically reassess repeated AI tasks for deterministic automation, improved
documentation/retrieval/testing, smaller context, batching, cheaper suitable
models and elimination of duplicate analysis. The platform should become less
dependent on expensive reasoning for routine work as it becomes more capable.

## Seven-question decision rule

For every meaningful development task, implicitly answer:

1. Does it require AI?
2. Can local or cheaper AI complete it reliably?
3. If premium reasoning is required, should Codex or Claude Code own it?
4. Does the result genuinely require independent review by the other model?
5. What is the smallest necessary repository context?
6. What deterministic evidence will establish correctness?
7. What is the explicit token-spending stop condition?

## Pull-request promotion gates

A draft pull request may be marked ready only when:

1. the bounded objective and safety impact are documented;
2. the deterministic offline GitHub check passes;
3. relevant live-provider tests pass when provider behavior changed;
4. the other frontier model has challenged high-risk logic, or the author
   records why a second-model review adds no material value;
5. Critical and High findings are fixed; accepted Medium/Low findings are added
   to the risk register with an owner or future phase;
6. generated research data is excluded or preserved on a separately classified
   data branch;
7. no broker credential, order routing or real-money mode is introduced.
8. affected sandbox layers, permissions, limits and promotion gates are stated;
9. no agent can promote its own result, merge, deploy or increase its own
   permissions.

Only the user may authorise marking a pull request ready or merging it.

## Repeatable operating sequence

1. Open an `Investment platform change` issue and complete its objective,
   evidence, cheapest-implementation, risk and acceptance fields.
2. Create one feature branch for that issue. The selected builder implements
   only the bounded objective and runs the smallest relevant deterministic
   tests.
3. Open a draft pull request using the repository template. Generated research
   outputs remain excluded or separately classified.
4. Assign one frontier model as builder only when deterministic/local work is
   insufficient. If the issue requires independent review, give the other model
   the issue requirements and exact commit/PR diff read-only.
5. The builder fixes valid findings or records a reasoned disagreement.
   Critical and High findings cannot be accepted as debt.
6. Run automated tests and update the PR checklist and risk register.
7. The user alone authorises `Ready for review` and merge.

The issue form is `.github/ISSUE_TEMPLATE/investment-platform-change.yml`; the
promotion checklist is `.github/pull_request_template.md`.
The cross-cutting isolation and promotion policy is
`docs/SANDBOX_GOVERNANCE.md`.
