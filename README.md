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
python3 scripts/test/check_streams.py --limit 50
python3 -m unittest discover -v
```

Regenerate a source only when needed:

```bash
python3 sources/core/generate.py --check-streams
python3 sources/cdn/generate.py
python3 sources/pluto-tv/generate.py
python3 sources/tvivu/generate.py --no-logo-resolve
```

## Stream selection and health

`scripts/test/check_streams.py` records status, latency, and consecutive failures for every source stream. The selector considers reachability, failure history, response time, source priority, quality, and stream identity.

A single failed check does not remove a stream. Repeated failures mark it degraded or unstable; after six consecutive failures it is disabled unless the source is explicitly ephemeral. A successful check clears the failure count and restores the stream. When a preferred source fails, a healthy alternate can automatically take its place.

## Automation

| Workflow | Purpose |
| --- | --- |
| `update-core.yml` | Refresh the core source playlist |
| `update-cdn.yml` | Refresh CDN Live TV tokens and channels |
| `update-pluto.yml` | Refresh Pluto TV session tokens |
| `update-tvivu.yml` | Extract TVivu HD/FHD music streams |
| `update-logos.yml` | Refresh the metadata-only K-yzu raw artwork index |
| `update-epg.yml` | Refresh EPGShare XMLTV channel ID mappings |
| `discover-channels.yml` | Rebuild the canonical channel catalog |
| `test-streams.yml` | Update stream health with failure hysteresis |
| `build-playlists.yml` | Build the single canonical `playlists/all.m3u` |
| `validate.yml` | Run tests and playlist integrity checks |

Run the validation and test commands above before submitting changes.

## Disclaimer

This project does not host, store, or transmit video content. It only references publicly accessible streaming URLs and remote artwork URLs. Availability, licensing, and legality vary by source and jurisdiction. Users are responsible for complying with applicable law and the rights of content owners. If you own content and want a reference removed, open an issue.
