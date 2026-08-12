# Legacy portfolio preview

The files in `data/research/portfolios/` were created before the immutable
decision ledger. They may be useful as historical research references, but they
are not validated investment decisions or validated outcomes.

Run the read-only inventory from the repository root:

```bash
.venv/bin/python scripts/preview_legacy_portfolios.py
```

The command reads and hashes each `research_portfolio_*.json` file and writes a
derived manifest under `data/backups/legacy_import_previews/`. That directory is
gitignored. It does not modify the source snapshots and does not connect or
write to PostgreSQL.

Every entry is classified `UNVALIDATED_LEGACY` and remains ineligible for
promotion. The manifest records only observed values and explicit gaps,
including absent portfolio IDs, ledger evidence, Git revisions, data cutoffs,
execution mode and model versions. It does not invent missing metadata.

Importing any copy into a database, even as reference-only data, is a separate
approval and implementation gate. This preview provides the evidence needed to
make that decision safely.
