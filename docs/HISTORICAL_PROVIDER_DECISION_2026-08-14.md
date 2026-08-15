# Historical replay provider decision — 14 August 2026

## Decision

Do **not** purchase or upgrade FMP, EODHD, Norgate, Intrinio or another
historical-data service yet. The default incremental recurring-data budget is
**GBP 0** until one candidate proves the complete mandatory replay dataset with
representative samples and acceptable terms.

This is a cost and evidence decision, not a permanent rejection of paid data.
The platform must not spend money merely to unlock a subset of the data and
then mistake an incomplete backtest for realistic validation.

## Delivery architecture update — 15 August 2026

The selected operating model is now a cloud-native HTTPS REST API or versioned
flat-file delivery path that runs directly on macOS/Linux and can later run on
Linux infrastructure. Windows-only local updater/database products are outside
the active architecture. Norgate Data is therefore no longer a purchase or
implementation candidate for this path because using it would require a
separate Windows virtual machine and extraction operation.

Intrinio, Polygon.io, Databento and direct Nasdaq delivery may be investigated
as cloud-compatible candidates, but the architecture match proves no data
capability. Each still needs representative files and exact terms proving the
complete combined-universe mandate before qualification or any purchase
recommendation. The GBP 0 spending gate remains unchanged.

## Free integration pilot — Massive, 15 August 2026

Manual vendor-email outreach is no longer the next step. For a credentialed
schema pilot, Massive (formerly Polygon.io) is the most direct current option:
its Stocks Basic plan is advertised at USD 0 per month, supports immediate
dashboard API-key access, five calls per minute and two years of historical
end-of-day stock data. Databento provides useful sign-up credits but requires
payment information and can charge beyond the credit; Intrinio provides a free
developer sandbox but states that the sandbox may be incomplete or out of date.
This selects the lowest-friction integration pilot only; it does not qualify
Massive for the combined historical replay mandate.

Official access references checked on 15 August 2026:

- Massive Stocks Basic plan and limits:
  https://massive.com/stocks?auth=signup
- Databento payment-information requirement:
  https://databento.com/blog/why-payment-information-required
- Intrinio Developer Sandbox limitations:
  https://product.intrinio.com/developer-sandbox

- Create the free Massive account:
  https://massive.com/dashboard/signup
- Retrieve the API key after login:
  https://massive.com/dashboard
- Confirm the custom-bars response contract:
  https://massive.com/docs/rest/stocks/aggregates/custom-bars

`scripts/ingest_massive_historical_sample.py` can now stage either one bounded
Massive custom-bars API response or a local JSON/CSV sample. API mode makes one
fixed-host, redirect-rejecting request for at most 31 days and 120 unadjusted
daily bars. The key is accepted only from `MASSIVE_API_KEY`, the legacy
`POLYGON_API_KEY` environment variable, or a separately supplied local key-file
path; it is never placed in the URL or output. File mode performs no network
access.

Massive documents `t` as the start of a custom aggregate window, but its sample
contract does not prove a historical `available_at` or a stable row identifier.
The pilot therefore preserves the bar-window start only inside the payload and
uses local receipt time as the conservative `effective_at` and `available_at`.
It cannot admit the observation to an earlier decision. Derived identity and
timestamp bases are explicitly marked unqualified, while source authentication,
selection, role coverage, engine readiness, replay, broker and trading flags
remain false.

## Evidence status

The user obtained written support answers from FMP and EODHD and transcribed
them into the project conversation. Those answers are useful negative evidence:
they identify capabilities the vendors do not claim to provide. They are not
authenticated source artifacts and therefore cannot satisfy the existing
provider-qualification or sealed-dataset admission ledgers.

Public vendor documentation was checked again on 14 August 2026. It is
consistent with the material limitations below, but it still does not prove the
complete combined-universe dataset.

### FMP

- FMP support did not confirm that `historical-nasdaq-constituent` is
  point-in-time **Nasdaq-100** membership. FMP's public legacy documentation
  describes it as historical companies listed on the Nasdaq exchange.
