"""Configuration, health, and logo helpers for playlist builds."""

from __future__ import annotations

import hashlib
import json
import re
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


def load_name_patterns(root: str | Path = ".") -> list[tuple[re.Pattern[str], str]]:
    """Return (compiled regex, category slug) pairs from the category config.

    Brand and sport channels are published under generic groups such as
    "Sports", so the display name is the only reliable signal. Order follows
    the config so specific brands win over the broad sport patterns.
    """
    data = load_json(Path(root) / "data/categories.json")
    pairs: list[tuple[re.Pattern[str], str]] = []
    for slug, config in data.get("categories", {}).items():
        for pattern in config.get("name_patterns", []):
            try:
                pairs.append((re.compile(str(pattern), re.I), slug))
            except re.error as error:
                raise ValueError(f"invalid name_pattern for {slug!r}: {pattern!r} ({error})") from error
    return pairs


def load_exclusions(root: str | Path = ".") -> dict[str, Any]:
    """Return the exclusion rules used to drop unwanted channels."""
    data = load_json(Path(root) / "data/categories.json")
    config = data.get("exclude", {})
    return {
        "categories": {str(value) for value in config.get("categories", [])},
        "channels": {str(value) for value in config.get("channels", [])},
        "name_patterns": [
            re.compile(str(pattern), re.I) for pattern in config.get("name_patterns", [])
        ],
        "group_patterns": [
            re.compile(str(pattern), re.I) for pattern in config.get("group_patterns", [])
        ],
    }


def is_excluded_group(group: str, rules: dict[str, Any]) -> bool:
    """True when a source group-title should not contribute channels at all."""
    return any(pattern.search(group or "") for pattern in rules.get("group_patterns", []))


# --------------------------------------------------------------- language

def load_language_index(root: str | Path = ".") -> dict[str, dict[str, Any]]:
    """Load the optional per-channel language index.

    The index is a build input produced by scripts/languages/build_index.py.
    When it is absent every channel is simply unclassified and the language
    filter keeps everything, so a missing index never deletes channels.
    """
    path = Path(root) / "data/languages.json"
    if not path.exists():
        return {}
    channels = load_json(path).get("channels")
    return channels if isinstance(channels, dict) else {}


def load_language_filter(root: str | Path = ".") -> dict[str, Any]:
    config = load_json(Path(root) / "data/categories.json").get("language_filter", {})
    return {
        "keep": [str(value).lower() for value in config.get("keep", ["eng"])],
        "categories": {str(value) for value in config.get("categories", [])},
        "except_categories": {str(value) for value in config.get("except_categories", [])},
        "except_channels": {str(value) for value in config.get("except_channels", [])},
        "unknown": str(config.get("unknown", "keep")).lower(),
    }


NOT_24_7_RE = re.compile(r"\[\s*not\s*24\s*/\s*7\s*\]", re.I)
RESOLUTION_RE = re.compile(r"\(\s*([1-9][0-9]{2,3})\s*[pi]\s*\)", re.I)
HD_WORD_RE = re.compile(r"\b(?:FHD|UHD|4K|HEVC|HD)\b", re.I)


def load_quality_filter(root: str | Path = ".") -> dict[str, Any]:
    config = load_json(Path(root) / "data/categories.json").get("quality_filter", {})
    return {
        "categories": {str(value) for value in config.get("categories", [])},
        "max_height": config.get("max_height"),
        "require_247": bool(config.get("require_247", False)),
    }


def name_quality(name: str) -> tuple[int | None, bool]:
    """Return (advertised height, is_not_24_7) parsed from a channel name."""
    text = name or ""
    height: int | None = None
    for match in RESOLUTION_RE.finditer(text):
        value = int(match.group(1))
        height = value if height is None else max(height, value)
    if height is None and HD_WORD_RE.search(text):
        height = 1080 if re.search(r"\b(?:FHD|UHD|4K|HEVC)\b", text, re.I) else 720
    return height, bool(NOT_24_7_RE.search(text))


def language_exclusion_reason(
    channel_id: str,
    name: str,
    categories: list[str],
    languages: list[str],
    english: bool | None,
    rules: dict[str, Any],
) -> str:
    """Return why a channel fails the language filter, or '' to keep it.

    Unknown language is kept by default: removing a channel because we failed
    to find data about it is far more damaging than leaving a non-English
    channel in the list.
    """
    if channel_id in rules["except_channels"]:
        return ""
    category_set = set(categories)
    if not (category_set & rules["categories"]):
        return ""
    if category_set & rules["except_categories"]:
        return ""
    if english is None:
        return "" if rules["unknown"] == "keep" else "language:unknown"
    if english:
        return ""
    listed = {str(value).lower() for value in languages}
    if listed & set(rules["keep"]):
        return ""
    return f"language:{','.join(sorted(listed)) or 'non-english'}"


def quality_exclusion_reason(
    name: str,
    categories: list[str],
    rules: dict[str, Any],
) -> str:
    """Enforce per-category quality rules such as music being SD and 24/7.

    Only a positively advertised high resolution disqualifies a channel: an
    unknown resolution is kept rather than assumed to be HD.
    """
    if not (set(categories) & set(rules.get("categories", []))):
        return ""
    height, not_247 = name_quality(name)
    max_height = rules.get("max_height")
    if max_height is not None and height is not None and height > int(max_height):
        return f"quality:{height}p"
    if rules.get("require_247") and not_247:
        return "not-24/7"
    return ""


def match_name_category(name: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    for pattern, slug in patterns:
        if pattern.search(name or ""):
            return slug
    return ""


def is_excluded(channel_id: str, name: str, categories: list[str], rules: dict[str, Any]) -> str:
    """Return the rule that excludes a channel, or an empty string to keep it."""
    if channel_id in rules["channels"]:
        return f"channel:{channel_id}"
    if rules["categories"].intersection(categories):
        return "category"
    for pattern in rules["name_patterns"]:
        if pattern.search(name or ""):
            return f"name:{pattern.pattern}"
    return ""


def canonical_category(entry: M3UEntry, aliases: dict[str, str], default: str) -> str:
    group_key = normalize_name(entry.group)
    if group_key in aliases:
        return aliases[group_key]
    if entry.name.lstrip().startswith("["):
        return "entertainment"
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
        """Order candidates: health first, then source trust, then latency.

        Source priority deliberately outranks response time. A verified proxy
        hop such as GradeTV is typically a few hundred milliseconds slower
        than a direct link, but it re-checks the feed and self-heals, so the
        extra latency is worth the higher-trust source.
        """
        response = self.response_time(identifier)
        return (
            self.status_rank(identifier),
            source_priority,
            min(self.failures(identifier), self.disable_after_failures),
            response if response is not None else 10**9,
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
