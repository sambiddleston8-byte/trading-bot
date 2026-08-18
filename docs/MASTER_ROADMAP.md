# Revised Master Plan — Executive Multi-Bot Investment Platform

## Governing architecture

All admitted point-in-time data flows independently to Specialist Bots. The initial specialist set is Technical, SEC Form 4 Insider, Fundamental/Valuation, Catalyst/Event, Political Disclosure, and Macro/Cross-Asset. Every alpha Specialist feeds one and only one Ultimate Executive Aggregator. A separate Risk/Regime Bot supplies constraints to the Executive. The Executive alone emits portfolio intentions. GuardrailedBacktestEngine and the production-equivalent executor deterministically enforce execution mechanics; broker adapters only transport already-authorized instructions.

Authority rules:

1. Specialist Bots advise; they never size positions, route orders, or call brokers.
2. The Risk/Regime Bot produces constraints, not alpha-weighted votes.
3. One Executive Aggregator is the only component that creates portfolio intentions.
4. One shared deterministic mechanics core powers backtest, paper, and live-intent generation. It enforces immutable hard safety maxima, costs, settlement, market hours, lot/tick rules, liquidity feasibility, and executes only protective stops already issued as standing instructions by the Executive. It contains no alpha, ranking, or cross-symbol allocation discretion. The Risk Bot owns regime-conditional discretionary limits; the Executive applies them. A stop breach without a matching standing instruction raises HOLD plus an alert and never originates an order.
5. Broker adapters cannot invent or modify instructions.
6. A kill switch may block or reduce risk but can never initiate risk.

## Canonical Specialist contract

Every Specialist implements identical tick and vector interfaces:

```python
score_tick(context) -> SpecialistSignal
score_frame(frame) -> DataFrame[SpecialistSignal]
```

Required SpecialistSignal fields:

- specialist_id and specialist_version
- symbol
- decision_at
- horizon
- label_horizon_bars
- directional_score in [-1,+1]
- confidence in [0,1]
- coverage in [0,1]
- status: ACTIVE | NEUTRAL | ABSTAIN | STALE | INVALID
- maximum input available_at
- evidence count and evidence SHA-256
- reason codes and model/feature versions

Rules:

- 0 means a genuinely neutral opinion.
- Missing or inapplicable coverage is ABSTAIN, never neutral.
- Invalid, future, stale, or misaligned evidence fails closed.
- Vector and tick results reconcile exactly.
- Specialists cannot read another specialist's features, signals, or weights.
- Every derived input satisfies available_at <= decision_at.
- Every Executive configuration declares exactly one decision horizon. A mismatched Specialist must use a frozen, versioned horizon mapping or ABSTAIN with HORIZON_MISMATCH. Separate horizons require separate Executive instances and an independently versioned capital allocation between them.

## Risk/Regime contract

The Risk Bot returns a separate RiskEnvelope containing regime, new_entries_allowed, forced_exit, regime-conditional gross_exposure_cap, symbol_exposure_cap, position_size_multiplier in [0,1], freshness status, maximum input available_at, evidence hash, and reason codes.

Risk obeys the same PIT, version, alignment, evidence, and freshness rules as SpecialistSignal. Risk is applied once. A VALID fresh hard veto forces cash or risk reduction. A soft regime scales exposure without changing specialist scores. A STALE or INVALID RiskEnvelope blocks new entries, preserves current target weights, emits HOLD/RISK_STALE, and alerts; absence of data never originates a liquidation. forced_exit is honoured only from a VALID fresh envelope or an independently authenticated kill-switch event.

## Executive aggregation and conflict resolution

For eligible alpha specialists:

```text
effective_weight_i = base_weight_i * confidence_i * coverage_i * health_i
participation = sum(effective_weight_i for eligible specialists) / sum(base_weight_i for all registered specialists)
consensus = sum(effective_weight_i * score_i) / sum(effective_weight_i)
disagreement = sum(effective_weight_i * abs(score_i - consensus)) / sum(effective_weight_i)
conviction = consensus * participation * (1 - disagreement)
```

Consensus captures direction. Participation preserves how much of the registered ensemble actually spoke. Conviction captures direction, participation, and agreement, and is the only alpha quantity mapped into target exposure. Renormalization can redistribute direction but can never restore missing participation.

