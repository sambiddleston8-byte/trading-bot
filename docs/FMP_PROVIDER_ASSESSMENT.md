# Financial Modeling Prep provider assessment

Assessed: 14 August 2026

## Outcome

The locally configured FMP key authenticates, but the current account is **not
qualified as the sole source for historical replay**. It remains useful as a
supplementary research provider. The assessment concerns the exact current
account access and the roadmap's strict protection against survivorship and
look-ahead bias.

The secret-safe live probe returned:

| Capability | Current account |
|---|---|
| Historical S&P 500 additions/removals | Unavailable — HTTP 401/402 across secret-safe probes |
| Historical Nasdaq additions/removals | Unavailable — HTTP 402 |
| Delisted-company directory | Available |
| As-reported filing-form financials | Available |
| Dividend-adjusted daily prices | Available |
| Stock splits | Available |
| Symbol-change history | Unavailable — HTTP 402 |

FMP's official documentation describes [historical S&P 500 and Nasdaq
membership](https://site.financialmodelingprep.com/developer/docs/stable),
[delisted companies](https://site.financialmodelingprep.com/developer/docs/delisted-companies-api),
[symbol changes](https://site.financialmodelingprep.com/developer/docs/stable/symbol-changes-list),
and [dividend-adjusted prices](https://site.financialmodelingprep.com/developer/docs/stable).
Availability alone would still not prove adequate coverage, revision history,
stable identifiers, terminal cash/zero outcomes, or permitted use.
Representative samples and terms must be authenticated and examined before
provider qualification can pass.

FMP support did not confirm that its historical Nasdaq endpoint provides
point-in-time Nasdaq-100 membership; FMP's public legacy documentation describes
historical companies listed on the Nasdaq exchange. Support also did not confirm
the complete symbol-lineage, terminal-outcome, correction-history,
corporate-action and total-return evidence required by this roadmap. This
strengthens the no-upgrade decision; it does not authenticate a provider sample
or qualify FMP.

## Safe use now

- Continue using FMP for supplementary as-reported statements, analyst evidence,
  prices and cross-checks where the account permits.
- Keep SEC filings authoritative for reported US-company financial facts.
- Do not build the sealed replay universe from today's surviving symbols.
- Do not infer missing constituent removals, symbol changes or terminal outcomes.
- Do not upgrade on the current evidence. A paid endpoint for Nasdaq-listed
  companies is not a substitute for Nasdaq-100 point-in-time membership.
- The current project-wide spending decision is recorded in
  `docs/HISTORICAL_PROVIDER_DECISION_2026-08-14.md`.

The read-only `scripts/assess_fmp_capabilities.py` probe prints only
availability, record counts and field names. It does not print the key or
financial values, download a dataset, approve a provider, run a replay or
enable trading.
