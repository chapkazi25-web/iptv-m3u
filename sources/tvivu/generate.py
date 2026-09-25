#!/usr/bin/env python3
"""
generate.py — Extract HD/FHD music-channel streams from tvivu.com
and generate a VLC / Tivimate compatible M3U playlist.

Sources:
  Channels : https://tvivu.com/categories/music (paginated ?page=N)
  Watch    : https://tvivu.com/watch/<slug>  (embedded stream JSON)
  Logos    : K-yzu/Logos raw artwork (https://github.com/K-yzu/Logos)

Usage:
  pip install requests
  python sources/tvivu/generate.py --output playlists/sources/tvivu-music.m3u
  python sources/tvivu/generate.py --output out.m3u --max-channels 20 --workers 8
  python sources/tvivu/generate.py --output out.m3u --include-unknown --no-logo-resolve

Playlist output is `#EXTM3U` + `#EXTINF` entries with
tvg-id / tvg-logo / tvg-country / group-title="Music" plus
EXTVLCOPT user-agent/referrer hints (ignored by Tivimate, used by VLC).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html as htmlmod
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: the 'requests' package is required. Install with: pip install requests",
          file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "playlists/sources/tvivu-music.m3u"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "data/raw/tvivu.json"
BASE = "https://tvivu.com"
CATEGORY_URL = f"{BASE}/categories/music"
WATCH_URL = f"{BASE}/watch"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------- quality

HD_QUALITIES = {"720p", "720p+", "1080p", "1080i", "hd", "fhd",
                "1280x720", "1920x1080", "720", "1080"}

RE_HEIGHT_P = re.compile(r"(\d{3,4})\s*p\+?", re.IGNORECASE)
RE_WxH = re.compile(r"(\d{3,4})\s*x\s*(\d{3,4})", re.IGNORECASE)


def quality_height(quality: str | None) -> int | None:
    """Return vertical resolution in px for a quality label, else None."""
    if not quality:
        return None
    q = quality.strip().lower()
    if q in ("null", "none", "unknown", "auto", ""):
        return None
    m = RE_WxH.search(q)
    if m:
        try:
            return int(m.group(2))
        except ValueError:
            pass
    m = RE_HEIGHT_P.search(q)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    if q in HD_QUALITIES:
        return 720 if q in ("720p", "720p+", "hd", "720", "1280x720") else 1080
    return None


def is_hd_quality(quality: str | None, min_height: int = 720) -> bool:
    h = quality_height(quality)
    return h is not None and h >= min_height


def quality_rank(quality: str | None) -> int:
    h = quality_height(quality)
    return h or 0

# ------------------------------------------------------------ logo sources

KYZU_BASE = "https://raw.githubusercontent.com/K-yzu/Logos/main"

# ISO code -> K-yzu folder
KYZU_FOLDER = {
    "US": "TV:US", "GB": "TV:UK", "UK": "TV:UK", "CA": "TV:CA",
    "AU": "TV:AU", "FR": "TV:FR", "NZ": "TV:NZ",
}

# Hand-checked overrides: (Channel Name lower, CC upper) -> direct logo URL.
# Add entries here when auto-resolution picks a wrong/missing logo.
LOGO_OVERRIDES: dict[tuple[str, str], str] = {
    # e.g.: ("mtv classic", "US"): "https://raw.githubusercontent.com/.../mtv-classic-us.png",
}

_logo_cache: dict[tuple[str, str], str] = {}


def slugify(name: str) -> str:
    s = name.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-+", "-", s)


def url_ok(session: requests.Session, url: str, timeout: float = 6.0) -> bool:
    try:
        r = session.head(url, timeout=timeout, allow_redirects=True,
                         headers=HEADERS)
        if r.status_code == 200:
            ct = r.headers.get("Content-Type", "")
            return "image" in ct or "octet-stream" in ct or ct == ""
        # Some hosts (wikimedia/raw) dislike HEAD -> try ranged GET
        if r.status_code in (403, 405):
            g = session.get(url, timeout=timeout, stream=True,
                            headers={**HEADERS, "Range": "bytes=0-0"})
            ok = g.status_code in (200, 206)
            g.close()
            return ok
        return False
    except requests.RequestException:
        return False


def resolve_logo(session: requests.Session, channel: str, country: str,
                 fallback: str = "", check: bool = True) -> str:
    """Resolve artwork strictly from K-yzu/Logos raw URLs.

    ``fallback`` is accepted for call-site compatibility but is intentionally
    ignored: generated playlists must not reintroduce a second artwork host.
    """
    key = (channel.lower(), (country or "").upper())
    if key in _logo_cache:
        return _logo_cache[key]
    if key in LOGO_OVERRIDES and "raw.githubusercontent.com/K-yzu/Logos/" in LOGO_OVERRIDES[key]:
        _logo_cache[key] = LOGO_OVERRIDES[key]
        return LOGO_OVERRIDES[key]

    cc = (country or "").upper()
    folder = KYZU_FOLDER.get(cc, "MISC")
    filename = urllib.parse.quote(f"{channel}.png", safe="")
    url = f"{KYZU_BASE}/{urllib.parse.quote(folder, safe=':')}/{filename}"
    if not check or url_ok(session, url):
        _logo_cache[key] = url
        return url

    _logo_cache[key] = ""
    return ""

# ------------------------------------------------------- tvivu extraction

def normalize_escapes(text: str) -> str:
    """Collapse the JS-string escapes tvivu embeds in HTML.

    Watch/category pages embed JSON with single-backslash escaping, e.g.
    ``{\\"slug\\":\\"zo\\'r-tv\\",\\"url\\":\\"https://...m3u8\\u0026exp=..\\"}``.
    Decoding \\uXXXX / \\/ / \\' up front keeps the field regexes simple
    (only \\" then remains as a delimiter).
    """
    text = re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\/", "/").replace("\\'", "'")


# channel card in category HTML (single-backslash escaped JSON):
# {\"slug\":\"mtv-classic-3\",\"name\":\"MTV Classic\",\"logoUrl\":\"...\",\"countryCode\":\"US\",...}
RE_CARD = re.compile(
    r'\\"slug\\":\\"([a-z0-9][a-z0-9\-]*)\\",'
    r'\\"name\\":\\"([^\"\\]*)\\",'
    r'\\"logoUrl\\":\\"([^\"\\]*)\\",'
    r'\\"countryCode\\":\\"([A-Z]{2})\\"'
)

# stream object in watch HTML:
# {\"id\":\"...\",\"url\":\"https://...\",\"streamType\":\"hls\",\"quality\":\"720p\",
#  \"userAgent\":... ,\"referrer\":...}
RE_STREAM = re.compile(
    r'\\"url\\":\\"((?:https?:)?[^\"\\]+?)\\",'
    r'\\"streamType\\":\\"([^\"\\]*)\\",'
    r'\\"quality\\":(?:\\"([^\"\\]*)\\"|null)'
    r'(?:,\\"userAgent\\":(?:\\"([^\"\\]*)\\"|null))?'
    r'(?:,\\"referrer\\":(?:\\"([^\"\\]*)\\"|null))?'
)

RE_CH_META = re.compile(
    r'\\"channel\\":\{\\"id\\":\\"[^\"\\]*\\",'
    r'\\"slug\\":\\"([a-z0-9][a-z0-9\-]*)\\",'
    r'\\"name\\":\\"([^\"\\]*)\\",'
    r'\\"logoUrl\\":\\"([^\"\\]*)\\",'
    r'\\"countryCode\\":\\"([A-Z]{2})\\".*?'
    r'\\"streamUrl\\":\\"([^\"\\]*)\\"'
)


def unescape(s: str) -> str:
    return (s.replace("\\u0026", "&").replace("\\/", "/")
             .replace('\\"', '"').replace("\\'", "'"))


def unwrap_proxy(url: str) -> str:
    """Unwrap tvivu proxy URLs (srv*.pxfy.dev/?url=<encoded>&exp=&sig=)."""
    try:
        p = urllib.parse.urlparse(url)
        if p.netloc.endswith("pxfy.dev") or "url=" in (p.query or ""):
            qs = urllib.parse.parse_qs(p.query)
            if "url" in qs and qs["url"]:
                inner = urllib.parse.unquote(qs["url"][0])
                if inner.startswith("http"):
                    return inner
    except Exception:
        pass
    return url


def fetch_text(session: requests.Session, url: str, timeout: float,
               retries: int = 5) -> str:
    last = None
    for i in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 429:  # rate limited: back off and retry
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 3.0 * (i + 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last  # type: ignore[misc]


def get_category_channels(session: requests.Session, timeout: float,
                          max_pages: int = 30, delay: float = 0.3,
                          verbose: bool = True) -> list[dict]:
    """Paginate /categories/music?page=N and collect channel cards."""
    channels: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}?page={page}"
        html_text = normalize_escapes(fetch_text(session, url, timeout))
        found = 0
        for m in RE_CARD.finditer(html_text):
            slug, raw_name, logo, cc = m.groups()
            if slug == "music" or slug in channels:
                continue
            name = unescape(raw_name)
            channels[slug] = {"slug": slug, "name": name,
                              "logoUrl": logo, "countryCode": cc}
            found += 1
        if verbose:
            print(f"[category] page {page}: +{found} new "
                  f"(total {len(channels)})")
        time.sleep(delay)
        if found == 0:
            break  # last page reached
    return list(channels.values())


def get_channel_streams(session: requests.Session, slug: str,
                        timeout: float) -> dict:
    """Fetch a watch page and return meta + stream list."""
    html_text = normalize_escapes(
        fetch_text(session, f"{WATCH_URL}/{slug}", timeout))
    meta = {"slug": slug, "name": slug, "logoUrl": "",
            "countryCode": "", "streamUrl": ""}
    m = RE_CH_META.search(html_text)
    if m:
        meta = {"slug": m.group(1), "name": unescape(m.group(2)),
                "logoUrl": m.group(3), "countryCode": m.group(4),
                "streamUrl": unescape(m.group(5))}
    streams: list[dict] = []
    for sm in RE_STREAM.finditer(html_text):
        raw_url, stype, quality, ua, ref = sm.groups()
        url = unescape(raw_url)
        if not url.startswith("http"):
            url = "https:" + url if url.startswith("//") else url
        if not url.startswith("http"):
            continue
        streams.append({
            "url": unwrap_proxy(url),
            "raw_url": url,
            "streamType": stype or "hls",
            "quality": quality,
            "userAgent": unescape(ua) if ua else None,
            "referrer": unescape(ref) if ref else None,
        })
    # dedupe by direct URL, keep best quality label
    seen: dict[str, dict] = {}
    for s in streams:
        prev = seen.get(s["url"])
        if prev is None or quality_rank(s["quality"]) > quality_rank(prev["quality"]):
            seen[s["url"]] = s
    return {"meta": meta, "streams": list(seen.values())}


def pick_hd_streams(streams: list[dict], min_height: int,
                    include_unknown: bool = False) -> list[dict]:
    out = []
    for s in streams:
        q = s.get("quality")
        if is_hd_quality(q, min_height):
            out.append(s)
        elif include_unknown and quality_height(q) is None and q in (None, "null"):
            out.append({**s, "quality": "unknown"})
    out.sort(key=lambda s: quality_rank(s.get("quality")), reverse=True)
    return out

# ------------------------------------------------------------------- m3u

SAFE_TVG_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def tvg_id(name: str, country: str) -> str:
    base = re.sub(r"\s+", "", name)
    base = SAFE_TVG_ID_RE.sub("", base)
    return f"{base}.{country.lower()}" if country else base


def m3u_entry(name: str, country: str, logo: str, url: str,
              quality: str | None, user_agent: str | None,
              referrer: str | None) -> str:
    q = (quality or "").strip()
    h = quality_height(q)
    if h is not None and h >= 1080:
        qlabel = "FHD"
    elif h is not None and h >= 720:
        qlabel = "HD"
    elif q and q.lower() != "unknown":
        qlabel = q
    else:
        qlabel = ""
    label = f"{name} ({qlabel})" if qlabel else name
    lines = [f'#EXTINF:-1 tvg-id="{tvg_id(name, country)}" '
             f'tvg-logo="{logo}" tvg-country="{country}" '
             f'group-title="Music",{label}']
    if user_agent:
        lines.append(f"#EXTVLCOPT:http-user-agent={user_agent}")
    if referrer:
        lines.append(f"#EXTVLCOPT:http-referrer={referrer}")
    lines.append(url)
    return "\n".join(lines)


def write_playlist(path: str, entries: list[str]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U x-tvg-url=\"\"\n")
        for e in entries:
            f.write(e + "\n")
    return destination

# ------------------------------------------------------------------- main

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Extract HD/FHD music channels from tvivu.com into an M3U playlist.")
    ap.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT),
                    help=f"Output .m3u file (default: {DEFAULT_OUTPUT})")
    ap.add_argument("--max-channels", type=int, default=0,
                    help="Limit number of category channels (0 = all)")
    ap.add_argument("--workers", type=int, default=4,
                    help="Parallel watch-page fetchers (default: 4)")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="HTTP timeout seconds (default: 20)")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="Delay between category page fetches (default: 0.25)")
    ap.add_argument("--min-height", type=int, default=720,
                    help="Minimum height px to count as HD (default: 720)")
    ap.add_argument("--include-unknown", action="store_true",
                    help="Also keep streams with unknown quality")
    ap.add_argument("--no-logo-resolve", action="store_true",
                    help="Skip remote logo verification and construct K-yzu raw URLs directly")
    ap.add_argument("--dump-json", default=str(DEFAULT_JSON_OUTPUT),
                    help="path to dump extracted channel JSON; empty disables it")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Fetching music category: {CATEGORY_URL}")
    channels = get_category_channels(session, args.timeout,
                                     delay=args.delay)
    print(f"Found {len(channels)} music channels.")
    if args.max_channels and args.max_channels > 0:
        channels = channels[:args.max_channels]
        print(f"Limited to first {len(channels)} channels.")

    # ---- fetch watch pages in parallel
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(get_channel_streams, requests.Session(),
                          ch["slug"], args.timeout): ch for ch in channels}
        # share default headers with worker sessions
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            ch = futs[fut]
            done += 1
            try:
                data = fut.result()
                # merge category card meta as fallback
                for k in ("name", "logoUrl", "countryCode"):
                    if not data["meta"].get(k) and ch.get(k):
                        data["meta"][k] = ch[k]
                data["meta"].setdefault("slug", ch["slug"])
                results.append(data)
            except Exception as e:  # noqa: BLE001 - report and continue
                print(f"[warn] {ch['slug']}: {e}")
            if done % 25 == 0 or done == len(channels):
                print(f"[watch] {done}/{len(channels)} pages fetched")

    # ---- filter HD/FHD + resolve logos + build entries
    entries: list[str] = []
    kept = skipped = 0
    for data in results:
        meta = data["meta"]
        hd = pick_hd_streams(data["streams"], args.min_height,
                             args.include_unknown)
        if not hd:
            skipped += 1
            continue
        kept += 1
        best = hd[0]  # highest quality stream for this channel
        logo = meta.get("logoUrl", "")
        logo = resolve_logo(
            session,
            meta["name"],
            meta.get("countryCode", ""),
            fallback=logo,
            check=not args.no_logo_resolve,
        )
        entries.append(m3u_entry(meta["name"], meta.get("countryCode", ""),
                                 logo, best["url"], best.get("quality"),
                                 best.get("userAgent"), best.get("referrer")))

    entries.sort(key=str.lower)
    write_playlist(args.output, entries)
    print(f"Channels with HD/FHD streams: {kept} | without: {skipped}")
    print(f"Wrote {len(entries)} entries -> {args.output}")

    if args.dump_json:
        dump = [{"slug": d["meta"].get("slug"), "name": d["meta"].get("name"),
                 "country": d["meta"].get("countryCode"),
                 "streams": d["streams"]} for d in results]
        dump_path = Path(args.dump_json)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with dump_path.open("w", encoding="utf-8") as f:
            json.dump({"version": 1, "source": "tvivu", "channels": dump}, f, indent=2, ensure_ascii=False)
        print(f"Dumped raw data -> {dump_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