If the effective-weight denominator is zero or the quorum fails, consensus and disagreement are not computed, participation and conviction are recorded as zero, and the Executive emits HOLD for existing positions and no new entries with NO_QUORUM. Zero participation never causes a liquidation; only a valid fresh Risk forced_exit, a matching standing stop, or the kill switch may reduce exposure independently of alpha quorum.

Policy:

- Weights sum to one and are versioned.
- Base weights are selected only on TRAIN from a finite, predeclared and hash-registered scheme set: equal, confidence-weighted, inverse-signal-volatility, or ablation-ranked. Free continuous weight optimization is prohibited.
- No weight changes after the candidate is frozen.
- Research evaluation requires at least two eligible alpha Specialists and at least 50% of registered base weight participating. A production candidate requires at least three registered alpha Specialists and no base weight above 0.40. A two-Specialist research candidate uses equal 0.50/0.50 weights and is explicitly non-promotable.
- Each candidate manifest declares its required-Specialist set. Abstaining Specialists receive zero effective weight; direction may be renormalized only after the named required set, two-Specialist minimum, and 50% participation quorum pass.
- Invalid or stale required inputs block new risk.
- health_i is binary and computed by the Executive from frozen observables only: current status is not INVALID/STALE, tick/vector parity is passing, and evidence-hash continuity is intact. It is emitted in the decision and cannot be manually supplied or tuned.
- Strong disagreement reduces conviction and therefore exposure.
- No alpha specialist has an undeclared veto.
- A Risk hard-veto overrides consensus only by sending a constraint to the Executive.

The Executive emits one immutable ExecutivePortfolioIntent per decision_at. It contains every eligible symbol, action ENTER_LONG | HOLD | REDUCE | EXIT | CASH, pre-cap conviction, participation, consensus, disagreement, risk multiplier, final target weight, Specialist evidence hashes, reason codes, and any pre-authorized standing stop instruction. The frozen long-only platform defaults are entry_threshold = 0.20, exit_threshold = 0.05, and max_position_weight = 0.10; exit_threshold must remain below entry_threshold. A candidate may use stricter preregistered values but may not change them after its TRAIN manifest is hashed.

For each symbol, the Executive computes `alpha_weight = clip(conviction * conviction_to_gross, 0, 1) * max_position_weight`. `conviction_to_gross` is selected on purged TRAIN only from the preregistered finite set {1, 2, 4, 8}; free optimization is prohibited, and the selected value is frozen in the candidate hash before VALIDATION. A new position or increase is eligible only when conviction is at least entry_threshold. An existing position with conviction from exit_threshold up to but excluding entry_threshold may only HOLD or REDUCE and uses `min(current_weight, alpha_weight)`. Conviction below exit_threshold targets zero. The Executive then applies the Risk symbol cap, scales all positive targets down pro-rata only when their sum exceeds the Risk gross cap, and finally multiplies each target by position_size_multiplier. Every step is down-only; no step may increase a prior weight. Unused gross exposure remains cash. Exact ties use the permanent security identifier, and ranking never forces investment. Actions are the deterministic comparison of final target and current weights after the frozen precision/lot policy.

ExecutivePortfolioIntent contains the union of every currently eligible symbol and every currently held symbol. A held symbol that becomes ineligible receives UNIVERSE_EXIT and target zero, with an orderly exit over no more than five decision periods subject to participation limits; its standing stop remains active until close. Any non-empty position without a SymbolIntent is a reconciliation failure, never an implicit hold.

Protective stops are pre-authorized standing instructions issued in the ExecutivePortfolioIntent at entry, including reference price, trigger rule, order type, and evidence hash. The mechanics core may execute that instruction but never create or widen it. GuardrailedBacktestEngine fills a triggered stop at the worse of the trigger price and next available price, including gap-through, under both cost models.

Mechanics-core rejection is per instruction, never whole-intent. Hard maxima reject only risk-increasing instructions; REDUCE, EXIT, CASH, standing-stop, and kill-switch instructions are never rejected for exceeding a maximum. Each rejection is dropped, reason-coded, alerted, and recorded; surviving instructions proceed, and reconciliation uses intended minus rejected as the expected book. The mechanics core may otherwise reject or defer for mechanical infeasibility but can never reprioritize or reallocate between symbols. Every non-stop instruction expires at the next decision_at and is cancelled if unfilled; deferral is permitted only within its current decision period. Standing stops are the only persistent instruction class and expire when the position closes. A superseding intent cancels all prior open non-stop instructions before transmitting replacements. Long-only remains the default. Shorting requires a separate future authorization.

