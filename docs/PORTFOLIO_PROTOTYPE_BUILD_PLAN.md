# Portfolio Construction Prototype — Step-by-Step Build Plan

## Purpose

Produce a transparent, evidence-led prototype that researches the combined
S&P 500 and Nasdaq-100 universe, proposes a diversified portfolio, explains
every inclusion or exclusion, and is clear enough to demonstrate on a phone.

It is a research prototype, not automated investment advice or live trading.

## 1. Protect the data and research gates — in progress

- Keep SEC filings as the primary source for reported company financials.
- Add independent data only as supplementary evidence until it is reconciled.
- Show an itemised audit result and a practical next action for every failed
  or review-level research record.
- Reject irrelevant RSS headlines before they can create false catalysts.
- Keep evidence confidence below certainty and separate it from expected
  investment outcomes.

## 2. Expand research coverage across the full universe — in progress

- Maintain the saved combined S&P 500 and Nasdaq-100 universe (517 unique
  companies at the current saved snapshot).
- Refresh a paced, checkpointed batch of companies at a time, balancing new
  sectors with stale research.
- Use SEC, market data, FRED macro data, Yahoo Finance and configured
  independent-provider inputs for broader coverage.
- Surface missing or rejected provider inputs rather than fabricating a value.

## 3. Produce a decision-ready company record — in progress

For every researched company, preserve and display:

- fundamentals, valuation and a five-year DCF forecast period;
- business quality, financial intelligence, management, moat, industry,
  earnings-event and competitor specialist reviews;
- company-specific news, validated catalysts, sentiment and market signals;
- an adversarial thesis challenge, evidence audit and research-failure
  diagnostics;
- a master portfolio decision explaining whether the company is eligible.

## 4. Construct the proposed portfolio — active prototype behaviour

- Include only research-complete companies with a cleared evidence audit,
  viable valuation, surviving thesis and current master approval.
- Target 15 holdings when enough eligible companies exist; never pad the
  portfolio with weak research merely to reach the target.
- Keep the prototype fully invested: cash allocation is fixed at 0%.
- Prevent duplicate share classes of the same economic issuer.
- Apply a 50% hard sector limit, position limits and a separate risk review.
- Size positions unequally using opportunity, evidence quality, valuation gap,
  volatility and risk quality.

## 5. Explain portfolio results in the website — in progress

- Show a sector-allocation pie chart and full allocation breakdown.
- List proposed holdings by risk-adjusted opportunity, with company name,
  ticker, decision, audit, allocation and evidence confidence.
- Make every ticker open a structured company research page with clear,
  plain-English headings.
- Label model output accurately: the value gap is based on the DCF forecast
  period and is not a promised or date-certain return.

## 6. Monitor the current paper portfolio — active prototype behaviour

- Keep the proposed portfolio separate from the current paper portfolio, so a
  new idea never silently replaces an existing holding.
- Run a dated health check that compares the construction-date price with the
  latest available market price and re-checks the saved audit, thesis and
  master-decision status.
- Raise an early review at a 12% loss and a stop-loss review at a 20% loss.
  These are prompts to investigate and make a manual decision, not automatic
  sale instructions.
- Display every alert and the next review step in the Current portfolio view.

## 7. Add an honest S&P 500 benchmark — in progress

- Use the S&P 500 (`^GSPC`) as the market-context and future-performance
  benchmark.
- State that relative performance is not measurable until the paper portfolio
  records a construction date and subsequent price history.
- When tracking is enabled, compare like-for-like total returns over 1, 3, 6
  and 12 months before making any outperformance claim.

## 8. Prepare the Monday phone demonstration — next

- Keep the primary portfolio list usable as compact company cards on a phone;
  leave the wide comparison table as an optional desktop view.
- Demonstrate locally on the same Wi-Fi network from the Mac, or deploy the
  reviewed GitHub version to Streamlit Community Cloud for a shareable URL.
- Capture the approved screens and include them in the stakeholder report.

## 9. Build after the prototype — later phase

- Paper-portfolio monitoring, rebalancing and full benchmark attribution.
- Historical point-in-time backtests and out-of-sample tests.
- Direct transcript sourcing, field-level provider reconciliation and more
  primary-source catalyst validation.
- Outcome learning based on recorded portfolio decisions, never on hindsight
  inserted into the original research record.
