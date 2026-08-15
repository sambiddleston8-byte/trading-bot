# Cloud-native historical provider sample request

Use this same request for Intrinio, Polygon.io, Databento and direct Nasdaq
enquiries. Send it separately to each provider. Do not include API keys,
passwords, account tokens or other credentials in the message or attachments.

This request is an evidence-gathering step only. A response, quote, trial or
sample does not approve a provider, authorize a subscription, admit a dataset
or permit a replay.

## Copyable vendor message

**Subject:** Point-in-time S&P 500 and Nasdaq-100 historical data sample request

Hello,

We are evaluating a historical-data product for private internal investment
research. Our software runs natively on macOS/Linux, so delivery must be through
a documented HTTPS REST API or reproducible versioned flat files. We cannot use
a Windows-only updater, local database or SDK.

Our required universe is the combined S&P 500 and Nasdaq-100, including former
constituents. Before considering a subscription, please identify the exact
product/plan and provide written documentation, applicable terms, and
representative sample files addressing every question below. Please distinguish
what is present in the supplied product from what may exist in another product.

1. What historical date range is available for point-in-time S&P 500 and
   Nasdaq-100 membership?
2. Do records include additions and removals, with both the effective time and
   the time the change first became publicly available?
3. Is there a permanent security identifier spanning ticker, name, exchange,
   merger, acquisition, spin-off and other symbol changes?
4. Are historical prices retained for removed and delisted securities?
5. How are acquisition proceeds, bankruptcy/liquidation recovery, confirmed
   zero recovery and last-tradable terminal outcomes represented?
6. Which corporate actions are included: cash dividends, stock dividends,
   splits, mergers/acquisitions, spin-offs, symbol changes and bankruptcy or
   liquidation?
7. Are raw/unadjusted prices available? Please document every adjusted and
   total-return field, adjustment factor, ex-date, payment date and treatment of
   dividends and corporate actions.
8. Are point-in-time fundamentals available with publication/availability
   timestamps and revision vintages?
9. Are exchange calendars, early closes, closures and trading halts available
   historically?
10. Are corrections and revisions preserved as separate vintages, or does the
    product overwrite history? How can an export be reproduced later?
11. For every sample record, please provide or document an unambiguous mapping
    to `effective_at`, `available_at`, stable provider/dataset/record identity
    and record/version identity. We will record our own `retrieved_at` when the
    bytes are acquired.
12. For REST delivery, please document authentication, pagination, rate limits,
    retry guidance, snapshot/version parameters and response formats. For flat
    files, please document manifests, checksums, naming/version conventions and
    replacement/correction handling.
13. Do the licence and terms permit private local research, historical replay,
    internal derived results, persistent storage, and later processing on
    private Linux cloud infrastructure? State whether redistribution of source
    data or derived outputs is allowed, any deletion duty after trial or
    cancellation, and whether derived results may be retained.
14. Please provide the exact recurring and one-off price, minimum term, trial
    scope, refund/cancellation terms and whether the supplied samples are
    available before payment.
15. Please provide credential-free HTTPS source URLs on provider-controlled
    hosts for the exact terms, documentation and sample bytes. If authentication
    is needed to retrieve a URL, describe it separately without embedding a
    credential in the URL. Email-only attachments cannot satisfy our evidence-
    provenance control.

Please include representative machine-readable JSON or CSV files, not only
screenshots or example prose. Redacted identifiers are acceptable only through
consistent pseudonymization that preserves permanent-identifier linkage across
records, symbol/exchange changes, revisions and corporate actions, while also
preserving the real field structure, timestamps and economic semantics. If
cross-record linkage is not preserved, identity capabilities remain
`UNRESOLVED`.

Thank you.

## Required evidence package

Keep the original response and attachments unchanged. The qualification process
requires three separately authenticated evidence classes from the provider's
approved host:

- `TERMS`: the applicable licence, permitted-use, retention, cancellation and
  deletion terms for the exact product/plan;
- `DOCUMENTATION`: field definitions, endpoint/export behaviour, coverage,
  identifiers, corrections, corporate actions and price-adjustment methodology;
- `SAMPLE`: representative machine-readable bytes from the exact product/plan.

Each evidence item must be retrievable from a documented, credential-free HTTPS
URL on a provider-controlled approved host. Authentication may occur outside the
URL. Email-only attachments cannot be ingested as authenticated qualification
evidence and leave the affected items `UNRESOLVED` until the exact bytes are
available from an acceptable provider URL.

An email assertion without matching documentation and sample evidence is
`UNRESOLVED`; a documentation claim absent from the sample is also `UNRESOLVED`.
Do not modify a sample to make it fit the platform schema. Preserve the raw
bytes first; a later provider-specific normalizer may map only documented source
semantics.

## Minimum representative sample cases

The sample package may use multiple files/endpoints, but collectively it must
contain:

- at least one addition and one removal for both `SP500` and `NASDAQ100`;
- a stable security identity across at least one ticker or exchange change;
- a removed security with prices continuing through its last tradable session;
- an acquisition/merger outcome and a bankruptcy/liquidation or confirmed
  zero-recovery outcome;
- a cash dividend, stock dividend, split, merger/acquisition, spin-off, symbol
  change and bankruptcy/liquidation record;