A Specialist is unstable when either its signed forward association changes sign in more than half of purged TRAIN folds or removing it changes full-ensemble TRAIN Sharpe by more than 50% relative to max(abs(full Sharpe), 0.50). Instability blocks production registration unless a named, hash-recorded exception is approved before VALIDATION.

## Specialist delivery

Specialists are added as isolated vertical slices:

1. Technical: trend, momentum, volatility, and breadth.
2. SEC Form 4 Insider: PIT purchases/sales, clustering, and role weighting.
3. Fundamental/Valuation: PIT filings, revisions, and valuation dispersion.
4. Catalyst/Event: earnings, guidance, and corporate-event timing.
5. Political Disclosure: publication-delayed official disclosures.
6. Macro/Cross-Asset: rates, inflation, liquidity, and broad regime conditions.

Each slice contains in one functional PR: bounded ingestion; immutable raw quarantine; PIT normalization; admitted feature family; tick and vector signal implementations; leakage/alignment tests; TRAIN-only ablation; BASE and PESSIMISTIC backtests; and final high-risk review. Technical and Form 4 are completed before the first Executive/Risk integration. Specialists 3–6 are then added incrementally. Registering any new Specialist creates a new candidate version and re-enters TRAIN ensemble development; it never mutates a frozen candidate. A Specialist that adds no stable incremental TRAIN value remains research-only and is not registered with the Executive.

## Evaluation contract

GuardrailedBacktestEngine is the sole authoritative evaluator. Purge equals the longest forward label horizon H used by any Specialist in the candidate; embargo is at least one bar. Both are frozen in the evaluation manifest. Every evaluation includes next-tradable-price execution, standing-stop gap-through execution, BASE and PESSIMISTIC costs, commission/spread/slippage/latency/market impact, liquidity/participation constraints, cash settlement, splits/dividends, benchmark-relative returns, and forced terminal liquidation.

Backtest, paper, and live-intent paths share one deterministic mechanics core; only the fill source differs. A conformance suite replays one fixed ExecutivePortfolioIntent tape through simulated and paper-intent paths and requires byte-identical intended orders, sizes, and cash reservations before broker fill variance. It includes a deferred instruction crossing the next decision boundary and proves cancellation before the superseding intent is transmitted.

Standard outputs are net return, CAGR, Sharpe, maximum drawdown, win rate, annual turnover, completed trade count, raw excess return versus SPY, exposure-matched excess return versus SPY, average and maximum realized gross exposure, realized universe turnover, cost degradation, specialist ablation, and at most five execution rows in routine reports. For decision period t, exposure-matched SPY return equals `SPY_return_t * realized_gross_exposure_t`, with residual capital earning the identical cash rate applied to the candidate; the gating excess CAGR is compounded from the candidate and matched-benchmark period returns. Raw SPY excess remains descriptive and cannot penalize deliberately undeployed cash. This benchmark formula is frozen at Stage 0 and candidate-hashed.

## Partition and promotion policy

- TRAIN: feature development, specialist development, weight selection, and purged walk-forward analysis.
- VALIDATION: one authorized frozen-candidate evaluation per pass with no post-result tuning; the partition has a lifetime budget of three passes across all candidate versions.
- TEST: completely sealed until the final deployment-readiness gate.
- Shadow and paper evidence cannot substitute for TEST; TEST cannot substitute for paper/live reconciliation.

Partitions are strictly chronological and non-overlapping, with dead zones of H + embargo bars at the TRAIN|VALIDATION and VALIDATION|TEST seams. Dead-zone bars belong to no partition and cannot enter labels, weight selection, or reports. Stage 1 records immutable seam dates, H, and embargo in the partition manifest.

VALIDATION passes are recorded in an append-only register with candidate hash, explicit human authorization, and outcome. The VALIDATION absolute Sharpe floor is 0.35 on pass 1, 0.45 on pass 2, and 0.55 on pass 3. When the lifetime budget is exhausted, that window is permanently sealed; promotion then requires a new, clean, previously unadmitted window qualified through Stage 1. Adding a Specialist does not itself authorize or require a VALIDATION pass.

Absolute PESSIMISTIC-cost floors apply before relative degradation: net Sharpe >= 0.50 on TRAIN and at least the pass-specific floor on VALIDATION; exposure-matched excess CAGR versus SPY > 0 on both; maximum drawdown <= 20%; at least 100 completed TRAIN trades and 30 completed VALIDATION trades; TRAIN spans at least three years and contains at least one declared drawdown regime; VALIDATION spans at least six months and 60 decision periods; one-way annual turnover <= 4.0 times average NAV, where turnover is annualized total buy notional divided by average NAV; no intended order exceeds 5% of trailing 20-session median daily volume; and every declared liquidity rule passes. A candidate may preregister stricter values only.

