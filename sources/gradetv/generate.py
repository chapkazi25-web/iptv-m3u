#!/usr/bin/env python3
"""
generate.py — Build an M3U source playlist from the Grade TV public API.

Source:
  Catalog : GET https://gradetv.net/api/channels  (paginated, 20 per page)
  Filters : country, q (free-text), category, language, network, quality
  Stream  : GET https://m3m8.gradetv.net/api/s/:id   (stable per-channel hop)
  Logo    : GET https://gradetv.net/logos/:id          (card variant, <=256px)

Grade TV does not store or re-host video: it is a stable directory hop in
front of each channel's public feed. Every playlist entry therefore points
at the Grade hop rather than the upstream origin.

Usage:
  python sources/gradetv/generate.py
  python sources/gradetv/generate.py --country TZ --query trace
  python sources/gradetv/generate.py --online-only --limit 500
  python sources/gradetv/generate.py --all           # full playable catalog

By default only the curated channel list from config/gradetv.json is
fetched. Pass --all to sweep the entire playable catalog.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "playlists/sources/gradetv.m3u"
DEFAULT_CONFIG = PROJECT_ROOT / "config/gradetv.json"

API_BASE = "https://gradetv.net/api"
STREAM_BASE = "https://m3m8.gradetv.net/api/s"
LOGO_BASE = "https://gradetv.net/logos"
PAGE_SIZE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

SAFE_TVG_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
QUALITY_RE = re.compile(r"(\d{3,4})\s*[pi]", re.I)

# Grade TV reports categories in Portuguese; map them onto our own slugs.
CATEGORY_MAP = {
    "general": "entertainment",
    "variety": "entertainment",
    "entertainment": "entertainment",
    "series": "entertainment",
    "comedy": "entertainment",
    "classic": "entertainment",
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
    "sports": "sports",
    "sports;music": "sports",
    "music": "music",
    "religious;music": "music",
    "kids": "kids",
    "animation": "kids",
    "family": "kids",
    "education": "kids",
    "movies": "movies",
    "auto": "movies",
    "shop": "movies",
    "interactive": "entertainment",
    "public": "news",
    "legislative": "news",
}


class GradeTVError(RuntimeError):
    pass


def fetch_json(url: str, timeout: float = 45.0, retries: int = 4) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as error:
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise GradeTVError(f"failed to fetch {url}: {last}")


def query_url(params: dict[str, Any]) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "", [])}
    return f"{API_BASE}/channels?{urllib.parse.urlencode(clean, doseq=True)}"


def search(
    *,
    country: str = "",
    query: str = "",
    category: str = "",
    limit: int = 0,
    online_only: bool = False,
) -> list[dict[str, Any]]:
    """Page through /api/channels and return the matching channel records."""
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        params: dict[str, Any] = {
            "country": country,
            "q": query,
            "category": category,
            "limit": PAGE_SIZE,
            "offset": offset,
            "playable": 1,
        }
        if online_only:
            params["online"] = 1
        payload = fetch_json(query_url(params))
        items = payload.get("items") or []
        if not items:
            break
        found.extend(items)
        if limit and len(found) >= limit:
            return found[:limit]
        offset += len(items)
        total = int(payload.get("total") or 0)
        if offset >= total or offset >= 10000:
            break
        time.sleep(0.25)
    return found


def quality_rank(value: str | None) -> int:
    match = QUALITY_RE.search(str(value or ""))
    return int(match.group(1)) if match else 0


def primary_category(record: dict[str, Any]) -> str:
    for value in record.get("categories") or []:
        mapped = CATEGORY_MAP.get(str(value).lower())
        if mapped:
            return mapped
    return "entertainment"


def tvg_id(record: dict[str, Any]) -> str:
    channel_id = str(record.get("id") or record.get("slug") or record.get("name") or "")
    return SAFE_TVG_ID_RE.sub(".", channel_id).strip(".")


def stream_url(record: dict[str, Any]) -> str:
    """Return the Grade hop for the selected feed of a channel.

    Grade TV publishes one stream per feed (HD, SD, ...) and each carries its
    own health. The channel-level flag can disagree with the feed actually
    served, so the feed list decides which URL we publish.
    """
    chosen = choose_stream(record)
    return str(chosen.get("url") or "") if chosen else ""


def choose_stream(record: dict[str, Any], *, online_only: bool = True) -> dict[str, Any] | None:
    streams = [s for s in (record.get("streams") or []) if s.get("url")]
    if not streams:
        return None
    if online_only:
        online = [s for s in streams if (s.get("health_ext") or {}).get("status") == "online"]
        if online:
            streams = online
    return max(
        streams,
        key=lambda s: (
            int((s.get("health_ext") or {}).get("score") or 0),
            quality_rank(s.get("quality")),
        ),
    )


def logo_url(record: dict[str, Any]) -> str:
    channel_id = str(record.get("id") or "")
    if not channel_id:
        return ""
    return f"{LOGO_BASE}/{urllib.parse.quote(channel_id, safe='')}"


def m3u_escape(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").strip()


def render(records: Iterable[dict[str, Any]]) -> str:
    lines = ["#EXTM3U"]
    for record in records:
        url = stream_url(record)
        if not url:
            continue
        name = str(record.get("name") or record.get("slug") or "").strip()
        if not name:
            continue
        country = str(record.get("country") or "").upper()
        logo = logo_url(record)
        attrs = [f'tvg-id="{m3u_escape(tvg_id(record))}"', f'tvg-name="{m3u_escape(name)}"']
        if logo:
            attrs.append(f'tvg-logo="{m3u_escape(logo)}"')
        if country:
            attrs.append(f'tvg-country="{country}"')
        attrs.append(f'group-title="{m3u_escape(primary_category(record))}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{name}')
        lines.append(url)
    return "\n".join(lines) + "\n"


def dedupe(records: list[dict[str, Any]], *, include_offline: bool = False) -> list[dict[str, Any]]:
    """Keep one record per channel, preferring the healthiest resolved feed.

    Grade TV reports per-feed health, so a channel whose every feed is
    offline is skipped unless include_offline is set. Publishing a link the
    provider already knows is dead only adds noise for the health checker.
    """
    best: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
    for record in records:
        key = tvg_id(record)
        chosen = choose_stream(record)
        if not key or chosen is None:
            continue
        health = chosen.get("health_ext") or {}
        is_online = health.get("status") == "online"
        if not is_online and not include_offline:
            continue
        rank = (1 if is_online else 0, int(health.get("score") or 0))
        current = best.get(key)
        if current is None or rank > current[0]:
            best[key] = (rank, record)
    return [best[key][1] for key in sorted(best)]


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_detail(record: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    """Resolve the per-feed stream list for one channel.

    The search listing only carries channel-level metadata; the playable
    stream URLs and their individual health live on the detail endpoint.
    """
    channel_id = str(record.get("id") or "")
    if not channel_id:
        return None
    try:
        payload = fetch_json(
            f"{API_BASE}/channels/{urllib.parse.quote(channel_id, safe='')}",
            timeout=timeout,
        )
    except GradeTVError as error:
        print(f"warning: {channel_id}: {error}", file=sys.stderr)
        return None
    detail = payload.get("channel") or payload
    merged = dict(record)
    merged["streams"] = detail.get("streams") or []
    for key in ("logo_url", "website", "owners", "categories", "country", "name"):
        if detail.get(key) and not merged.get(key):
            merged[key] = detail[key]
    return merged


def resolve_details(
    records: list[dict[str, Any]],
    *,
    workers: int,
    timeout: float,
) -> list[dict[str, Any]]:
    if not records:
        return []
    resolved: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_detail, record, timeout): record for record in records}
        for future in concurrent.futures.as_completed(futures):
            detail = future.result()
            if detail and detail.get("streams"):
                resolved.append(detail)
    return resolved


def collect_from_config(config: dict[str, Any], *, online_only: bool) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_requests: set[tuple[str, str]] = set()

    for country in config.get("countries", []):
        key = (str(country), "")
        if key not in seen_requests:
            seen_requests.add(key)
            records.extend(search(country=str(country), online_only=online_only))
            time.sleep(0.3)

    for query in config.get("queries", []):
        key = ("", str(query))
        if key not in seen_requests:
            seen_requests.add(key)
            records.extend(search(query=str(query), online_only=online_only))
            time.sleep(0.3)

    for channel_id in config.get("channels", []):
        try:
            payload = fetch_json(f"{API_BASE}/channels/{urllib.parse.quote(str(channel_id), safe='')}")
        except GradeTVError as error:
            print(f"warning: {channel_id}: {error}", file=sys.stderr)
            continue
        record = payload.get("channel") or payload
        if record.get("id"):
            records.append(record)

    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--country", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--all", action="store_true", help="sweep the full playable catalog")
    parser.add_argument("--online-only", action="store_true")
    parser.add_argument(
        "--include-offline",
        action="store_true",
        help="keep channels whose every feed is reported offline",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args(argv)

    if args.all:
        records = search(
            country=args.country,
            query=args.query,
            category=args.category,
            limit=args.limit,
            online_only=args.online_only,
        )
    else:
        records = collect_from_config(load_config(args.config), online_only=args.online_only)

    print(f"collected {len(records)} candidate channels; resolving streams", file=sys.stderr)
    resolved = resolve_details(records, workers=args.workers, timeout=args.timeout)

    unique = dedupe(resolved, include_offline=args.include_offline)
    online = sum(
        1
        for record in unique
        if ((choose_stream(record) or {}).get("health_ext") or {}).get("status") == "online"
    )
    print(f"resolved {len(resolved)} channels, {len(unique)} unique, {online} with an online feed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(unique), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
