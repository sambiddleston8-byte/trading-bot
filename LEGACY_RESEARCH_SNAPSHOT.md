# Legacy research snapshot — 9 August 2026

This branch is a preservation-only backup of generated research output that was
present locally before the Phase 1 immutable decision-ledger work began.

## Classification

- **Unvalidated:** these records have not passed the Phase 1 provenance and
  immutable-ledger requirements.
- **Not learning evidence:** every newly preserved learning-history entry is
  still `OPEN`, with empty 1-, 3-, 6- and 12-month outcomes.
- **Not a trading instruction:** the snapshot must not trigger portfolio changes
  or order execution.
- **Legacy format:** the research reports do not contain the current pipeline,
  policy and Git-version provenance required for attribution.

## Contents

- Nine generated research reports covering AAPL, AMZN, GOOG, JOBY, META, MSFT,
  NVDA (two reports) and RKLB.
- Nine corresponding additions to `data/learning_history.json`.
- Refreshed evidence-summary files for eight tickers. These summaries currently
  report zero evidence items and differ primarily by generation timestamp.

## Retention policy

This branch exists so the original files are recoverable while the project moves
toward immutable object storage. It should not be merged into `main` or into a
software feature pull request. Future migration must retain this classification
and must not silently promote these records into validated outcomes.
