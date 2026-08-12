# Phase 9 Block Buzz collaboration workspace

The roadmap's Buzz is Block's open-source project at
https://github.com/block/buzz, not the unrelated sales platform. Its current
README describes a self-hostable human/agent workspace with a signed event log,
channels, `buzz-cli`, YAML workflows and an ACP harness for Codex and Claude
Code. It also states that some workflow approval glue remains in progress and
warns against treating unfinished features as compliance controls.

Issue #135 therefore starts with a fail-closed local manifest rather than an
installation. It defines the ten roadmap channels and separate inactive
identities for Codex, Claude Code and Hermes. Each identity has a narrow role and
is denied merging, deployment, production-rule changes, experiment promotion,
AWS/broker credentials, evidence deletion and live trading.

Buzz is coordination only. GitHub remains the code authority; immutable ledgers
and the database remain state authority; human approval gates remain external.
The planned relay is local (`ws://localhost:3000`). No software, relay, database,
Redis, object storage, workflow, webhook, private key or agent is installed,
started or connected by this change. A later local pilot requires explicit
installation and secret-key handling decisions after the core platform is ready.
