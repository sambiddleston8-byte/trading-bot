from __future__ import annotations

"""Load optional provider keys from a local, untracked ``.env`` file."""

import os
from pathlib import Path


class ProviderConfiguration:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    ENVIRONMENT_PATH = PROJECT_ROOT / ".env"
    _loaded = False

    @classmethod
    def load_local_environment(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        try:
            lines = cls.ENVIRONMENT_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
