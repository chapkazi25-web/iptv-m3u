"""Small, dependency-free M3U parser and writer.

The parser intentionally keeps source-specific directives such as EXTVLCOPT
attached to their entry. The build pipeline can therefore select a stream and
retain the headers required by that stream without understanding every source
format in advance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

EXTINF_PREFIX = "#EXTINF:"
ATTR_RE = re.compile(r'([\w-]+)="((?:\\.|[^"])*)"')
QUALITY_SUFFIX_RE = re.compile(r"\s+\((?:FHD|UHD|HD|SD|4K)\)\s*$", re.I)
COUNTRY_GROUP_RE = re.compile(r"\b([A-Z]{2})\s+TV\b", re.I)


class M3UError(ValueError):
    """Raised when a playlist cannot be parsed safely."""


def _unescape_attr(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _escape_attr(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def _split_extinf(line: str) -> tuple[str, dict[str, str], str]:
    """Split an EXTINF line at the first comma outside quoted attributes."""
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            metadata = line[len(EXTINF_PREFIX) : index]
            title = line[index + 1 :].strip()
            break
    else:
        raise M3UError(f"EXTINF line has no title delimiter: {line[:120]!r}")

    pieces = metadata.split(None, 1)
    duration = pieces[0] if pieces else "-1"
    attribute_text = pieces[1] if len(pieces) == 2 else ""
    attrs = {
        key: _unescape_attr(value)
        for key, value in ATTR_RE.findall(attribute_text)
    }
    return duration, attrs, title


@dataclass
class M3UEntry:
    """A single playable item and its associated directives."""

    duration: str
    attrs: dict[str, str]
    title: str
    url: str = ""
    extra_lines: list[str] = field(default_factory=list)
    line_number: int = 0

    @property
    def name(self) -> str:
        return (self.attrs.get("tvg-name") or self.title).strip()

    @property
    def group(self) -> str:
        return self.attrs.get("group-title", "Unknown").strip() or "Unknown"

    @property
    def explicit_country(self) -> str:
        value = self.attrs.get("tvg-country", "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", value):
            return "GB" if value == "UK" else value
        match = COUNTRY_GROUP_RE.search(self.group)
        if match:
            value = match.group(1).upper()
            return "GB" if value == "UK" else value
        return ""

    def clean_name(self) -> str:
        return QUALITY_SUFFIX_RE.sub("", self.name).strip()

    def candidate_names(self) -> set[str]:
        values = {
            self.name,
            self.title,
            self.clean_name(),
            self.attrs.get("tvg-id", ""),
        }
        return {value.strip() for value in values if value and value.strip()}

    def inferred_country(self, default_country: str | None = None) -> str:
        if self.explicit_country:
            return self.explicit_country
        # Never infer geography from a source-specific tvg-id. Catalog discovery
        # may safely infer a country later when an exact channel name has one
        # unambiguous country across all sources.
        return (default_country or "").strip().upper()


def parse_m3u_text(text: str, *, source: str = "<memory>") -> tuple[str, list[M3UEntry]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header = "#EXTM3U"
    # Comments that appear between the #EXTM3U line and the first entry belong
    # to the playlist, not to any channel, and are kept as part of the header
    # so a parse and re-render round trip does not silently drop them. That is
    # what lets the published playlist carry its attribution.
    preamble: list[str] = []
    entries: list[M3UEntry] = []
    current: M3UEntry | None = None

    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        if line_number == 1 and line.upper().startswith("#EXTM3U"):
            header = line
            continue
        if line.startswith(EXTINF_PREFIX):
            if current is not None:
                entries.append(current)
            duration, attrs, title = _split_extinf(line)
            current = M3UEntry(
                duration=duration,
                attrs=attrs,
                title=title,
                line_number=line_number,
            )
            continue
        if line.startswith("#"):
            if current is None:
                preamble.append(raw_line.rstrip())
            else:
                current.extra_lines.append(raw_line.rstrip())
            continue
        if current is None:
            continue
        current.url = line
        entries.append(current)
        current = None

    if current is not None:
        entries.append(current)
    return "\n".join([header, *preamble]), entries


def parse_m3u(path: str | Path) -> tuple[str, list[M3UEntry]]:
    playlist_path = Path(path)
    return parse_m3u_text(
        playlist_path.read_text(encoding="utf-8", errors="replace"),
        source=str(playlist_path),
    )


def render_extinf(entry: M3UEntry) -> str:
    parts = [f'{key}="{_escape_attr(value)}"' for key, value in entry.attrs.items() if value != ""]
    metadata = " ".join(parts)
    prefix = f"{EXTINF_PREFIX}{entry.duration}"
    if metadata:
        prefix += f" {metadata}"
    return f"{prefix},{entry.title}"


def render_m3u(header: str, entries: Iterable[M3UEntry]) -> str:
    # The header may be several lines when it carries a comment block, which
    # render_m3u passes through as written.
    lines = (header.rstrip() or "#EXTM3U").split("\n")
    for entry in entries:
        lines.append(render_extinf(entry))
        lines.extend(entry.extra_lines)
        lines.append(entry.url.rstrip())
    return "\n".join(lines) + "\n"


def write_m3u(path: str | Path, header: str, entries: Iterable[M3UEntry]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(render_m3u(header, entries), encoding="utf-8")
    temporary.replace(destination)
    return destination

