"""Guardrail: no secrets committed to the tree (technical plan §7).

Scans tracked files for private keys, cloud credentials, and secret-like
assignments. This is what enforces "don't store secrets" — if someone later
pastes a key into a source or config file, this test fails before it ships.
"""

from __future__ import annotations

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(__file__))

# This test and the placeholder env file legitimately mention secret *names*;
# they contain no real values, so exclude them from the scan.
_EXCLUDE = {"tests/test_no_secrets.py", ".env.example"}

_PATTERNS = {
    "PEM private key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),
    "GitHub token": re.compile(r"\bgh[posru]_[0-9A-Za-z]{20,}\b"),
    "secret-like assignment": re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{16,}",
    ),
}


def _tracked_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
        files = [f for f in out.splitlines() if f]
        if files:
            return files
    except Exception:
        pass
    # fallback: walk the tree, skipping generated/vendored dirs
    skip = {".venv", "__pycache__", ".git", "data", ".pytest_cache"}
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    return found


def test_no_secrets_in_tracked_files():
    findings: list[str] = []
    for rel in _tracked_files():
        if rel in _EXCLUDE:
            continue
        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary or gone — nothing to leak in text form
        for label, pattern in _PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{rel}: possible {label}")
    assert not findings, "Secret-like content found:\n" + "\n".join(findings)


def test_env_files_are_gitignored():
    gitignore = os.path.join(ROOT, ".gitignore")
    with open(gitignore, encoding="utf-8") as f:
        content = f.read()
    for needle in (".env", "*.pem", "*.key", "secrets/"):
        assert needle in content, f"{needle} must be gitignored"
