from __future__ import annotations

import unittest

from scripts.discover.build_catalog import consolidate_existing
from scripts.lib.catalog import normalize_name, slugify
from scripts.lib.m3u import M3UEntry
from scripts.lib.pipeline import (
    HealthStore,
    canonical_category,
    is_excluded,
    is_excluded_group,
    load_category_config,
    load_exclusions,
    load_name_patterns,
    match_name_category,
)

ROOT = "."


class CategoryNameRuleTests(unittest.TestCase):
    """Brand and sport channels are published under generic groups such as
    "Sports", so the display name is the only reliable grouping signal."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.patterns = load_name_patterns(ROOT)

    def test_sports_brand_names_map_to_own_category(self) -> None:
        cases = {
            "Sky Sports Football": "sky-sports",
            "Sky Sport 1": "sky-sports",
            "TSN 2": "tsn",
            "TNT Sports 4": "tnt-sports",
            "Stan Sport 9": "stan-sports",
            "beIN SPORTS 1": "bein-sports",
            "beIN Sports USA": "bein-sports",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), expected)

    def test_live_event_sports_split_into_own_categories(self) -> None:
        cases = {
            "Fox Soccer Plus": "soccer",
            "Futbol TV": "soccer",
            "NFL Network": "nfl",
            "[NFL] Falcons vs Packers": "nfl",
            "NBA TV": "nba",
            "UFC": "ufc",
            "Bellator MMA": "ufc",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), expected)

    def test_unrelated_channels_are_not_recategorized(self) -> None:
        for name in ("CNN", "Trace Mziki", "Sky News"):
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), "")

    def test_tanzanian_channels_get_their_own_category(self) -> None:
        cases = {
            "TBC1": "tanzania",
            "TBC2": "tanzania",
            "TBCN": "tanzania",
            "Dodoma TV": "tanzania",
            "IBN TV": "tanzania",
            "Mahaasin TV": "tanzania",
            "Tanzania Safari Channel": "tanzania",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), expected)

    def test_a_tournament_name_is_not_treated_as_tanzanian(self) -> None:
        # "[Clasificacion] Tanzania vs Guinea-Bissau" names the country but is
        # a football event, not a Tanzanian channel.
        name = "[Clasificación para la Copa Africana de Naciones] Tanzania vs Guinea-Bissau | BeIN Sports Ñ"
        self.assertNotEqual(match_name_category(name, self.patterns), "tanzania")

    def test_brand_rules_win_over_generic_sport_names(self) -> None:
        # "Sky Sports Cricket" is both a Sky channel and a sport; the brand
        # rule is listed first and must win.
        self.assertEqual(match_name_category("Sky Sports Cricket", self.patterns), "sky-sports")


class GroupCategoryTests(unittest.TestCase):
    """Live-event group titles map to the split categories even when the
    channel name itself carries no sport keyword."""

    @classmethod
    def setUpClass(cls) -> None:
        _, cls.default, cls.aliases = load_category_config(ROOT)

    def _category(self, group: str, name: str = "Channel") -> str:
        entry = M3UEntry(duration="-1", attrs={"group-title": group}, title=name, url="http://x/y")
        return canonical_category(entry, self.aliases, self.default)

    def test_live_event_groups_map_to_split_categories(self) -> None:
        cases = {
            "Live - Soccer": "soccer",
            "Live - American Football": "nfl",
            "Live - Basketball": "nba",
            "Music": "music",
            "News": "news",
        }
        for group, expected in cases.items():
            with self.subTest(group=group):
                self.assertEqual(self._category(group), expected)

    def test_removed_live_event_groups_fall_back_to_default(self) -> None:
        # These groups are excluded upstream, so they must never resolve to a
        # live-events category that no longer exists.
        for group in ("Live - Baseball", "Live - Hockey", "Live - Other Events"):
            with self.subTest(group=group):
                self.assertNotEqual(self._category(group), "live-events")

    def test_bracket_prefixed_entries_do_not_use_removed_category(self) -> None:
        entry = M3UEntry(
            duration="-1",
            attrs={"group-title": "Something Unknown"},
            title="[Note] Channel",
            url="http://x/y",
        )
        self.assertNotEqual(canonical_category(entry, self.aliases, self.default), "live-events")


class ExclusionRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_exclusions(ROOT)

    def test_geo_blocked_and_not_24_7_channels_are_excluded(self) -> None:
        cases = {
            "9Gem [Geo-blocked]": "entertainment",
            "arte (Germany) [Geo-Blocked]": "documentary",
            "100% Auto Moto TV (406p) [Not 24/7]": "entertainment",
            "Q'hubo TV [Not 24/7]": "news",
        }
        for name, category in cases.items():
            with self.subTest(name=name):
                self.assertTrue(is_excluded("some-id", name, [category], self.rules))

    def test_weather_channels_are_excluded(self) -> None:
        for name in ("The Weather Channel", "WeatherSpy (India)", "Fox Weather", "AccuWeather NOW"):
            with self.subTest(name=name):
                self.assertTrue(is_excluded("some-id", name, ["news"], self.rules))

    def test_business_channels_are_excluded(self) -> None:
        for name in ("CNBC", "CNBC (720p)", "Bloomberg TV Asia", "Fox Business Network"):
            with self.subTest(name=name):
                self.assertTrue(is_excluded("some-id", name, ["news"], self.rules))

    def test_big_brother_camera_feeds_are_excluded(self) -> None:
        for name in ("Big Brother Camera 1", "Big Brother Quad View"):
            with self.subTest(name=name):
                self.assertTrue(is_excluded("some-id", name, ["entertainment"], self.rules))

    def test_main_big_brother_channel_is_kept(self) -> None:
        self.assertFalse(is_excluded("big-brother", "Big Brother", ["entertainment"], self.rules))

    def test_regional_category_channels_are_excluded(self) -> None:
        self.assertTrue(is_excluded("some-id", "Local Channel", ["regional"], self.rules))

    def test_local_market_news_channels_are_excluded(self) -> None:
        for name in (
            "CBS News Bay Area",
            "CBS News Colorado",
            "CBS News Minnesota",
            "CBS News Philly",
            "CBS News Sacramento",
            "Access Sacramento Channel 17",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_excluded("some-id", name, ["news"], self.rules))

    def test_national_news_channels_survive(self) -> None:
        for name in ("CBS News 24/7", "ABC News Live", "ABC News Live 7", "NBC News NOW", "BBC News"):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("some-id", name, ["news"], self.rules))

    def test_ordinary_channels_survive(self) -> None:
        for name, cats in (("CNN", ["news"]), ("Trace Naija", ["music"]), ("Sky News", ["news"])):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("some-id", name, cats, self.rules))


class ExcludedGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_exclusions(ROOT)

    def test_unwanted_live_event_groups_are_dropped(self) -> None:
        for group in (
            "Live - Other Events",
            "Live - Baseball",
            "Live - Hockey",
            "Live - Racing",
            "Live - Tennis",
        ):
            with self.subTest(group=group):
                self.assertTrue(is_excluded_group(group, self.rules))

    def test_kept_live_event_groups_survive(self) -> None:
        for group in ("Live - Soccer", "Live - American Football", "Live - Basketball", "Sports"):
            with self.subTest(group=group):
                self.assertFalse(is_excluded_group(group, self.rules))


class QualitySuffixTests(unittest.TestCase):
    """Publishers append the feed quality to the channel name. Both name
    matching and the canonical slug must ignore it, otherwise one channel
    turns into several near-identical records."""

    def test_resolution_suffixes_are_stripped(self) -> None:
        base = normalize_name("Trace Africa")
        for suffix in ("(1080p)", "(720p)", "(480p)", "(360p)", "(576i)", "(270p)"):
            with self.subTest(suffix=suffix):
                self.assertEqual(normalize_name(f"Trace Africa {suffix}"), base)

    def test_labelled_qualities_are_stripped(self) -> None:
        base = normalize_name("CNN")
        for suffix in ("(HD)", "(SD)", "(FHD)", "(UHD)", "(4K)"):
            with self.subTest(suffix=suffix):
                self.assertEqual(normalize_name(f"CNN {suffix}"), base)

    def test_slugs_match_across_quality_suffixes(self) -> None:
        self.assertEqual(slugify("Trace Africa (1080p)"), slugify("Trace Africa"))

    def test_non_quality_parentheticals_are_preserved(self) -> None:
        # "[Not 24/7]" and "(Live)" are not quality markers.
        self.assertIn("not 24 7", normalize_name("Some Channel [Not 24/7]"))
        self.assertNotEqual(normalize_name("Foo (Live)"), normalize_name("Foo"))

    def test_consolidation_merges_duplicate_records(self) -> None:
        existing = {
            "trace-africa": {
                "name": "Trace Africa",
                "aliases": ["Trace Africa"],
                "countries": ["FR"],
                "sources": ["gradetv"],
                "source_refs": {"gradetv": ["TraceAfrica.fr"]},
                "epg_id": "Trace.fr",
            },
            "trace-africa-1080p": {
                "name": "Trace Africa (1080p)",
                "aliases": ["Trace Africa (1080p)"],
                "countries": [],
                "sources": ["iptv-org"],
                "source_refs": {"iptv-org": ["TraceAfrica.fr.SD"]},
                "manual": True,
            },
        }
        merged = consolidate_existing(existing)
        self.assertEqual(list(merged), ["trace-africa"])
        record = merged["trace-africa"]
        # Curated data from both records must survive the merge.
        self.assertEqual(record["epg_id"], "Trace.fr")
        self.assertTrue(record["manual"])
        self.assertEqual(sorted(record["sources"]), ["gradetv", "iptv-org"])
        self.assertEqual(sorted(record["countries"]), ["FR"])
        self.assertEqual(
            sorted(record["source_refs"]), ["gradetv", "iptv-org"]
        )
        # The public name drops the advertised resolution.
        self.assertEqual(record["name"], "Trace Africa")

    def test_consolidation_is_idempotent(self) -> None:
        existing = {
            "trace-africa": {"name": "Trace Africa", "sources": ["gradetv"]},
            "trace-africa-720p": {"name": "Trace Africa (720p)", "sources": ["iptv-org"]},
        }
        once = consolidate_existing(existing)
        self.assertEqual(consolidate_existing(once), once)

    def test_distinct_channels_are_not_merged(self) -> None:
        existing = {
            "cnn": {"name": "CNN", "countries": ["US"]},
            "sky-news": {"name": "Sky News", "countries": ["GB"]},
        }
        self.assertEqual(consolidate_existing(existing), existing)


class SourcePreferenceTests(unittest.TestCase):
    """A higher-trust source must win over a marginally faster one.

    Verified proxy hops (GradeTV) are slower than direct links (iptv-org) but
    re-check their feed, so source priority outranks response time once both
    streams are equally healthy.
    """

    def _store(self, **streams: dict) -> HealthStore:
        return HealthStore({"version": 1, "disable_after_failures": 6, "streams": streams})

    def test_higher_trust_source_wins_despite_slower_response(self) -> None:
        store = self._store(
            gradetv={"status": "online", "failures": 0, "response_time_ms": 1312},
            iptv_org={"status": "online", "failures": 0, "response_time_ms": 1037},
        )
        self.assertLess(store.score("gradetv", 4), store.score("iptv_org", 5))

    def test_health_still_outranks_source_priority(self) -> None:
        store = self._store(
            trusted_but_degraded={"status": "degraded", "failures": 1, "response_time_ms": 200},
            less_trusted_but_online={"status": "online", "failures": 0, "response_time_ms": 900},
        )
        # A working stream from a lower-priority source must still be chosen.
        self.assertLess(
            store.score("less_trusted_but_online", 5),
            store.score("trusted_but_degraded", 1),
        )

    def test_untested_stream_is_used_when_no_health_data_exists(self) -> None:
        store = self._store()
        self.assertEqual(store.status_rank("never-probed"), 1)
        self.assertEqual(store.score("never-probed", 9), (1, 9, 0, 10**9))

    def test_response_time_breaks_ties_within_one_source(self) -> None:
        store = self._store(
            slow={"status": "online", "failures": 0, "response_time_ms": 900},
            fast={"status": "online", "failures": 0, "response_time_ms": 300},
        )
        self.assertLess(store.score("fast", 4), store.score("slow", 4))


if __name__ == "__main__":
    unittest.main()
