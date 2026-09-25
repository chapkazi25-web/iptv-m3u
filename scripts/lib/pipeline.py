"""Configuration, health, and logo helpers for playlist builds."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import normalize_name
from .m3u import M3UEntry

QUALITY_RANK = {"SD": 0, "HD": 720, "FHD": 1080, "UHD": 2160, "4K": 2160}


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    playlist: Path
    priority: int
    default_country: str
    ephemeral: bool

    @classmethod
    def from_dict(cls, source_id: str, value: dict[str, Any], root: Path) -> "SourceConfig":
        return cls(
            source_id=source_id,
            name=str(value.get("name", source_id)),
            playlist=root / str(value.get("playlist", f"playlists/sources/{source_id}.m3u")),
            priority=int(value.get("priority", 999)),
            default_country=str(value.get("default_country") or "").upper(),
            ephemeral=bool(value.get("ephemeral", False)),
        )


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_sources(root: str | Path = ".") -> dict[str, SourceConfig]:
    project_root = Path(root)
    data = load_json(project_root / "data/sources.json")
    return {
        source_id: SourceConfig.from_dict(source_id, value, project_root)
        for source_id, value in data.get("sources", {}).items()
    }


def load_category_config(root: str | Path = ".") -> tuple[dict[str, dict[str, Any]], str, dict[str, str]]:
    data = load_json(Path(root) / "data/categories.json")
    categories = data.get("categories", {})
    aliases: dict[str, str] = {}
    for slug, config in categories.items():
        aliases[normalize_name(slug)] = slug
        aliases[normalize_name(config.get("name", slug))] = slug
        for alias in config.get("aliases", []):
            aliases[normalize_name(str(alias))] = slug
    return categories, str(data.get("default", "entertainment")), aliases


def canonical_category(entry: M3UEntry, aliases: dict[str, str], default: str) -> str:
    group_key = normalize_name(entry.group)
    if group_key in aliases:
        return aliases[group_key]
    if entry.name.lstrip().startswith("["):
        return "live-events"
    return default


def category_name(category_id: str, categories: dict[str, dict[str, Any]], default: str = "Entertainment") -> str:
    return str(categories.get(category_id, {}).get("name", default))


def quality_rank(entry: M3UEntry) -> int:
    upper = entry.title.upper()
    for label in ("UHD", "FHD", "HD", "SD", "4K"):
        if f"({label})" in upper:
            return QUALITY_RANK[label]
    return 0


def stream_id(source: str, channel_id: str, url: str) -> str:
    payload = f"{source}\0{channel_id}\0{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


class HealthStore:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.disable_after_failures = int(data.get("disable_after_failures", 6))
        self.streams: dict[str, dict[str, Any]] = dict(data.get("streams", {}))

    @classmethod
    def load(cls, path: str | Path) -> "HealthStore":
        return cls(load_json(path) if Path(path).exists() else {})

    def record(self, identifier: str) -> dict[str, Any]:
        return self.streams.get(identifier, {})

    def enabled(self, identifier: str) -> bool:
        value = self.record(identifier)
        if not value:
            return True
        if value.get("enabled") is False:
            return False
        return int(value.get("failures", 0) or 0) < self.disable_after_failures

    def status_rank(self, identifier: str) -> int:
        value = self.record(identifier)
        if not value:
            return 1  # untested but usable
        status = str(value.get("status", "unknown")).lower()
        return {"online": 0, "unknown": 1, "degraded": 2, "unstable": 3, "offline": 4}.get(status, 2)

    def failures(self, identifier: str) -> int:
        return int(self.record(identifier).get("failures", 0) or 0)

    def response_time(self, identifier: str) -> int | None:
        value = self.record(identifier).get("response_time_ms")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def score(self, identifier: str, source_priority: int) -> tuple[Any, ...]:
        response = self.response_time(identifier)
        return (
            self.status_rank(identifier),
            min(self.failures(identifier), self.disable_after_failures),
            response if response is not None else 10**9,
            source_priority,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "disable_after_failures": self.disable_after_failures,
            "streams": {key: self.streams[key] for key in sorted(self.streams)},
        }


class LogoIndex:
    """Name/country lookup over the metadata-only K-yzu artwork index."""

    def __init__(self, records: list[dict[str, Any]], base_url: str) -> None:
        self.records = records
        self.base_url = base_url.rstrip("/")
        self._by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._by_country: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            name_key = normalize_name(str(record.get("name", "")))
            if name_key:
                self._by_name[name_key].append(record)
            country = str(record.get("country", "")).upper()
            if name_key and country:
                self._by_country[(name_key, country)].append(record)

    @classmethod
    def load(cls, path: str | Path, base_url: str) -> "LogoIndex":
        if not Path(path).exists():
            return cls([], base_url)
        data = load_json(path)
        return cls(data.get("logos", []), str(data.get("raw_base_url", base_url)))

    def find(self, name: str, country: str = "") -> str:
        key = normalize_name(name)
        if country:
            candidates = self._by_country.get((key, country.upper()), [])
        else:
            candidates = self._by_name.get(key, [])
        if not candidates:
            return ""
        candidates = sorted(
            candidates,
            key=lambda item: (
                0 if str(item.get("country", "")).upper() == country.upper() else 1,
                0 if str(item.get("kind", "")) == "channel" else 1,
                len(str(item.get("path", ""))),
                str(item.get("path", "")),
            ),
        )
        record = candidates[0]
        url = str(record.get("url", "")).strip()
        if url:
            return url
        path = str(record.get("path", "")).replace("\\", "/")
        return f"{self.base_url}/{urllib.parse.quote(path, safe='/:')}" if path else ""


def stream_headers(entry: M3UEntry) -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; iptv-m3u/1.0)"}
    for line in entry.extra_lines:
        if not line.lower().startswith("#extvlcopt:http-"):
            continue
        key, separator, value = line.split(":", 1)[1].partition("=")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "http-user-agent" and value:
            headers["User-Agent"] = value
        elif key == "http-referrer" and value:
            headers["Referer"] = value
    return headers
