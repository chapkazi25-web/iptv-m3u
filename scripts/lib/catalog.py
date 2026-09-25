"""Canonical channel catalog and conservative alias resolution."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .m3u import M3UEntry

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Publishers advertise the feed quality in the channel name, either as a
# parenthesised resolution ("Trace Africa (1080p)") or as a bare trailing word
# ("Trace Urban HD"). Both forms have to be ignored, otherwise one channel is
# catalogued once per advertised quality.
QUALITY_SUFFIX_RE = re.compile(
    r"\s+\((?:FHD|UHD|HD|SD|4K|[1-9][0-9]{2,3}[pi])\)\s*$",
    re.I,
)
QUALITY_WORD_RE = re.compile(r"\s+(?:FHD|UHD|HD|SD|4K|HEVC)\s*$", re.I)


def strip_quality(value: str) -> str:
    """Return the channel name without any advertised quality marker."""
    text = QUALITY_SUFFIX_RE.sub("", value or "").strip()
    # Repeat: a name can carry both forms, e.g. "Trace Urban HD (1080p)".
    previous = None
    while previous != text:
        previous = text
        text = QUALITY_WORD_RE.sub("", text).strip()
    return text or (value or "").strip()


def normalize_name(value: str) -> str:
    """Return a conservative, punctuation-insensitive comparison key."""
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("&", " and ")
    text = strip_quality(text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("&", " and ")
    text = strip_quality(text)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "unknown-channel"


def country_codes(record: dict[str, Any]) -> set[str]:
    values: list[str] = []
    country = record.get("country")
    if isinstance(country, str):
        values.append(country)
    countries = record.get("countries", [])
    if isinstance(countries, list):
        values.extend(str(value) for value in countries)
    normalized = {value.strip().upper() for value in values if re.fullmatch(r"[A-Za-z]{2}", value.strip())}
    if "UK" in normalized:
        normalized.remove("UK")
        normalized.add("GB")
    return normalized


def record_names(record: dict[str, Any], channel_id: str) -> set[str]:
    values = [channel_id, channel_id.replace("-", " "), record.get("name", "")]
    aliases = record.get("aliases", [])
    if isinstance(aliases, list):
        values.extend(str(value) for value in aliases)
    return {normalize_name(value) for value in values if value and normalize_name(value)}


@dataclass(frozen=True)
class Resolution:
    channel_id: str
    country: str
    matched_by: str


class ChannelCatalog:
    def __init__(
        self,
        records: dict[str, dict[str, Any]],
        *,
        source_aliases: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.records = records
        # Alias keys are stored in whatever form the source published them
        # ("CNN HD", "cnn hd"). Normalizing on load keeps them matching even
        # though quality markers are now ignored in channel names.
        self.source_aliases = {
            str(source): {
                normalize_name(str(alias)): str(target)
                for alias, target in values.items()
            }
            for source, values in (source_aliases or {}).items()
        }
        self._names: dict[str, set[str]] = defaultdict(set)
        for channel_id, record in records.items():
            for name in record_names(record, channel_id):
                self._names[name].add(channel_id)

    @classmethod
    def load(cls, root: str | Path = ".") -> "ChannelCatalog":
        project_root = Path(root)
        catalog_data = json.loads((project_root / "data/channels.json").read_text(encoding="utf-8"))
        records = catalog_data.get("channels", catalog_data)
        if not isinstance(records, dict):
            raise ValueError("data/channels.json must contain a 'channels' object")
        aliases_path = project_root / "data/aliases.json"
        alias_data = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.exists() else {}
        source_aliases = {
            str(source): dict(values)
            for source, values in (alias_data.get("sources", {}) if isinstance(alias_data, dict) else {}).items()
            if isinstance(values, dict)
        }
        ref_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
        for channel_id, record in records.items():
            refs_by_source = record.get("source_refs", {})
            if not isinstance(refs_by_source, dict):
                continue
            for source, refs in refs_by_source.items():
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    ref_targets[(str(source), str(ref))].add(str(channel_id))
        for (source, ref), targets in ref_targets.items():
            if len(targets) == 1:
                source_aliases.setdefault(source, {}).setdefault(ref, next(iter(targets)))
        return cls(records, source_aliases=source_aliases)

    def get(self, channel_id: str) -> dict[str, Any] | None:
        return self.records.get(channel_id)

    def resolve(self, source: str, entry: M3UEntry, default_country: str | None = None) -> Resolution:
        country = entry.inferred_country(default_country)
        explicit = {
            source: self.source_aliases.get(source, {}),
        }
        for alias in entry.candidate_names():
            normalized = normalize_name(alias)
            mapped = explicit[source].get(alias) or explicit[source].get(normalized)
            if mapped and mapped in self.records:
                return Resolution(mapped, country, "source-alias")

        # Display metadata is more reliable than source-specific tvg-id values;
        # several legacy sources reuse generic IDs such as Live.Event.us.
        ordered_aliases = [entry.name, entry.title, entry.clean_name(), entry.attrs.get("tvg-id", "")]
        candidates: set[str] = set()
        for alias in ordered_aliases:
            if not alias:
                continue
            matches = self._names.get(normalize_name(alias), set())
            if matches:
                candidates = set(matches)
                break
        if not candidates:
            fallback = self.fallback_id(entry, country)
            return Resolution(fallback, country, "fallback")

        if len(candidates) > 1:
            if country:
                exact_matches = {
                    channel_id
                    for channel_id in candidates
                    if country in country_codes(self.records[channel_id])
                }
                if exact_matches:
                    candidates = exact_matches
                else:
                    agnostic = {
                        channel_id
                        for channel_id in candidates
                        if not country_codes(self.records[channel_id])
                    }
                    if agnostic:
                        candidates = agnostic
            else:
                agnostic = {
                    channel_id
                    for channel_id in candidates
                    if not country_codes(self.records[channel_id])
                }
                if len(agnostic) == 1:
                    candidates = agnostic

        if len(candidates) == 1:
            channel_id = next(iter(candidates))
            return Resolution(channel_id, country, "canonical-name")

        # Never merge ambiguous names. A source-qualified fallback prevents an
        # unsafe false positive while keeping the output deterministic.
        fallback = self.fallback_id(entry, country, qualified=True)
        return Resolution(fallback, country, "ambiguous-fallback")

    def fallback_id(self, entry: M3UEntry, country: str, *, qualified: bool = False) -> str:
        base = slugify(entry.clean_name())
        if country and base in self.records and country not in country_codes(self.records[base]):
            return f"{base}-{country.lower()}"
        if qualified:
            return f"unmapped-{base}"
        return base


def validate_catalog_records(records: Iterable[tuple[str, dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    seen_names: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
    for channel_id, record in records:
        if not ID_RE.fullmatch(channel_id):
            errors.append(f"invalid channel id {channel_id!r}")
        if not str(record.get("name", "")).strip():
            errors.append(f"channel {channel_id!r} has no display name")
        countries = country_codes(record)
        for name in record_names(record, channel_id):
            seen_names[name].append((channel_id, countries))

    for name, matches in seen_names.items():
        for index, (channel_id, countries) in enumerate(matches):
            for other_id, other_countries in matches[index + 1 :]:
                if channel_id == other_id:
                    continue
                # A country-less record is an intentional generic fallback:
                # country-specific records take precedence when geography is
                # known, while the generic record handles unknown feeds.
                if not countries or not other_countries:
                    ambiguous = not countries and not other_countries
                else:
                    ambiguous = bool(countries & other_countries)
                if ambiguous:
                    errors.append(
                        f"alias {name!r} is ambiguous between {channel_id!r} and {other_id!r}"
                    )
    return errors
