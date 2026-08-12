## Bounded objective

<!-- Link the governing issue and state the single delivered outcome. -->

Closes #

## Why this is needed

<!-- State the problem, evidence and why existing deterministic software is insufficient. -->

## What changed

<!-- Summarise source/configuration changes. Keep generated research data separate. -->

## Verification

- [ ] Smallest relevant deterministic tests pass locally.
- [ ] Offline GitHub safety check passes.
- [ ] Live-provider tests were run if provider behaviour changed, or are not applicable.
- [ ] Existing functionality relevant to this change was checked.

## Independent challenge

- [ ] Claude reviewed the specific diff read-only because judgment added value.
- [ ] Claude review was not justified for this routine change; reason recorded below.
- [ ] All Critical/High findings are fixed.
- [ ] Accepted Medium/Low findings are recorded with an owner or future phase.

Claude review decision/findings:

## Investment and data risks

- [ ] Look-ahead bias considered.
- [ ] Survivorship bias considered.
- [ ] Data leakage considered.
- [ ] Overfitting/multiple testing considered.
- [ ] Benchmark and attribution impact considered.
- [ ] These are not applicable; explanation recorded below.

Risk notes:

## Safety and promotion gates

- [ ] Affected code, data, strategy, agent and cloud sandbox layers are identified.
- [ ] Test/experiment outputs cannot overwrite authoritative histories.
- [ ] Resource, runtime, API and model-cost limits are defined where unattended work changes.
- [ ] No agent can approve, merge, deploy or increase its own permissions.
- [ ] Sandbox/shadow/backtest results are not presented as a live track record.
- [ ] No broker credential, live-order route or real-money mode is introduced.
- [ ] The change remains record-only or paper-only where execution is relevant.
- [ ] Generated research outputs are excluded or separately classified.
- [ ] Model, prompt, data and Git versions are captured where decisions are affected.
- [ ] The user has authorised marking this PR ready and merging it.

Sandbox impact and deliberately blocked capabilities:
