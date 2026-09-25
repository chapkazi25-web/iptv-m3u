#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.cdnlivetv.is/api/v1/channels/"
PLAYER = "https://cdnlivetv.tv/api/v1/channels/player/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFERER = "https://cdnlivetv.is/"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "playlists/sources/cdn.m3u"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "data/raw/cdn.json"
DEFAULT_LOGO_INDEX = PROJECT_ROOT / "data/raw/cdn-logo-index.json"
ENGLISH_CODES = ("us", "gb", "ca", "au", "nz")

LOGO_TREE_URL = "https://api.github.com/repos/K-yzu/Logos/git/trees/main?recursive=1"
LOGO_RAW = "https://raw.githubusercontent.com/K-yzu/Logos/main/"
LOGO_INDEX_FILE = str(DEFAULT_LOGO_INDEX)
KYZU_COUNTRY_DIR = {"us": "TV:US", "gb": "TV:UK", "ca": "TV:CA",
                    "au": "TV:AU", "nz": "TV:NZ", "fr": "TV:FR"}

OVERRIDES = {
    "Altitude": "countries/united-states/altitude-sports-us.png",
    "CBS Sports Golazo": "countries/united-states/cbs-sports-golazo-network-us.png",
    "DAZN 1": "countries/united-kingdom/dazn1-uk.png",
    "Euro Sport 1": "countries/united-kingdom/eurosport-1-uk.png",
    "Euro Sport 2": "countries/united-kingdom/eurosport-2-uk.png",
    "GOLF TV": "countries/united-states/nbc-golf-channel-us.png",
    "Hallmark": "countries/united-states/hallmark-channel-us.png",
    "MAX": "countries/united-states/hbo-max-us.png",
    "Nickelodeon TV": "countries/united-states/nickelodeon-us.png",
    "Red Bull": "countries/international/red-bull-tv-int.png",
    "Telemundo": "countries/united-states/telemundo-us.png",
    "TUDN": "countries/united-states/tudn-us.png",
    "truTV": "countries/united-states/tru-tv-us.png",
    "Univision": "countries/united-states/us-local/univision/univision-us.png",
    "Willow Cricket": "countries/united-states/willow-us.png",
}

WIKI_COMMONS = {
    "Arizona Diamondbacks": "File:Arizona_Diamondbacks_logo_teal.svg",
    "Atlanta Braves": "File:Atlanta_Braves_Insignia.svg",
    "BBC": "File:BBC.svg",
    "Boston Red Sox": "File:Boston_Red_Sox_cap_logo.svg",
    "CBS": "File:CBS logo.svg",
    "Chicago Cubs": "File:Chicago_Cubs_logo.svg",
    "Chicago White Sox": "File:Chicago_White_Sox.svg",
    "Cincinnati Reds": "File:Cincinnati_Reds_Logo.svg",
    "Cleveland Guardians": "File:Cleveland_Guardians_cap_logo.svg",
    "Colorado Rockies": "File:Colorado_Rockies_Cap_Insignia.svg",
    "Detroit Tigers": "File:Detroit_Tigers_logo.svg",
    "ESPN News": "File:ESPNews.svg",
    "FOX Deportes": "File:FOX Deportes logo.png",
    "History": "File:History (2021).svg",
    "Houston Astros": "File:Houston-Astros-Logo.svg",
    "Kansas City Royals": "File:Kansas_City_Royals_Primary_Logo.svg",
    "Los Angeles Angels": "File:Los_Angeles_Angels_of_Anaheim.svg",
    "Los Angeles Dodgers": "File:Los_Angeles_Dodgers_Logo.svg",
    "Minnesota Twins": "File:Minnesota_Twins_New_Logo.svg",
    "New York Mets": "File:New_York_Mets_Insignia.svg",
    "New York Yankees": "File:New_York_Yankees_Primary_Logo.svg",
    "Oakland Athletics": "File:Oakland_A's_logo.svg",
    "Philadelphia Phillies": "File:Philadelphia_Phillies_Insignia.svg",
    "Pittsburgh Pirates": "File:Pittsburgh_Pirates_logo_2014.svg",
    "San Diego Padres": "File:SD_Logo_Brown.svg",
    "San Francisco Giants": "File:San_Francisco_Giants_Cap_Insignia.svg",
    "SportsNet New York": "File:SNY_logo.svg",
    "St. Louis Cardinals": "File:St._Louis_Cardinals_insignia_logo.svg",
    "Tampa Bay Rays": "File:Tampa_Bay_Rays_Logo.svg",
    "Texas Rangers": "File:Texas Rangers logo.svg",
    "TV ONE": "File:TV One US 2012.png",
    "USA Network": "File:USA_Network_2020.svg",
}

WIKI_ENWIKI = {
    "Baltimore Orioles": "File:Baltimore Orioles Cap Insignia.svg",
    "Miami Marlins": "File:Marlins team logo.svg",
    "Milwaukee Brewers": "File:Milwaukee Brewers logo.svg",
    "Seattle Mariners": "File:Seattle Mariners Insignia.svg",
    "Toronto Blue Jays": "File:Toronto Blue Jay Primary Logo.svg",
}


def http_get(url, referer=REFERER, tries=5):
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "*/*",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if url.startswith("https://api.github.com/") and token:
        headers["Authorization"] = f"token {token}"
    for attempt in range(tries):
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 429:
                wait = min(60, (attempt + 1) * 5)
                print(f"  rate limited, sleeping {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}")


def b64dec(s):
    s = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.b64decode(s).decode("utf-8", "replace")


def extract_stream_url(html):
    ch = re.search(r'var\s+_CH\s*=\s*"([0-9a-f]+)"', html)
    if not ch:
        return None
    chunks = re.findall(r"var\s+\w+\s*=\s*'([A-Za-z0-9+/=]+)'\s*;", html)
    full = "".join(b64dec(c) for c in chunks)
    m = re.search(
        r"https?://[^\"'`\s]+?/playlist\.m3u8\?token=[A-Za-z0-9+/=]+"
        r"|\?token=[A-Za-z0-9+/=]+",
        full,
    )
    if not m:
        return None
    url = m.group(0)
    if url.startswith("?"):
        url = f"https://cdnlivetv.tv/secure/api/v1/{ch.group(1)}/playlist.m3u8" + url
    return url


def normalize(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.replace("&", " and ").replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def load_logo_index(index_file=LOGO_INDEX_FILE):
    paths = []
    cached = None
    if os.path.exists(index_file):
        try:
            with open(index_file, encoding="utf-8") as cache_file:
                cached = json.load(cache_file)
            paths = list(cached)
        except Exception:
            cached = None
    fresh = None
    try:
        data = json.loads(http_get(LOGO_TREE_URL))
        fresh = [t["path"] for t in data.get("tree", [])
                 if t.get("type") == "blob" and t["path"].endswith(".png")]
        if fresh:
            paths = fresh
            index_path = Path(index_file)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with index_path.open("w", encoding="utf-8") as cache_file:
                json.dump(paths, cache_file)
    except Exception as e:
        if not paths:
            raise SystemExit(f"Could not fetch K-yzu/Logos index: {e}")
        print(f"  using cached logo index ({len(paths)} logos): {e}",
              file=sys.stderr)
    paths = [p for p in paths if p.startswith(("TV:", "RADIO:", "MISC/")) and Path(p).suffix.lower() in {".png", ".gif", ".jpg", ".jpeg"}]
    by_file = {}
    for p in paths:
        by_file.setdefault(normalize(Path(p).stem), []).append(p)
    return by_file, cached is not None and not fresh


def match_logo(name, code, by_file):
    """Choose a K-yzu path for a channel, preferring its country directory."""
    base = normalize(name)
    country_dir = KYZU_COUNTRY_DIR.get((code or "").lower(), "")

    # Legacy overrides are kept as name hints; resolve their basename against
    # the current K-yzu tree rather than assuming the old countries/ layout.
    for override in (OVERRIDES.get(name), OVERRIDES.get(base)):
        if not override:
            continue
        stem = normalize(Path(override).stem)
        for path in by_file.get(stem, []):
            if not country_dir or path.startswith(country_dir + "/"):
                return path

    candidates = []
    for key in (base, base + f"-{(code or '').lower()}"):
        candidates.extend(by_file.get(normalize(key), []))
    if candidates:
        preferred = [path for path in candidates if path.startswith(country_dir + "/")] if country_dir else []
        return sorted(preferred or candidates)[0]

    # K-yzu keeps display names, so a normalized exact match is usually enough.
    for path in sorted(by_file.get(base, [])):
        if not country_dir or path.startswith(country_dir + "/"):
            return path
    return None


def public_logo_url(path):
    """Return the verified upstream artwork URL; the final build remaps it locally."""
    return f"{LOGO_RAW}{path}"


def build_playlist(limit=None, codes=ENGLISH_CODES, delay=0.0, logo_index=LOGO_INDEX_FILE):
    params = {"user": "cdnlivetv", "plan": "free"}
    url = API + "?" + urlencode(params)
    print("Fetching channel list ...", file=sys.stderr)
    data = json.loads(http_get(url))
    channels = [c for c in data.get("channels", []) if c.get("code") in codes]
    print(f"Total: {data.get('total_channels')}, English: {len(channels)}",
          file=sys.stderr)

    print("Fetching K-yzu/Logos index ...", file=sys.stderr)
    by_file, used_cache = load_logo_index(logo_index)
    if used_cache:
        print("  (used cached logo index)", file=sys.stderr)

    channels = channels if limit is None else channels[:limit]
    entries = []
    failed = 0
    matched = 0
    for i, ch in enumerate(channels, 1):
        name = ch["name"]
        q = urlencode({
            "name": name, "code": ch["code"],
            "user": "cdnlivetv", "plan": "free",
        })
        stream = None
        tries = 3
        while tries > 0:
            try:
                html = http_get(PLAYER + "?" + q)
                stream = extract_stream_url(html)
                break
            except Exception as e:
                tries -= 1
                print(f"  error {name}: {e}", file=sys.stderr)
                time.sleep(3)
        if stream:
            logo = match_logo(name, ch["code"], by_file)
            ch = dict(ch)
            if logo:
                ch["image"] = public_logo_url(logo)
                matched += 1
            else:
                ch["image"] = ""
            entries.append((ch, stream))
        else:
            failed += 1
            print(f"  skipped: {name} (no stream url)", file=sys.stderr)
        print(f"  [{i}/{len(channels)}] {name}", file=sys.stderr)
        if delay:
            time.sleep(delay)

    print(f"Logos matched: {matched}/{len(channels)}", file=sys.stderr)
    return entries, failed


def write_m3u(entries, out):
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for ch, stream in entries:
            esc = lambda s: re.sub(r"[\s,]", "_", s)
            f.write(
                f'#EXTINF:-1 tvg-id="{esc(ch["name"])}" '
                f'tvg-name="{ch["name"]}" '
                f'tvg-logo="{ch.get("image", "")}" '
                f'tvg-country="{ch["code"].upper()}" '
                f'group-title="{esc(ch["code"].upper())} TV",{ch["name"]}\n'
            )
            f.write(stream + "\n")
    return destination


def main():
    ap = argparse.ArgumentParser(
        description="Generate an m3u playlist of English channels from CDN Live TV.")
    ap.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                    help="output playlist file")
    ap.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT),
                    help="optional raw channel JSON output")
    ap.add_argument("--logo-index", default=LOGO_INDEX_FILE,
                    help="cache path for the upstream logo tree")
    ap.add_argument("--codes", default=",".join(ENGLISH_CODES),
                    help="comma-separated country codes to include")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process first N channels (for testing)")
    ap.add_argument("--delay", type=float, default=0.7,
                    help="seconds to wait between player page requests")
    args = ap.parse_args()

    codes = tuple(c.strip().lower() for c in args.codes.split(",") if c.strip())
    entries, failed = build_playlist(limit=args.limit, codes=codes,
                                     delay=args.delay, logo_index=args.logo_index)
    output = write_m3u(entries, args.output)
    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "cdn",
                "channels": [
                    {
                        "source_id": channel.get("code", ""),
                        "name": channel.get("name", ""),
                        "country": channel.get("code", "").upper(),
                        "logo": channel.get("image", ""),
                        "url": stream,
                    }
                    for channel, stream in entries
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} channels to {output} "
          f"({failed} failed) and raw data to {json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
