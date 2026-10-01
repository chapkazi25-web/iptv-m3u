#!/usr/bin/env python3
"""Rebuild channels + categories from tv_guide_USA_UK_CA_Australia.txt.

- 11 categories = one per top-level txt section, including the two live
  leagues as separate categories: ``nfl`` (NFL Sunday Ticket) and ``nba``
  (NBA League Pass).
- Full wipe: only txt channels are kept; source streams matched where found.
- Txt channels with no source stream become disabled placeholders
  (excluded_by=no-source).
- Live games carry kickoff data parsed from the txt entry itself
  (``start:YYYY-MM-DD HH:MM:SS`` timestamps, ``Jul 16`` style date hints)
  and the playlist builder lists each league's games in kickoff order.
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import slugify, normalize_name
from scripts.lib.m3u import parse_m3u
from scripts.lib.pipeline import load_sources, write_json
from scripts.events.fixture_schedule import (
    EVENT_PREFIX_RE,
    event_start as fixture_event_start,
    parse_event,
    tokens,
)

TXT = PROJECT_ROOT / "tv_guide_USA_UK_CA_Australia.txt"

# Txt order: first-seen section wins as a channel's primary category.
SECTION_MAP = OrderedDict(
    [
        ("TV Guide (USA)", ("usa", "USA")),
        ("TV Guide (UK)", ("uk", "UK")),
        ("TV Guide (Canada)", ("canada", "Canada")),
        ("TV Guide (Australia)", ("australia", "Australia")),
        ("NFL Sunday Ticket", ("nfl", "NFL Sunday Ticket")),
        ("NBA League Pass", ("nba", "NBA League Pass")),
        ("Entertainment Channels", ("entertainment", "Entertainment")),
        ("Movie Networks", ("movies", "Movies")),
        ("News Networks", ("news", "News")),
        ("Kids Channels", ("kids", "Kids")),
        ("Sport Networks", ("sports", "Sports")),
    ]
)

# Txt subsections whose entries are match games (sorted by kickoff).
# Everything else keeps txt lineup order.
GAME_SUBSECTIONS = {
    "nfl": {"sunday ticket"},
    "nba": {"nba games list 2"},
    "soccer": set(),
}

# League slugs with their own separate playlist groups.
LIVE_LEAGUES = ("nfl", "nba", "soccer")

# Source groups whose live games are ingested (normalize_name form, as
# compared in ingest: punctuation folds to spaces, so "Live - Soccer" is
# "live soccer"). Baseball, hockey, racing, tennis and other-events stay
# excluded.
LIVE_SOURCE_GROUPS = {"live soccer", "live american football"}

# Fixture sport -> league category for ingested source games.
SPORT_LEAGUE = {"Soccer": "soccer", "American Football": "nfl", "Basketball": "nba"}

BACKUP_RE = re.compile(r"\s*[\(\[]\s*backup\s*[\)\]]\s*", re.I)
# A quality marker on its own inside brackets ("[4K]") must go as a whole,
# otherwise the brackets are left behind as "[ ]" residue.
BRACKET_QUALITY_RE = re.compile(
    r"\[\s*(?:\d{3,4}\s*[pPiI]|\b(?:UHD|FHD|HD|SD|4K|8K)\b)\s*\]",
    re.I,
)
QUALITY_RE = re.compile(
    r"\s*(?:\b\d{3,4}\s*[pi]\b|\b(?:UHD|FHD|HD|SD|4K|8K)\b|[ᵁᴴᴰᴿ³⁸⁴⁰ᴾ]+)",
    re.I,
)

START_RE = re.compile(r"start\s*:\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)")
MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b",
    re.I,
)
MONTHS = {
    name: index
    for index, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def clean_display(name: str) -> str:
    s = BACKUP_RE.sub(" ", name)
    s = BRACKET_QUALITY_RE.sub(" ", s)
    s = QUALITY_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_kickoff(raw: str) -> tuple[str | None, str | None]:
    """Return (event_start ISO, event_hint MM-DD) parsed from a txt entry.

    Only what the guide itself prints is used: an embedded
    ``start:YYYY-MM-DD HH:MM:SS`` timestamp becomes a real ``event_start``;
    a bare ``Jul 16`` style date becomes an ordering hint. No year is ever
    invented for a hint, so hints only ever order games relative to each
    other inside one league section.
    """
    match = START_RE.search(raw or "")
    if match:
        day, clock = match.group(1), match.group(2)
        if len(clock.split(":")) == 2:
            clock += ":00"
        return f"{day}T{clock}+00:00", None
    hint = MONTH_DAY_RE.search(raw or "")
    if hint:
        return None, f"{MONTHS[hint.group(1).lower()]:02d}-{int(hint.group(2)):02d}"
    return None, None


def _subsection_key(header: str) -> str:
    text = re.sub(r"^#+\s*", "", header).strip()
    text = re.sub(r"\s*#+$", "", text).strip()
    return re.sub(r"\s+", " ", text).lower()


def canonical_live_game(title: str) -> dict[str, str] | None:
    """Collapse one source feed title into its game.

    Every feed of the same match (``| Win Sports``, ``(TVF90)`` ...) maps to
    one ``[League] Home vs Away`` channel, so feeds become stream candidates
    instead of duplicate guide rows. Returns None when the title is not a
    recognised ``[League] Home vs Away`` event.
    """
    prefix_match = EVENT_PREFIX_RE.match(str(title or ""))
    parsed = parse_event(str(title or ""))
    if not prefix_match or not parsed:
        return None
    prefix = re.sub(r"\s+", " ", prefix_match.group(1)).strip(" -|")
    # Bare trailing digits are feed numbers ("X vs Y 1 (PLIBRE)" -> game "X
    # vs Y"), not part of the fixture. Parenthesised codes are already gone
    # via parse_event's annotation stripping.
    home = re.sub(r"\s+\d+$", "", parsed["home"]).strip()
    away = re.sub(r"\s+\d+$", "", parsed["away"]).strip()
    if not home or not away:
        return None
    display = f"[{prefix}] {home} vs {away}"
    return {
        "league": SPORT_LEAGUE.get(parsed["sport"], ""),
        "prefix": prefix,
        "home": home,
        "away": away,
        "sport": parsed["sport"],
        "display": display,
        "key": slugify(display),
    }


def pair_score(left: str, right: str) -> float:
    """Team-name similarity that understands pro nicknames.

    ``Bengals`` vs ``Cincinnati Bengals`` scores 0.5 on raw overlap, yet it
    is unambiguously the same team. A token set fully contained in the other
    therefore scores 0.75 — still below an exact match, still above noise.
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if not shared:
        return 0.0
    score = shared / max(len(a), len(b))
    if a <= b or b <= a:
        score = max(score, 0.75)
    return score


