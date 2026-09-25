#!/usr/bin/env python3
"""Import EPGShare XMLTV channel IDs and attach them to canonical channels.

The XML file is streamed and never committed. Matching is intentionally
conservative: an EPGShare ID is assigned only when a source reference or an
unambiguous country-aware display name identifies exactly one canonical
channel. Unmatched channels keep their stable internal ID in the public M3U.
"""

from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import country_codes, normalize_name
from scripts.lib.pipeline import load_json, write_json

DEFAULT_CONFIG = PROJECT_ROOT / "config/epg.json"
DEFAULT_CHANNELS = PROJECT_ROOT / "data/channels.json"
DEFAULT_MAP = PROJECT_ROOT / "data/epg-map.json"
DEFAULT_OVERRIDES = PROJECT_ROOT / "config/epg-overrides.json"
CHANNEL_RE = re.compile(r"<channel\b[^>]*\bid=\"([^\"]+)\"")
DISPLAY_NAME_RE = re.compile(r"<display-name(?:\s+lang=\"([^\"]*)\")?>(.*?)</display-name>")
COUNTRY_SUFFIX_RE = re.compile(r"\.([a-z]{2})(?:2|_locals\d+)?$", re.I)


def normalize_id(value: str) -> str:
    text = value.lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def epg_country(channel_id: str) -> str:
    match = COUNTRY_SUFFIX_RE.search(channel_id)
    if not match:
        return ""
    country = match.group(1).upper()
    return "GB" if country == "UK" else country


def is_usable(channel_id: str) -> bool:
    normalized = normalize_id(channel_id)
    return bool(normalized) and "dummy" not in normalized


@contextmanager
def open_xml(source: str, timeout: float) -> Iterator[io.TextIOBase]:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "iptv-m3u-epg-importer/1.0", "Accept-Encoding": "gzip"},
        )
        response = urllib.request.urlopen(request, timeout=timeout)
        try:
            if source.endswith(".gz"):
                binary: io.BufferedIOBase = gzip.GzipFile(fileobj=response)
            else:
                binary = response
            with io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace") as text:
                yield text
        finally:
            response.close()
        return

    path = Path(source)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", errors="replace") as text:
        yield text


def read_epg_channels(source: str, timeout: float) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    with open_xml(source, timeout) as text:
        for line in text:
            if current is None:
                match = CHANNEL_RE.search(line)
                if match:
                    current = {"id": html.unescape(match.group(1)).strip(), "names": []}
                continue
            for language, name in DISPLAY_NAME_RE.findall(line):
                value = html.unescape(name).strip()
                if value:
                    current["names"].append((language or "en", value))
            if "</channel>" in line:
                if current["id"]:
                    channels.append(current)
                current = None
    return channels


def load_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = load_json(path)
    return {str(key): str(value) for key, value in data.get("channels", {}).items()}


