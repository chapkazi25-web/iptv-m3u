#!/usr/bin/env python3
"""Normalize source M3U metadata without changing stream URLs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.m3u import parse_m3u, write_m3u
from scripts.lib.pipeline import category_name, canonical_category, load_category_config, load_sources



def normalize(root: Path) -> int:
    categories, default, aliases = load_category_config(root)
    changed = 0
    for source in load_sources(root).values():
        header, entries = parse_m3u(source.playlist)
        for entry in entries:
            entry.attrs["tvg-name"] = entry.clean_name()
            category = canonical_category(entry, aliases, default)
            entry.attrs["group-title"] = category_name(category, categories)
        write_m3u(source.playlist, header, entries)
        changed += len(entries)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    print(f"normalized {normalize(args.root.resolve())} source entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
