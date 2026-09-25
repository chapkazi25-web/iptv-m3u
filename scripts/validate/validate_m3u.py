#!/usr/bin/env python3
"""Validate M3U syntax, canonical IDs, and generated playlist consistency."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import ChannelCatalog, validate_catalog_records
from scripts.lib.m3u import parse_m3u
from scripts.lib.pipeline import load_sources



def validate(root: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    catalog = ChannelCatalog.load(root)
    catalog_data = json.loads((root / "data/channels.json").read_text(encoding="utf-8"))
    errors.extend(validate_catalog_records(catalog_data.get("channels", {}).items()))

    playlist_files = sorted((root / "playlists").rglob("*.m3u"))
    if not playlist_files:
        errors.append("no M3U playlists found")

    all_entries = []
    all_by_id: dict[str, object] = {}
    epg_to_canonical = {
        str(record.get("epg_id")): channel_id
        for channel_id, record in catalog.records.items()
        if record.get("epg_id")
    }
    for path in playlist_files:
        relative = path.relative_to(root).as_posix()
        header, entries = parse_m3u(path)
        if not header.upper().startswith("#EXTM3U"):
            errors.append(f"{relative}: missing #EXTM3U header")
        for entry in entries:
            if not entry.title.strip():
                errors.append(f"{relative}:{entry.line_number}: empty channel title")
            if not entry.url.startswith(("http://", "https://")):
                errors.append(f"{relative}:{entry.line_number}: unsupported or empty URL")
            identifier = entry.attrs.get("tvg-id", "").strip()
            if not identifier:
                errors.append(f"{relative}:{entry.line_number}: missing tvg-id")
            elif relative == "playlists/all.m3u":
                canonical_id = identifier if catalog.get(identifier) else epg_to_canonical.get(identifier)
                if canonical_id is None:
                    errors.append(f"playlists/all.m3u: id {identifier!r} is absent from the channel catalog")
                    continue
                if canonical_id in all_by_id:
                    errors.append(f"playlists/all.m3u: duplicate canonical id {canonical_id!r}")
                all_by_id[canonical_id] = entry
                all_entries.append(entry)

    for source in load_sources(root).values():
        if not source.playlist.exists():
            try:
                display_path = source.playlist.relative_to(root)
            except ValueError:
                display_path = source.playlist
            errors.append(f"configured source output is missing: {display_path}")

    index_path = root / "data/stream-index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        indexed = index.get("channels", {})
        if set(indexed) != set(all_by_id):
            missing = sorted(set(all_by_id) - set(indexed))
            extra = sorted(set(indexed) - set(all_by_id))
            errors.append(f"stream index mismatch (missing={missing[:5]}, extra={extra[:5]})")
        for identifier, channel in indexed.items():
            selected = channel.get("selected_stream_id")
            if not selected:
                errors.append(f"stream index channel {identifier!r} has no selected stream")
    else:
        errors.append("data/stream-index.json is missing")

    if not all_entries:
        errors.append("playlists/all.m3u has no entries")
    country_counts = Counter(entry.attrs.get("tvg-country", "") for entry in all_entries)
    if country_counts.get("", 0):
        warnings.append(f"{country_counts['']} channels have no country metadata")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "playlists": len(playlist_files),
        "channels": len(all_entries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = validate(args.root.resolve())
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        for warning in result["warnings"]:
            print(f"warning: {warning}")
        for error in result["errors"]:
            print(f"error: {error}")
        print(
            f"validated {result['playlists']} playlists and {result['channels']} canonical channels: "
            f"{'PASS' if result['valid'] else 'FAIL'}"
        )
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
