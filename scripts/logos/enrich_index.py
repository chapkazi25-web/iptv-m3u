#!/usr/bin/env python3
"""
enrich_index.py — Build fallback artwork entries for channels K-yzu misses.

The primary artwork source is the metadata-only K-yzu/Logos index produced by
remote_index.py. That repository only covers a few thousand US/GB channels, so
most of the catalog has no logo. This script fills the gap from two sources, in
the order the project prefers:

  1. Grade TV   https://gradetv.net/logos/<id>
                The provider already hosts a card-size logo per channel and
                documents it, so these URLs are stable and cheap to resolve.
  2. Wikipedia  the article lead image, or a "logo" file listed on the
                article, resolved to a 256px raster thumbnail on
                upload.wikimedia.org.

Nothing is downloaded: only URLs are recorded, matching the K-yzu index.

Wikipedia is queried politely. Titles are batched 50 per request, requests are
sequential with a small delay, a descriptive User-Agent is sent as Wikimedia's
policy requires, and the result is cached to disk so a re-run costs nothing.

Usage:
  python scripts/logos/enrich_index.py
  python scripts/logos/enrich_index.py --limit 200 --wikipedia-delay 1.0
  python scripts/logos/enrich_index.py --skip-wikipedia
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import normalize_name, strip_quality
from scripts.lib.pipeline import write_json

DEFAULT_OUTPUT = PROJECT_ROOT / "data/logo-fallbacks.json"
GRADETV_API = "https://gradetv.net/api/channels"
GRADETV_LOGO = "https://gradetv.net/logos"
WIKI_API = "https://en.wikipedia.org/w/api.php"
THUMB_WIDTH = 256
BATCH = 50

# Wikimedia asks every client to identify itself with a contactable UA.
USER_AGENT = (
    "iptv-m3u-logo-index/1.0 (https://github.com/chapkazi25-web/iptv-m3u; "
    "playlist artwork lookup) python-urllib"
)

# Page furniture that matches "logo" but is never a channel logo.
JUNK_TITLE_RE = re.compile(
    r"commons-logo|wiktionary|wikipedia|wikimedia|wikisource|wikiquote|"
    r"portal|ambox|question[_ ]?book|edit[_-]?icon|sisterproject|"
    r"symbol|flag[_ ]?of|icon|user[_-]?icon|wiki[_-]?letter|"
    r"disambig|clear|commons-logo|poweredby|foldunfold",
    re.I,
)
LOGO_HINT_RE = re.compile(r"logo", re.I)
BRACKET_ANNOTATION_RE = re.compile(r"[\[(][^\])]*[\])]")


def title_variants(display: str) -> list[str]:
    """Query forms for a channel name, most specific first.

    Sources append editorial annotations to the channel name ("9Gem (720p)
    [Geo-blocked]"). No logo index uses that full string, so the annotation is
    stripped and the bare name retried.
    """
    candidates = [display.strip()]
    without_brackets = re.sub(r"\s{2,}", " ", BRACKET_ANNOTATION_RE.sub("", display)).strip()
    if without_brackets and without_brackets not in candidates:
        candidates.append(without_brackets)
    base = strip_quality(without_brackets or display).strip()
    if base and base not in candidates:
        candidates.append(base)
    return [value for value in candidates if value]


def looks_like_logo(file_name: str, article_title: str) -> bool:
    """True when a file on a channel article is plausibly its logo.

    Broadcast logos are almost always vector artwork, so an SVG lead image on a
    channel article is accepted even when the file name omits the word "logo"
    ("60-minutes.svg" on the 60 Minutes article). A raster lead image is only
    accepted when it explicitly says "logo", which keeps photographs such as
    "Cbs-building.jpg" out.
    """
    name = str(file_name or "")
    if not name or JUNK_TITLE_RE.search(name):
        return False
    if LOGO_HINT_RE.search(name):
        return True
    if not name.lower().endswith((".svg", ".pdf")):
        return False
    stem = re.sub(r"\.(svg|pdf)$", "", name.split("/")[-1].split(":")[-1], flags=re.I)
    stem = re.sub(r"[^a-z0-9]+", " ", stem).strip()
    wanted = re.sub(r"[^a-z0-9]+", " ", str(article_title or "").lower()).strip()
    return bool(wanted) and bool(stem) and (stem == wanted or wanted.startswith(stem))


def fetch_json(url: str, timeout: float = 60.0, retries: int = 3, delay: float = 0.0) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        if delay:
            time.sleep(delay)
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as error:
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


# ------------------------------------------------------------------ gradetv

def gradetv_entries() -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        params = urllib.parse.urlencode({"limit": 20, "offset": offset})
        payload = fetch_json(f"{GRADETV_API}?{params}", delay=0.15)
        items = payload.get("items") or []
        if not items:
            break
        for record in items:
            channel_id = str(record.get("id") or "")
            name = str(record.get("name") or "").strip()
            if not channel_id or not name:
                continue
            by_name[normalize_name(name)] = {
                "name": name,
                "url": f"{GRADETV_LOGO}/{urllib.parse.quote(channel_id, safe='')}",
                "country": str(record.get("country") or "").upper(),
                "kind": "channel",
                "source": "gradetv",
            }
        offset += len(items)
        if offset >= int(payload.get("total") or 0) or offset >= 20000:
            break
    return by_name


# ----------------------------------------------------------------- wikipedia

def wiki_batch(titles: list[str], prop: str, delay: float, extra: str = "") -> dict[str, dict[str, Any]]:
    """Query Wikipedia for up to BATCH titles, returning pages keyed by title."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "1",
        "prop": prop,
        "redirects": "1",
        "titles": "|".join(titles),
    }
    if extra:
        params.update(json.loads(extra))
    url = f"{WIKI_API}?{urllib.parse.urlencode(params)}"
    payload = fetch_json(url, delay=delay)
    pages = payload.get("query", {}).get("pages", [])
    if isinstance(pages, dict):  # formatversion=1 returns a list
        pages = list(pages.values())
    return {str(page.get("title", "")).lower(): page for page in pages if page.get("title")}


