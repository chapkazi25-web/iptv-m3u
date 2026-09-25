#!/usr/bin/env python3
"""Stage generated artifacts, commit when changed, and push safely.

Every file committed by these helpers is produced by a generator, never
hand-edited. That makes a textual merge of two independently generated
artifacts meaningless: when a rebase conflicts, the only correct
resolution is to throw the local result away and regenerate it from the
updated branch. Pass the generating commands via ``--regenerate`` so the
script can recover from a push race unattended.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_ATTEMPTS = 3


def run(
    command: list[str] | str,
    *,
    check: bool = True,
    capture: bool = False,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        check=check,
        capture_output=capture,
        shell=shell,
    )


def current_branch() -> str:
    return run(["git", "branch", "--show-current"], check=True, capture=True).stdout.strip() or "main"


def stage(paths: list[str]) -> None:
    run(["git", "add", "-A", "--", *paths])


def has_staged_changes() -> bool:
    return (
        run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0
    )


def commit(message: str, paths: list[str]) -> bool:
    stage(paths)
    if not has_staged_changes():
        return False
    run(["git", "commit", "-m", message])
    return True


def publish(message: str, paths: list[str], regenerate: list[str]) -> int:
    branch = current_branch()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pull = run(["git", "pull", "--rebase", "origin", branch], check=False)
        if pull.returncode == 0:
            push = run(["git", "push", "origin", f"HEAD:{branch}"], check=False)
            if push.returncode == 0:
                return 0
            print(f"push attempt {attempt} failed; retrying", file=sys.stderr)
            continue

        print("rebase conflict on generated files; regenerating from origin", file=sys.stderr)
        run(["git", "rebase", "--abort"], check=False)
        if not regenerate:
            print("no --regenerate command given; cannot resolve conflict", file=sys.stderr)
            return 1
        run(["git", "fetch", "origin", branch], check=False)
        run(["git", "reset", "--hard", "--quiet", "FETCH_HEAD"], check=False)
        for command in regenerate:
            if run(command, check=False, shell=True).returncode != 0:
                print(f"regeneration command failed: {command}", file=sys.stderr)
                return 1
        if not commit(message, paths):
            print("regeneration produced no changes; nothing to publish")
            return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--push", action="store_true")
    parser.add_argument(
        "--regenerate",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Shell command that regenerates the committed files; re-run after a failed rebase.",
    )
    args = parser.parse_args()

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])

    if not commit(args.message, args.paths):
        print("No generated changes to commit")
        return 0
    if args.push:
        return publish(args.message, args.paths, args.regenerate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
