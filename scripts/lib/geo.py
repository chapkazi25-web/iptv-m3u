"""Country codes, country names, and the region qualifiers sources append.

Most sources publish a channel's geography somewhere other than the
``tvg-country`` attribute. iptv-org encodes it inside ``tvg-id`` as
``Name.CC.<variant>`` (``PlutoTVParanormal.de.ES``) and writes the country
*name* into the display name (``Pluto TV Paranormal (Germany)``). Grade TV and
TVivu use the shorter ``Name.CC`` form (``DodomaTV.tz``). The same feeds are
often published once per region, so the names also carry a trailing qualifier
that has to be recognised before one channel can be preferred over its
duplicates.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# ISO 3166-1 alpha-2. A segment of a source id only counts as a country when it
# appears here, which is what keeps quality and language segments such as "HD",
# "SD" and "no" out of the geography data.
COUNTRY_CODES: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI
    BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN
    CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK
    FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM
    HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
    KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK
    ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP
    NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
    TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
    VN VU WF WS YE YT ZA ZM ZW
    """.split()
)

# Display name -> alpha-2. Only the spellings that actually appear in channel
# names are listed, plus the common alternates a source may use instead.
COUNTRY_NAMES: dict[str, str] = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "argentina": "AR",
    "australia": "AU", "austria": "AT", "azerbaijan": "AZ", "bangladesh": "BD",
    "belarus": "BY", "belgium": "BE", "bolivia": "BO", "bosnia": "BA",
    "brazil": "BR", "bulgaria": "BG", "burkina faso": "BF", "cambodia": "KH",
    "cameroon": "CM", "canada": "CA", "chile": "CL", "china": "CN",
    "colombia": "CO", "congo": "CG", "costa rica": "CR", "croatia": "HR",
    "cuba": "CU", "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "ecuador": "EC", "egypt": "EG", "el salvador": "SV",
    "estonia": "EE", "ethiopia": "ET", "finland": "FI", "france": "FR",
    "georgia": "GE", "germany": "DE", "ghana": "GH", "greece": "GR",
    "guatemala": "GT", "honduras": "HN", "hong kong": "HK", "hungary": "HU",
    "iceland": "IS", "india": "IN", "indonesia": "ID", "iran": "IR",
    "iraq": "IQ", "ireland": "IE", "israel": "IL", "italy": "IT",
    "ivory coast": "CI", "jamaica": "JM", "japan": "JP", "jordan": "JO",
    "kazakhstan": "KZ", "kenya": "KE", "kuwait": "KW", "latvia": "LV",
    "lebanon": "LB", "libya": "LY", "lithuania": "LT", "luxembourg": "LU",
    "malaysia": "MY", "maldives": "MV", "mali": "ML", "malta": "MT",
    "mexico": "MX", "moldova": "MD", "mongolia": "MN", "montenegro": "ME",
    "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "burma": "MM",
    "nepal": "NP", "netherlands": "NL", "holland": "NL", "new zealand": "NZ",
    "nigeria": "NG", "north macedonia": "MK", "macedonia": "MK", "norway": "NO",
    "oman": "OM", "pakistan": "PK", "panama": "PA", "paraguay": "PY",
    "peru": "PE", "philippines": "PH", "poland": "PL", "portugal": "PT",
    "qatar": "QA", "romania": "RO", "russia": "RU", "russian federation": "RU",
    "rwanda": "RW", "saudi arabia": "SA", "senegal": "SN", "serbia": "RS",
    "sierra leone": "SL", "singapore": "SG", "slovakia": "SK",
    "slovenia": "SI", "somalia": "SO", "south africa": "ZA",
    "south korea": "KR", "korea": "KR", "spain": "ES", "sri lanka": "LK",
    "sudan": "SD", "sweden": "SE", "switzerland": "CH", "taiwan": "TW",
    "tanzania": "TZ", "thailand": "TH", "tunisia": "TN", "turkey": "TR",
    "turkiye": "TR", "uganda": "UG", "ukraine": "UA",
    "united arab emirates": "AE", "united kingdom": "GB", "great britain": "GB",
    "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "united states": "US", "united states of america": "US", "usa": "US",
    "uruguay": "UY", "uzbekistan": "UZ", "venezuela": "VE", "vietnam": "VN",
    "viet nam": "VN", "zambia": "ZM", "zimbabwe": "ZW",
}

# "UK" is the spelling sources use for the United Kingdom; the rest of the
# project normalises to the ISO code.
COUNTRY_CODES = COUNTRY_CODES | {"UK"}
COUNTRY_NAMES["uk"] = "GB"

