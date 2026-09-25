#!/usr/bin/env python3
"""Stage generated artifacts, commit when changed, and push safely."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=PROJECT_ROOT, text=True, check=check)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", "-A", "--", *args.paths])
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode
    if staged == 0:
        print("No generated changes to commit")
        return 0
    run(["git", "commit", "-m", args.message])
    if args.push:
        branch = run(["git", "branch", "--show-current"], check=True).stdout.strip() or "main"
        for attempt in range(1, 4):
            pull = run(["git", "pull", "--rebase", "origin", branch], check=False)
            push = run(["git", "push", "origin", f"HEAD:{branch}"], check=False) if pull.returncode == 0 else None
            if pull.returncode == 0 and push is not None and push.returncode == 0:
                return 0
            print(f"push attempt {attempt} failed; retrying", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
