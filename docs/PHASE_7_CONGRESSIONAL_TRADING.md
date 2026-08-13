# Phase 7 congressional-trading research

Congressional disclosures are delayed, approximate research evidence—not trade
instructions. The system must never pretend a transaction was known on its trade
date. It records the filing, first evidenced public availability and actual
system-observation times separately.

## Source policy

- The official House Clerk publishes downloadable financial disclosures, but
  its search page warns that disclosure information may not be used for a
  commercial purpose except by news and communications media. House automation
  therefore remains disabled until a documented legal/terms review establishes
  an appropriate access basis:
  https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch
- The Senate Ethics Committee says PTRs cover reportable transactions over
  $1,000 and must be filed within 30 days after written notification and no
  later than 45 days after the transaction. The official public search is the
  preferred underlying source where technically and legally appropriate:
  https://www.ethics.senate.gov/public/index.cfm/financialdisclosure
- Capitol Trades can be a convenient secondary research source, but its public
  site currently states that website history is limited to three years. The
  platform will not scrape it; ingestion requires an expressly licensed feed
  and recorded terms review: https://www.capitoltrades.com/trades

## Implemented boundary

Issue #129 adds an append-only source-neutral disclosure ledger. Every record
pins the raw source and availability evidence, chamber, filer, owner, asset,
transaction type, reported value range and all relevant dates. Historical
point-in-time availability and live system observation remain distinct.

No connector, downloader or scraper exists. A disclosure is research evidence
only; it cannot become an automatic/copy-trading signal, connect to a broker,
submit an order or enable live trading. Synthetic tests use no real politician
data.

The source-activation preflight now converts this policy into a fail-closed
technical boundary. It requires hashed terms evidence and affirmative legal and
automation findings; a commercial source additionally requires an in-force
licensed-feed contract. Even a passing assessment grants only permission to
review a separate connector design. See
`docs/PHASE_7_SOURCE_TERMS_ASSESSMENT.md`.

The current policy never approves official House or Senate sources for
commercial investment research. Caller-supplied review text cannot override
that restriction; the currently available route is an expressly licensed
provider, still subject to separate connector review.

## Point-in-time activity snapshots

Issue #131 adds deterministic issuer-level research snapshots using only
disclosures available by the chosen timestamp. Historical evidenced-public and
live system-observed modes are separate. Snapshots count buys, sells, exchanges,
unique and repeated politicians; preserve reported amount ranges; calculate
conservative net lower/upper bounds; and disclose minimum, mean and maximum lag.

Future disclosures are excluded. Committee relevance, historical politician
reliability and subsequent abnormal returns remain explicitly unavailable until
their own evidence foundations exist. The snapshot is one non-executable
research factor, never a standalone recommendation or copy trade.

## Point-in-time issuer identity

A reviewed issuer-mapping boundary now prevents a disclosure ticker from being
treated as a permanent company identity. Each mapping pins the ticker, exact
disclosed asset name, stable reviewed identifier, effective date range, the
date-time the mapping became knowable and hashed source evidence. Resolution
uses the transaction date but excludes mappings learned after the disclosure
became publicly available. Missing, changed, tampered or conflicting mappings
remain unresolved.

This foundation does not fetch mapping data and is not yet wired into an active
political-data connector or activity snapshot. That later integration requires
an approved source, real reviewed mapping evidence and separate tests. Mapping
success creates research identity only and cannot recommend or execute a trade.

## Point-in-time committee membership

A separate evidence boundary now records reviewed House or Senate committee and
subcommittee membership, role, effective period, knowledge timestamp and hashed
source evidence. A membership is available to a disclosure only when the
politician and chamber match, the transaction falls inside the membership
period and the evidence was knowable when the disclosure became public.

This deliberately does not infer that a committee is relevant to a company or
that membership creates an investment advantage. Those are distinct hypotheses
requiring reviewed company/sector mapping, preregistration and out-of-sample
testing. No external committee data has been ingested and no current activity
snapshot uses this evidence yet.
