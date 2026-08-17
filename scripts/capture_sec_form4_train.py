#!/usr/bin/env python3
"""Bounded official SEC capture for the fixed TRAIN Form 4 slice."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import time
from urllib.parse import urlsplit

import requests

from core.research.sec_form4_insider_specialist import (
    OFFICIAL_SOURCE_URLS,
    normalize_form4_train_sources,
    write_form4_artifact,
    write_immutable_private_bytes,
)
from core.research.stage4_train_insider_ensemble_evaluation import (
    FORM4_CAPTURE_ARTIFACT,
)


MAX_BYTES = {"2024Q4": 12 * 1024 * 1024, "2025Q1": 16 * 1024 * 1024, "AAPL": 25 * 1024 * 1024, "MSFT": 25 * 1024 * 1024}
if set(MAX_BYTES) != set(OFFICIAL_SOURCE_URLS):
    raise RuntimeError("SEC response byte limits differ from the source pins")


def _download(name: str, *, user_agent: str, session: requests.Session) -> bytes:
    url = OFFICIAL_SOURCE_URLS[name]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}:
        raise ValueError("SEC capture target escaped the fixed official hosts")
    response = session.get(
        url,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=45,
        allow_redirects=False,
        stream=True,
    )
    if response.status_code != 200:
        raise RuntimeError(f"SEC capture rejected for {name}: HTTP {response.status_code}")
    content_length = response.headers.get("Content-Length")
    if content_length is not None and int(content_length) > MAX_BYTES[name]:
        raise RuntimeError(f"SEC capture response exceeds the {name} byte limit")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_BYTES[name]:
            raise RuntimeError(f"SEC capture response exceeds the {name} byte limit")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload:
        raise RuntimeError(f"SEC capture returned no bytes for {name}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--retrieved-at", required=True)
    parser.add_argument("--user-agent", required=True)
    args = parser.parse_args()
    if (
        not args.user_agent.strip()
        or args.user_agent != args.user_agent.strip()
        or len(args.user_agent) > 256
        or any(not 0x20 <= ord(character) <= 0x7E for character in args.user_agent)
        or ("@" not in args.user_agent and "github.com/" not in args.user_agent)
    ):
        raise ValueError("SEC user agent must include a bounded contact identity")
    retrieved_at = datetime.fromisoformat(args.retrieved_at.replace("Z", "+00:00"))
    if retrieved_at.tzinfo is None or retrieved_at.astimezone(timezone.utc) > (
        datetime.now(timezone.utc) + timedelta(minutes=1)
    ):
        raise ValueError("retrieved_at must be timezone-aware and not in the future")
    with requests.Session() as session:
        captured = {}
        for index, name in enumerate(OFFICIAL_SOURCE_URLS):
            if index:
                time.sleep(0.2)
            captured[name] = _download(
                name, user_agent=args.user_agent, session=session
            )
    quarantine = (
        args.repository_root / FORM4_CAPTURE_ARTIFACT.parent / "quarantine"
    )
    raw_capture = {}
    for name, payload in captured.items():
        digest = hashlib.sha256(payload).hexdigest()
        path = quarantine / f"{digest}.bin"
        write_immutable_private_bytes(path, payload)
        raw_capture[name] = {
            "sha256": digest,
            "path": path.relative_to(args.repository_root).as_posix(),
        }
    artifact = normalize_form4_train_sources(
        quarter_archives={name: captured[name] for name in ("2024Q4", "2025Q1")},
        issuer_submissions={name: captured[name] for name in ("AAPL", "MSFT")},
        retrieved_at=args.retrieved_at,
        source_urls=OFFICIAL_SOURCE_URLS,
    )
    artifact_bytes_sha256 = write_form4_artifact(
        args.repository_root / FORM4_CAPTURE_ARTIFACT, artifact
    )
    print(
        {
            "artifact_path": FORM4_CAPTURE_ARTIFACT.as_posix(),
            "artifact_sha256": artifact["artifact_sha256"],
            "artifact_bytes_sha256": artifact_bytes_sha256,
            "record_count": len(artifact["records"]),
            "raw_capture": raw_capture,
            "validation_data_read": False,
            "untouched_test_included": False,
        }
    )


if __name__ == "__main__":
    main()
