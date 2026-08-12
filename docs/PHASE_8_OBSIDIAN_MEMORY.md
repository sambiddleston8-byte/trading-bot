# Phase 8 Obsidian institutional memory

Obsidian is a human-friendly view of institutional knowledge, not a database.
The immutable ledgers/database and Git history remain authoritative. Obsidian
does not need an API: a vault is an ordinary folder of Markdown files.

Issue #133 adds a deterministic exporter for the roadmap folders:

- `Companies`
- `Investment-Decisions`
- `Strategies`
- `Research-Lessons`
- `Experiments`
- `Architecture`
- `Investment-Committee`
- `Post-Mortems`

Generated pages use the suffix `.generated.md`, include the authoritative
source record and hash, and state prominently that they are non-authoritative.
Each page carries an integrity marker. An unchanged export is idempotent; a
verified generated page can be refreshed; any manual edit, unmanaged file or
symlink causes the exporter to refuse the overwrite. Personal notes should use
ordinary `.md` files and are never managed by the exporter.

No Obsidian installation, plugin or AI call is needed for this foundation. No
vault has been selected or written outside tests; future user configuration will
choose its location.
