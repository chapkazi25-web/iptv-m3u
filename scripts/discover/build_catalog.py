#!/usr/bin/env python3
"""Build the canonical channel catalog from source playlists and overrides."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import (
    QUALITY_SUFFIX_RE,
    ChannelCatalog,
    country_codes,
    normalize_name,
    slugify,
    strip_quality,
)
from scripts.lib.m3u import M3UEntry, parse_m3u
from scripts.lib.pipeline import (
    canonical_category,
    event_exclusion_reason,
    is_excluded,
    is_excluded_group,
    country_exclusion_reason,
    language_exclusion_reason,
    load_category_config,
    load_country_filter,
    load_exclusions,
    load_event_filter,
    load_language_filter,
    load_language_index,
    load_local_filter,
    load_name_patterns,
    load_quality_filter,
    load_radio_filter,
    load_region_filter,
    load_sources,
    local_exclusion_reason,
    match_name_category,
    name_quality,
    quality_exclusion_reason,
    radio_exclusion_reason,
    region_duplicates,
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


def _union(current: Any, incoming: Any) -> list[str]:
    """Return the ordered union of two catalog list values."""
    merged = [str(value) for value in current] if isinstance(current, list) else []
    seen = set(merged)
    if isinstance(incoming, list):
        for value in incoming:
            text = str(value)
            if text not in seen:
                merged.append(text)
                seen.add(text)
    return merged


def consolidate_existing(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold quality-suffix duplicates of one channel into a single record.

    An older catalog could hold both "Trace Africa" and "Trace Africa (1080p)"
    as separate IDs. Both register the same normalized alias, which makes name
    resolution ambiguous and blocks the rebuild. Merging them preserves
    curated data such as EPG IDs and manual flags, and is idempotent once the
    canonical ID equals the slug.
    """
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for channel_id, record in records.items():
        groups[slugify(str(record.get("name", "") or channel_id))].append((channel_id, record))

    merged: dict[str, dict[str, Any]] = {}
    for slug, members in groups.items():
        if len(members) == 1:
            merged[members[0][0]] = members[0][1]
            continue
        # Prefer the record that already owns the canonical slug, then the
        # shortest id, so the choice is deterministic across rebuilds.
        members.sort(key=lambda item: (item[0] != slug, len(item[0])))
        base_id, base = members[0]
        record = dict(base)
        # A name without an advertised resolution reads better publicly.
        plain = [
            item for _, item in members
            if not QUALITY_SUFFIX_RE.search(str(item.get("name", "")))
        ]
        if plain:
            record["name"] = str(plain[0].get("name"))
        refs: dict[str, set[str]] = {
            str(source): {str(value) for value in values}
            for source, values in (record.get("source_refs") or {}).items()
            if isinstance(values, list)
        }
        for _, other in members[1:]:
            for key in ("aliases", "countries", "categories", "sources"):
                merged_values = _union(record.get(key), other.get(key))
                if merged_values:
                    record[key] = merged_values
            for source, values in (other.get("source_refs") or {}).items():
                if isinstance(values, list):
                    refs.setdefault(str(source), set()).update(str(value) for value in values)
            if not record.get("epg_id") and other.get("epg_id"):
                record["epg_id"] = other["epg_id"]
            if other.get("manual") is True:
                record["manual"] = True
            if not record.get("logo") and other.get("logo"):
                record["logo"] = other["logo"]
            if other.get("enabled") is not False:
                record["enabled"] = True
        if refs:
            record["source_refs"] = {source: sorted(values) for source, values in sorted(refs.items())}
        merged[base_id] = record
    return merged


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
    exclusion_rules: dict[str, Any] | None = None,
    verbose: bool = True,
) -> tuple[list[Item], dict[str, dict[str, Any]]]:
    sources = load_sources(root)
    category_map, default_category, category_aliases = load_category_config(root)
    del category_map
    rules = exclusion_rules if exclusion_rules is not None else load_exclusions(root)
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

    dropped_groups: Counter[str] = Counter()
    preliminary: list[Item] = []
    for source_id, source in sources.items():
        if not source.playlist.exists():
            continue
        _, entries = parse_m3u(source.playlist)
        for entry in entries:
            if is_excluded_group(entry.group, rules):
                dropped_groups[entry.group] += 1
                continue
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

    if dropped_groups and verbose:
        for group, count in dropped_groups.most_common():
            print(f"excluded group {group!r}: {count} entries", file=sys.stderr)

    return preliminary, existing


