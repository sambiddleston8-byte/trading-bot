from __future__ import annotations

"""Verified, inert inputs for one active-pipeline replay invocation."""

from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from core.data_quality.authenticated_source_content import (
    AuthenticatedSourceContentLedger,
)
from core.decision_ledger import LedgerIntegrityError, current_git_revision
from core.orchestration.active_pipeline_replay_plan import REQUIRED_COMPONENTS


_INVOCATION_AUTHORITY = object()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _safe_file(path: str | Path, name: str) -> Path:
    resolved = Path(path)
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{name} must be a regular non-symlink file")
    return resolved.resolve()


def _safe_directory(path: str | Path, name: str) -> Path:
    resolved = Path(path)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ValueError(f"{name} must be a regular non-symlink directory")
    return resolved.resolve()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe_engine_registry(
    engine_registry: Mapping[str, Any],
    *,
    required_keys: set[str] | frozenset[str],
) -> dict[str, dict[str, str]]:
    """Derive each implementation identity from its loaded source bytes."""

    if not isinstance(engine_registry, Mapping) or set(engine_registry) != set(
        required_keys
    ):
        raise ValueError("engine_registry must contain exactly the sealed engine keys")
    identities: dict[str, dict[str, str]] = {}
    for name in sorted(required_keys):
        implementation = engine_registry[name]
        if not callable(implementation):
            raise ValueError(f"engine_registry.{name} must be callable")
        source_name = inspect.getsourcefile(implementation)
        if not source_name:
            raise ValueError(f"engine_registry.{name} has no inspectable source file")
        source_path = _safe_file(source_name, f"engine_registry.{name} source")
        version = str(getattr(implementation, "VERSION", "") or "").strip()
        if not version:
            module_name = str(getattr(implementation, "__module__", "") or "").strip()
            qualified_name = str(
                getattr(implementation, "__qualname__", "")
                or getattr(implementation, "__name__", "")
                or ""
            ).strip()
            if not module_name or not qualified_name:
                raise ValueError(f"engine_registry.{name} has no stable version identity")
            version = f"unversioned:{module_name}.{qualified_name}"
        identities[name] = {
            "version": version,
            "source_sha256": _file_sha256(source_path),
        }
    return identities


