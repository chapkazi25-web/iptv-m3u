#!/usr/bin/env python3
"""Merge source playlists into one canonical, de-duplicated public playlist."""

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

from scripts.lib.catalog import ChannelCatalog, country_codes
from scripts.lib.m3u import M3UEntry, parse_m3u, write_m3u
from scripts.lib.pipeline import (
    HealthStore,
    LogoIndex,
    category_name,
    load_category_config,
    load_sources,
    quality_rank,
    stream_id,
    write_json,
)

RAW_BASE_URL = "https://raw.githubusercontent.com/K-yzu/Logos/main"
EPG_URL = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"
ALL_HEADER = f'#EXTM3U url-tvg="{EPG_URL}"'


@dataclass
class Candidate:
    source: str
    channel_id: str
    country: str
    entry: M3UEntry
    identifier: str

    def stream_data(self, health: HealthStore, priority: int) -> dict[str, Any]:
        record = health.record(self.identifier)
        return {
            "source": self.source,
            "source_priority": priority,
            "url": self.entry.url,
            "stream_id": self.identifier,
            "status": record.get("status", "unknown"),
            "enabled": health.enabled(self.identifier),
            "failures": health.failures(self.identifier),
            "response_time_ms": health.response_time(self.identifier),
            "quality_rank": quality_rank(self.entry),
        }


def output_entry(
    record: dict[str, Any],
    candidate: Candidate,
    number: int,
    logo: str,
    category: str,
) -> M3UEntry:
    countries = sorted(country_codes(record))
    country = candidate.country or (countries[0] if len(countries) == 1 else "")
    attrs = {
        "tvg-chno": str(number),
        "tvg-id": str(record.get("epg_id") or candidate.channel_id),
        "tvg-name": str(record["name"]),
    }
    if logo:
        attrs["tvg-logo"] = logo
    if country:
        attrs["tvg-country"] = country
    attrs["group-title"] = category
    return M3UEntry(
        duration="-1",
        attrs=attrs,
        title=str(record["name"]),
        url=candidate.entry.url,
        extra_lines=list(candidate.entry.extra_lines),
    )


def build(root: Path, *, strict: bool = True) -> dict[str, Any]:
    sources = load_sources(root)
    catalog = ChannelCatalog.load(root)
    health = HealthStore.load(root / "data/stream-health.json")
    logos = LogoIndex.load(root / "data/logo-index.json", RAW_BASE_URL)
    categories, default_category, _ = load_category_config(root)

    candidates_by_channel: dict[str, list[Candidate]] = defaultdict(list)
    unresolved: set[str] = set()
    source_counts: Counter[str] = Counter()
    seen_streams: set[str] = set()
    for source_id, source in sources.items():
        if not source.playlist.exists():
            raise FileNotFoundError(f"source playlist is missing: {source.playlist}")
        _, entries = parse_m3u(source.playlist)
        for entry in entries:
            resolution = catalog.resolve(source_id, entry, source.default_country or None)
            if resolution.matched_by in {"fallback", "ambiguous-fallback"}:
                unresolved.add(f"{source_id}:{resolution.channel_id}")
            identifier = stream_id(source_id, resolution.channel_id, entry.url)
            if identifier in seen_streams:
                continue
            seen_streams.add(identifier)
            candidate = Candidate(
                source=source_id,
                channel_id=resolution.channel_id,
                country=resolution.country,
                entry=entry,
                identifier=identifier,
            )
            candidates_by_channel[resolution.channel_id].append(candidate)
            source_counts[source_id] += 1

    if strict and unresolved:
        sample = ", ".join(sorted(unresolved)[:10])
        raise ValueError(f"{len(unresolved)} channels are not in data/channels.json: {sample}")

    selected_entries: dict[str, M3UEntry] = {}
    selected_candidates: dict[str, Candidate] = {}
    selected_categories: dict[str, str] = {}
    selected_countries: dict[str, str] = {}
    index_channels: dict[str, Any] = {}

    ordered_ids = sorted(
        candidates_by_channel,
        key=lambda channel_id: (
            str(catalog.get(channel_id) or {}.get("name", channel_id)).casefold(),
            channel_id,
        ),
    )
    for channel_id in ordered_ids:
        record = catalog.get(channel_id)
        if record is None or not record.get("enabled", True):
            continue
        source = sources
        usable = [
            candidate
            for candidate in candidates_by_channel[channel_id]
            if health.enabled(candidate.identifier)
        ]
        if not usable:
            continue
        selected = min(
            usable,
            key=lambda candidate: (
                *health.score(candidate.identifier, source[candidate.source].priority),
                -quality_rank(candidate.entry),
                candidate.identifier,
                candidate.entry.url,
            ),
        )
        primary_category = str((record.get("categories") or [default_category])[0])
        logo = logos.find(str(record.get("name", "")), selected.country)
        if not logo:
            configured_logo = str(record.get("logo", "") or "")
            if "raw.githubusercontent.com/K-yzu/Logos/" in configured_logo:
                logo = configured_logo
        entry = output_entry(
            record,
            selected,
            len(selected_entries) + 1,
            logo,
            category_name(primary_category, categories),
        )
        selected_entries[channel_id] = entry
        selected_candidates[channel_id] = selected
        selected_categories[channel_id] = primary_category

        countries = sorted(country_codes(record))
        selected_country = selected.country or (countries[0] if len(countries) == 1 else "")
        selected_countries[channel_id] = selected_country
        index_channels[channel_id] = {
            "name": record["name"],
            "country": selected_country or None,
            "categories": record.get("categories", [default_category]),
            "selected_source": selected.source,
            "selected_stream_id": selected.identifier,
            "streams": [
                candidate.stream_data(health, source[candidate.source].priority)
                for candidate in sorted(
                    candidates_by_channel[channel_id],
                    key=lambda value: (source[value.source].priority, value.identifier),
                )
            ],
        }

    all_entries = [selected_entries[channel_id] for channel_id in ordered_ids if channel_id in selected_entries]
    all_path = root / "playlists/all.m3u"
    write_m3u(all_path, ALL_HEADER, all_entries)

    category_counts: Counter[str] = Counter()
    for channel_id in ordered_ids:
        if channel_id not in selected_entries:
            continue
        record = catalog.get(channel_id) or {}
        for category_id in record.get("categories", []):
            category_counts[str(category_id)] += 1

    country_groups: dict[str, list[str]] = defaultdict(list)
    international: list[str] = []
    for channel_id in ordered_ids:
        if channel_id not in selected_entries:
            continue
        country = selected_countries[channel_id]
        if country:
            country_groups[country].append(channel_id)
        else:
            international.append(channel_id)

    stream_index = {
        "version": 1,
        "counts": {
            "channels": len(all_entries),
            "sources": dict(sorted(source_counts.items())),
            "countries": {country: len(ids) for country, ids in sorted(country_groups.items())},
            "categories": dict(sorted(category_counts.items())),
            "unresolved": len(unresolved),
        },
        "channels": {key: index_channels[key] for key in ordered_ids if key in index_channels},
    }
    write_json(root / "data/stream-index.json", stream_index)
    report = {
        "version": 1,
        "total_channels": len(all_entries),
        "source_channels": dict(sorted(source_counts.items())),
        "category_channels": dict(sorted(category_counts.items())),
        "country_channels": {country: len(ids) for country, ids in sorted(country_groups.items())},
        "international_channels": len(international),
        "unresolved_channels": sorted(unresolved),
    }
    write_json(root / "data/build-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--allow-unmapped", action="store_true")
    args = parser.parse_args(argv)
    report = build(args.root.resolve(), strict=not args.allow_unmapped)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
