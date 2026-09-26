# IPTV-M3U

A collection of automatically generated IPTV playlists from publicly available streaming sources. The repository consolidates playlist generation, stream extraction, channel identity, remote artwork links, health checks, and validation into one pipeline.

## Playlists

### All channels

```text
https://raw.githubusercontent.com/chapkazi25-web/iptv-m3u/main/playlists/all.m3u
```

### Internal source snapshots

`playlists/sources/` contains source snapshots used by the build and health checker. These files are internal implementation details; `playlists/all.m3u` is the only public playlist.

## Usage

The main playlist is compatible with VLC, TiviMate, Kodi, IPTV Smarters, and other M3U-capable players. Add the raw URL above to your player. Availability and stream quality can change without notice.

Artwork is not stored in this repository. `tvg-logo` values are generated from public raw files in [K-yzu/Logos](https://github.com/K-yzu/Logos), with the repository's `data/logo-index.json` containing only remote path metadata. Unmatched channels are emitted without a logo rather than pointing at a second artwork host.

## EPG channel IDs

`data/epg-map.json` is generated from the public [EPGShare01 XMLTV feed](https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz). When a channel can be matched conservatively, its XMLTV `channel id` is emitted as `tvg-id` in `playlists/all.m3u`. The stable internal canonical ID remains in `data/channels.json`; channels not present in the EPGShare feed keep that internal ID as a fallback. The XML file itself is streamed during import and is not committed.

## How it works

```text
sources/                 Source-specific generators
    ↓
playlists/sources/       Source snapshots and expiring stream URLs
    ↓
data/channels.json       Canonical channel IDs, aliases, source references, countries, categories
     ↓
data/logo-index.json     Metadata-only index of K-yzu raw artwork paths
    ↓
data/stream-health.json  Reachability and failure history
    ↓
scripts/build/           Normalize, group, rank, and select one stream per channel
    ↓
playlists/all.m3u        One entry per canonical channel
```

A source is never treated as the channel identity. For example, CDN, core, and Pluto entries that resolve to the canonical ID `cnn` remain separate stream candidates, while `all.m3u` contains only one CNN entry. The build selects the best usable candidate without changing the public playlist URL.

## Local development

Python 3.9 or newer is supported. Only the TVivu generator requires a third-party package.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Refresh generated data and playlists without contacting source APIs:

```bash
make catalog
make logos
make build
make apply-logos
make validate
make test
```

`make epg` downloads the EPGShare XML on demand; it is intentionally not part of the lightweight `make refresh` target.

Useful individual commands:

```bash
python3 scripts/discover/build_catalog.py
python3 scripts/epg/import_epgshare.py
python3 scripts/logos/remote_index.py
python3 scripts/logos/apply_logos.py
python3 scripts/build/merge_playlists.py
python3 scripts/validate/validate_m3u.py
python3 scripts/validate/check_streams.py --limit 50
python3 -m unittest discover -v
```

Regenerate a source only when needed:

```bash
python3 sources/core/generate.py --check-streams
python3 sources/cdn/generate.py
python3 sources/pluto-tv/generate.py
python3 sources/gradetv/generate.py
python3 sources/iptv-org/generate.py
python3 sources/tvivu/generate.py --no-logo-resolve
```

Compare source quality by probing a sample of each playlist:

```bash
make sample            # 20 channels per source
```

## Sources

| Source | Upstream | Priority | Notes |
| --- | --- | --- | --- |
| `cdn` | [CDN Live TV](https://cdnlivetv.is) | 1 | Channel list and per-channel player tokens |
| `core` | [doms9/iptv](https://github.com/doms9/iptv) | 2 | Group titles reorganised into categories |
| `pluto-tv` | [Pluto TV](https://pluto.tv) | 3 | Fresh session token per run |
| `gradetv` | [Grade TV](https://gradetv.net) | 4 | Public API; resolves per-feed health |
| `iptv-org` | [iptv-org/iptv](https://github.com/iptv-org/iptv) | 5 | Community index, links are unverified |
| `tvivu` | [TVivu](https://tvivu.com) | 6 | Last-resort fallback, slowest to refresh |

Priority is applied in this order: stream health, then source priority, then
failure count, then response time. Source trust deliberately outranks latency,
so a verified Grade TV hop wins over a marginally faster direct iptv-org link.

Grade TV is queried through its documented API. Each channel exposes one
stream per feed (HD, SD, ...) and each feed carries its own health, so the
generator resolves the detail endpoint and publishes the healthiest feed
rather than the first one listed. The curated channel list lives in
`config/gradetv.json`; run with `--all` to sweep the full playable catalog.

## Credits

None of the streams in this playlist are hosted here. Every one is a public URL
published by somebody else, and this repository only decides which of them to
link. That work is the entire reason the list is worth anything, so the
upstream projects deserve the credit rather than a passing mention in a table:

| Project | What it contributes |
| --- | --- |
| [iptv-org](https://github.com/iptv-org/iptv) | The community channel index this list is largely built on. Thousands of channels, maintained by volunteers, with the country and language metadata the filters read. |
| [doms9/iptv](https://github.com/doms9/iptv) | A continuously refreshed playlist and its EPG, plus the health-report layout the stream checker here follows. |
| [CDN Live TV](https://cdnlivetv.is) | A documented channel API and per-channel player tokens for the US, CA, GB, AU and NZ feeds. |
| [Grade TV](https://gradetv.net) | A per-channel API exposing a stable hop in front of each feed, the per-feed health this project selects on, and hosted artwork cards. |
| [Pluto TV](https://pluto.tv) | The free ad-supported linear channels and the session API that lists them. |
| [TVivu](https://tvivu.com) | The music-channel catalogue used as the last-resort source. |
| [K-yzu/Logos](https://github.com/K-yzu/Logos) | The bulk of the channel artwork in `tvg-logo`. |
| [EPGShare01](https://epgshare01.online) | The public XMLTV feed that every `tvg-id` is matched against. |
| [Wikipedia](https://en.wikipedia.org) | Artwork for the channels K-yzu and Grade TV do not cover, queried under its API etiquette. |
| [TheSportsDB](https://www.thesportsdb.com) | The free fixture schedule that expires live-event channels on time. |

Two things follow from depending on other people's work. Their terms apply to
what they publish, and this project inherits none of it — the disclaimer below
is not a formality. And when one of them changes an endpoint, a rate limit or
a licence, the breakage shows up here first, so issues traced to a single
upstream are worth reporting to them as well as opening one here.

## Categories

`data/categories.json` is the single source of truth. Live events are split
into `soccer`, `nfl`, `nba` and `ufc`, sports broadcasters get their own
groups: `sky-sports`, `tsn`, `tnt-sports`, `stan-sports` and `bein-sports`,
and the Tanzanian national channels are collected under `tanzania`.

Because broadcasters publish their channels under a generic `Sports` group,
categories are matched on the channel name first (`name_patterns`) and only
then on the source `group-title`. Order in the config decides precedence, so
brand rules win over the broad sport patterns.

The same file carries an `exclude` block. Channels matching a name pattern, a
category, or an explicit ID are kept in the catalog as disabled records — they
keep their identity and source references across rebuilds but never reach the
public playlist. `group_patterns` drops a whole source group before catalog
resolution, which is how the unwanted live-event groups (baseball, hockey,
racing, tennis, other events) are removed. The group pattern for `Local News`
is what removes Pluto's city-level news feeds.

## Language filtering

`data/categories.json` also carries a `language_filter` and a `quality_filter`.
The language filter keeps English channels in documentary, entertainment, kids,
movies, music, news and sports, with beIN Sports excepted and the Tanzanian
channels named in `except_channels` protected explicitly. The quality filter
limits music to standard definition, 24/7 channels.

The catalog carries no language data of its own, so `data/languages.json` is
built by `scripts/languages/build_index.py` from the two public sources that
have any:

- **Grade TV** publishes ISO 639-3 codes per channel for ~95% of its catalog.
- **iptv-org** has no language field, but many entries carry `alt_names` in the
  channel's original script. Its primary names are transliterated to ASCII, so
  that alt-name script is the only language signal available.

A channel is only ever labelled `english: false` when a source positively
identifies another language. When nothing is known the channel is marked
`english: null` and **kept** — roughly 62% of the catalog currently falls into
that bucket, so the filter is deliberately partial and improves on its own as
Grade TV adds language data. Deleting a channel because no data was found for
it would be far more damaging than the occasional non-English channel slipping
through. Run `make languages` to refresh the index; the scheduled
`update-languages` workflow does it weekly.

## Geography

The catalog carries almost no `tvg-country` data, so `scripts/lib/geo.py`
recovers a channel's geography from the places sources do record it:

- the `tvg-country` attribute, when a source publishes one;
- the source id, because iptv-org encodes it as `Name.CC.variant`
  (`PlutoTVParanormal.de.ES`, where the segment before the last one is the
  channel's country and the last one is the audience) while Grade TV and TVivu
  use the shorter `Name.CC` (`DodomaTV.tz`);
- the display name, when it carries a country in brackets
  (`Pluto TV Paranormal (Germany)`).

A segment only counts as a country when it is a real ISO 3166-1 code, which is
what keeps `HD`, `SD` and a channel's own name (`DodomaTV` is not `DO`) out of
the result. That resolves a country for all but two records in the catalog, and
`playlists/all.m3u` publishes it as `tvg-country` as well.

`country_filter` then keeps only Canada, the United States, the United Kingdom,
New Zealand and Australia. Two groups are excepted: brands that publish a
worldwide feed under one name — beIN Sports, Trace, Xite and Vevo — and the
`Tanzania` category. Unlike the language filter, an unknown country is
**dropped**: a feed with no geography at all is nearly always a regional stream
that is dead outside its own market, which is the opposite of the case for an
unknown *language*. A channel that resolves to at least one allowed country
survives even when it also resolves to others, so a US and French feed of the
same channel stays.

`region_filter` collapses the same feed published once per region. A record is
only ever folded into a record that already exists under the unqualified name,
which is what collapses the fifteen `Angel TV` feeds into one entry and the
`Pluto TV Paranormal` region feeds into a single one, while leaving `Trace
Africa` and `Trace Naija` alone — they share a brand, not a channel. Folding
happens after every other filter, so the entry kept for a group is always one
the playlist will actually carry.

`local_filter` removes the affiliate feeds that are useless to a list read from
more than one place: US call signs (`ABC KATC`, `CBS KCCI`), the Australian
state suffix a broadcaster appends to its regional feed (`ABC TV NT`), municipal
and community-access channels (`City of Fort Pierce`, `Kern County TV`,
`CAN TV19`), and a source id ending in `locals`. A call sign is only read as one
in the country that issues call signs, so `Terra Mater WILD` and `TV-WEST` stay
ordinary channels and are left to the country filter.

`radio_filter` keeps audio-only stations out of the `music` category, which is
for music video. Its `FM` and `AM` patterns are case sensitive so that the
English word in a title such as *I Am Famous* is not read as an AM station.

Geo-blocked and not-24/7 feeds are ordinary `exclude` name patterns, so they
are removed in every category rather than only in music.

## Artwork

`data/logo-index.json` is the metadata-only index of the K-yzu/Logos repository
and is always preferred. Because that repository only covers a few thousand
US and GB channels, `data/logo-fallbacks.json` fills the gap from two more
sources, consulted in order: Grade TV's hosted cards, then Wikipedia. Only URLs
are stored; no artwork is downloaded or committed.

`scripts/logos/enrich_index.py` builds the fallbacks. Wikipedia is queried
politely as its policy requires: a descriptive User-Agent, titles batched 50
per request, sequential requests with a delay, and results cached to
`data/logo-fallbacks.json`. A channel is only matched when the file looks like
a logo — page furniture such as `Commons-logo.svg` is rejected, and a vector
lead image is accepted even when the file name omits the word "logo"
("60-minutes.svg" on the *60 Minutes* article) while a photograph is not.

Channel names are published without any resolution or bitrate marker.
Advertised quality is kept as data in `quality_height`, which the music filter
reads, so the filter still works after the name is cleaned.

## Live events

Live-event channels are named after the match they carry, for example
`[Liga MX] América vs Monterrey`. No EPG in the project covers them: EPGShare
maps none of them and Grade TV does not carry them. Instead,
`scripts/events/fixture_schedule.py` resolves the fixture from TheSportsDB, a
free open schedule API, by parsing the sport from the `[...]` prefix and the
two teams from the `X vs Y` part. The kickoff time is stored on the record as
`event_start`, and the `event_filter` block in `data/categories.json` drops the
channel once the window for its sport has passed — so an event is removed on the
next scheduled run rather than when its stream happens to time out.

A channel whose fixture cannot be resolved is never removed, because a missing
fixture is not evidence that the event finished. The schedule API is sparse on
its free tier and rate limits aggressively, so fetched days are cached and
matching is deliberately conservative: both team names have to match.

## Stream selection and health

`scripts/validate/check_streams.py` records status, latency, and consecutive failures for every source stream. The selector considers reachability, source priority, failure history, and response time.

A single failed check does not remove a stream. Repeated failures mark it degraded or unstable; after six consecutive failures it is disabled unless the source is explicitly ephemeral. A successful check clears the failure count and restores the stream. When a preferred source fails, a healthy alternate can automatically take its place.

## Automation

| Workflow | Purpose |
| --- | --- |
| `update-core.yml` | Refresh the core source playlist |
| `update-cdn.yml` | Refresh CDN Live TV tokens and channels |
| `update-pluto.yml` | Refresh Pluto TV session tokens |
| `update-gradetv.yml` | Refresh the Grade TV playlist from its public API |
| `update-iptv-org.yml` | Refresh the iptv-org community index |
| `update-tvivu.yml` | Extract TVivu HD/FHD music streams |
| `update-logos.yml` | Refresh the metadata-only K-yzu raw artwork index |
| `update-logo-fallbacks.yml` | Refresh the Grade TV and Wikipedia artwork fallbacks |
| `update-languages.yml` | Refresh the per-channel language index and reapply filters |
| `update-events.yml` | Resolve live-event kickoff times and drop finished events |
| `update-epg.yml` | Refresh EPGShare XMLTV channel ID mappings |
| `discover-channels.yml` | Rebuild the canonical channel catalog |
| `test-streams.yml` | Update stream health with failure hysteresis |
| `build-playlists.yml` | Build the single canonical `playlists/all.m3u` |
| `validate.yml` | Run tests and playlist integrity checks |

Source refreshes each own their playlist file, so they run in parallel. The
four workflows that rewrite the same generated artifacts share a
`generated-artifacts` concurrency group and serialize. If a push still races,
`scripts/ci/commit_changes.py` regenerates from the updated branch instead of
merging two independently generated playlists.

Run the validation and test commands above before submitting changes.

## Disclaimer

This project does not host, store, or transmit video content. It only references publicly accessible streaming URLs and remote artwork URLs. It claims no ownership of any channel, stream, or logo, and it hosts none of them. The channels themselves belong to their respective broadcasters, and the artwork belongs to the projects credited above.

Availability, licensing, and legality vary by source and jurisdiction, and each upstream publishes its own terms; using this playlist means following theirs. Users are responsible for complying with applicable law and the rights of content owners. If you own content and want a reference removed, open an issue.
