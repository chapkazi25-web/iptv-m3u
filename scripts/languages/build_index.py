#!/usr/bin/env python3
"""
build_index.py — Build a per-channel language index for the catalog.

The catalog itself carries no language data, and the two public sources that
do carry it disagree in coverage:

  Grade TV   GET https://gradetv.net/api/channels   ISO 639-3 per channel,
             present for ~95% of its own catalog. Authoritative where present.
  iptv-org   GET https://iptv-org.github.io/api/channels.json
             has no language field, but many entries carry alt_names in the
             channel's original script (Cyrillic, Arabic, Devanagari, ...).
             iptv-org transliterates its primary names to ASCII, so the script
             of an alt_name is the only remaining language signal.

Both are combined into data/languages.json. Nothing is ever labelled English
by absence of evidence: a channel is english=true only when a source says so,
english=false when a source positively identifies another language, and
english=null when nothing is known. The catalog filter relies on that
distinction to avoid deleting channels on a guess.

Usage:
  python scripts/languages/build_index.py
  python scripts/languages/build_index.py --output data/languages.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import normalize_name

DEFAULT_OUTPUT = PROJECT_ROOT / "data/languages.json"
GRADETV_API = "https://gradetv.net/api/channels"
IPTVORG_CHANNELS = "https://iptv-org.github.io/api/channels.json"
PAGE_SIZE = 20

# Unicode script names that indicate a non-English original title. The list is
# deliberately conservative: a false positive would delete a good channel.
NON_LATIN_SCRIPTS = (
    "ARABIC", "CYRILLIC", "DEVANAGARI", "GREEK", "HEBREW", "CJK", "HIRAGANA",
    "KATAKANA", "HANGUL", "THAI", "BENGALI", "TAMIL", "TELUGU", "KANNADA",
    "MALAYALAM", "GUJARATI", "GURMUKHI", "ORIYA", "SINHALA", "ARMENIAN",
    "GEORGIAN", "LAO", "KHMER", "MYANMAR", "ETHIOPIC", "TIBETAN", "MONGOLIAN",
    "THAANA", "SYRIAC", "NKO", "TIFINAGH", "BALINESE", "JAVANESE",
    "SUNDANESE", "TAGALOG", "LIMBU", "MEETEI", "OL CHIKI", "BOPOMOFO",
    "YI", "CHEROKEE", "CANADIAN", "RUNIC", "GLAGOLITIC", "BRAILLE",
)

# iptv-org appends the feed quality to its tvg-id; Grade TV ids do not.
IPTVORG_QUALITY_RE = re.compile(
    r"\.(?:SD|HD|FHD|UHD|4K|360p|480p|540p|576[pi]|720p|1080[pi]|2160p)$", re.I
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; iptv-m3u-language-index/1.0)",
    "Accept": "application/json",
}


def fetch(url: str, timeout: float = 120.0, retries: int = 3) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as error:
            last = error
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def has_non_latin_script(value: str) -> bool:
    for character in value or "":
        if ord(character) < 128:
            continue
        try:
            name = unicodedata.name(character)
        except ValueError:
            continue
        if any(script in name for script in NON_LATIN_SCRIPTS):
            return True
    return False


def gradetv_languages() -> dict[str, set[str]]:
    """Map Grade TV channel id and normalized name to language codes."""
    by_key: dict[str, set[str]] = defaultdict(set)
    offset = 0
    while True:
        params = urllib.parse.urlencode({"limit": PAGE_SIZE, "offset": offset})
        payload = fetch(f"{GRADETV_API}?{params}")
        items = payload.get("items") or []
        if not items:
            break
        for record in items:
            languages = {
                str(value).lower()
                for value in (record.get("languages") or [])
                if str(value).strip()
            }
            if not languages:
                continue
            keys = []
            if record.get("id"):
                keys.append(str(record["id"]).lower())
            for name in [record.get("name")] + list(record.get("alt_names") or []):
                normalized = normalize_name(str(name or ""))
                if normalized:
                    keys.append(normalized)
            for key in keys:
                by_key[key].update(languages)
        offset += len(items)
        if offset >= int(payload.get("total") or 0) or offset >= 20000:
            break
        time.sleep(0.2)
    return by_key


def iptvorg_script_languages() -> dict[str, set[str]]:
    """Flag iptv-org entries whose alt_names use a non-Latin script."""
    by_key: dict[str, set[str]] = defaultdict(set)
    payload = fetch(IPTVORG_CHANNELS)
    for record in payload if isinstance(payload, list) else []:
        alt_names = [str(value) for value in (record.get("alt_names") or []) if value]
        if not any(has_non_latin_script(name) for name in alt_names):
            continue
        keys = []
        if record.get("id"):
            keys.append(str(record["id"]).lower())
            keys.append(IPTVORG_QUALITY_RE.sub("", str(record["id"]).lower()))
        normalized = normalize_name(str(record.get("name") or ""))
        if normalized:
            keys.append(normalized)
        for key in keys:
            # A sentinel that marks "known to be non-English" without
            # claiming a specific language.
            by_key[key].add("__nonlatin__")
    return by_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-gradetv", action="store_true")
    parser.add_argument("--skip-iptv-org", action="store_true")
    args = parser.parse_args(argv)

    merged: dict[str, set[str]] = defaultdict(set)
    if not args.skip_gradetv:
        gradetv = gradetv_languages()
        for key, values in gradetv.items():
            merged[key].update(values)
        print(f"gradetv: {len(gradetv)} keys with language data", file=sys.stderr)
    if not args.skip_iptv_org:
        script = iptvorg_script_languages()
        for key, values in script.items():
            merged[key].update(values)
        print(f"iptv-org: {len(script)} keys flagged non-Latin", file=sys.stderr)

    channels: dict[str, dict[str, Any]] = {}
    for key, values in merged.items():
        real = sorted(value for value in values if value != "__nonlatin__")
        if any(value.startswith("eng") for value in real):
            english: bool | None = True
        elif real:
            english = False
        else:
            english = False if "__nonlatin__" in values else None
        channels[key] = {"languages": real, "english": english}

    english = sum(1 for value in channels.values() if value["english"] is True)
    non_english = sum(1 for value in channels.values() if value["english"] is False)
    payload = {
        "version": 1,
        "sources": {
            "gradetv": not args.skip_gradetv,
            "iptv-org": not args.skip_iptv_org,
        },
        "counts": {
            "keys": len(channels),
            "english": english,
            "non_english": non_english,
        },
        "channels": dict(sorted(channels.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {len(channels)} keys ({english} english, {non_english} non-english) to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
