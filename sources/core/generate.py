#!/usr/bin/env python3
"""Download M3U and reorganize flat group-titles into neat categories.

Sources:
  M3U: https://s.id/d9M3U8  (-> github doms9/iptv TV.m3u8)
  EPG: https://s.id/d9sEPG  (-> github doms9/iptv TV.xml, referenced raw, not downloaded)

What it does:
  1. Downloads only the M3U (follows s.id redirect).
  2. Parses the M3U (keeps #EXTVLCOPT lines attached to each channel).
  3. Re-assigns `group-title` from the raw "TV" / "Live Events" into
     neat categories (News, Sports, Movies, Kids, ...) using
     keyword rules on the channel name. Live events are split by
     sport taken from the "[Sport]" prefix in the name.
  4. Points the #EXTM3U url-tvg header at the raw EPG URL (no EPG
     download/filtering), renumbers tvg-chno, writes playlist.m3u
     (no per-category files are generated).
  5. Optionally probes every stream URL (--check-streams) and writes
     a report with active/dead counts plus a dead-channel table
     (same style as https://github.com/doms9/iptv).

Stdlib only. Usage:
  python3 sources/core/generate.py [--output PATH] [--check-streams]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

M3U_URL = "https://s.id/d9M3U8"
EPG_URL = "https://s.id/d9sEPG"

# Professional output file naming (kebab-case artifacts).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYLIST_FILENAME = "core.m3u"
SOURCE_FILENAME = "core.m3u8"
REPORT_FILENAME = "core.md"
DEFAULT_OUTPUT = PROJECT_ROOT / "playlists" / "sources" / PLAYLIST_FILENAME
DEFAULT_RAW_OUTPUT = PROJECT_ROOT / "data" / "raw" / SOURCE_FILENAME
DEFAULT_REPORT = PROJECT_ROOT / "data" / "reports" / REPORT_FILENAME
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "data" / "raw" / "core.json"


def slugify_category(name: str) -> str:
    """Category display name -> kebab-case file stem (e.g. 'Live - Soccer' -> 'live-soccer')."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

# ---------------------------------------------------------------- category rules
# Ordered (first match wins). All matching is case-insensitive on channel name.

NEWS_RE = re.compile(
    r"news|cnn|hln|ms now|msnbc|bloomberg|cnbc|cheddar|fox live|"
    r"livenow|court tv|law & crime|weather|accuweather|reuters|"
    r"spectrum news|newsmax|newsnation|one america|telemundo.*noticia|"
    r"sky news|bbc world|abc news|cbs news|nbc news|fox news|"
    r"yahoo finance|fox business|fox weather",
    re.I,
)

SPORTS_RE = re.compile(
    r"espn|espnews|espnu|fox sports|fs1|fs2|nbc sports|sportsnet|tsn\b|"
    r"tsn [0-9]|bein|dazn|sky sports|tnt sports|cbs sports|golf channel|"
    r"tennis channel|mlb network|nfl network|nba tv|nhl network|"
    r"sec network|acc network|big ten|marquee|nesn|yes network|\bmsg\b|"
    r"altitude|racing|fanduel|flo?racing|racer|outdoor channel|willow|"
    r"fox soccer|premier sports|tudn|fox deportes|espn deportes|"
    r"spectrum sportsnet|space city|chicago sports|nbc sports now|"
    r"sports \d|golazo",
    re.I,
)

MOVIES_RE = re.compile(
    r"\bhbo\b|cinemax|showtime|starz|encore|mgm\+?|paramount|sundance|"
    r"turner classic|\btcm\b|amc\b|fx movie|reelz|hallmark|"
    r"lifetime movie|lifetime\b|flix",
    re.I,
)

KIDS_RE = re.compile(
    r"cartoon|adult swim|boomerang|disney|nickelodeon|\bnick\b|nick jr|"
    r"nicktoons|disney jr|disney xd|buzzr|cozi|metv.*kids",
    re.I,
)

MUSIC_RE = re.compile(r"\baxs\b|mtv live|\bmtv\b|\bvh1\b|fuse\b|axstv", re.I)

DOCU_RE = re.compile(
    r"discovery|nat geo|national geographic|history channel|science channel|"
    r"animal planet|investigation|crime & investigation|crime\b|oxygen|"
    r"smithsonian|destination america|cooking|food network|tlc\b|fyi\b|"
    r"vice tv|weather channel|weathernation|circle country|heartland|insp\b",
    re.I,
)