- raw/unadjusted prices plus the fields needed to reproduce documented
  total-return treatment;
- a point-in-time fundamental with its original version and a later revision;
- a market holiday or early close and a trading-halt example;
- a corrected/revised record showing both vintages and their availability
  timestamps;
- an export manifest, checksum or version token demonstrating reproducibility.

Every observation must expose or have an exact documented mapping for:

- `EFFECTIVE_AT` → canonical `effective_at`;
- `PUBLICLY_AVAILABLE_AT` → canonical `available_at`;
- `RETRIEVED_AT` → recorded by our authenticated ingestion boundary;
- provider, dataset and record/version identity;
- immutable raw-source and normalized-payload hashes calculated locally.

## Qualification acceptance checklist

Record each item as `PASS`, `FAIL` or `UNRESOLVED`. Only `PASS` is affirmative.
Any missing evidence remains `UNRESOLVED`; it must never be inferred.

### Delivery and terms

- [ ] Exact product/plan and credential-free provider hosts identified.
- [ ] Native macOS/Linux delivery through HTTPS REST or versioned flat files.
- [ ] No Windows-only updater, SDK, database or VM dependency.
- [ ] `LOCAL_RESEARCH`, `HISTORICAL_REPLAY` and
      `INTERNAL_DERIVED_RESULTS` are expressly permitted.
- [ ] Persistent storage, cancellation/deletion duties and derived-result
      retention are explicit.
- [ ] `redistribution_allowed` for source data and derived outputs is explicitly
      recorded from the exact product terms; absence is not treated as consent.
- [ ] Terms, documentation and sample bytes have exact HTTPS source URLs on
      provider-controlled approved hosts; email-only attachments are unresolved.
- [ ] Reproducible export/version controls and correction behaviour documented.
- [ ] Exact price, minimum term, trial, cancellation and refund terms supplied.

### Universe and identity

- [ ] Both `SP500` and `NASDAQ100` evaluated across the required date range.
- [ ] `HISTORICAL_CONSTITUENTS` sampled and documented.
- [ ] `ADDITIONS_AND_REMOVALS` sampled and documented.
- [ ] `STABLE_SECURITY_IDENTIFIERS` sampled across symbol/exchange changes.
- [ ] `DELISTED_SECURITIES` retain historical observations.
- [ ] `TERMINAL_OUTCOMES` represent acquisition, bankruptcy/liquidation and
      confirmed zero recovery without inference.

### Prices, events and fundamentals

- [ ] `TOTAL_RETURN_PRICES` methodology and raw-price basis are documented and
      sampled.
- [ ] `POINT_IN_TIME_FUNDAMENTALS` preserve versions and availability.
- [ ] `MARKET_CALENDARS_AND_HALTS` include closures, early closes and halts.
- [ ] `CORPORATE_ACTIONS` include `CASH_DIVIDENDS`, `STOCK_DIVIDENDS`, `SPLITS`,
      `MERGERS_AND_ACQUISITIONS`, `SPINOFFS`, `SYMBOL_CHANGES` and
      `BANKRUPTCY_OR_LIQUIDATION`.

### Point-in-time and reproducibility

- [ ] `EFFECTIVE_AT`, `PUBLICLY_AVAILABLE_AT` and `RETRIEVED_AT` mappings are
      explicit and do not substitute one clock for another.
- [ ] Corrections/revisions are retained as separately available vintages.
- [ ] Documentation and observed sample semantics agree.
- [ ] `REPRODUCIBLE_VERSIONED_EXPORT` is demonstrated by manifest, checksum,
      snapshot version or equivalent immutable evidence.
- [ ] Every required capability has exact `DOCUMENTATION` and `SAMPLE` evidence.
- [ ] No known limitation blocks a required capability.

## Decision outcomes

- Any `FAIL` or `UNRESOLVED` required item: provider qualification is `FAILED`
  or remains incomplete. Do not purchase, normalize missing semantics, combine
  incompatible claims or run a replay.
- All items supported by authenticated terms, documentation and representative
  samples: record `PASSED_AWAITING_HUMAN_APPROVAL`. This still does not approve
  the provider or authorize spending.
- Only after a separate human approval may the exact product/plan become
  eligible for bounded dataset admission. Dataset admission and replay remain
  separate later controls.

## Internal handling sequence

1. Save the original vendor email, documentation, terms and sample attachments
   without rewriting them, but do not treat email-only attachments as
   qualification evidence.
2. Retrieve the exact terms, documentation and sample bytes only from their
   documented credential-free HTTPS URLs on provider-controlled approved hosts,
   then authenticate and hash each source through the existing content ledger
   using the actual retrieval time. If those URLs are absent, leave the related
   items `UNRESOLVED`; never invent a `source_uri` or retrieval event.
3. Complete the checklist against observed bytes; do not score marketing claims
   as sample evidence.
4. Run any structurally compatible synthetic/conformance copy through the mock
   JSON/CSV normalizer. Never relabel the mock result as real provider evidence.
5. If—and only if—the real sample semantics are documented, implement a small
   provider-specific offline normalizer and test its mapping against the
   canonical historical role/cutoff boundary.
6. Record qualification. Stop for separate human approval before any purchase,
   credentialed download or dataset admission.