_ALIAS_KEY_RE = re.compile(r"[^a-z0-9]+")
# A source id segment is a country when it is a two letter code, optionally
# followed by a variant marker: "us", "tz", "us2", "uk_locals1". A separator is
# required before the marker so a name like "DodomaTV" is never read as "DO".
_ID_SEGMENT_RE = re.compile(r"^([a-z]{2})(?:[._][a-z]*\d*)?\d*$")
_PARENTHESISED_RE = re.compile(r"\s*\(([^)]*)\)\s*$")


def alias_key(value: str) -> str:
    """Return a comparison key for a country name or a region qualifier."""
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return _ALIAS_KEY_RE.sub(" ", text).strip()


COUNTRY_BY_NAME: dict[str, str] = {alias_key(name): code for name, code in COUNTRY_NAMES.items()}


def is_country_code(value: str) -> bool:
    """True when a token is an ISO 3166-1 alpha-2 code."""
    return len(value) == 2 and value.isalpha() and value.upper() in COUNTRY_CODES


def is_region_code(value: str) -> bool:
    """True when a trailing token reads as a country qualifier.

    Tuvalu is a real country, but "TV" ends almost every channel name and
    never marks a region, so it is not treated as one.
    """
    return value.upper() != "TV" and is_country_code(value)


def country_code(value: str) -> str:
    """Return the alpha-2 code for a country name, or an empty string."""
    return COUNTRY_BY_NAME.get(alias_key(value), "")


def _code_from_segment(segment: str) -> str:
    match = _ID_SEGMENT_RE.match(segment.strip().lower())
    if not match:
        return ""
    code = match.group(1).upper()
    return "GB" if code == "UK" else code


def country_from_reference(reference: str) -> str:
    """Read the country out of a source id such as ``PlutoTVParanormal.de.ES``.

    The segment before the last one wins. ``ParanormalState.us.UK`` is a US
    channel published for a UK audience and ``1001Noites.br.SD`` is Brazilian,
    so reading the trailing pair would mislabel both. Only when that segment is
    not a country does the last one count, which is how the shorter
    ``DodomaTV.tz`` form is read.
    """
    segments = [segment for segment in str(reference or "").split(".") if segment.strip()]
    if len(segments) < 2:
        return ""
    for index in (-2, -1):
        code = _code_from_segment(segments[index])
        if code and is_country_code(code):
            return code
    return ""


def country_from_name(name: str) -> str:
    """Read the country out of a display name such as ``Arte (France)``."""
    match = _PARENTHESISED_RE.search(name or "")
    if match:
        return country_code(match.group(1))
    return ""


def record_countries(record: dict[str, Any]) -> set[str]:
    """Resolve every country a channel is published for.

    An explicit ``tvg-country`` wins, because it is the only value a source
    states outright. Source ids and the display name are consulted afterwards,
    which is what recovers a country for the majority of the catalog.
    """
    resolved: set[str] = set()
    for value in record.get("countries") or []:
        text = str(value).strip().upper()
        if len(text) == 2 and text.isalpha():
            resolved.add("GB" if text == "UK" else text)
    if resolved:
        return resolved
    references = record.get("source_refs")
    if isinstance(references, dict):
        for values in references.values():
            for value in values if isinstance(values, list) else []:
                code = country_from_reference(value)
                if code:
                    resolved.add(code)
    if not resolved:
        code = country_from_name(str(record.get("name", "")))
        if code:
            resolved.add(code)
    return resolved


def region_base_name(name: str, words: Iterable[str]) -> str:
    """Strip trailing region qualifiers from a channel name.

    ``Pluto TV Paranormal (Germany) ES`` reduces to ``Pluto TV Paranormal`` and
    ``Angel TV Arabia`` to ``Angel TV``. The result is only ever used to look
    for a channel that already exists under the unqualified name, so an
    over-eager strip costs nothing.
    """
    text = (name or "").strip()
    qualifiers = {alias_key(word) for word in words if word}
    for _ in range(4):
        parenthetical = _PARENTHESISED_RE.search(text)
        if parenthetical and country_code(parenthetical.group(1)):
            candidate = text[: parenthetical.start()]
        else:
            head, separator, tail = text.rpartition(" ")
            if not separator:
                break
            if qualifiers and alias_key(tail) in qualifiers:
                candidate = head
            elif tail.isupper() and is_region_code(tail):
                # "48 Hours CA" and "Are You The One? DK": the code repeats the
                # country the unqualified record already carries.
                candidate = head
            else:
                break
        candidate = candidate.strip(" -–—")
        if not candidate or candidate == text:
            break
        text = candidate
    return text or (name or "").strip()