ENTERTAINMENT_RE = re.compile(
    r"abc\b|cbs\b|nbc\b|\bfox\b|cw\b|tbs\b|tnt\b|usa network|\bfx\b|fxx\b|"
    r"comedy central|syfy|bravo|e! entertainment|bet\b|tv land|tv one|"
    r"ion tv|bounce|grit|charge!|comet|pop tv|trutv|freeform|game show|"
    r"fetv|great entertainment|a&e\b|bio\b",
    re.I,
)


def categorize_tv(name: str) -> str:
    """Map a regular TV channel name to a neat category."""
    n = name.strip()
    if NEWS_RE.search(n):
        return "News & Weather"
    if SPORTS_RE.search(n):
        return "Sports"
    if MOVIES_RE.search(n):
        return "Movies & Premium"
    if KIDS_RE.search(n):
        return "Kids & Family"
    if MUSIC_RE.search(n):
        return "Music"
    if DOCU_RE.search(n):
        return "Documentary & Reality"
    if ENTERTAINMENT_RE.search(n):
        return "Entertainment"
    return "Entertainment"  # sensible default for general channels


# Live-event "[Bracket]" -> neat live sub-category
def categorize_live(name: str) -> str:
    m = re.search(r"\[([^\]]+)\]", name)
    tag = m.group(1).lower() if m else ""
    if re.search(r"soccer|liga|serie|primera|copa|bundesliga|mls|ecuador|uruguay|paraguay|romania|sweden division|portugal|uzbekistan|cosafa|femenino|danmark|koge|division", tag):
        return "Live - Soccer"
    if re.search(r"american football|nfl", tag):
        return "Live - American Football"
    if re.search(r"baseball|mlb", tag):
        return "Live - Baseball"
    if re.search(r"basketball|wnba", tag):
        return "Live - Basketball"
    if re.search(r"hockey|nhl|allsvenskan", tag):
        return "Live - Hockey"
    if re.search(r"tennis|wta", tag):
        return "Live - Tennis"
    if re.search(r"racing|horse", tag):
        return "Live - Racing"
    if re.search(r"volleyball|cycling|friendly|live event", tag):
        return "Live - Other Events"
    return "Live - Other Events"


def categorize(name: str, raw_group: str) -> str:
    if raw_group.strip().lower() == "live events" or name.strip().startswith("["):
        return categorize_live(name)
    return categorize_tv(name)


# ---------------------------------------------------------------- M3U parsing
EXTINF_RE = re.compile(r'#EXTINF:(?P<dur>[^\s]+)\s*(?P<attrs>.*),(?P<title>.*)$')
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text: str) -> tuple[str, list[dict]]:
    lines = text.splitlines()
    header = lines[0] if lines and lines[0].startswith("#EXTM3U") else "#EXTM3U"
    entries: list[dict] = []
    i = 1 if lines and lines[0].startswith("#EXTM3U") else 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.match(line)
            attrs = dict(ATTR_RE.findall(m.group("attrs"))) if m else {}
            title = m.group("title").strip() if m else ""
            extra: list[str] = []
            i += 1
            # collect #EXTVLCOPT / #EXTGRP etc. until URL line
            while i < len(lines) and lines[i].strip().startswith("#") and not lines[i].strip().startswith("#EXTINF"):
                extra.append(lines[i].rstrip("\n"))
                i += 1
            url = lines[i].strip() if i < len(lines) else ""
            entries.append({"extinf_attrs": attrs, "title": title,
                            "name": attrs.get("tvg-name", title),
                            "raw_group": attrs.get("group-title", "Unknown"),
                            "extra": extra, "url": url, "dur": m.group("dur") if m else "-1"})
        i += 1
    return header, entries


def build_extinf(entry: dict) -> str:
    attrs = entry["extinf_attrs"]
    # deterministic attribute order for readability
    order = ["tvg-chno", "tvg-id", "tvg-name", "tvg-logo", "group-title"]
    parts = []
    for k in order:
        if k in attrs:
            parts.append(f'{k}="{attrs[k]}"')
    for k, v in attrs.items():
        if k not in order:
            parts.append(f'{k}="{v}"')
    return f"#EXTINF:{entry['dur']} {' '.join(parts)},{entry['title']}"


# ---------------------------------------------------------------- download
def download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (m3u-organizer)"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
    return dest


# ---------------------------------------------------------------- stream health check
VLCOPT_RE = re.compile(r"#EXTVLCOPT:([\w-]+)=(.*)")
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) m3u-organizer"
PROBE_BYTES = 65536


def _probe_headers(entry: dict) -> dict:
    """Build request headers for a stream, honoring its #EXTVLCOPT lines."""
    headers = {"User-Agent": DEFAULT_UA}
    for line in entry.get("extra", []):
        m = VLCOPT_RE.match(line.strip())
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "http-user-agent" and value:
            headers["User-Agent"] = value
        elif key == "http-referrer" and value:
            headers["Referer"] = value
    return headers


