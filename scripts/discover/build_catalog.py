#!/usr/bin/env python3
"""Build the canonical channel catalog from source playlists and overrides."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import ChannelCatalog, country_codes, normalize_name, slugify
from scripts.lib.m3u import M3UEntry, parse_m3u
from scripts.lib.pipeline import (
    canonical_category,
    load_category_config,
    load_sources,
    write_json,
)



@dataclass
class Item:
    source: str
    entry: M3UEntry
    name: str
    country: str
    base_id: str
    explicit_id: str = ""
    channel_id: str = ""


def load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("channels", data)
    if not isinstance(records, dict):
        raise ValueError(f"{path} must contain a channels object")
    return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}


def alias_for(source: str, entry: M3UEntry, aliases: dict[str, Any]) -> str:
    source_map = aliases.get(source, {}) if isinstance(aliases, dict) else {}
    for candidate in sorted(entry.candidate_names(), key=lambda value: (len(value), value)):
        if candidate in source_map:
            return str(source_map[candidate])
        normalized = normalize_name(candidate)
        if normalized in source_map:
            return str(source_map[normalized])
    return ""


def collect_items(
    root: Path,
    existing: dict[str, dict[str, Any]],
    aliases: dict[str, Any],
) -> tuple[list[Item], dict[str, dict[str, Any]]]:
    sources = load_sources(root)
    category_map, default_category, category_aliases = load_category_config(root)
    del category_map
    manual_existing = {
        channel_id: record
        for channel_id, record in existing.items()
        if record.get("manual") is True
    }
    # Historical records are used only as identity hints. ChannelCatalog applies
    # country-aware disambiguation, and stale records are removed below when no
    # current source observation uses them.
    source_aliases = {
        source: dict(values)
        for source, values in (aliases.get("sources", {}) if aliases else {}).items()
    }
    ref_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for channel_id, record in existing.items():
        for source, refs in record.get("source_refs", {}).items():
            if not isinstance(refs, list):
                continue
            for ref in refs:
                ref_targets[(str(source), str(ref))].add(channel_id)
    for (source, ref), targets in ref_targets.items():
        if len(targets) == 1:
            source_aliases.setdefault(source, {})[ref] = next(iter(targets))
    catalog = ChannelCatalog(existing, source_aliases=source_aliases)

    preliminary: list[Item] = []
    for source_id, source in sources.items():
        if not source.playlist.exists():
            continue
        _, entries = parse_m3u(source.playlist)
        for entry in entries:
            name = entry.clean_name()
            country = entry.inferred_country(source.default_country or None)
            preliminary.append(
                Item(
                    source=source_id,
                    entry=entry,
                    name=name,
                    country=country,
                    base_id=slugify(name),
                    explicit_id=alias_for(source_id, entry, aliases.get("sources", {})),
                )
            )

    for item in preliminary:
        if not item.explicit_id:
            resolution = catalog.resolve(item.source, item.entry)
            if resolution.matched_by in {"canonical-name", "source-alias"}:
                item.explicit_id = resolution.channel_id

    # An unqualified slug is safe only when all observations share one country.
    country_groups: dict[str, set[str]] = defaultdict(set)
    automatic = [item for item in preliminary if not item.explicit_id]
    for item in automatic:
        country_groups[item.base_id].add(item.country)
    for item in automatic:
        if len(country_groups[item.base_id]) > 1:
            suffix = item.country.lower() if item.country else ""
            item.channel_id = f"{item.base_id}-{suffix}" if suffix else item.base_id
        else:
            item.channel_id = item.base_id
        if item.channel_id in manual_existing:
            suffix = item.country.lower() if item.country else "unmapped"
            item.channel_id = f"{item.base_id}-{suffix}"
    for item in preliminary:
        if item.explicit_id:
            item.channel_id = item.explicit_id

    return preliminary, existing


def make_record(items: list[Item], existing: dict[str, dict[str, Any]], default_category: str, category_aliases: dict[str, str]) -> dict[str, Any]:
    ordered = sorted(
        items,
        key=lambda item: (
            {"core": 0, "cdn": 1, "pluto-tv": 2, "tvivu": 3}.get(item.source, 9),
            item.name.lower(),
            item.entry.url,
        ),
    )
    first = ordered[0]
    record = dict(existing.get(first.channel_id, {}))
    record["name"] = first.name
    aliases: list[str] = []
    for item in ordered:
        aliases.extend([item.entry.title, item.name])
    if record.get("manual") is True:
        aliases.extend(str(value) for value in record.get("aliases", []))
    unique_aliases: list[str] = []
    seen_aliases: set[str] = set()
    for alias in aliases:
        key = normalize_name(alias)
        if alias.strip() and key and key not in seen_aliases:
            unique_aliases.append(alias.strip())
            seen_aliases.add(key)
    record["aliases"] = unique_aliases[:40]

    countries = {item.country for item in ordered if item.country}
    if record.get("manual") is True:
        countries.update(country_codes(record))
    record["countries"] = sorted(countries)

    category_counts: Counter[str] = Counter()
    for item in ordered:
        category_counts[canonical_category(item.entry, category_aliases, default_category)] += 1
    configured_values = record.get("categories", []) if record.get("manual") is True else []
    configured = [value for value in configured_values if isinstance(value, str)]
    category_order = list(configured)
    for category, _ in category_counts.most_common():
        if category not in category_order:
            category_order.append(category)
    record["categories"] = category_order or [default_category]

    discovered_sources = {item.source for item in ordered}
    if record.get("manual") is True:
        discovered_sources.update(record.get("sources", []))
    record["sources"] = sorted(discovered_sources)
    source_refs: dict[str, set[str]] = {}
    if record.get("manual") is True:
        for source, refs in record.get("source_refs", {}).items():
            if isinstance(refs, list):
                source_refs[str(source)] = {str(ref) for ref in refs}
    for item in ordered:
        refs = source_refs.setdefault(item.source, set())
        source_id = item.entry.attrs.get("tvg-id", "").strip()
        refs.add(source_id or normalize_name(item.name))
    record["source_refs"] = {
        source: sorted(refs) for source, refs in sorted(source_refs.items()) if refs
    }
    configured_logo = str(record.get("logo", "") or "")
    if "raw.githubusercontent.com/K-yzu/Logos/" not in configured_logo:
        record["logo"] = ""
    else:
        record["logo"] = configured_logo
    if not record.get("logo"):
        record["logo"] = next(
            (
                item.entry.attrs.get("tvg-logo", "")
                for item in ordered
                if "raw.githubusercontent.com/K-yzu/Logos/" in item.entry.attrs.get("tvg-logo", "")
            ),
            "",
        )
    record.setdefault("enabled", True)
    return record


def build_catalog(root: Path) -> dict[str, Any]:
    channels_path = root / "data/channels.json"
    aliases_path = root / "data/aliases.json"
    existing = load_existing(channels_path)
    aliases = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.exists() else {}
    _, default_category, category_aliases = load_category_config(root)
    items, _ = collect_items(root, existing, aliases)
    grouped: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        grouped[item.channel_id].append(item)

    explicit_targets = {
        str(target)
        for source_map in aliases.get("sources", {}).values()
        for target in source_map.values()
    }
    records = {
        channel_id: record
        for channel_id, record in existing.items()
        if record.get("manual") is True
        or not record.get("sources")
        or channel_id in grouped
        or channel_id in explicit_targets
    }
    for channel_id, channel_items in grouped.items():
        records[channel_id] = make_record(
            channel_items,
            existing,
            default_category,
            category_aliases,
        )

    return {
        "version": 1,
        "channels": {key: records[key] for key in sorted(records)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output = args.output or root / "data/channels.json"
    catalog = build_catalog(root)
    if args.dry_run:
        print(json.dumps({"channels": len(catalog["channels"])}))
        return 0
    write_json(output, catalog)
    print(f"wrote {len(catalog['channels'])} canonical channels to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
