#!/usr/bin/env python3
"""Rewrite playlist tvg-logo attributes to public K-yzu raw URLs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import ChannelCatalog
from scripts.lib.m3u import parse_m3u, write_m3u
from scripts.lib.pipeline import LogoIndex, load_sources

DEFAULT_INDEX = PROJECT_ROOT / "data/logo-index.json"
DEFAULT_BASE = "https://raw.githubusercontent.com/K-yzu/Logos/main"


def rewrite(root: Path, index_path: Path) -> dict[str, int]:
    catalog = ChannelCatalog.load(root)
    logos = LogoIndex.load(index_path, DEFAULT_BASE)
    sources = load_sources(root)
    source_by_playlist = {
        source.playlist.resolve(): (source.source_id, source.default_country)
        for source in sources.values()
    }
    matched = 0
    unmatched = 0
    files = 0
    for path in sorted((root / "playlists").rglob("*.m3u")):
        header, entries = parse_m3u(path)
        source_info = source_by_playlist.get(path.resolve(), ("", ""))
        for entry in entries:
            country = entry.explicit_country or source_info[1]
            names = [entry.name, entry.title, entry.clean_name()]
            if source_info[0]:
                resolution = catalog.resolve(source_info[0], entry, source_info[1] or None)
                record = catalog.get(resolution.channel_id)
                if record:
                    names.insert(0, str(record.get("name", "")))
            logo = next((logos.find(name, country) for name in names if name), "")
            if logo:
                entry.attrs["tvg-logo"] = logo
                matched += 1
            else:
                entry.attrs.pop("tvg-logo", None)
                unmatched += 1
        write_m3u(path, header, entries)
        files += 1
    return {"files": files, "matched": matched, "unmatched": unmatched}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    args = parser.parse_args(argv)
    result = rewrite(args.root.resolve(), args.index)
    print(
        f"rewrote {result['files']} playlists: {result['matched']} K-yzu logos matched, "
        f"{result['unmatched']} entries left without a logo"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