Sharpe is reported with a deterministic stationary-block-bootstrap standard error and 90% interval using 10,000 hash-seeded resamples and frozen mean block length of five decision periods. Only the one-sided TRAIN 90% lower bound must clear the 0.50 floor. VALIDATION uses its point estimate, pass-specific floor, and the 30% degradation test; its interval is contextual and never gates when the window is shorter than 500 decision periods. Before a VALIDATION pass is authorized, a deterministic pre-pass check using only the frozen TRAIN result and partition manifest confirms that `0.70 * TRAIN Sharpe` can meet the pass-specific VALIDATION floor, all minimum span/count requirements can be evaluated, and `(minimum completed VALIDATION trades / VALIDATION window years) * frozen TRAIN average filled entry weight <= 4.0`. A six-month candidate therefore needs sufficient breadth—normally at least 15 concurrent positions at a 0.10 cap—or must preregister a longer VALIDATION window. An impossible, concentrated, or underpowered candidate is rejected without opening VALIDATION or consuming a pass.

The existing October 2024-July 2025 three-symbol sample is an engineering and TRAIN-research fixture only and is non-promotable. Stage 1 cannot qualify a production candidate until an authorized source provides the longer PIT history above. No subscription purchase, provider request, credential use, or capture is implied; any such step remains a separate human decision.

Promotion fails if an absolute floor fails; causal/PIT checks fail; VALIDATION Sharpe degradation exceeds 30%; the candidate depends on one unstable Specialist; performance exists only under favorable execution; or the Executive, Risk, feature, horizon, or weight policy changes after freezing.

Sharpe degradation = (TRAIN Sharpe - VALIDATION Sharpe) / max(abs(TRAIN Sharpe), 0.50). The relative test is inapplicable and automatically fails if the TRAIN absolute Sharpe floor is not met. Drawdown, CAGR, trade-count, turnover, and liquidity are governed by their absolute gates rather than unstable near-zero ratios.

## Unified delivery stages

### Stage 0 — Contract consolidation

Freeze SpecialistSignal, RiskEnvelope, ExecutivePortfolioIntent containing per-symbol SymbolIntent records, and the mechanics-core interface. Freeze the conviction thresholds, hysteresis, position sizing scheme, stop, universe-exit, instruction-expiry, benchmark, turnover/liquidity, and per-instruction rejection semantics. Nominal backtest NAV is $100,000 and the candidate and exposure-matched benchmark both use the same frozen zero cash-rate series until a separately qualified PIT cash-rate series is preregistered. All costs, market impact, participation, and ADV gates are evaluated at nominal NAV. Every candidate also reports capacity at $50,000, $100,000, and $200,000; all absolute floors must pass at nominal NAV and the applicable VALIDATION Sharpe floor must still pass at $200,000. A future Stage 7 live capital ceiling cannot exceed nominal NAV without a new authorized capacity evaluation and an explicit human capital decision.

The production default decision cadence is once per NYSE trading session at session close plus 30 minutes, and only after all required inputs satisfy available_at <= decision_at; execution remains at the next tradable price.

The production universe starts from PIT S&P 500 constituents and excludes SPY as an alpha asset. Eligibility requires point-in-time price >= $5 and trailing 20-session median dollar volume >= $20 million. Membership is reviewed on the final NYSE session of each calendar month: a non-member enters when ranked in the top 100 by that liquidity measure; an incumbent remains while ranked 120 or better; and the buffered universe has a hard ceiling of 120, resolved by liquidity rank then permanent-security-ID tie break. Price or dollar-volume floor failure, index removal, or delisting triggers the frozen exit policy immediately; membership otherwise remains constant between reviews. The three-symbol AAPL/MSFT/SPY universe remains a research-only fixture. Decision cadence and universe rules are candidate-hashed and cannot change after TRAIN begins. Purge H, embargo, block length, and orderly-exit duration use the same declared daily decision-period unit. Archive the legacy ten-phase roadmap and historical evidence modules without deleting them. Exit: interface tests and one authoritative roadmap.

### Stage 1 — PIT data foundation