- Support described historical S&P 500 membership and a delisted-company
  dataset, but did not document complete ticker lineage, merger/spin-off
  outcomes, terminal delisting proceeds, correction history or an unambiguous
  total-return field for this mandate.
- The missing historical endpoints require a paid tier, while the current
  account's secret-safe probes deny access.

Result: retain FMP as a supplementary research/cross-check source under the
current entitlement. Do not upgrade or purchase it while the GBP 0 evidence
gate stands. A combined-provider purchase also requires the user's separate
explicit spending decision.

### EODHD

- EODHD support confirmed point-in-time constituent data for the S&P 500, but
  only current constituents for the Nasdaq-100.
- Support stated that ticker-change history starts in 2022, many delisted
  tickers are available, splits/dividends/spin-offs are available, and
  `adjusted_close` is adjusted for splits and dividends.
- Support confirmed there is no correction history. EODHD's published terms
  also require stored data to be deleted within one month after a subscription
  ends and state that payments are non-refundable.
- Public documentation describes S&P 500 historical constituents from 2000 and
  useful delisted-symbol history, but does not close the missing Nasdaq-100
  point-in-time membership or full terminal-outcome requirements.

Result: retain the locally stored EODHD key for free/current-tier capability
checks and supplementary evidence. Do not upgrade or purchase it while the GBP
0 evidence gate stands. A combined-provider purchase also requires the user's
separate explicit spending decision.

## Mandatory proof before any purchase recommendation

A candidate must provide all of the following for the required historical
period, not merely advertise one endpoint:

1. point-in-time S&P 500 **and** Nasdaq-100 membership with effective and public
   dates, additions and removals;
2. stable security identity through ticker/name/exchange changes;
3. historical prices for removed and delisted securities plus explicit
   acquisition, bankruptcy, liquidation or last-tradable terminal outcomes;
4. splits, dividends, mergers and spin-offs with total-return treatment;
5. exchange calendars, halts and data corrections/revisions;
6. reproducible versioned exports and terms permitting the intended private
   research, storage and future Mac/Linux processing through HTTPS REST or
   versioned flat-file delivery, without a Windows-only runtime;
7. representative sample files that pass the existing provider-neutral
   qualification controls before payment.

The standardized evidence questions in
`docs/HISTORICAL_PROVIDER_SAMPLE_REQUEST.md` remain the qualification contract,
but no manual vendor email is currently planned. Public documentation and
self-service samples can populate only the questions they actually prove. A
vendor-specific omission remains unresolved; it must not be filled from another
candidate's answer or inferred from a successful API call.

## Spending gate

No agent or automated worker may purchase, upgrade, subscribe to, or recommend
activation of a provider solely because an API key exists or a capability
probe succeeds. A future recommendation must state:

- the exact product and total recurring/one-off cost;
- which mandatory capabilities its authenticated samples prove;
- every remaining limitation and mitigation;
- why free/current entitlements and the other candidates are insufficient;
- the smallest reversible trial and cancellation/data-deletion consequences;
- the user's separate explicit spending decision.

The same principle applies to AWS, Buzz and other optional services: defer new
recurring cost until it unlocks evidence or operation that the current local
software can genuinely use.

## Public references checked

- FMP legacy endpoint catalogue:
  https://site.financialmodelingprep.com/developer/docs/legacy-endpoints
- EODHD S&P 500 historical-constituents description:
  https://eodhd.com/financial-apis-blog/reworked-sp-500-historical-constituents
- EODHD delisted-company documentation:
  https://eodhd.com/financial-apis/delisted-stock-companies-data-2
- EODHD terms, including storage/deletion and non-refundable payments:
  https://eodhd.com/financial-apis/terms-conditions

## Safety boundary

This decision authorizes the user to create a free Massive account and a later
single bounded sample request after supplying the credential locally. It
authorizes no paid plan, trial that can incur a charge, bulk download, provider
qualification or approval, replay, performance claim, broker connection, paper
order or real-money trading.
