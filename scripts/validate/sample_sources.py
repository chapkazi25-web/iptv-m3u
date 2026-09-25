#!/usr/bin/env python3
"""Probe a sample of streams per source and report which ones actually play.

Usage:
  python scripts/validate/sample_sources.py --per-source 20
  python scripts/validate/sample_sources.py --per-source 20 --timeout 15
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.m3u import parse_m3u
from scripts.lib.pipeline import load_sources, stream_headers

PROBE_BYTES = 4096
USER_AGENT = "Mozilla/5.0 (compatible; iptv-m3u-sample/1.0)"


def probe(url: str, headers: dict[str, str], timeout: float) -> tuple[bool, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{PROBE_BYTES - 1}", **headers},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(PROBE_BYTES)
            elapsed = round((time.perf_counter() - started) * 1000)
            text = body[:2048].lstrip()
            if text.startswith(b"#EXTM3U"):
                return True, f"{elapsed}ms hls"
            if text.startswith(b"\x1f\x8b") or body[:5] == b"#EXTM":
                return True, f"{elapsed}ms hls"
            return True, f"{elapsed}ms http {response.status}"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except urllib.error.URLError as error:
        return False, f"{type(error.reason).__name__ if error.reason else 'URLError'}"
    except Exception as error:  # noqa: BLE001 - report any transport failure
        return False, type(error).__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--per-source", type=int, default=20)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    targets: dict[str, list[tuple[str, str, dict[str, str]]]] = defaultdict(list)
    for source_id, source in load_sources(root).items():
        if not source.playlist.exists():
            print(f"{source_id}: playlist missing, skipped", file=sys.stderr)
            continue
        _, entries = parse_m3u(source.playlist)
        # Spread the sample across the playlist instead of taking a prefix,
        # which would only exercise one publisher's block.
        step = max(1, len(entries) // args.per_source)
        for entry in entries[::step][: args.per_source]:
            targets[source_id].append((entry.title or "?", entry.url, stream_headers(entry)))

    jobs = [
        (source_id, title, url, headers)
        for source_id, items in targets.items()
        for title, url, headers in items
    ]
    results: dict[str, list[tuple[str, bool, str]]] = defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, url, headers, args.timeout): (sid, title) for sid, title, url, headers in jobs}
        for future in concurrent.futures.as_completed(futures):
            source_id, title = futures[future]
            ok, detail = future.result()
            results[source_id].append((title, ok, detail))

    print(f"{'source':<12} {'played':>10} {'failed':>8}   rate")
    print("-" * 52)
    totals = [0, 0]
    for source_id in sorted(results):
        rows = results[source_id]
        ok = sum(1 for _, good, _ in rows if good)
        bad = len(rows) - ok
        totals[0] += ok
        totals[1] += bad
        print(f"{source_id:<12} {ok:>10} {bad:>8}   {ok / len(rows) * 100:.0f}%")
    print("-" * 52)
    print(f"{'TOTAL':<12} {totals[0]:>10} {totals[1]:>8}")

    print("\nfailures:")
    for source_id in sorted(results):
        for title, ok, detail in sorted(results[source_id]):
            if not ok:
                print(f"  {source_id:<10} {title[:44]:<44} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