Qualify bars, corporate actions, calendars, five-timestamp feature records, chronological partition seams, and dead zones. Add a survivorship-free security master with permanent identifiers, PIT listing/delisting dates, PIT index membership, and ticker-reuse resolution. Reconstruct the candidate universe at each decision_at from that master, never from a current-day list. Retain delisted names through their delisting date and liquidate under the frozen delisting policy. Admit TRAIN and VALIDATION only. Exit: immutable qualified partitions meeting the production history/count requirements or explicitly labelled research-only partitions; TEST inaccessible; a deliberately delisted-name fixture reproduces exactly.

Implementation checkpoint: the provider-neutral PIT security master, buffered-universe reconstruction, corporate-action research boundary, and XNYS session/partition research mechanics are complete. Immutable permanent-security-ID events cover listings, ticker changes, index additions/removals and delistings under effective-time and availability-time cutoffs; overlapping ticker assignments fail closed, ticker reuse resolves by permanent identity, and a deterministic delisted-name fixture reproduces exactly. The monthly S&P 500 selection path applies the frozen price and 20-session median-dollar-volume floors, top-100 entry/top-120 retention buffer, 120-name ceiling, SPY exclusion and permanent-ID tie-break without consulting a current constituent list. A provider-neutral append-only corporate-action ledger pins source bytes, records splits, cash dividends and terminal outcomes under the five-timestamp contract, links every event to permanent identity and terminal outcomes to the exact delisting record, and materializes only information available by its economic effective time into GuardrailedBacktestEngine inputs; late evidence fails closed. Current-master reconciliation surfaces stale evidence without rewriting its pinned historical prefix, explicit replacements preserve a single immutable supersession chain with an enumerated reason, and only the active leaf may materialize for replay. A provider-neutral append-only calendar/partition ledger normalizes regular and early-close XNYS sessions under the same contract, pins source bytes, records immutable TRAIN, VALIDATION and TEST boundaries, and enforces exactly H + embargo daily decision periods at both seams. Calendar corrections preserve one explicit immutable supersession chain; coverage is exact except for an enumerated overlapping boundary correction, only the active calendar may derive or serve its single partition manifest, and superseded calendars and manifests remain auditable but fail closed at consumption. A provider-neutral append-only daily-bar ledger now pins synthetic TRAIN rows to the active calendar, partition manifest, permanent-security-master prefix and five-timestamp contract; it enforces complete synchronized security/session rectangles over a constant-membership security set, preserves explicit correction chains, and permits only the active leaf on the active calendar to materialize immutable GuardrailedBacktestEngine bars. Snapshots spanning a listing, delisting, index-membership change or ticker change fail closed, so a per-security PIT coverage interval remains a declared prerequisite for survivorship-free production bars. All current fixtures remain explicitly synthetic, incomplete, unqualified and unadmitted, with performance/promotion authority false and VALIDATION and TEST access disabled. The currently authorized Massive Basic route provides only two years of end-of-day history, so it may support a wider research-only TRAIN campaign but cannot satisfy the unchanged three-year production TRAIN minimum. No provider payload, fresh VALIDATION window or TEST data is admitted by this checkpoint; Stage 1 remains open for qualified production-scale source capture, authoritative corporate-action and calendar coverage, per-security PIT coverage intervals, and admission.

### Stage 2 — Specialist vertical slices

Complete Technical and Form 4 first. Exit: two independent bounded research signals with exact tick/vector parity and TRAIN ablations. The two-Specialist candidate is non-promotable.

### Stage 3 — Executive and Risk integration

Replace adapter-level trading rules with the single ExecutivePortfolioIntent path. Remove double-counted Risk and Technical vetoes. Prove that no discretionary limit is applied by two components and that the mechanics core only validates immutable maxima or mechanical feasibility. Exit: every intended order traces to one complete portfolio intent.

Implementation checkpoint: Stage 3 mechanics are complete on TRAIN. The ExecutivePortfolioIntent contract and ExecutiveAggregatorBot emit one complete cross-symbol decision; the engine processes synchronized sessions, executes reductions before increases, allocates aggregate risk and settled cash pro rata, records immutable per-symbol intent and cash-reservation traces, and is invariant to input symbol order. Synthetic fixtures exercise binding shared-cash orders; the admitted AAPL/MSFT/SPY TRAIN conformance report (`84b60115a60691aa1fea0ac938b5f0cb28b27c718632409f2f884e872914b442`) passes under BASE and PESSIMISTIC costs with 159 intent traces per scenario. It produces zero orders because the admitted two-Specialist evidence still does not clear quorum, which remains a fail-closed research result rather than performance evidence. Missing volatility history is STALE Risk and cannot originate liquidation. EnsembleSignalAdapter remains frozen and non-promotable. VALIDATION and TEST remain sealed; Stage 4 may now continue TRAIN ensemble development, but no candidate is frozen or promotable.

