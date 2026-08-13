# Historical replay provider options

Reviewed: 13 August 2026

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
| Current FMP account | Delisted directory, as-reported financials, dividend-adjusted prices and splits are available. | Historical S&P/Nasdaq membership and symbol changes are unavailable on the configured account. Complete terminal outcomes and corrections/revisions are unproven; paid-plan documentation does not itself prove complete replay coverage. | Retain as a supplementary research source; do not upgrade without an exact entitlement and sample-data confirmation. |
| Current EODHD account | Account and secret-safe adapter are configured. Official documentation describes S&P 500 historical components, delisted price history, adjusted data, splits and dividends. | The latest live probe returned HTTP 403 for historical S&P membership and did not qualify the delisted-price sample schema. EODHD's separately published S&P/Dow historical-constituents product does not include Nasdaq-100. Symbol lineage, complete terminal outcomes and corrections/revisions remain unproven. | Retain the key locally but do not purchase an upgrade without written combined-universe entitlement and representative sample confirmation. The currently evidenced product cannot solve the full mandate alone. |
| Norgate US Stocks Platinum | Official coverage includes S&P 500 and Nasdaq-100 historical constituents, delisted/formerly listed securities and daily US data back to 1990. Its content table documents dividend and capital-event indicators. | Six months costs USD 346.50. Norgate Data Updater and Python support are Windows-only; native macOS and future Linux/AWS operation would require a separate Windows virtual machine and export/ingestion process. Exact terminal-outcome treatment, corrections/revisions and internal-use automation terms still require confirmation. | Strong data candidate, but do not purchase until a Windows extraction architecture and licence/automation terms are explicitly accepted. |
| Intrinio | API-first access is compatible with macOS and Linux; its product catalogue includes index constituents, EOD history, adjustment factors and delisted securities. | Public material inspected does not establish historical Nasdaq-100 membership. Corporate-action scope, terminal outcomes, corrections/revisions, internal-use terms and combined price require sales confirmation; index constituents are enterprise/contact-sales. | Do not assume it solves the universe. Request a scoped written quote/sample only after exact historical membership is confirmed. |
| Official Nasdaq Global Index Watch | Nasdaq's methodology describes an API dissemination channel for current and historical index constituents. | Product entitlement, usable history depth, S&P coverage, delisted prices, corporate actions, terminal outcomes, corrections, permitted use and price are not established. | Treat as a separate direct-Nasdaq enquiry, not as an Intrinio feature. |
| Nasdaq Data Link Sharadar | API-first point-in-time US prices, identifiers and fundamentals may strengthen replay inputs. | Public documentation inspected does not prove historical S&P 500 and Nasdaq-100 membership, complete terminal outcomes or the needed corrections/corporate-action coverage. | Do not treat it as the universe provider without explicit product confirmation. |

## Current recommendation

Do not buy a provider yet. First obtain written confirmation or representative
sample access proving all mandatory capabilities for the combined universe.
The least-waste sequence is:

1. Ask FMP whether the exact current/upgrade entitlement provides both index
   histories, stable symbol lineage, delisted prices and terminal outcomes.
2. Ask EODHD whether any Mac/Linux-compatible API product supplies Nasdaq-100
   history alongside its S&P history and delisted data. Its separately
   published S&P/Dow product is insufficient regardless of displayed currency.
3. If neither can prove full coverage, compare separate scoped Intrinio and
   direct-Nasdaq quotes
   with the all-in cost of Norgate Platinum plus a Windows virtual machine.
4. Authenticate representative samples and terms through the existing provider
   qualification and source-approval ledgers before downloading a sealed
   dataset or running a replay.

An S&P-only validation remains possible only as a separately named strategy
variant with its own preregistration. It must never be presented as validation
of the current combined-universe strategy.

## Sources

- FMP assessment: `docs/FMP_PROVIDER_ASSESSMENT.md`
- EODHD historical-constituents coverage and price: https://eodhd.com/lp/spglobal
- EODHD plans: https://eodhd.com/pricing
- Norgate US package coverage and price: https://norgatedata.com/stockmarketpackages.php
- Norgate index coverage: https://norgatedata.com/data-content-tables.php
- Norgate macOS/Windows limitation: https://norgatedata.com/ndu-faq.php
- Norgate Python limitation: https://norgatedata.com/subscribe/subscribe.php
- Intrinio products and access: https://intrinio.com/pricing
- Nasdaq Global Index Watch architecture: https://indexes.nasdaq.com/docs/Nasdaq_Index_Methodology_Guide.pdf
- Nasdaq Data Link API product model: https://docs.data.nasdaq.com/docs/data-organization
