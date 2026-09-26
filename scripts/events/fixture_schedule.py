#!/usr/bin/env python3
"""
fixture_schedule.py — Resolve live-event channels to real kickoff times.

Live-event channels are named after the match they carry, for example
"[Liga MX] América vs Monterrey". Nothing in the catalog records *when* that
match is, so an event that finished this morning keeps being published until
the health checker happens to notice its stream died.

No EPG in the project covers these channels: EPGShare maps none of them and
Grade TV does not carry them. This script therefore resolves the fixture from
TheSportsDB, a free open schedule API, by parsing the channel name:

    "[Liga MX] América vs Monterrey | TUDN"  ->  Soccer, América vs Monterrey

The resolved kickoff time is written onto the catalog record as
``event_start``. data/categories.json then drops the channel once the event
window has passed, so the removal happens on the next scheduled build rather
than waiting for a stream to time out.

A channel whose fixture cannot be found is left alone: an unknown kickoff is
never treated as an expired event.

Usage:
  python scripts/events/fixture_schedule.py
  python scripts/events/fixture_schedule.py --days 10
  python scripts/events/fixture_schedule.py --sport Soccer --verbose
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.pipeline import write_json

API = "https://www.thesportsdb.com/api/v1/json/3/eventsday.php"
USER_AGENT = "iptv-m3u-fixture-schedule/1.0 (playlist event expiry)"

# Channel-name prefix -> TheSportsDB sport filter.
SPORT_HINTS: list[tuple[str, str]] = [
    (r"american football|nfl|ncaaf|\bncaa\b", "American Football"),
    (r"basketball|euroleague|\bnba\b", "Basketball"),
    (r"motorsport|\bf1\b|rally", "Motorsport"),
    (r"ice hockey|hockey", "Ice Hockey"),
    (r"baseball|\bmlb\b", "Baseball"),
    (r"rugby", "Rugby"),
    (r"\bmma\b|ufc|bellator|boxing", "Fighting"),
    (r"tennis", "Tennis"),
    (r"cricket", "Cricket"),
]

# Sports worth querying at all. Everything else is treated as football.
ACTIVE_SPORTS = (
    "Soccer",
    "American Football",
    "Basketball",
    "Motorsport",
    "Ice Hockey",
)

EVENT_PREFIX_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")
VS_SPLIT_RE = re.compile(r"\s+(?:v|vs\.?|x|-)\s+", re.I)
# Trailing feed annotations such as "| TUDN", "(FAWA)", "1 (PLIBR...)".
ANNOTATION_RE = re.compile(r"[\(\[][^)\]]*[\)\]]|\|.*$")
NON_WORD_RE = re.compile(r"[^a-z0-9]+")
CLUB_SUFFIXES = (
    "fc", "cf", "sc", "ac", "afc", "cd", "ud", "rc", "rcd", "sv", "bk",
    "if", "ik", "un", "us", "ss", "as", "ca", "club", "atletico",
)


def fetch_events(day: date, sport: str, retries: int = 4) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"d": day.isoformat(), "s": sport})
    url = f"{API}?{params}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            return payload.get("events") or []
        except urllib.error.HTTPError as error:
            last = error
            if error.code in (429, 503):
                # The free tier rate limits aggressively. Honour Retry-After
                # when present and otherwise back off well past the limit.
                wait = float(error.headers.get("Retry-After") or 0) if error.headers else 0.0
                time.sleep(wait or 6.0 * (attempt + 1))
                continue
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last = error
            time.sleep(1.5 * (attempt + 1))
    print(f"warning: {sport} {day} failed: {last}", file=sys.stderr)
    return []


def tokens(name: str) -> set[str]:
    cleaned = NON_WORD_RE.sub(" ", str(name or "").lower()).strip()
    words = {word for word in cleaned.split() if len(word) > 2}
    trimmed = {word for word in words if word not in CLUB_SUFFIXES}
    return trimmed or words


def score_pair(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if not shared:
        return 0.0
    return shared / max(len(a), len(b))


def parse_event(name: str) -> dict[str, str] | None:
    """Extract sport, home and away from a live-event channel name."""
    match = EVENT_PREFIX_RE.match(str(name or ""))
    if not match:
        return None
    prefix, rest = match.group(1), match.group(2)
    rest = ANNOTATION_RE.sub(" ", rest)
    rest = re.sub(r"\s{2,}", " ", rest).strip(" -|")
    parts = [part.strip() for part in VS_SPLIT_RE.split(rest) if part.strip()]
    if len(parts) < 2:
        return None
    sport = "Soccer"
    for pattern, value in SPORT_HINTS:
        if re.search(pattern, prefix, re.I):
            sport = value
            break
    return {"sport": sport, "home": parts[0], "away": parts[1]}


def event_start(event: dict[str, Any]) -> str | None:
    """Combine TheSportsDB's date and time fields into a UTC timestamp.

    The free API is inconsistent about the time format: some sports return
    "16:00", others "00:15:00" and some none at all, so the value is
    normalised before parsing rather than assuming a single shape.
    """
    raw_date = str(event.get("dateEvent") or "").strip()
    raw_time = str(event.get("strTime") or "").strip()
    if not raw_date:
        return None
    time_part = raw_time.split("+")[0].strip() or "00:00"
    if len(time_part.split(":")) == 3:
        time_part = ":".join(time_part.split(":")[:2])
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(f"{raw_date} {time_part}", fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc).isoformat()
    return None


def match(parsed: dict[str, str], events: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for event in events:
        home = str(event.get("strHomeTeam") or "")
        away = str(event.get("strAwayTeam") or "")
        score = min(score_pair(parsed["home"], home), score_pair(parsed["away"], away))
        if score >= threshold and (best is None or score > best[0]):
            best = (score, event)
    return best[1] if best else None


def _load_cache(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cache = data.get("days") if isinstance(data, dict) else None
    return {str(key): list(value) for key, value in cache.items()} if isinstance(cache, dict) else {}


def _save_cache(path: Path, cache: dict[str, list[dict[str, Any]]]) -> None:
    write_json(path, {"version": 1, "days": {key: cache[key] for key in sorted(cache)}})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=PROJECT_ROOT / "data/channels.json")
    parser.add_argument("--output", type=Path, default=None,
                        help="defaults to writing back to --catalog")
    parser.add_argument("--cache", type=Path, default=PROJECT_ROOT / "data/event-schedule.json")
    parser.add_argument("--days", type=int, default=7, help="days ahead to look for fixtures")
    parser.add_argument("--back", type=int, default=1, help="days behind to include")
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--delay", type=float, default=1.2, help="seconds between schedule requests")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    channels = catalog.get("channels", {})

    wanted: list[tuple[str, dict[str, Any], dict[str, str]]] = []
    for channel_id, record in channels.items():
        if not record.get("enabled", True):
            continue
        parsed = parse_event(str(record.get("name", "")))
        if parsed:
            wanted.append((channel_id, record, parsed))
    if args.limit:
        wanted = wanted[: args.limit]
    if not wanted:
        print("no live-event channels found", file=sys.stderr)
        return 0

    sports = sorted({parsed["sport"] for _, _, parsed in wanted} & set(ACTIVE_SPORTS))
    if args.verbose:
        print(f"{len(wanted)} live-event channels, sports: {sports}", file=sys.stderr)

    by_sport: dict[str, list[dict[str, Any]]] = {}
    cache = _load_cache(args.cache)
    today = datetime.now(timezone.utc).date()
    for sport in sports:
        events: list[dict[str, Any]] = []
        for offset in range(-args.back, args.days + 1):
            day = today + timedelta(days=offset)
            cache_key = f"{day.isoformat()}|{sport}"
            if cache_key in cache:
                events.extend(cache[cache_key])
                continue
            fetched = fetch_events(day, sport)
            cache[cache_key] = fetched
            events.extend(fetched)
            # The free tier rate limits, so pace requests deliberately.
            time.sleep(args.delay)
        by_sport[sport] = events
        if args.verbose:
            print(f"  {sport}: {len(events)} fixtures", file=sys.stderr)
    _save_cache(args.cache, cache)

    resolved = 0
    for channel_id, record, parsed in wanted:
        event = match(parsed, by_sport.get(parsed["sport"], []), args.threshold)
        if not event:
            continue
        start = event_start(event)
        if not start:
            continue
        record["event_start"] = start
        record["event_fixture"] = str(event.get("strEvent") or "")
        record["event_sport"] = parsed["sport"]
        resolved += 1
        if args.verbose:
            print(f"  {record['name'][:52]:52s} -> {start}", file=sys.stderr)

    destination = args.output or args.catalog
    write_json(destination, catalog)
    print(
        f"resolved {resolved}/{len(wanted)} live-event fixtures -> {destination}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