def active_pipeline_component_registry(
    engine_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve the exact active-route components that the replay will execute."""

    from core.application.portfolio_construction_service import (
        PortfolioConstructionService,
    )
    from core.portfolio.portfolio_engine import PortfolioEngine
    from core.research.factor_lineage import active_factor_lineage_policy
    from core.research.investment_research_pipeline import InvestmentResearchPipeline
    from core.research.research_contract import ResearchContract

    return {
        "investment_research_pipeline": InvestmentResearchPipeline,
        "research_contract": ResearchContract,
        "master_portfolio_decision_engine": engine_registry["master_decision"],
        "factor_lineage_policy": active_factor_lineage_policy,
        "portfolio_engine": PortfolioEngine,
        "portfolio_construction_service": PortfolioConstructionService,
    }


def authenticated_source_snapshot_digests(
    ledger_path: str | Path,
    blob_directory: str | Path,
) -> tuple[str, str]:
    """Verify source evidence and hash the exact ledger and blob manifest."""

    ledger = _safe_file(ledger_path, "authenticated_source_ledger_path")
    blobs = _safe_directory(
        blob_directory, "authenticated_source_blobs_directory"
    )
    records = AuthenticatedSourceContentLedger(ledger, blobs).verify()
    manifest = [
        {
            "blob_relative_path": record["blob_relative_path"],
            "source_input_sha256": record["source_input_sha256"],
            "byte_length": record["byte_length"],
        }
        for record in records
    ]
    return (
        _file_sha256(ledger),
        hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest(),
    )


def learning_state_digest(path: str | Path) -> str:
    """Hash the exact immutable learning-state file used by the invocation."""

    source = _safe_file(path, "learning_state_path")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("learning_state_path must contain valid JSON") from error
    if not isinstance(payload, (dict, list)):
        raise ValueError("learning_state_path must contain a JSON object or list")
    return _file_sha256(source)


@dataclass(frozen=True, slots=True, init=False)
class SealedReplayInvocation:
    """Ledger-issued replay inputs; it exposes no execution or broker method."""

    registration_id: str
    context_sha256: str
    as_of: str
    as_of_index: int
    git_revision: str
    engine_registry: Mapping[str, Any]
    engine_identities: Mapping[str, Mapping[str, str]]
    pipeline_component_identities: Mapping[str, Mapping[str, str]]
    authenticated_source_ledger_path: Path
    authenticated_source_blobs_directory: Path
    learning_state_path: Path
    authenticated_source_ledger_sha256: str
    authenticated_source_blobs_manifest_sha256: str
    learning_state_sha256: str
    _authority: object

    def __init__(
        self,
        *,
        registration_id: str,
        context_sha256: str,
        as_of: str,
        as_of_index: int,
        git_revision: str,
        engine_registry: Mapping[str, Any],
        engine_identities: Mapping[str, Mapping[str, str]],
        pipeline_component_identities: Mapping[str, Mapping[str, str]],
        authenticated_source_ledger_path: Path,
        authenticated_source_blobs_directory: Path,
        learning_state_path: Path,
        authenticated_source_ledger_sha256: str,
        authenticated_source_blobs_manifest_sha256: str,
        learning_state_sha256: str,
        _authority: object | None = None,
    ) -> None:
        if _authority is not _INVOCATION_AUTHORITY:
            raise PermissionError(
                "SealedReplayInvocation must be issued by the verified context ledger"
            )
        object.__setattr__(self, "registration_id", registration_id)
        object.__setattr__(self, "context_sha256", context_sha256)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "as_of_index", as_of_index)
        object.__setattr__(self, "git_revision", git_revision)
        object.__setattr__(
            self, "engine_registry", MappingProxyType(dict(engine_registry))
        )
        object.__setattr__(
            self,
            "engine_identities",
            MappingProxyType(
                {
                    key: MappingProxyType(dict(value))
                    for key, value in engine_identities.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "pipeline_component_identities",
            MappingProxyType(
                {
                    key: MappingProxyType(dict(value))
                    for key, value in pipeline_component_identities.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "authenticated_source_ledger_path",
            authenticated_source_ledger_path,
        )
        object.__setattr__(
            self,
            "authenticated_source_blobs_directory",
            authenticated_source_blobs_directory,
        )
        object.__setattr__(self, "learning_state_path", learning_state_path)
        object.__setattr__(
            self,
            "authenticated_source_ledger_sha256",
            authenticated_source_ledger_sha256,
        )
        object.__setattr__(
            self,
            "authenticated_source_blobs_manifest_sha256",
            authenticated_source_blobs_manifest_sha256,
        )
        object.__setattr__(self, "learning_state_sha256", learning_state_sha256)
        object.__setattr__(self, "_authority", _authority)

    def require_valid(self) -> SealedReplayInvocation:
        if self._authority is not _INVOCATION_AUTHORITY:
            raise PermissionError("replay invocation authority is invalid")
        if current_git_revision(Path(__file__).resolve().parents[2]) != self.git_revision:
            raise ValueError("checked-out Git revision changed after invocation issue")
        engine_identities = describe_engine_registry(
            self.engine_registry,
            required_keys=set(self.engine_identities),
        )
        if engine_identities != {
            key: dict(value) for key, value in self.engine_identities.items()
        }:
            raise ValueError("engine source identity changed after invocation issue")
        component_identities = describe_engine_registry(
            active_pipeline_component_registry(self.engine_registry),
            required_keys=REQUIRED_COMPONENTS,
        )
        if component_identities != {
            key: dict(value)
            for key, value in self.pipeline_component_identities.items()
        }:
            raise ValueError("active pipeline source identity changed after invocation issue")
        try:
            source_ledger, blob_manifest = authenticated_source_snapshot_digests(
                self.authenticated_source_ledger_path,
                self.authenticated_source_blobs_directory,
            )
        except (OSError, LedgerIntegrityError, ValueError) as error:
            raise ValueError(
                "authenticated source ledger or blobs changed after invocation issue"
            ) from error
        if source_ledger != self.authenticated_source_ledger_sha256:
            raise ValueError("authenticated source ledger changed after invocation issue")
        if blob_manifest != self.authenticated_source_blobs_manifest_sha256:
            raise ValueError("authenticated source blobs changed after invocation issue")
        if learning_state_digest(self.learning_state_path) != self.learning_state_sha256:
            raise ValueError("learning state changed after invocation issue")
        return self

    def now(self) -> str:
        return self.as_of


def _issue_sealed_replay_invocation(**values: Any) -> SealedReplayInvocation:
    return SealedReplayInvocation(**values, _authority=_INVOCATION_AUTHORITY)