def pick_logo_file(page: dict[str, Any]) -> str:
    """Choose the file title most likely to be the channel logo."""
    title = str(page.get("title", ""))
    images = [str(item.get("title", "")) for item in page.get("images") or []]
    candidates = [name for name in images if looks_like_logo(name, title)]
    if not candidates:
        return ""

    def rank(name: str) -> tuple[int, int, int, str]:
        stem = name.lower()
        tokens = [token for token in re.split(r"[^a-z0-9]+", title.lower()) if len(token) > 2]
        overlap = sum(1 for token in tokens if token in stem)
        exact = 1 if title and stem.startswith(f"file:{title.lower()} ") else 0
        svg = 0 if stem.endswith(".svg") else 1
        return (-exact, -overlap, svg, name)

    return sorted(candidates, key=rank)[0]


def file_key(title: str) -> str:
    """Normalised key for a File: title.

    Wikipedia rewrites underscores to spaces in the titles it returns
    ("File:Fox_News_Channel_logo.svg" becomes "File:Fox News Channel logo.svg"),
    so the requested and returned titles must be reduced to the same form
    before they can be matched.
    """
    return str(title).split(":", 1)[-1].replace("_", " ").strip().lower()


def resolve_thumbnails(files: dict[str, str], delay: float) -> dict[str, str]:
    """Map a chosen File: title to a raster thumbnail URL."""
    out: dict[str, str] = {}
    items = list(files.items())
    for start in range(0, len(items), BATCH):
        chunk = items[start : start + BATCH]
        # files maps a channel key to the chosen "File:..." title.
        titles = [file_title for _, file_title in chunk]
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "1",
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": str(THUMB_WIDTH),
            "titles": "|".join(titles),
        }
        url = f"{WIKI_API}?{urllib.parse.urlencode(params)}"
        try:
            payload = fetch_json(url, delay=delay)
        except RuntimeError:
            continue
        pages = payload.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            thumb = info.get("thumburl") or info.get("url") or ""
            if not thumb:
                continue
            out[file_key(str(page.get("title", "")))] = thumb.split("?")[0]
    return out