def probe_stream(entry: dict, timeout: float) -> tuple[bool, str]:
    """GET the first bytes of a stream URL. Returns (is_active, error)."""
    url = entry.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return False, "Missing URL"
    try:
        req = urllib.request.Request(url, headers=_probe_headers(entry))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.getcode()
            if status not in (200, 206):
                return False, f"HTTP Error ({status})"
            if not r.read(PROBE_BYTES):
                return False, "Empty response"
            return True, ""
    except urllib.error.HTTPError as e:
        return False, f"HTTP Error ({e.code})"
    except urllib.error.URLError as e:
        reason = getattr(e.reason, "strerror", None) or str(e.reason)
        return False, f"URL Error ({reason})"[:80]
    except (socket.timeout, TimeoutError):
        return False, "Timeout"
    except Exception as e:  # noqa: BLE001 - report any probe failure as dead
        return False, f"{type(e).__name__}"[:80]


def check_streams(entries: list[dict], timeout: float, workers: int) -> tuple[int, int]:
    """Probe all entries concurrently. Sets entry['active']/entry['error']. Returns (active, dead)."""
    total = len(entries)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_entry = {pool.submit(probe_stream, e, timeout): e for e in entries}
        for fut in concurrent.futures.as_completed(future_to_entry):
            entry = future_to_entry[fut]
            entry["active"], entry["error"] = fut.result()
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  checked {done}/{total}...", flush=True)
    active = sum(1 for e in entries if e.get("active"))
    return active, total - active


# ---------------------------------------------------------------- README
def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _md_link(text: str, url: str) -> str:
    safe_text = text.replace("[", "\\[").replace("]", "\\]")
    safe_url = url.replace(")", "%29")
    return f"[{safe_text}]({safe_url})"


