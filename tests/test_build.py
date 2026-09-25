from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build.merge_playlists import build
from scripts.lib.m3u import parse_m3u_text
from scripts.lib.pipeline import stream_id, write_json
from scripts.validate.validate_m3u import validate


class BuildIntegrationTests(unittest.TestCase):
    def test_canonical_channel_fails_over_to_healthy_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/raw").mkdir(parents=True)
            (root / "playlists/sources").mkdir(parents=True)
            write_json(
                root / "data/sources.json",
                {
                    "version": 1,
                    "sources": {
                        "cdn": {
                            "name": "CDN",
                            "playlist": "playlists/sources/cdn.m3u",
                            "priority": 1,
                        },
                        "backup": {
                            "name": "Backup",
                            "playlist": "playlists/sources/backup.m3u",
                            "priority": 2,
                            "default_country": "US",
                        },
                    },
                },
            )
            write_json(
                root / "data/categories.json",
                {
                    "version": 1,
                    "default": "entertainment",
                    "categories": {
                        "news": {"name": "News", "aliases": ["News"]},
                        "entertainment": {"name": "Entertainment", "aliases": []},
                    },
                },
            )
            write_json(
                root / "data/channels.json",
                {
                    "version": 1,
                    "channels": {
                        "cnn": {
                            "name": "CNN",
                            "aliases": ["CNN", "CNN HD"],
                            "countries": ["US"],
                            "categories": ["news"],
                            "logo": "",
                            "sources": ["cdn", "backup"],
                            "enabled": True,
                        }
                    },
                },
            )
            write_json(root / "data/logo-index.json", {"version": 1, "logos": []})
            cdn_url = "https://cdn.example/cnn.m3u8"
            backup_url = "https://backup.example/cnn.m3u8"
            (root / "playlists/sources/cdn.m3u").write_text(
                '#EXTM3U\n#EXTINF:-1 tvg-id="CNN" tvg-name="CNN" '
                'group-title="News",CNN\n' + cdn_url + "\n",
                encoding="utf-8",
            )
            (root / "playlists/sources/backup.m3u").write_text(
                '#EXTM3U\n#EXTINF:-1 tvg-id="CNN HD" tvg-name="CNN HD" '
                'group-title="News",CNN HD\n' + backup_url + "\n",
                encoding="utf-8",
            )
            cdn_stream_id = stream_id("cdn", "cnn", cdn_url)
            write_json(
                root / "data/stream-health.json",
                {
                    "version": 1,
                    "disable_after_failures": 6,
                    "streams": {
                        cdn_stream_id: {
                            "channel": "cnn",
                            "source": "cdn",
                            "url": cdn_url,
                            "status": "offline",
                            "enabled": False,
                            "failures": 6,
                        }
                    },
                },
            )

            report = build(root)
            self.assertEqual(report["total_channels"], 1)
            _, all_entries = parse_m3u_text((root / "playlists/all.m3u").read_text(encoding="utf-8"))
            self.assertEqual(len(all_entries), 1)
            self.assertEqual(all_entries[0].attrs["tvg-id"], "cnn")
            self.assertEqual(all_entries[0].url, backup_url)
            index = json.loads((root / "data/stream-index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["channels"]["cnn"]["selected_source"], "backup")
            self.assertTrue(validate(root)["valid"])


if __name__ == "__main__":
    unittest.main()
