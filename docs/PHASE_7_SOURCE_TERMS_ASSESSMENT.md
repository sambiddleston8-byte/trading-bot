# Phase 7 source-terms assessment

Reviewed: 13 August 2026

This is a technical activation assessment, not legal advice. It identifies the
evidence that must exist before any congressional-trading data connector can be
designed or enabled.

## Current findings

- The official [House financial-disclosure search](https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch)
  displays restrictions including a prohibition on commercial use except for
  news and communications media. The platform must not automate House access
  for investment research unless a qualified review documents that the exact
  intended use and access method are permitted.
- The official [Senate public-disclosure search](https://efdsearch.senate.gov/search/home/)
  requires the user to accept materially similar use restrictions before
  searching. Public visibility is not treated as permission for automated
  investment-system ingestion.
- The [Senate Ethics financial-disclosure guidance](https://www.ethics.senate.gov/public/index.cfm/financialdisclosure)
  explains the reporting timetable and public availability. It does not by
  itself establish permission for this platform's automated use.
- Capitol Trades remains a possible secondary source only through an express
  licensed feed. No verified feed agreement or sufficiently reviewable current
  terms evidence is held by the project, so scraping and ingestion remain
  blocked.

## Implemented decision boundary

The source-activation preflight requires a pinned terms URL and content hash,
review reference, review date and reviewer role. Both the intended use and the
automated access method must be affirmatively permitted. A commercial provider
also requires an in-force licensed-feed contract.

Passing the preflight means only that a separate connector design may be
reviewed. It does not download data, implement or enable a scraper, approve
redistribution, create a trading signal, connect to a broker or authorize an
order. A future terms change creates different evidence and requires another
assessment.

## Parked human decisions

1. Obtain qualified advice on whether and how official House/Senate disclosure
   data may be used for the platform's precise intended purpose.
2. Alternatively, assess the scope and cost of a provider licence that expressly
   permits the required automated internal use.
3. Only after one route passes the preflight should a narrowly scoped, rate-
   limited, point-in-time connector be proposed for separate review.