def match_live_game(
    parsed: dict[str, str], events: list[dict], threshold: float = 0.6
) -> dict | None:
    """Match a parsed game to a cached fixture, either home/away alignment."""
    best: tuple[float, dict] | None = None
    for event in events:
        home = str(event.get("strHomeTeam") or "")
        away = str(event.get("strAwayTeam") or "")
        straight = min(pair_score(parsed["home"], home), pair_score(parsed["away"], away))
        swapped = min(pair_score(parsed["home"], away), pair_score(parsed["away"], home))
        score = max(straight, swapped)
        if score >= threshold and (best is None or score > best[0]):
            best = (score, event)
    return best[1] if best else None


def load_cached_fixtures(root: Path) -> dict[str, list[dict]]:
    """Group cached TheSportsDB fixtures by sport (offline, no API calls)."""
    path = root / "data/event-schedule.json"
    if not path.exists():
        return {}
    try:
        days = json.loads(path.read_text(encoding="utf-8")).get("days", {})
    except ValueError:
        return {}
    by_sport: dict[str, list[dict]] = {}
    for cache_key, fixtures in days.items():
        sport = str(cache_key).split("|")[-1]
        by_sport.setdefault(sport, []).extend(fixtures or [])
    return by_sport


def fixture_date(event: dict) -> str:
    """Return the fixture's calendar date (UTC) as YYYY-MM-DD."""
    raw = str(event.get("dateEvent") or "").strip()
    if len(raw) >= 10:
        return raw[:10]
    start = fixture_event_start(event)
    return start[:10] if start else ""