### Stage 4 — TRAIN ensemble development

Run purged/embargoed walk-forward evaluation, specialist ablations, and fixed-weight comparisons under both cost models. Exit: one frozen candidate or explicit no-promotion.

Implementation checkpoint: Stage 4 exits with explicit no-promotion. Fundamental/Valuation, Catalyst/Event, Political Disclosure, and Macro/Cross-Asset are isolated research-only Specialists; their deterministic synthetic AAPL/MSFT/SPY ablations prove mechanics and PIT conformance but cannot establish incremental alpha or support registration. The production Executive remains Technical plus SEC Form 4, and Risk/Regime remains a separate constraint source. The legacy AAPL/MSFT/SPY VALIDATION window from 2025-03-03 through 2025-04-30 was evaluated at least four times by pre-budget Stage 3 feature-strategy paths and is therefore selection-contaminated and permanently ineligible for Stage 5. Those legacy evaluators are now TRAIN-only and reject any attempted VALIDATION pass identifier. The next candidate must return to Stage 1, qualify a fresh production-scale PIT dataset and previously unevaluated VALIDATION window, then re-enter TRAIN ensemble development. VALIDATION and TEST otherwise remain sealed.

### Stage 5 — Budgeted single-pass VALIDATION

With explicit human authorization, evaluate the frozen candidate once and consume one lifetime pass. Apply the 30% degradation, pass-adjusted Sharpe floor, interval, sample-size, and all absolute risk gates. Exit: rejected candidate or immutable paper candidate. TEST remains sealed.

### Stage 6 — Shadow and paper execution

Run the same frozen Executive, Risk, and execution contracts on contemporaneous data. Reconcile expected versus observed orders, fills, cash, positions, costs, and latency. Exit: sufficient forward paper evidence and independently reviewed reconciliation.

### Stage 7 — AWS deployment and final promotion gate

Deploy the unchanged paper system to AWS with least privilege, monitoring, backups, stale-data shutdown, and kill-switch rehearsal. Open TEST only at the final deployment-readiness gate. Any limited-live proposal requires a new human decision, capital ceiling, and loss budget.

## Efficiency rules

- One vertical slice per functional PR.
- No micro-PRs for isolated administrative checks.
- No roadmap-header or status-only PRs.
- Focused tests during development; one full suite at release readiness.
- One final Opus review for high-risk financial/execution changes.
- Deterministic tools handle hashes, fixtures, formatting, and tests.
- Dashboard and Obsidian are read-only projections of authoritative records.
- Dashboard work occurs only when a stable contract needs visibility.
- No duplicate calculations in Streamlit, notebooks, or broker adapters.
- Human stops are limited to VALIDATION-pass authorization, spending, credentials, external-account activation, AWS deployment, paper-order authority, and live trading.

## Immediate implementation order

1. Repair and merge Stage 4 PR #288's approved clock-fixture correction.
2. Publish the Opus-approved Form 4 commit aacb210.
3. Replace ExecutiveAggregatorBot.aggregate() -> SpecialistSignal with decide(decision_at) -> ExecutivePortfolioIntent.
4. Move Risk/Regime into the separate RiskEnvelope.
5. Remove Technical and Risk gates from EnsembleSignalAdapter.
6. Run TRAIN-only prior-versus-refactored parity and ensemble comparison.
7. Freeze one candidate before any VALIDATION access.

## Review request

Act as an independent Principal Quant Architect. Decide whether this is the most efficient safe route from the current repository state to the user's required multi-specialist, one-Ultimate-Executive architecture. Challenge authority boundaries, signal semantics, conflict resolution, weight calibration, Risk/Regime treatment, partition policy, execution realism, and stage ordering. Preserve PIT integrity, immutable provenance, GuardrailedBacktestEngine authority, realistic costs, TEST sealing, broker safety, and human-only live authorization. Identify only material High/Medium issues. Reply exactly PASS if no material changes are required; otherwise provide a concise corrected replacement for each finding. Do not edit files, use credentials, contact providers/brokers, deploy AWS, or perform trading actions.
