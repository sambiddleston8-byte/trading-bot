# Master Roadmap amendment — simulation-to-live integrity

Status: governing cross-phase requirement

## Purpose

Backtest, simulated and paper success must not be assumed to transfer to real
markets. Every performance claim and future promotion decision must account for
the ways simulations can be easier than actual execution.

Real-money autonomous trading remains disabled. This amendment creates evidence
requirements only; it does not authorise or implement a live route.

## Mandatory measurement ladder

```text
point-in-time historical replay
→ untouched out-of-sample test
→ pessimistic execution stress
→ shadow portfolio using contemporaneous data
→ broker paper account
→ reconciliation of expected versus observed orders and fills
→ extended forward evidence
→ independent review
→ human decision on whether a separate limited-live project may begin
```

Evidence from one stage cannot substitute for the next stage.

## One strategy and data contract

Backtest, shadow, paper and any future live candidate must use the same versioned:

- eligible universe and security identifiers;
- research features, decision rules and portfolio constraints;
- data-field definitions, timestamps, corporate actions and calendars;
- order-generation rules and rebalance schedule; and
- model, policy, dependency, container and Git identities.

Any unavoidable difference must be recorded and its effect measured. Results
from a materially different historical strategy cannot validate the active one.

## Conservative execution requirements

Historical and simulated performance must include, where applicable:

- commissions, regulatory fees and borrowing costs;
- bid-ask spread and adverse slippage;
- decision, network, broker and market latency;
- next-tradable-price rules rather than guaranteed close-price fills;
- partial fills, rejections, cancellations and market closures;
- liquidity and participation limits;
- cash drag, unsettled cash and fractional-share constraints; and
- delistings, mergers, splits, dividends and other corporate actions.

Base, favourable and deliberately pessimistic execution scenarios must be kept
separate. A strategy that succeeds only under favourable fills fails promotion.

## Paper-to-live limitations

Paper trading remains necessary but cannot reproduce queue position, all market
impact, every rejection mode, liquidity competition or the psychological and
operational consequences of real capital. Paper results must therefore be
labelled paper results, never a live track record.

Before any future limited-live proposal, the platform must reconcile:

- proposed, accepted, rejected, cancelled, partially filled and filled orders;
- expected versus observed price, spread, slippage and latency;
- internal versus broker positions, cash, buying power and fees; and
- data available to research versus data available at order time.

## Pre-registered promotion criteria

Before each experiment begins, record its hypothesis, incumbent comparison,
sample period, metrics, minimum evidence, cost model, maximum drawdown, turnover
limit and failure conditions. Preserve a final untouched test period.

Repeated experiments must account for selection bias. Overlapping investment
horizons require time-aware validation with appropriate gaps or embargoes.
Parameter search occurs inside training periods only.

## Future limited-live principle

If a separate future decision ever permits real-money testing, it must begin
with a human-set loss budget and capital ceiling small enough that complete loss
is acceptable. It must use independent exposure caps, stale-data protection,
broker reconciliation, alerts and a tested kill switch. Scaling requires new
forward evidence and new human approval; it is never automatic.

## Roadmap placement

- Phase 4 must collect immutable broker paper orders, fills and reconciliation.
- Phase 5 must measure realistic costs and simulation-to-paper differences.
- Phase 6 may learn from outcomes only after label and execution evidence pass.
- Phase 10 must treat the full measurement ladder as a promotion gate.
- AWS deployment may improve uptime but does not relax any investment or
  execution-validity requirement.

The current ten-item boolean checklist is a legacy, unverified diagnostic only.
Its values are caller assertions rather than evidence, so it can never establish
review eligibility. Before Phase 10 can assess live readiness, the platform must
implement an append-only, hash-chained evidence ledger that binds every gate to
verified source identity, content hash, exact location, observation time and an
independent review. Even that future evidence gate may block or permit human
review only; it cannot enable a broker route or live trading.
