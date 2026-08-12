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
