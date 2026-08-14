# Historical replay provider options

Reviewed: 14 August 2026

The current human decision is recorded in
`docs/HISTORICAL_PROVIDER_DECISION_2026-08-14.md`. It sets the incremental
recurring-data budget to GBP 0 until complete representative evidence supports
a specific purchase. Support answers are useful limitation evidence, not
authenticated qualification artifacts.

## Decision context

The active research mandate is the combined S&P 500 and Nasdaq-100 universe,
not an S&P-only strategy. A replay provider must therefore support both
point-in-time memberships, removed and delisted securities, stable identity
through symbol changes, corporate actions, total-return prices, terminal
outcomes, corrections and permitted internal use. Passing an API capability
probe is not provider qualification or dataset admission.

## Evidence gathered

| Route | Useful evidence | Material gap | Current decision |
|---|---|---|---|
| Current FMP account | Delisted directory, as-reported financials, dividend-adjusted prices and splits are available. Support describes historical S&P 500 changes on paid tiers. | The configured account cannot access the histories. FMP support did not confirm point-in-time Nasdaq-100 membership; public legacy documentation instead describes historical Nasdaq-listed companies. Complete identity lineage, corporate/terminal outcomes, corrections and total-return treatment remain unproven. | Retain as a supplementary research source. Do not upgrade or purchase it while the GBP 0 evidence gate stands, including as one part of a combined-provider route. |
| Current EODHD account | Account and secret-safe adapter are configured. Support and public documentation describe historical S&P 500 components, many delisted prices, splits, dividends and limited symbol history. | Support confirms current-only Nasdaq-100 constituents, ticker changes only from 2022 and no correction history. The latest live probe returned HTTP 403 for historical membership and did not qualify the delisted-price sample schema. Stored subscription data must be deleted within one month after cancellation. | Retain the key locally for current/free capability checks. Do not upgrade or purchase it while the GBP 0 evidence gate stands, including as one part of a combined-provider route. |
| Norgate US Stocks Platinum | Official coverage includes S&P 500 and Nasdaq-100 historical constituents, delisted/formerly listed securities and daily US data back to 1990. Its content table documents dividend and capital-event indicators. | Six months costs USD 346.50. Norgate Data Updater and Python support are Windows-only; native macOS and future Linux/AWS operation would require a separate Windows virtual machine and export/ingestion process. Exact terminal-outcome treatment, corrections/revisions and internal-use automation terms still require confirmation. | Strong data candidate, but do not purchase until a Windows extraction architecture and licence/automation terms are explicitly accepted. |
| Intrinio | API-first access is compatible with macOS and Linux; its product catalogue includes index constituents, EOD history, adjustment factors and delisted securities. | Public material inspected does not establish historical Nasdaq-100 membership. Corporate-action scope, terminal outcomes, corrections/revisions, internal-use terms and combined price require sales confirmation; index constituents are enterprise/contact-sales. | Do not assume it solves the universe. Request a scoped written quote/sample only after exact historical membership is confirmed. |
| Official Nasdaq Global Index Watch | Nasdaq's methodology describes an API dissemination channel for current and historical index constituents. | Product entitlement, usable history depth, S&P coverage, delisted prices, corporate actions, terminal outcomes, corrections, permitted use and price are not established. | Treat as a separate direct-Nasdaq enquiry, not as an Intrinio feature. |
| Nasdaq Data Link Sharadar | API-first point-in-time US prices, identifiers and fundamentals may strengthen replay inputs. | Public documentation inspected does not prove historical S&P 500 and Nasdaq-100 membership, complete terminal outcomes or the needed corrections/corporate-action coverage. | Do not treat it as the universe provider without explicit product confirmation. |

## Current recommendation

Do not buy a provider yet. FMP and EODHD support have now answered the first
questions and neither answer proves the full combined universe. First obtain
representative sample access proving all mandatory capabilities.
The least-waste sequence is:

1. Do not upgrade FMP or EODHD on the evidence currently available.
2. Ask any remaining candidate for representative samples and exact terms, not
   another general marketing confirmation.
3. Compare separate scoped Intrinio and direct-Nasdaq quotes
   with the all-in cost of Norgate Platinum plus a Windows virtual machine.
4. Authenticate representative samples and terms through the existing provider
   qualification and source-approval ledgers before downloading a sealed
   dataset or running a replay.

An S&P-only validation remains possible only as a separately named strategy
variant with its own preregistration. It must never be presented as validation
of the current combined-universe strategy.

## Sources

- FMP assessment: `docs/FMP_PROVIDER_ASSESSMENT.md`
- Current human decision: `docs/HISTORICAL_PROVIDER_DECISION_2026-08-14.md`
- EODHD historical-constituents coverage and price: https://eodhd.com/lp/spglobal
- EODHD plans: https://eodhd.com/pricing
- Norgate US package coverage and price: https://norgatedata.com/stockmarketpackages.php
- Norgate index coverage: https://norgatedata.com/data-content-tables.php
- Norgate macOS/Windows limitation: https://norgatedata.com/ndu-faq.php
- Norgate Python limitation: https://norgatedata.com/subscribe/subscribe.php
- Intrinio products and access: https://intrinio.com/pricing
- Nasdaq Global Index Watch architecture: https://indexes.nasdaq.com/docs/Nasdaq_Index_Methodology_Guide.pdf
- Nasdaq Data Link API product model: https://docs.data.nasdaq.com/docs/data-organization
