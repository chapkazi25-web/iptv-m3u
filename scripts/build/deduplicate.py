#!/usr/bin/env python3
"""Compatibility entry point for the canonical playlist de-duplication build."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build.merge_playlists import build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--allow-unmapped", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.root.resolve(), strict=not args.allow_unmapped), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