def match_channels(epg_channels: list[dict[str, Any]], catalog: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_name_country: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for channel in epg_channels:
        channel_id = str(channel["id"])
        if not is_usable(channel_id):
            continue
        by_id[normalize_id(channel_id)].append(channel)
        country = epg_country(channel_id)
        seen_names: set[str] = set()
        for language, name in channel["names"]:
            if language not in ("en", "") and language in seen_names:
                continue
            key = normalize_name(name)
            if not key or key in seen_names:
                continue
            seen_names.add(key)
            by_name[key].append(channel)
            if country:
                by_name_country[(key, country)].append(channel)

    records = catalog.get("channels", catalog)
    # If multiple canonical channels point at the same source ID, that source
    # ID is generic and cannot safely identify a channel.
    source_id_targets: dict[str, set[str]] = defaultdict(set)
    for channel_id, record in records.items():
        for refs in record.get("source_refs", {}).values():
            if isinstance(refs, list):
                for source_ref in refs:
                    source_id_targets[normalize_id(str(source_ref))].add(channel_id)

    proposals: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for channel_id, record in records.items():
        countries = country_codes(record)
        source_matches: set[str] = set()
        for refs in record.get("source_refs", {}).values():
            if not isinstance(refs, list):
                continue
            for source_ref in refs:
                for candidate in by_id.get(normalize_id(str(source_ref)), []):
                    normalized_candidate = normalize_id(str(candidate["id"]))
                    if len(source_id_targets.get(normalized_candidate, {channel_id})) == 1:
                        source_matches.add(str(candidate["id"]))
        if len(source_matches) == 1:
            proposals[channel_id].append((next(iter(source_matches)), "source-id"))
            continue

        names = {normalize_name(str(record.get("name", "")))}
        names.update(normalize_name(str(alias)) for alias in record.get("aliases", []))
        name_matches: set[str] = set()
        for name in names:
            if not name:
                continue
            if countries:
                for country in countries:
                    for candidate in by_name_country.get((name, country), []):
                        name_matches.add(str(candidate["id"]))
            else:
                for candidate in by_name.get(name, []):
                    name_matches.add(str(candidate["id"]))
        if len(name_matches) == 1:
            proposals[channel_id].append((next(iter(name_matches)), "name-country" if countries else "name"))

    # Remove collisions where two canonical channels claim the same EPG ID.
    epg_claims: dict[str, set[str]] = defaultdict(set)
    for channel_id, choices in proposals.items():
        for epg_id, _ in choices:
            epg_claims[epg_id].add(channel_id)
    matches: dict[str, dict[str, str]] = {}
    ambiguous = 0
    for channel_id, choices in proposals.items():
        usable = [(epg_id, method) for epg_id, method in choices if len(epg_claims[epg_id]) == 1]
        if not usable:
            ambiguous += 1
            continue
        usable.sort(key=lambda value: (0 if value[1] == "source-id" else 1, value[0]))
        matches[channel_id] = {"epg_id": usable[0][0], "method": usable[0][1]}

    stats = {
        "epg_channels": len(epg_channels),
        "usable_epg_channels": sum(1 for channel in epg_channels if is_usable(str(channel["id"]))),
        "catalog_channels": len(records),
        "matched": len(matches),
        "ambiguous": ambiguous,
        "unmatched": len(records) - len(matches) - ambiguous,
    }
    return matches, stats


def update_catalog(path: Path, matches: dict[str, dict[str, str]], overrides: dict[str, str]) -> None:
    data = load_json(path)
    records = data.get("channels", data)
    for channel_id, record in records.items():
        if channel_id in overrides:
            record["epg_id"] = overrides[channel_id]
        elif channel_id in matches:
            record["epg_id"] = matches[channel_id]["epg_id"]
        else:
            record.pop("epg_id", None)
    write_json(path, data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source", default=None, help="local .xml/.xml.gz path or override URL")
    parser.add_argument("--channels", type=Path, default=DEFAULT_CHANNELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    args = parser.parse_args(argv)

    config = load_json(args.config)
    source = args.source or str(config["xml_url"])
    timeout = float(config.get("timeout_seconds", 300))
    epg_channels = read_epg_channels(source, timeout)
    catalog = load_json(args.channels)
    matches, stats = match_channels(epg_channels, catalog)
    overrides = load_overrides(args.overrides)
    update_catalog(args.channels, matches, overrides)

    mapped = dict(matches)
    for channel_id, epg_id in overrides.items():
        mapped[channel_id] = {"epg_id": epg_id, "method": "override"}
    write_json(
        args.output,
        {
            "version": 1,
            "source": str(config.get("xml_url", source)),
            "source_url": str(config.get("xml_url", source)),
            "pdf_url": str(config.get("pdf_url", "")),
            "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "stats": stats,
            "channels": {key: mapped[key] for key in sorted(mapped)},
        },
    )
    print(json.dumps({**stats, "overrides": len(overrides)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
