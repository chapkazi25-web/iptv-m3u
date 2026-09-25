from __future__ import annotations

import unittest

from scripts.lib.catalog import ChannelCatalog
from scripts.lib.m3u import M3UEntry
from scripts.lib.pipeline import HealthStore
from scripts.validate.check_streams import ProbeTarget, update_record


class ChannelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ChannelCatalog(
            {
                "cnn": {
                    "name": "CNN",
                    "aliases": ["CNN", "CNN HD", "CNN US"],
                    "countries": ["US"],
                    "categories": ["news"],
                },
                "bbc-one-us": {
                    "name": "BBC One",
                    "aliases": ["BBC One"],
                    "countries": ["US"],
                    "categories": ["entertainment"],
                },
                "bbc-one-gb": {
                    "name": "BBC One",
                    "aliases": ["BBC One"],
                    "countries": ["GB"],
                    "categories": ["entertainment"],
                },
            },
            source_aliases={"pluto-tv": {"cnn hd": "cnn"}},
        )

    def test_source_alias_maps_to_stable_id(self) -> None:
        entry = M3UEntry(-1, {"tvg-name": "CNN HD"}, "CNN HD", "https://example.com/cnn")
        resolution = self.catalog.resolve("pluto-tv", entry)
        self.assertEqual(resolution.channel_id, "cnn")
        self.assertEqual(resolution.matched_by, "source-alias")

    def test_same_name_uses_country_instead_of_merging_wrong_channels(self) -> None:
        gb = M3UEntry(-1, {"tvg-name": "BBC One"}, "BBC One", "https://example.com/gb", line_number=1)
        gb.attrs["tvg-country"] = "GB"
        us = M3UEntry(-1, {"tvg-name": "BBC One"}, "BBC One", "https://example.com/us", line_number=1)
        us.attrs["tvg-country"] = "US"
        self.assertEqual(self.catalog.resolve("cdn", gb).channel_id, "bbc-one-gb")
        self.assertEqual(self.catalog.resolve("cdn", us).channel_id, "bbc-one-us")


class HealthHysteresisTests(unittest.TestCase):
    def test_repeated_failures_disable_but_success_restores(self) -> None:
        health = HealthStore({"disable_after_failures": 6, "streams": {}})
        target = ProbeTarget("cdn", "cnn", "stream-1", "https://example.com/cnn", {}, False)
        for _ in range(5):
            update_record(health, target, False, 500, "timeout", "2026-01-01T00:00:00Z")
        self.assertTrue(health.enabled("stream-1"))
        self.assertEqual(health.record("stream-1")["status"], "unstable")
        update_record(health, target, False, 500, "timeout", "2026-01-01T00:00:01Z")
        self.assertFalse(health.enabled("stream-1"))
        self.assertEqual(health.record("stream-1")["status"], "offline")
        update_record(health, target, True, 120, "", "2026-01-01T00:00:02Z")
        self.assertTrue(health.enabled("stream-1"))
        self.assertEqual(health.record("stream-1")["status"], "online")
        self.assertEqual(health.record("stream-1")["failures"], 0)


if __name__ == "__main__":
    unittest.main()
