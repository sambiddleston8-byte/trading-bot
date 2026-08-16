"""Small, fail-closed Stage-0 readiness projection.

The five earlier evidence components remain importable historical records.  They
are no longer inputs to the active readiness calculation.  A credential is
necessary but never sufficient: provider use also needs a separate, effective
bounded activation record.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat as stat_module
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_MASSIVE_KEY_PATH = Path("/private/tmp/massive_api_key.txt")
LEGACY_EVIDENCE_ARCHIVE: tuple[Mapping[str, str], ...] = tuple(
    MappingProxyType({"module": module, "status": "READ_ONLY_HISTORICAL_RECORD"})
    for module in (
        "core.orchestration.campaign_v2_revision_2_account_entitlement",
        "core.orchestration.campaign_v2_revision_2_account_export_verifier",
        "core.orchestration.campaign_v2_revision_2_dashboard_capture",
        "core.orchestration.campaign_v2_revision_2_account_plan_supplement",
        "core.orchestration.campaign_v2_revision_2_public_documentation",
    )
)


def credential_file_present(path: str | Path = DEFAULT_MASSIVE_KEY_PATH) -> bool:
    """Check only for a safe owner-only regular file; never read key bytes."""

    candidate = Path(path)
    try:
        stat = candidate.lstat()
    except OSError:
        return False
    return (
        stat_module.S_ISREG(stat.st_mode)
        and stat.st_uid == os.getuid()
        and stat.st_mode & 0o077 == 0
    )


def stage_0_readiness(
    *, credential_present: bool, provider_use_authorized: bool
) -> dict[str, Any]:
    """Return the complete two-factor Stage-0 status with no side effects."""

    if type(credential_present) is not bool or type(provider_use_authorized) is not bool:
        raise TypeError("Stage-0 readiness inputs must be booleans")
    complete = credential_present and provider_use_authorized
    blockers = []
    if not credential_present:
        blockers.append("CREDENTIAL_NOT_PRESENT")
    if not provider_use_authorized:
        blockers.append("PROVIDER_USE_NOT_AUTHORIZED")
    return {
        "policy": "credential_present AND provider_use_authorized",
        "credential_present": credential_present,
        "provider_use_authorized": provider_use_authorized,
        "stage_0_complete": complete,
        "stage_0_status": "COMPLETE" if complete else "INCOMPLETE_BLOCKED",
        "block_reasons": blockers,
    }
