from __future__ import annotations

"""Deterministic Obsidian Markdown export; source ledgers remain authoritative."""

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import canonical_timestamp
from core.performance.portfolio_valuation import _write_all


EXPORT_SCHEMA_VERSION = "1.0"
EXPORT_POLICY_VERSION = "non-authoritative-obsidian-export-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_CATEGORIES = {
    "Companies", "Investment-Decisions", "Strategies", "Research-Lessons",
    "Experiments", "Architecture", "Investment-Committee", "Post-Mortems",
}
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MANAGED_HASH = re.compile(r"\n<!-- obsidian-generated-body-sha256:([0-9a-f]{64}) -->\n$")


def _required(value: Any, name: str, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _yaml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _validate_existing_managed(content: str) -> None:
    match = _MANAGED_HASH.search(content)
    if match is None:
        raise FileExistsError("Existing Obsidian page is not a managed generated file")
    body = content[: match.start()]
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != match.group(1):
        raise FileExistsError("Existing generated Obsidian page was manually edited; refusing overwrite")


class ObsidianKnowledgeExporter:
    """Writes generated `.generated.md` pages and never edits authoritative data."""

    def __init__(self, vault_root: str | Path) -> None:
        self.vault_root = Path(vault_root)

    def render(
        self, *, category: str, record_id: str, title: str,
        authoritative_source: str, authoritative_record_hash: str,
        sections: Mapping[str, Sequence[str] | str],
        generated_at: str | datetime,
    ) -> str:
        if category not in ALLOWED_CATEGORIES:
            raise ValueError("category is not supported")
        resolved_id = _required(record_id, "record_id", 200)
        if not _RECORD_ID.fullmatch(resolved_id):
            raise ValueError("record_id contains unsafe path characters")
        source_hash = str(authoritative_record_hash or "").strip().lower()
        if not _HASH.fullmatch(source_hash):
            raise ValueError("authoritative_record_hash must be a lowercase SHA-256 digest")
        generated = _timestamp(generated_at)
        if generated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("generated_at cannot be in the future")
        if not isinstance(sections, Mapping) or not sections:
            raise ValueError("at least one section is required")
        lines = [
            "---",
            f"schema_version: {_yaml_string(EXPORT_SCHEMA_VERSION)}",
            f"export_policy: {_yaml_string(EXPORT_POLICY_VERSION)}",
            "authoritative: false",
            "generated: true",
            f"category: {_yaml_string(category)}",
            f"record_id: {_yaml_string(resolved_id)}",
            f"authoritative_source: {_yaml_string(_required(authoritative_source, 'authoritative_source', 1000))}",
            f"authoritative_record_hash: {_yaml_string(source_hash)}",
            f"generated_at: {_yaml_string(generated.isoformat())}",
            "---",
            "",
            f"# {_required(title, 'title', 300)}",
            "",
            "> Generated institutional-memory view. Do not treat this page as transactional state.",
            "> The database/immutable ledger and Git history remain authoritative.",
        ]
        for heading, content in sections.items():
            resolved_heading = _required(heading, "section heading", 200)
            if "\n" in resolved_heading or resolved_heading.startswith("#"):
                raise ValueError("section heading is invalid")
            values = [content] if isinstance(content, str) else list(content)
            if not values:
                raise ValueError("sections cannot be empty")
            lines.extend(["", f"## {resolved_heading}", ""])
            lines.extend(_required(value, "section content", 20_000) for value in values)
        body = "\n".join(lines).rstrip() + "\n"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return body + f"\n<!-- obsidian-generated-body-sha256:{digest} -->\n"

    def export(
        self, *, category: str, record_id: str, title: str,
        authoritative_source: str, authoritative_record_hash: str,
        sections: Mapping[str, Sequence[str] | str],
        generated_at: str | datetime | None = None,
    ) -> Path:
        generated = generated_at or datetime.now(timezone.utc)
        content = self.render(
            category=category, record_id=record_id, title=title,
            authoritative_source=authoritative_source,
            authoritative_record_hash=authoritative_record_hash,
            sections=sections, generated_at=generated,
        )
        root = self.vault_root.resolve()
        category_root = (root / category).resolve()
        if root != category_root and root not in category_root.parents:
            raise ValueError("category path escapes the vault")
        category_root.mkdir(parents=True, exist_ok=True)
        target = category_root / f"{record_id}.generated.md"
        if target.is_symlink():
            raise FileExistsError("Refusing to overwrite a symlinked Obsidian page")
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            _validate_existing_managed(existing)
            if existing == content:
                return target
        temporary = category_root / f".{record_id}.{uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_all(descriptor, content.encode("utf-8")); os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        return target