def make_record(
    items: list[Item],
    existing: dict[str, dict[str, Any]],
    default_category: str,
    category_aliases: dict[str, str],
    name_patterns: list[tuple[re.Pattern[str], str]],
    source_order: dict[str, int],
) -> dict[str, Any]:
    ordered = sorted(
        items,
        key=lambda item: (
            source_order.get(item.source, 999),
            item.name.lower(),
            item.entry.url,
        ),
    )
    first = ordered[0]
    record = dict(existing.get(first.channel_id, {}))
    # The advertised resolution is kept as data and stripped from the display
    # name, so the public playlist never shows "(1080p)" or a trailing "HD".
    # The best quality across all sources is what the channel can deliver, and
    # the quality filter reads this field rather than re-parsing the name.
    heights = [
        height
        for height in (name_quality(item.name)[0] for item in ordered)
        if height is not None
    ]
    record["name"] = strip_quality(first.name)
    record["quality_height"] = max(heights) if heights else None
    record["not_24_7"] = any(name_quality(item.name)[1] for item in ordered)
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
        # Brand and sport channels arrive under generic groups such as
        # "Sports", so the display name is checked before the group title.
        named = match_name_category(item.name, name_patterns)
        category_counts[named or canonical_category(item.entry, category_aliases, default_category)] += 1
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


IPTVORG_QUALITY_RE = re.compile(
    r"\.(?:SD|HD|FHD|UHD|4K|360p|480p|540p|576[pi]|720p|1080[pi]|2160p)$", re.I
)


def lookup_language(
    index: dict[str, dict[str, Any]],
    channel_id: str,
    name: str,
    record: dict[str, Any],
) -> tuple[list[str], bool | None]:
    """Resolve a channel's language from the index, or unknown.

    Matching is by canonical id, then by iptv-org source reference, then by
    normalized name. The id is preferred because one display name can be
    shared by channels in different countries and languages.
    """
    if not index:
        return [], None
    keys = [channel_id]
    for ref in (record.get("source_refs") or {}).get("iptv-org", []):
        text = str(ref)
        keys.append(text.lower())
        keys.append(IPTVORG_QUALITY_RE.sub("", text.lower()))
    normalized = normalize_name(name)
    if normalized:
        keys.append(normalized)
    languages: set[str] = set()
    english: bool | None = None
    for key in keys:
        entry = index.get(key)
        if not entry:
            continue
        languages |= {str(value).lower() for value in (entry.get("languages") or [])}
        flag = entry.get("english")
        if flag is True:
            return sorted(languages), True
        if flag is False and english is None:
            english = False
    if english is False and any(value.startswith("eng") for value in languages):
        english = True
    return sorted(languages), english


def build_catalog(root: Path, *, verbose: bool = True) -> dict[str, Any]:
    channels_path = root / "data/channels.json"
    aliases_path = root / "data/aliases.json"
    existing = load_existing(channels_path)
    before = len(existing)
    existing = consolidate_existing(existing)
    if verbose and len(existing) != before:
        print(
            f"merged {before - len(existing)} quality-suffix duplicate records",
            file=sys.stderr,
        )
    aliases = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.exists() else {}
    _, default_category, category_aliases = load_category_config(root)
    name_patterns = load_name_patterns(root)
    exclusion_rules = load_exclusions(root)
    language_index = load_language_index(root)
    language_rules = load_language_filter(root)
    quality_rules = load_quality_filter(root)
    country_rules = load_country_filter(root)
    region_rules = load_region_filter(root)
    local_rules = load_local_filter(root)
    radio_rules = load_radio_filter(root)
    event_rules = load_event_filter(root)
    source_order = {
        source_id: source.priority for source_id, source in load_sources(root).items()
    }
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
            name_patterns,
            source_order,
        )

    # Excluded channels stay in the catalog as disabled records so their
    # identity and source references survive a rebuild, but they are never
    # published to the public playlist. Region variants are resolved last: a
    # variant is only ever folded into a record that survived everything else,
    # so the entry kept for a group is always one the playlist will carry.
    reasons: dict[str, str] = {}
    for channel_id, record in records.items():
        if record.get("manual") is True:
            continue
        name = str(record.get("name", ""))
        categories = [str(value) for value in record.get("categories", [])]
        languages, english = lookup_language(language_index, channel_id, name, record)
        if languages:
            record["languages"] = languages
        record["english"] = english
        reasons[channel_id] = (
            language_exclusion_reason(
                channel_id, name, categories, languages, english, language_rules
            )
            or quality_exclusion_reason(record, categories, quality_rules)
            or radio_exclusion_reason(name, categories, radio_rules)
            or event_exclusion_reason(record, event_rules)
            or is_excluded(channel_id, name, categories, exclusion_rules)
            or local_exclusion_reason(name, record, local_rules)
            or country_exclusion_reason(name, categories, record, country_rules)
        )

    published = {channel_id for channel_id, reason in reasons.items() if not reason}
    for channel_id, winner in region_duplicates(records, published, region_rules).items():
        reasons[channel_id] = f"duplicate-of:{winner}"

    for channel_id, record in records.items():
        if record.get("manual") is True:
            continue
        reason = reasons.get(channel_id, "")
        if reason:
            record["enabled"] = False
            record["excluded_by"] = reason
        else:
            # Recompute rather than inherit: a channel excluded by an earlier
            # rule must return once that rule no longer applies, otherwise
            # stale exclusions accumulate and channels vanish silently.
            record["enabled"] = True
            record.pop("excluded_by", None)

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