def wikipedia_entries(
    wanted: list[dict[str, str]],
    delay: float,
    verbose: bool = True,
) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    # Every query variant is looked up, not just the display name, because a
    # channel called "9Gem (720p) [Geo-blocked]" has to reach the "9Gem" article.
    all_titles: list[str] = []
    for item in wanted:
        for variant in item.get("titles") or [item.get("title", "")]:
            if variant:
                all_titles.append(variant)
    titles = list(dict.fromkeys(all_titles))
    if not titles:
        return entries
    lead_pages: dict[str, dict[str, Any]] = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        try:
            lead_pages.update(wiki_batch(chunk, "pageimages", delay, '{"piprop": "name"}'))
        except RuntimeError as error:
            if verbose:
                print(f"warning: pageimages batch failed: {error}", file=sys.stderr)
        if verbose:
            print(f"  wikipedia lead pass {min(start + BATCH, len(titles))}/{len(titles)}", file=sys.stderr)

    chosen: dict[str, str] = {}
    remaining: list[str] = []
    for item in wanted:
        variants = item.get("titles") or [item.get("title", "")]
        page: dict[str, Any] = {}
        for variant in variants:
            page = lead_pages.get(variant.lower(), {})
            if page:
                break
        name = str(page.get("pageimage") or "")
        if looks_like_logo(name, str(page.get("title") or (variants[0] if variants else ""))):
            chosen[item["name_key"]] = f"File:{name}"
        else:
            remaining.extend(variant for variant in variants if variant)

    image_pages: dict[str, dict[str, Any]] = {}
    unique_remaining = list(dict.fromkeys(remaining))
    for start in range(0, len(unique_remaining), BATCH):
        chunk = unique_remaining[start : start + BATCH]
        try:
            image_pages.update(wiki_batch(chunk, "images", delay, '{"imlimit": "60"}'))
        except RuntimeError as error:
            if verbose:
                print(f"warning: images batch failed: {error}", file=sys.stderr)
        if verbose:
            print(f"  wikipedia image pass {min(start + BATCH, len(unique_remaining))}/{len(unique_remaining)}", file=sys.stderr)

    for item in wanted:
        variants = item.get("titles") or [item.get("title", "")]
        if not variants or item["name_key"] in chosen:
            continue
        page: dict[str, Any] = {}
        for variant in variants:
            page = image_pages.get(variant.lower(), {})
            if page:
                break
        file_title = pick_logo_file(page)
        if file_title:
            chosen[item["name_key"]] = file_title

    by_key = {item["name_key"]: item for item in wanted}
    file_to_url = resolve_thumbnails(
        {key: value for key, value in chosen.items()}, delay
    )
    for key, file_title in chosen.items():
        url = file_to_url.get(file_key(file_title))
        if not url:
            continue
        item = by_key[key]
        entries[key] = {
            "name": item["display"],
            "url": url,
            "country": item.get("country", ""),
            "kind": "channel",
            "source": "wikipedia",
        }
    return entries


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--catalog", type=Path, default=PROJECT_ROOT / "data/channels.json")
    parser.add_argument("--primary", type=Path, default=PROJECT_ROOT / "data/logo-index.json")
    parser.add_argument("--skip-gradetv", action="store_true")
    parser.add_argument("--skip-wikipedia", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--wiki-delay", type=float, default=0.34, dest="wiki_delay")
    args = parser.parse_args(argv)

    catalog = json.loads(args.catalog.read_text(encoding="utf-8")).get("channels", {})
    primary = json.loads(args.primary.read_text(encoding="utf-8")).get("logos", []) if args.primary.exists() else []
    have = {normalize_name(str(item.get("name", ""))) for item in primary}

    wanted: list[dict[str, str]] = []
    for channel_id, record in sorted(catalog.items()):
        if not record.get("enabled", True):
            continue
        display = str(record.get("name", ""))
        key = normalize_name(display)
        if not key or key in have:
            continue
        countries = record.get("countries") or []
        variants = title_variants(display)
        wanted.append({
            "title": variants[0],
            "titles": variants,
            "display": display,
            "name_key": key,
            "country": str(countries[0]).upper() if len(countries) == 1 else "",
        })
    if args.limit:
        wanted = wanted[: args.limit]
    print(f"{len(wanted)} channels have no K-yzu logo", file=sys.stderr)

    entries: dict[str, dict[str, Any]] = {}
    if not args.skip_gradetv:
        grade = gradetv_entries()
        for key, value in grade.items():
            if key in {item["name_key"] for item in wanted}:
                entries[key] = value
        print(f"gradetv: {len(grade)} logos available", file=sys.stderr)

    if not args.skip_wikipedia:
        remaining = [item for item in wanted if item["name_key"] not in entries]
        print(f"{len(remaining)} still need Wikipedia", file=sys.stderr)
        entries.update(wikipedia_entries(remaining, args.wiki_delay))

    payload = {
        "version": 1,
        "sources": ["gradetv", "wikipedia"],
        "counts": {
            "entries": len(entries),
            "gradetv": sum(1 for v in entries.values() if v["source"] == "gradetv"),
            "wikipedia": sum(1 for v in entries.values() if v["source"] == "wikipedia"),
        },
        "logos": [entries[key] for key in sorted(entries)],
    }
    write_json(args.output, payload)
    print(f"wrote {len(entries)} fallback logos -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
