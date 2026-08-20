from __future__ import annotations

"""Minimal macOS Keychain boundary for the licensed Sharadar API key."""

import getpass
import subprocess
from typing import Any, Callable


SERVICE = "trading-bot.sharadar-api"
MAX_KEY_BYTES = 500


def _account() -> str:
    value = getpass.getuser()
    if not value or value.strip() != value or len(value) > 200:
        raise RuntimeError("the local Keychain account could not be resolved")
    return value


def _validate(raw: bytes) -> str:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_KEY_BYTES + 2:
        raise RuntimeError("the Sharadar Keychain item is invalid")
    try:
        value = raw.decode("ascii").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise RuntimeError("the Sharadar Keychain item is invalid") from error
    if (
        not value
        or "\n" in value
        or "\r" in value
        or len(value) > MAX_KEY_BYTES
        or any(not 33 <= ord(character) <= 126 for character in value)
    ):
        raise RuntimeError("the Sharadar Keychain item is invalid")
    return value


def store_interactively(*, runner: Callable[..., Any] = subprocess.run) -> None:
    """Let macOS `security` prompt directly; the secret never enters argv."""

    result = runner(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            _account(),
            "-s",
            SERVICE,
            "-l",
            "Trading Bot Sharadar API",
            "-w",
        ],
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("the Sharadar API key was not stored in macOS Keychain")


def load(*, runner: Callable[..., Any] = subprocess.run) -> str:
    result = runner(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            _account(),
            "-s",
            SERVICE,
            "-w",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("the Sharadar API key is not available in macOS Keychain")
    return _validate(getattr(result, "stdout", None))
