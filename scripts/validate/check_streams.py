#!/usr/bin/env python3
"""Probe source streams and update health state with failure hysteresis."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.catalog import ChannelCatalog
from scripts.lib.m3u import parse_m3u
from scripts.lib.pipeline import HealthStore, load_sources, stream_headers, stream_id, write_json

PROBE_BYTES = 65536
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; iptv-m3u-health-check/1.0)"


@dataclass(frozen=True)
class ProbeTarget:
    source: str
    channel_id: str
    identifier: str
    url: str
    headers: dict[str, str]
    ephemeral: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HostLimiter:
    """Cap how many probes may hit the same host at once.

    The catalog is spread over thousands of hosts, but a few carry hundreds of
    channels each. Without a per-host cap, raising the worker count turns into
    a burst against those hosts, and the resulting 429/503 responses are
    recorded as genuine failures. A dead channel is only disabled after six
    consecutive failures, so a single rate-limited run can wrongly retire a
    healthy stream. Capping per host lets total concurrency rise safely.
    """

    def __init__(self, max_per_host: int) -> None:
        self.max_per_host = max(1, int(max_per_host))
        self._locks: dict[str, threading.Semaphore] = {}
        self._guard = threading.Lock()

    def lock_for(self, host: str) -> threading.Semaphore:
        key = host.lower()
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Semaphore(self.max_per_host)
                self._locks[key] = lock
            return lock

    def limit(self, url: str) -> threading.Semaphore:
        return self.lock_for(urllib.parse.urlparse(url).netloc)


def probe(
    target: ProbeTarget,
    timeout: float,
    limiter: HostLimiter | None = None,
) -> tuple[bool, int | None, str]:
    """Probe one stream, returning (online, response_time_ms, error)."""
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Range": f"bytes=0-{PROBE_BYTES - 1}", **target.headers}
    lock = limiter.limit(target.url) if limiter is not None else None
    if lock is not None:
        lock.acquire()
    started = time.perf_counter()
    try:
        request = urllib.request.Request(target.url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.getcode()
            body = response.read(PROBE_BYTES)
            elapsed = max(0, round((time.perf_counter() - started) * 1000))
            if status not in (200, 206):
                return False, elapsed, f"HTTP {status}"
            if not body:
                return False, elapsed, "empty response"
            path = urllib.parse.urlparse(target.url).path.lower()
            content_type = response.headers.get("Content-Type", "").lower()
            if (path.endswith(".m3u8") or "mpegurl" in content_type) and b"#EXTM3U" not in body:
                return False, elapsed, "response is not an HLS playlist"
            return True, elapsed, ""
    except urllib.error.HTTPError as error:
        return False, max(0, round((time.perf_counter() - started) * 1000)), f"HTTP {error.code}"
    except urllib.error.URLError as error:
        reason = getattr(error.reason, "strerror", None) or str(error.reason)
        return False, max(0, round((time.perf_counter() - started) * 1000)), f"URL error: {reason}"[:120]
    except (socket.timeout, TimeoutError):
        return False, max(0, round((time.perf_counter() - started) * 1000)), "timeout"
    except Exception as error:  # noqa: BLE001 - persist a useful health result
        return False, max(0, round((time.perf_counter() - started) * 1000)), type(error).__name__
    finally:
        if lock is not None:
            lock.release()


def collect_targets(root: Path, selected_sources: set[str]) -> list[ProbeTarget]:
    catalog = ChannelCatalog.load(root)
    sources = load_sources(root)
    targets: list[ProbeTarget] = []
    seen: set[str] = set()
    for source_id, source in sources.items():
        if selected_sources and source_id not in selected_sources:
            continue
        if not source.playlist.exists():
            raise FileNotFoundError(source.playlist)
        _, entries = parse_m3u(source.playlist)
        for entry in entries:
            resolution = catalog.resolve(source_id, entry, source.default_country or None)
            identifier = stream_id(source_id, resolution.channel_id, entry.url)
            if identifier in seen:
                continue
            seen.add(identifier)
            targets.append(
                ProbeTarget(
                    source=source_id,
                    channel_id=resolution.channel_id,
                    identifier=identifier,
                    url=entry.url,
                    headers=stream_headers(entry),
                    ephemeral=source.ephemeral,
                )
            )
    return targets


def update_record(
    health: HealthStore,
    target: ProbeTarget,
    success: bool,
    response_time_ms: int | None,
    error: str,
    checked_at: str,
) -> None:
    previous = health.record(target.identifier)
    failures = 0 if success else int(previous.get("failures", 0) or 0) + 1
    if success:
        status = "online"
    elif failures <= 2:
        status = "degraded"
    elif failures < health.disable_after_failures:
        status = "unstable"
    else:
        status = "offline"
    record: dict[str, Any] = {
        "channel": target.channel_id,
        "source": target.source,
        "url": target.url,
        "status": status,
        "enabled": not (not success and not target.ephemeral and failures >= health.disable_after_failures),
        "failures": failures,
        "last_check": checked_at,
        "present": True,
    }
    if response_time_ms is not None:
        record["response_time_ms"] = response_time_ms
    if success:
        record["last_success"] = checked_at
        record.pop("error", None)
    else:
        record["error"] = error
    health.streams[target.identifier] = record


def spread_by_host(targets: list[ProbeTarget]) -> list[ProbeTarget]:
    """Round-robin targets across hosts so no host forms one long run.

    Grouping by host would let a worker pool issue a burst of concurrent
    requests at a single provider, which looks like an attack and invites
    429/503 responses. Interleaving keeps instantaneous per-host load low,
    and the per-host cap then handles the remainder.
    """
    buckets: dict[str, list[ProbeTarget]] = {}
    for target in targets:
        host = urllib.parse.urlparse(target.url).netloc.lower()
        buckets.setdefault(host, []).append(target)
    ordered: list[ProbeTarget] = []
    depth = max((len(items) for items in buckets.values()), default=0)
    for index in range(depth):
        for items in buckets.values():
            if index < len(items):
                ordered.append(items[index])
    return ordered


def run(
    root: Path,
    *,
    workers: int,
    timeout: float,
    limit: int,
    selected_sources: set[str],
    max_per_host: int = 0,
) -> dict[str, Any]:
    health = HealthStore.load(root / "data/stream-health.json")
    targets = collect_targets(root, selected_sources)
    targets.sort(key=lambda target: (target.source, target.channel_id, target.identifier))
    if limit > 0:
        targets = targets[:limit]
    if not selected_sources and limit <= 0:
        seen = {target.identifier for target in targets}
        for identifier, record in health.streams.items():
            if identifier not in seen:
                record["present"] = False

    # Interleave hosts so a large provider is spread across the run instead of
    # arriving as one contiguous burst.
    targets = spread_by_host(targets)
    limiter = HostLimiter(max_per_host) if max_per_host > 0 else None
    online = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(probe, target, timeout, limiter): target for target in targets
        }
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            target = futures[future]
            success, response_time_ms, error = future.result()
            checked_at = utc_now()
            update_record(health, target, success, response_time_ms, error, checked_at)
            online += int(success)
            if completed % 500 == 0 or completed == len(targets):
                print(f"checked {completed}/{len(targets)} ({online} online)", flush=True)

    write_json(root / "data/stream-health.json", health.as_dict())
    return {
        "checked": len(targets),
        "online": online,
        "offline": len(targets) - online,
        "workers": workers,
        "timeout_seconds": timeout,
        "max_per_host": max_per_host,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--max-per-host",
        type=int,
        default=16,
        dest="max_per_host",
        help="max simultaneous probes per host; 0 disables the cap",
    )
    parser.add_argument("--limit", type=int, default=0, help="maximum streams to check; 0 checks all")
    parser.add_argument("--source", action="append", default=[], help="limit to a source id; repeatable")
    args = parser.parse_args()
    result = run(
        args.root.resolve(),
        workers=args.workers,
        timeout=args.timeout,
        limit=args.limit,
        selected_sources=set(args.source),
        max_per_host=args.max_per_host,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