def game_is_today(event_start: str | None, target: str) -> bool:
    """True when a game belongs in a today-only guide.

    A game with no resolved kickoff is kept: the source lists it today, and
    an unknown date is not evidence it already played. Only a positively
    dated game on another day is dropped.
    """
    if not event_start:
        return True
    return str(event_start)[:10] == target


def today_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


def parse_txt() -> OrderedDict[str, list[tuple[str, str]]]:
    """Return section -> [(subsection, channel name)] preserving txt order."""
    lines = TXT.read_text(encoding="utf-8").splitlines()
    top_pat = re.compile(r"^(.*)\((\d+) channels\)\s*$")
    sections: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    cur: str | None = None
    sub = ""
    for line in lines:
        s = line.strip()
        m = top_pat.search(s)
        if m and s.endswith("channels)") and s[:1] not in "0123456789 ":
            cur = m.group(1).strip()
            # strip leading emoji + space: e.g. "🇺🇸 TV Guide (USA)"
            cur = re.sub(r"^[^\w]+", "", cur).strip()
            sections[cur] = []
            sub = ""
            continue
        if cur is None:
            continue
        cm = re.match(r"^\s*\d+\.\s*(.*)$", line)
        if cm:
            name = cm.group(1).strip()
            if name.startswith("#"):
                sub = _subsection_key(name)
            else:
                sections[cur].append((sub, name))
    return sections


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default="",
        help="Guide date as YYYY-MM-DD (default: today UTC). Games dated on "
        "another day are disabled; the daily schedule reruns this.",
    )
    args = parser.parse_args(argv)
    target = args.date.strip() or datetime.now(timezone.utc).date().isoformat()
    try:
        datetime.strptime(target, "%Y-%m-%d")
    except ValueError:
        print(f"error: --date must be YYYY-MM-DD, got {target!r}")
        return 2
    print(f"guide date: {target}")

    sections = parse_txt()
    print("parsed sections:")
    for k, v in sections.items():
        subs = sorted({sub for sub, _ in v})
        print(f"  {k!r}: {len(v)} entries, subsections={subs}")

    # Build allowlist from all sections (live leagues included).
    key_display: dict[str, str] = {}
    key_cats: dict[str, list[str]] = {}
    key_live: dict[str, dict] = {}
    for sec, items in sections.items():
        if sec not in SECTION_MAP:
            print(f"WARNING: unknown section {sec!r}, skipping")
            continue
        slug, _ = SECTION_MAP[sec]
        sub_order: dict[str, int] = {}
        for order, (sub, raw) in enumerate(items):
            sub_order.setdefault(sub, len(sub_order))
            disp = clean_display(raw)
            if not disp:
                continue
            key = slugify(disp)
            if not key:
                continue
            if key not in key_display:
                key_display[key] = disp
            cats = key_cats.setdefault(key, [])
            if slug not in cats:
                if slug in LIVE_LEAGUES:
                    # Live leagues are separate categories: a channel listed
                    # under a live section belongs to its league first, so
                    # shared carriers (NFL Network, NBA TV) publish under the
                    # live group rather than the base guide.
                    cats.insert(0, slug)
                else:
                    cats.append(slug)
            if slug in LIVE_LEAGUES:
                start, hint = parse_kickoff(raw)
                info = key_live.setdefault(
                    key,
                    {
                        "league": slug,
                        "section": "games" if sub in GAME_SUBSECTIONS[slug] else "lineup",
                        "suborder": sub_order[sub],
                        "order": order,
                        "league_group": "",
                        "event_start": None,
                        "event_hint": None,
                    },
                )
                # One slug can merge several txt rows (e.g. plain + 4K feed
                # of the same game): keep the earliest kickoff evidence.
                if start and not info["event_start"]:
                    info["event_start"] = start
                if hint and not info["event_hint"]:
                    info["event_hint"] = hint
                info["suborder"] = min(info["suborder"], sub_order[sub])
                info["order"] = min(info["order"], order)

    print(f"unique txt channels: {len(key_display)}")

    # Match source streams
    sources = load_sources(PROJECT_ROOT)
    matched: dict[str, dict] = {
        k: {"sources": set(), "refs": {}, "countries": set(), "aliases": set()}
        for k in key_display
    }
    total_entries = 0
    for source_id, source in sources.items():
        if not source.playlist.exists():
            continue
        _, entries = parse_m3u(source.playlist)
        for e in entries:
            total_entries += 1
            disp = clean_display(e.clean_name())
            if not disp:
                continue
            key = slugify(disp)
            if key in matched:
                m = matched[key]
                m["sources"].add(source_id)
                ref = (e.attrs.get("tvg-id", "") or "").strip() or normalize_name(
                    e.clean_name()
                )
                m["refs"].setdefault(source_id, set()).add(ref)
                c = (e.inferred_country(source.default_country or None) or "").upper()
                if c:
                    m["countries"].add(c)
                m["aliases"].add(e.title.strip())
                m["aliases"].add(e.clean_name().strip())
    with_stream = sum(1 for k, v in matched.items() if v["sources"])
    print(
        f"source entries scanned: {total_entries}, "
        f"txt keys with >=1 stream: {with_stream}/{len(matched)}"
    )

    # Ingest live games from kept source groups (cdn/core). Every feed of one
    # match collapses into a single "[League] Home vs Away" game channel, so
    # feeds become failover stream candidates. Kickoffs match only the guide
    # date's cached fixtures: the guide lists today games, and the next
    # scheduled run rebuilds it for the new day.
    fixtures = load_cached_fixtures(PROJECT_ROOT)
    todays = {
        sport: [ev for ev in events if fixture_date(ev) == target]
        for sport, events in fixtures.items()
    }
    print(
        "cached fixtures for guide date: "
        + ", ".join(f"{sport}={len(todays.get(sport, []))}" for sport in sorted(todays))
    )
    games_suborder: dict[str, int] = {}
    league_counter: dict[str, int] = {}
    for sec, items in sections.items():
        slug = SECTION_MAP.get(sec, (None,))[0]
        if slug not in LIVE_LEAGUES:
            continue
        subs: dict[str, int] = {}
        for sub, _ in items:
            subs.setdefault(sub, len(subs))
        games = [subs[sub] for sub in subs if sub in GAME_SUBSECTIONS[slug]]
        games_suborder[slug] = min(games) if games else 0
        league_counter[slug] = len(items)
    feed_games = 0
    for source_id, source in sources.items():
        if not source.playlist.exists():
            continue
        _, entries = parse_m3u(source.playlist)
        for entry in entries:
            if normalize_name(entry.group) not in LIVE_SOURCE_GROUPS:
                continue
            game = canonical_live_game(entry.title)
            if not game or not game["league"]:
                continue
            league = game["league"]
            key = game["key"]
            if key not in key_display:
                key_display[key] = game["display"]
            cats = key_cats.setdefault(key, [])
            if league not in cats:
                cats.insert(0, league)
            info = key_live.setdefault(
                key,
                {
                    "league": league,
                    "section": "games",
                    "suborder": games_suborder.get(league, 0),
                    "order": league_counter.get(league, 0),
                    "league_group": game["prefix"].lower(),
                    "event_start": None,
                    "event_hint": None,
                },
            )
            league_counter[league] = league_counter.get(league, 0) + 1
            if not info["event_start"] and todays.get(game["sport"]):
                event = match_live_game(
                    {"sport": game["sport"], "home": game["home"], "away": game["away"]},
                    todays.get(game["sport"], []),
                )
                if event:
                    info["event_start"] = fixture_event_start(event)
                    info["event_fixture"] = str(event.get("strEvent") or "")
                    info["event_sport"] = game["sport"]
            m = matched.setdefault(
                key,
                {"sources": set(), "refs": {}, "countries": set(), "aliases": set()},
            )
            m["sources"].add(source_id)
            ref = (entry.attrs.get("tvg-id", "") or "").strip() or normalize_name(
                entry.clean_name()
            )
            m["refs"].setdefault(source_id, set()).add(ref)
            country = (entry.inferred_country(source.default_country or None) or "").upper()
            if country:
                m["countries"].add(country)
            m["aliases"].add(entry.title.strip())
            m["aliases"].add(entry.clean_name().strip())
            feed_games += 1
    print(f"source live games ingested: {feed_games} feeds")

    # Write channels.json
    channels = {}
    for key in sorted(key_display):
        m = matched[key]
        has = bool(m["sources"])
        record = {
            "name": key_display[key],
            "aliases": sorted({key_display[key]} | {a for a in m["aliases"] if a})[:40],
            "countries": sorted(m["countries"]),
            "categories": key_cats[key],
            "sources": sorted(m["sources"]),
            "logo": "",
            "enabled": has,
            "source_refs": {s: sorted(r) for s, r in sorted(m["refs"].items())},
            "english": None,
            "quality_height": None,
            "not_24_7": False,
        }
        if key in key_live:
            info = key_live[key]
            record["live_league"] = info["league"]
            record["live_section"] = info["section"]
            record["live_suborder"] = info["suborder"]
            record["live_order"] = info["order"]
            if info.get("league_group"):
                record["league_group"] = info["league_group"]
            if info["event_start"]:
                record["event_start"] = info["event_start"]
                record["event_date"] = info["event_start"][:10]
            if info["event_hint"]:
                record["event_hint"] = info["event_hint"]
            if info.get("event_fixture"):
                record["event_fixture"] = info["event_fixture"]
            if info.get("event_sport"):
                record["event_sport"] = info["event_sport"]
            if (
                info["section"] == "games"
                and info["event_start"]
                and not game_is_today(info["event_start"], target)
            ):
                # Today-only guide: a game dated on another day leaves the
                # playlist and returns on its own day's scheduled rebuild.
                # Carriers, team feeds and replays (no kickoff) are kept.
                record["enabled"] = False
                record["excluded_by"] = "event-not-today"
                print(f"  not today: {record['name']!r} ({info['event_start'][:10]})")
        if not has and "excluded_by" not in record:
            record["excluded_by"] = "no-source"
        channels[key] = record
    enabled = sum(1 for record in channels.values() if record["enabled"])
    write_json(PROJECT_ROOT / "data/channels.json", {"version": 1, "channels": channels})
    print(f"wrote {len(channels)} channels ({enabled} enabled)")

    # Write categories.json
    cats = {slug: {"name": disp, "aliases": []} for _, (slug, disp) in SECTION_MAP.items()}
    cats["soccer"] = {"name": "Soccer", "aliases": []}
    # friendly aliases so source group-titles still resolve
    cats["usa"]["aliases"] = ["usa", "united states", "america", "local networks"]
    cats["uk"]["aliases"] = ["uk", "united kingdom", "britain", "national networks", "sky networks"]
    cats["canada"]["aliases"] = ["canada", "canadian"]
    cats["australia"]["aliases"] = ["australia", "australian", "au"]
    cats["nfl"]["aliases"] = ["nfl", "nfl sunday ticket", "sunday ticket", "american football",
                              "live - american football"]
    cats["nba"]["aliases"] = ["nba", "nba league pass", "league pass", "basketball"]
    cats["soccer"]["aliases"] = ["soccer", "football", "live - soccer", "live soccer"]
    cats["entertainment"]["aliases"] = ["entertainment", "general", "entertainment channels"]
    cats["movies"]["aliases"] = ["movies", "movie networks", "movies & premium", "film"]
    cats["news"]["aliases"] = ["news", "news networks"]
    cats["kids"]["aliases"] = ["kids", "kids channels", "kids & family", "children", "animation", "family"]
    cats["sports"]["aliases"] = ["sports", "sport", "sport networks"]

    all_slugs = [slug for _, (slug, _) in SECTION_MAP.items()] + ["soccer"]
    new_config = {
        "version": 6,
        "default": "entertainment",
        "categories": cats,
        "language_filter": {
            "comment": "Keep English-language channels. Unknown is kept.",
            "keep": ["eng"],
            "categories": all_slugs,
            "except_categories": [],
            "except_channels": [],
            "unknown": "keep",
        },
        "quality_filter": {
            "comment": "Disabled for txt rebuild.",
            "categories": [],
            "max_height": None,
            "require_247": False,
        },
        "country_filter": {
            "comment": "USA/UK/CA/AU/NZ only. Unknown is dropped.",
            "allow": ["CA", "US", "GB", "NZ", "AU"],
            "unknown": "drop",
            "except_categories": [],
            "except_brands": ["bein", "trace", "xite", "vevo"],
        },
        "region_filter": json.loads((PROJECT_ROOT / "data/categories.json").read_text()).get(
            "region_filter", {"words": []}
        ),
        "local_filter": json.loads((PROJECT_ROOT / "data/categories.json").read_text()).get(
            "local_filter", {}
        ),
        "radio_filter": {
            "comment": "Disabled for txt rebuild (no music category).",
            "categories": [],
            "name_patterns": [],
            "case_sensitive_name_patterns": [],
        },
        "event_filter": {
            "comment": "TheSportsDB expiry stays off: live games are ordered by "
            "cached fixture kickoffs (event_start) and txt date hints, not by "
            "an external fixture lookup.",
            "enabled": False,
            "durations": {},
            "default_duration": 2.5,
        },
        "exclude": {
            "categories": [],
            "group_patterns": [
                "^live\\s*-\\s*baseball$",
                "^live\\s*-\\s*hockey$",
                "^live\\s*-\\s*racing$",
                "^live\\s*-\\s*tennis$",
                "^live\\s*-\\s*other\\s*events$",
                "^live\\s*-\\s*basketball$",
                "^local\\s*news$",
            ],
            "name_patterns": [
                "\\[\\s*geo[\\s-]*block(?:ed)?\\s*\\]",
                "\\[\\s*not\\s*24\\s*/\\s*7\\s*\\]",
            ],
            "channels": [],
        },
    }
    # preserve region/local words from old config faithfully
    old = json.loads((PROJECT_ROOT / "data/categories.json").read_text())
    if "region_filter" in old:
        new_config["region_filter"] = old["region_filter"]
    if "local_filter" in old:
        new_config["local_filter"] = old["local_filter"]
    write_json(PROJECT_ROOT / "data/categories.json", new_config)
    print("wrote new categories.json v6 with 12 categories (nfl + nba + soccer live)")

    # report live games in kickoff order
    for league in ("nfl", "nba", "soccer"):
        games = sorted(
            ((k, key_live[k]) for k in key_live if key_live[k]["league"] == league),
            key=lambda kv: (
                kv[1]["suborder"],
                kv[1].get("league_group", ""),
                kv[1]["event_start"] or "~~~",
                kv[1]["event_hint"] or "~~",
                kv[1]["order"],
            ),
        )
        print(f"-- {league} lineup order ({len(games)} entries) --")
        for k, info in games:
            if info["section"] == "games":
                print(
                    f"   [game] {key_display[k]!r} "
                    f"start={info['event_start']} hint={info['event_hint']}"
                )
    from collections import Counter

    c = Counter()
    for key, rec in channels.items():
        if not rec["enabled"]:
            for cat in rec["categories"]:
                c[cat] += 1
    print("no-source placeholders per category:", dict(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
