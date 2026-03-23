#!/usr/bin/env python3
"""Fail commit if staged changes contain likely API keys/secrets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EXCLUDE_FILES = {
    ".env.example",
}

PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "OpenAI key": re.compile(r"sk-(?:proj-|live-)?[0-9A-Za-z_-]{16,}"),
    "Anthropic key": re.compile(r"sk-ant-[0-9A-Za-z_-]{16,}"),
    "Generic API assignment": re.compile(
        r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}"
    ),
}


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def get_staged_files() -> list[str]:
    output = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    files = [line.strip() for line in output.splitlines() if line.strip()]
    return [f for f in files if Path(f).name not in EXCLUDE_FILES]


def get_staged_patch(file_path: str) -> str:
    return _run(["git", "diff", "--cached", "--", file_path])


def main() -> int:
    files = get_staged_files()
    if not files:
        return 0

    findings: list[tuple[str, str]] = []

    for file_path in files:
        patch = get_staged_patch(file_path)
        for line in patch.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((file_path, name))

    if not findings:
        return 0

    print("\n✗ Commit blocked: potential secrets detected in staged changes:\n")
    for file_path, secret_type in findings:
        print(f"  - {file_path}: {secret_type}")

    print(
        "\nActions:\n"
        "  1) Remove/replace secret values with placeholders.\n"
        "  2) Keep real keys only in .env (ignored by git).\n"
        "  3) If a key was exposed, rotate it immediately.\n"
    )

    return 1


if __name__ == "__main__":
    sys.exit(main())