def write_report(path: Path, entries: list[dict], checked: bool,
                 m3u_url: str, epg_url: str, playlist_path: Path) -> Path:
    """Write a generated health report for the consolidated repository."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = Counter(e["category"] for e in entries)
    lines = [
        "# Core IPTV Generation Report",
        "",
        f"## Playlist Log @ {stamp}",
        "",
    ]
    if checked:
        active = sum(1 for e in entries if e.get("active"))
        dead_entries = [e for e in entries if not e.get("active")]
        # Live-event links expire when the match ends, so keep them out of the dead log.
        live_dead = [e for e in dead_entries if e["category"].startswith("Live")]
        listed_dead = [e for e in dead_entries if not e["category"].startswith("Live")]
        lines += [f"### ✅ Active Streams: {active}", f"❌ Dead Streams: {len(listed_dead)}", ""]
        if live_dead:
            lines += [f"_Skipped {len(live_dead)} dead live-event streams — event links expire when the match ends._", ""]
        if listed_dead:
            lines += ["| Channel | Category | Error (Code) |", "| --- | --- | --- |"]
            for e in listed_dead:
                name = _md_cell(e["name"] or e["title"] or "Unknown")
                lines.append(f"| {_md_link(name, e['url'])} | {_md_cell(e['category'])} | {_md_cell(e['error'])} |")
            lines.append("")
    else:
        lines += [f"### Total Channels: {len(entries)}",
                  "_Stream health not checked in this run (use `--check-streams`)._", ""]
    lines += [
        "---",
        "",
        "### Playlist",
        "",
        "```",
        f"{playlist_path}",
        "```",
        "",
        "| Category | Channels |",
        "| --- | ---: |",
    ]
    for cat in sorted(counts):
        lines.append(f"| {_md_cell(cat)} | {counts[cat]} |")
    lines += [
        "",
        "### Sources",
        "",
        "```",
        f"M3U: {m3u_url}",
        f"EPG: {epg_url} (referenced raw via url-tvg, not downloaded)",
        "```",
        "",
        f"_Regenerated from upstream [doms9/iptv](https://github.com/doms9/iptv) every 12 hours via GitHub Actions._",
        "",
        "---",
        "",
        "### Legal Disclaimer",
        "",
        "I don't host, store, or broadcast any videos or streams here — this repo only collects links to streams that are "
        "already public on the internet, and those links can go offline or change at any time. Some of them may point "
        "to copyrighted content owned by someone else, so please only use them where it is legal for you, and treat "
        "everything here as shared for learning and testing purposes. I do not encourage piracy or illegal streaming in "
        "any way — following the law where you live is entirely your responsibility. If you own any of the linked content "
        "and want a link removed, just open an issue and I will take it down.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Download M3U and organize categories (EPG referenced raw, not downloaded).")
    ap.add_argument("--m3u-url", default=M3U_URL)
    ap.add_argument("--epg-url", default=EPG_URL,
                    help="Raw EPG URL written into the #EXTM3U url-tvg header (not downloaded).")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Generated source playlist path")
    ap.add_argument("--raw-output", default=str(DEFAULT_RAW_OUTPUT), help="Downloaded upstream M3U path")
    ap.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT), help="Generated raw channel JSON path")
    ap.add_argument("--no-renumber", action="store_true", help="Keep original tvg-chno values")
    ap.add_argument("--skip-download", action="store_true", help="Reuse the existing raw upstream file")
    ap.add_argument("--input-file", default=None,
                    help="Parse this local M3U file instead of downloading (overrides --m3u-url/--skip-download).")
    ap.add_argument("--check-streams", action="store_true",
                    help="Probe every stream URL and include active/dead stats in the report")
    ap.add_argument("--check-timeout", type=float, default=15.0,
                    help="Seconds to wait for each stream probe (default: 15)")
    ap.add_argument("--check-workers", type=int, default=25,
                    help="Concurrent stream probes (default: 25)")
    ap.add_argument("--report", default=str(DEFAULT_REPORT),
                    help="Where to write the generated health report")
    args = ap.parse_args()

    org_path = Path(args.output)
    out = org_path.parent
    out.mkdir(parents=True, exist_ok=True)
    raw_m3u = Path(args.raw_output)
    raw_m3u.parent.mkdir(parents=True, exist_ok=True)

    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8", errors="replace")
        print(f"Using local input: {args.input_file}")
    else:
        if not args.skip_download:
            print(f"Downloading M3U: {args.m3u_url}", flush=True)
            download(args.m3u_url, raw_m3u)
            print(f"  -> {raw_m3u} ({raw_m3u.stat().st_size/1024:.1f} KB)", flush=True)
        else:
            if not raw_m3u.exists():
                print("ERROR: --skip-download but raw M3U missing.", file=sys.stderr)
                return 2
        text = raw_m3u.read_text(encoding="utf-8", errors="replace")
    header, entries = parse_m3u(text)
    print(f"Parsed {len(entries)} channels (raw groups: {Counter(e['raw_group'] for e in entries)})")

    for e in entries:
        new_group = categorize(e["name"] or e["title"], e["raw_group"])
        e["extinf_attrs"]["group-title"] = new_group
        e["category"] = new_group

    # sort: category alpha, then name
    entries.sort(key=lambda e: (e["category"].lower(), (e["name"] or e["title"]).lower()))

    if not args.no_renumber:
        for n, e in enumerate(entries, start=1):
            e["extinf_attrs"]["tvg-chno"] = str(n)

    # Point header at the raw EPG URL (no local EPG download).
    if 'url-tvg=' in header:
        header = re.sub(r'url-tvg="[^"]*"', f'url-tvg="{args.epg_url}"', header)
    else:
        header += f' url-tvg="{args.epg_url}"'

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_payload = {
        "version": 1,
        "source": "core",
        "channels": [
            {
                "source_id": e["extinf_attrs"].get("tvg-id", ""),
                "name": e["name"] or e["title"],
                "country": e["extinf_attrs"].get("tvg-country", ""),
                "category": e["category"],
                "logo": e["extinf_attrs"].get("tvg-logo", ""),
                "url": e["url"],
            }
            for e in entries
        ],
    }
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {json_path}")

    with open(org_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for e in entries:
            f.write(build_extinf(e) + "\n")
            for x in e["extra"]:
                f.write(x + "\n")
            f.write(e["url"] + "\n")
    print(f"Wrote {org_path}")

    counts = Counter(e["category"] for e in entries)
    print("\nCategory summary:")
    for cat in sorted(counts):
        print(f"  {counts[cat]:3d}  {cat}")

    if args.check_streams:
        print("\nProbing streams...", flush=True)
        active, dead = check_streams(entries, args.check_timeout, args.check_workers)
        print(f"✅ Active: {active}  ❌ Dead: {dead}")
    else:
        print("\nSkipping stream check (pass --check-streams to probe URLs).")

    report_path = write_report(Path(args.report), entries, args.check_streams,
                               args.m3u_url, args.epg_url, org_path)
    print(f"Wrote {report_path}")

    print(f"\nDone. EPG is referenced raw via url-tvg={args.epg_url}")
    print(f"Load {org_path} in your IPTV player (e.g. TiviMate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
