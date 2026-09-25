#!/usr/bin/env python3
"""
generate.py — Convert the iptv-org community index into a source playlist.

Source:
  Index    : https://iptv-org.github.io/iptv/index.m3u
  Stream   : the upstream URL published in the index (not re-hosted)

iptv-org is a link directory rather than a host: it aggregates public
feeds and cannot guarantee a stream is still live. Entries are therefore
marked ephemeral so the health checker can retire dead links, and the
build prefers any verified source over them.

Usage:
  python sources/iptv-org/generate.py
  python sources/iptv-org/generate.py --output playlists/sources/iptv-org.m3u
  python sources/iptv-org/generate.py --country US,GB --category sports
  python sources/iptv-org/generate.py --drop-group "Live - Other Events"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "playlists/sources/iptv-org.m3u"
INDEX_URL = "https://iptv-org.github.io/iptv/index.m3u"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; iptv-m3u/1.0; +https://github.com/iptv-org/iptv)",
    "Accept": "audio/x-mpegurl,application/vnd.apple.mpegurl,*/*",
}

ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
SAFE_TVG_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
QUALITY_RE = re.compile(r"(\d{3,4})\s*[pi]", re.I)

# iptv-org uses semicolon-separated groups from a fixed vocabulary.
CATEGORY_MAP = {
    "general": "entertainment",
    "entertainment": "entertainment",
    "classic": "entertainment",
    "comedy": "entertainment",
    "series": "entertainment",
    "variety": "entertainment",
    "culture": "documentary",
    "documentary": "documentary",
    "science": "documentary",
    "travel": "documentary",
    "outdoor": "documentary",
    "cooking": "documentary",
    "lifestyle": "documentary",
    "religious": "documentary",
    "relax": "documentary",
    "news": "news",
    "public": "news",
    "legislative": "news",
    "sports": "sports",
    "music": "music",
    "kids": "kids",
    "animation": "kids",
    "family": "kids",
    "education": "kids",
    "movies": "movies",
    "auto": "movies",
    "shop": "movies",
    "weather": "weather",
    "business": "business",
    "interactive": "entertainment",
}


def download(url: str, timeout: float = 180.0, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            last = error
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}: {last}")


def parse_attrs(line: str) -> dict[str, str]:
    return {key.lower(): value for key, value in ATTR_RE.findall(line)}


def title_of(line: str) -> str:
    _, _, rest = line.partition(",")
    return rest.strip()


def m3u_escape(value: str) -> str:
    return value.replace('"', "'").strip()


def quality_rank(value: str) -> int:
    match = QUALITY_RE.search(value)
    return int(match.group(1)) if match else 0


def convert_group(group: str) -> str:
    for part in reversed([value.strip() for value in (group or "").split(";") if value.strip()]):
        mapped = CATEGORY_MAP.get(part.lower())
        if mapped:
            return mapped
    return "entertainment"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--country", default="", help="comma-separated ISO codes to keep")
    parser.add_argument("--category", default="", help="comma-separated groups to keep")
    parser.add_argument("--drop-group", default="", help="comma-separated groups to drop")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    keep_countries = {value.strip().upper() for value in args.country.split(",") if value.strip()}
    keep_categories = {value.strip().lower() for value in args.category.split(",") if value.strip()}
    drop_groups = {value.strip().lower() for value in args.drop_group.split(",") if value.strip()}

    raw = download(args.index_url, timeout=args.timeout)
    lines = raw.splitlines()

    entries: list[tuple[dict[str, str], str, str]] = []
    attributes: dict[str, str] = {}
    title = ""
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("#EXTINF"):
            attributes = parse_attrs(line)
            # The display name is the text after the final comma on the
            # #EXTINF line; attribute values may themselves contain commas.
            _, _, title = line.rpartition(",")
        elif line.startswith(("http://", "https://")) and attributes:
            group = attributes.get("group-title", "")
            country = attributes.get("tvg-country", "").upper()
            lowered = group.lower()
            if drop_groups and lowered in drop_groups:
                attributes = {}
                continue
            if keep_countries and country not in keep_countries:
                attributes = {}
                continue
            if keep_categories and not any(part.strip().lower() in keep_categories for part in group.split(";")):
                attributes = {}
                continue
            entries.append((attributes, title.strip(), line))
            attributes = {}

    # One entry per channel id, keeping the highest advertised quality.
    best: dict[str, tuple[int, tuple[dict[str, str], str, str]]] = {}
    for attributes, title, url in entries:
        channel_id = attributes.get("tvg-id") or attributes.get("tvg-name") or title
        key = SAFE_TVG_ID_RE.sub(".", channel_id).strip(".") or title
        if not (attributes.get("tvg-name") or "").strip() and not title:
            continue
        rank = quality_rank(title)
        current = best.get(key)
        if current is None or rank > current[0]:
            best[key] = (rank, (attributes, title, url))

    output = ["#EXTM3U"]
    for key in sorted(best):
        attributes, title, url = best[key][1]
        name = (attributes.get("tvg-name") or "").strip() or title
        if not name:
            continue
        attrs = [f'tvg-id="{m3u_escape(key)}"']
        attrs.append(f'tvg-name="{m3u_escape(name)}"')
        logo = attributes.get("tvg-logo", "")
        if logo:
            attrs.append(f'tvg-logo="{m3u_escape(logo)}"')
        country = attributes.get("tvg-country", "").upper()
        if country:
            attrs.append(f'tvg-country="{country}"')
        attrs.append(f'group-title="{m3u_escape(convert_group(attributes.get("group-title", "")))}"')
        output.append(f'#EXTINF:-1 {" ".join(attrs)},{name}')
        output.append(url)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(json.dumps({"index_entries": len(entries), "written": len(best), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
