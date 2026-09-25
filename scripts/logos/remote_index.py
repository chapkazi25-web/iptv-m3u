#!/usr/bin/env python3
"""Refresh a metadata-only index of the K-yzu/Logos raw artwork repository.

No artwork is downloaded or stored in this project. The generated index contains
only paths, names, and public raw URLs used to populate M3U ``tvg-logo``
attributes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.pipeline import write_json

DEFAULT_CONFIG = PROJECT_ROOT / "config/logo-remote.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/logo-index.json"
ALLOWED_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg"}
COUNTRY_DIRECTORIES = {
    "TV:US": "US",
    "TV:US2": "US",
    "TV:UK": "GB",
    "TV:CA": "CA",
    "TV:AU": "AU",
    "TV:NZ": "NZ",
    "TV:FR": "FR",
    "RADIO:US": "US",
    "RADIO:UK": "GB",
}


def request_json(url: str, token: str = "", tries: int = 4) -> dict[str, Any]:
    headers = {
        "User-Agent": "iptv-m3u-remote-logo-index/1.0",
        "Accept": "application/vnd.github+json",
    }
    if token and urllib.parse.urlparse(url).netloc.lower() == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (403, 429) and attempt + 1 < tries:
                time.sleep(2 ** (attempt + 1))
                continue
            if error.code == 404:
                return {}
            if attempt + 1 == tries:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt + 1 == tries:
                raise
        time.sleep(2 ** (attempt + 1))
    return {}


def raw_url(base_url: str, path: str) -> str:
    # Colons are meaningful in K-yzu folder names (TV:US). Spaces and other
    # filename characters must be percent-encoded for portable M3U consumers.
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(path, safe='/:')}"


def build_records(tree: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = str(item.get("path", ""))
        suffix = Path(path).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            continue
        parts = path.split("/")
        if len(parts) < 2:
            continue
        directory = parts[0]
        kind = "channel"
        country = COUNTRY_DIRECTORIES.get(directory, "")
        if directory.startswith("RADIO:"):
            kind = "radio"
        elif directory == "MISC":
            kind = "misc"
        elif directory == "FLAGS":
            kind = "flag"
        elif directory == "World Cup":
            kind = "special"
        elif directory == "STREAMING SERVICES":
            kind = "streaming"
        records.append(
            {
                "name": Path(path).stem,
                "path": path,
                "url": raw_url(base_url, path),
                "country": country,
                "kind": kind,
                "extension": suffix,
            }
        )
    return sorted(records, key=lambda record: str(record["path"]).casefold())


def refresh(config_path: Path, output: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    repository = str(config.get("repository", "K-yzu/Logos"))
    branch = str(config.get("branch", "main"))
    base_url = str(config.get("raw_base_url", f"https://raw.githubusercontent.com/{repository}/{branch}"))
    api_url = str(config.get("api_url") or f"https://api.github.com/repos/{repository}/git/trees/{branch}?recursive=1")
    token = os.environ.get("GITHUB_TOKEN", "")
    tree = request_json(api_url, token=token)
    if tree.get("truncated"):
        raise RuntimeError("GitHub returned a truncated tree; refusing to write an incomplete index")
    records = build_records(tree, base_url)
    if not records:
        raise RuntimeError(f"no artwork paths found at {api_url}")
    write_json(
        output,
        {
            "version": 1,
            "repository": repository,
            "branch": branch,
            "raw_base_url": base_url,
            "logos": records,
        },
    )
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    count = refresh(args.config, args.output)
    print(f"indexed {count} remote K-yzu artwork paths -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
