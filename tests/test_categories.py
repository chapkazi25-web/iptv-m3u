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
    """Txt rebuild uses 11 exact guide sections with no brand name_patterns."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.patterns = load_name_patterns(ROOT)

    def test_txt_rebuild_has_no_brand_name_patterns(self) -> None:
        # USA/UK/CA/AU + Entertainment/Movies/News/Kids/Sports are assigned
        # from the txt allowlist, not from name regexes.
        self.assertEqual(self.patterns, [])

    def test_former_brand_names_are_not_recategorized(self) -> None:
        for name in ("Sky Sports Football", "TSN 2", "TNT Sports 4", "beIN SPORTS 1"):
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), "")

    def test_live_event_names_are_not_split(self) -> None:
        # Live events are excluded until told otherwise; no soccer/nfl/nba/ufc.
        for name in ("Fox Soccer Plus", "NFL Network", "NBA TV", "UFC", "[NFL] Falcons vs Packers"):
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), "")

    def test_unrelated_channels_are_not_recategorized(self) -> None:
        for name in ("CNN", "Trace Mziki", "Sky News"):
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), "")

    def test_tanzanian_channels_get_their_own_category(self) -> None:
        # Tanzania category was removed in the txt rebuild; these names now
        # resolve to no special category.
        for name in ("TBC1", "Dodoma TV", "Tanzania Safari Channel"):
            with self.subTest(name=name):
                self.assertEqual(match_name_category(name, self.patterns), "")

    def test_a_tournament_name_is_not_treated_as_tanzanian(self) -> None:
        # "[Clasificacion] Tanzania vs Guinea-Bissau" names the country but is
        # a football event, not a Tanzanian channel.
        name = "[Clasificación para la Copa Africana de Naciones] Tanzania vs Guinea-Bissau | BeIN Sports Ñ"
        self.assertEqual(match_name_category(name, self.patterns), "")

    def test_no_brand_rule_to_win(self) -> None:
        self.assertEqual(match_name_category("Sky Sports Cricket", self.patterns), "")


class GroupCategoryTests(unittest.TestCase):
    """Txt rebuild: live-event groups are excluded, so they fall back to default."""

    @classmethod
    def setUpClass(cls) -> None:
        _, cls.default, cls.aliases = load_category_config(ROOT)

    def _category(self, group: str, name: str = "Channel") -> str:
        entry = M3UEntry(duration="-1", attrs={"group-title": group}, title=name, url="http://x/y")
        return canonical_category(entry, self.aliases, self.default)

    def test_txt_guide_groups_map_to_new_categories(self) -> None:
        cases = {
            "News": "news",
            "Sports": "sports",
            "Entertainment": "entertainment",
            "Movies": "movies",
            "Kids": "kids",
            "NFL Sunday Ticket": "nfl",
            "Sunday Ticket": "nfl",
            "Live - American Football": "nfl",
            "NBA League Pass": "nba",
            "League Pass": "nba",
            "Live - Soccer": "soccer",
            "Soccer": "soccer",
        }
        for group, expected in cases.items():
            with self.subTest(group=group):
                self.assertEqual(self._category(group), expected)

    def test_removed_live_event_groups_fall_back_to_default(self) -> None:
        # These groups are excluded upstream, so they must never resolve to a
        # live-events category that no longer exists.
        for group in ("Live - Baseball", "Live - Hockey", "Live - Other Events",
                      "Live - Racing", "Live - Tennis", "Live - Basketball",
                      "Music"):
            with self.subTest(group=group):
                self.assertEqual(self._category(group), self.default)

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

    def test_weather_channels_are_kept_for_txt_rebuild(self) -> None:
        # Txt News Networks explicitly lists weather/business channels.
        for name in ("The Weather Channel", "Fox Weather", "AccuWeather NOW"):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("some-id", name, ["news"], self.rules))

    def test_business_channels_are_kept_for_txt_rebuild(self) -> None:
        for name in ("CNBC", "CNBC (720p)", "Bloomberg TV Asia", "Fox Business Network"):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("some-id", name, ["news"], self.rules))

    def test_big_brother_feeds_are_kept_for_txt_rebuild(self) -> None:
        for name in ("Big Brother Camera 1", "Big Brother Quad View", "Big Brother"):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("big-brother", name, ["entertainment"], self.rules))

    def test_local_market_news_channels_are_kept_for_txt_rebuild(self) -> None:
        # Txt lists local networks explicitly (NBC [Chicago], CTV Vancouver...).
        for name in (
            "CBS News Bay Area",
            "CBS News Colorado",
            "CBS News Minnesota",
            "NBC [Chicago]",
            "CTV Vancouver",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_excluded("some-id", name, ["news"], self.rules))

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
            "Live - Basketball",
        ):
            with self.subTest(group=group):
                self.assertTrue(is_excluded_group(group, self.rules))

    def test_kept_groups_survive(self) -> None:
        for group in ("Sports", "News", "Entertainment", "Movies", "Kids",
                      "Live - Soccer", "Live - American Football"):
            with self.subTest(group=group):
                self.assertFalse(is_excluded_group(group, self.rules))


class LiveKickoffTests(unittest.TestCase):
    """Live games are ordered by the kickoff printed in the txt guide."""

    def test_start_timestamp_becomes_event_start(self) -> None:
        from scripts.discover.rebuild_from_tvguide import parse_kickoff

        start, hint = parse_kickoff(
            "NBA 02: Knicks (NYK) x Timberwolves (MIN) "
            "start:2025-01-18 00:20:00 stop:2025-01-18 04:20:00"
        )
        self.assertEqual(start, "2025-01-18T00:20:00+00:00")
        self.assertIsNone(hint)

    def test_month_day_becomes_hint_without_invented_year(self) -> None:
        from scripts.discover.rebuild_from_tvguide import parse_kickoff

        start, hint = parse_kickoff("NBA Summer League Lakers vs. Bulls jul 16 :NBA 03")
        self.assertIsNone(start)
        self.assertEqual(hint, "07-16")

    def test_entry_without_a_date_has_no_kickoff(self) -> None:
        from scripts.discover.rebuild_from_tvguide import parse_kickoff

        self.assertEqual(parse_kickoff("Pittsburgh @ Cleveland"), (None, None))
        self.assertEqual(parse_kickoff("No Game Today"), (None, None))

    def test_bracketed_quality_is_stripped_whole(self) -> None:
        from scripts.discover.rebuild_from_tvguide import clean_display

        self.assertEqual(clean_display("[4K] Pittsburgh @ Cleveland"), "Pittsburgh @ Cleveland")
        self.assertNotIn("[ ]", clean_display("[4K] Pittsburgh @ Cleveland"))

    def test_live_sort_key_orders_league_then_kickoff_then_guide(self) -> None:
        from scripts.build.merge_playlists import live_sort_key

        catalog = {
            "nfl-game": {
                "name": "Pittsburgh @ Cleveland",
                "live_league": "nfl",
                "live_suborder": 1,
                "live_order": 5,
            },
            "nba-timed": {
                "name": "NBA 02",
                "live_league": "nba",
                "live_suborder": 2,
                "live_order": 9,
                "event_start": "2025-01-18T00:20:00+00:00",
            },
            "nba-hint": {
                "name": "NBA 01",
                "live_league": "nba",
                "live_suborder": 2,
                "live_order": 8,
                "event_hint": "07-16",
            },
            "nba-plain": {
                "name": "NBA 06 :",
                "live_league": "nba",
                "live_suborder": 2,
                "live_order": 10,
            },
        }
        ordered = sorted(catalog, key=lambda cid: live_sort_key(cid, catalog))
        self.assertEqual(ordered, ["nfl-game", "nba-timed", "nba-hint", "nba-plain"])

    def test_live_sort_key_groups_competitions_before_kickoff(self) -> None:
        from scripts.build.merge_playlists import live_sort_key

        catalog = {
            "mls": {
                "name": "[MLS] Dallas vs Los Angeles FC",
                "live_league": "soccer",
                "live_suborder": 0,
                "live_order": 3,
                "league_group": "mls",
            },
            "copa": {
                "name": "[Copa Chile] Colo-Colo vs Audax Italiano",
                "live_league": "soccer",
                "live_suborder": 0,
                "live_order": 1,
                "league_group": "copa chile",
            },
        }
        ordered = sorted(catalog, key=lambda cid: live_sort_key(cid, catalog))
        self.assertEqual(ordered, ["copa", "mls"])


class LiveGameIngestTests(unittest.TestCase):
    """Source feeds collapse into one channel per game."""

    def test_feeds_of_one_game_share_a_key(self) -> None:
        from scripts.discover.rebuild_from_tvguide import canonical_live_game

        feeds = [
            "[Copa Chile] Colo-Colo vs Audax Italiano 1 (PLIBRE)",
            "[Copa Chile] Colo-Colo vs Audax Italiano | TNT Sports Premiun CL (TVF90)",
            "[Copa Chile] Colo-Colo vs Audax Italiano | TNT Sports Premiun CL HD (TVF90)",
        ]
        games = [canonical_live_game(feed) for feed in feeds]
        self.assertTrue(all(game is not None for game in games))
        self.assertEqual({game["key"] for game in games}, {"copa-chile-colo-colo-vs-audax-italiano"})
        self.assertEqual(games[0]["league"], "soccer")
        self.assertEqual(games[0]["display"], "[Copa Chile] Colo-Colo vs Audax Italiano")

    def test_non_event_titles_are_ignored(self) -> None:
        from scripts.discover.rebuild_from_tvguide import canonical_live_game

        self.assertIsNone(canonical_live_game("CNN"))
        self.assertIsNone(canonical_live_game("Pittsburgh @ Cleveland"))

    def test_nickname_subset_matches_a_fixture(self) -> None:
        from scripts.discover.rebuild_from_tvguide import match_live_game

        events = [
            {"strHomeTeam": "Buffalo Bills", "strAwayTeam": "Los Angeles Chargers"},
            {"strHomeTeam": "Cleveland Browns", "strAwayTeam": "Carolina Panthers"},
        ]
        parsed = {"sport": "American Football", "home": "Chargers", "away": "Bills"}
        matched = match_live_game(parsed, events)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["strHomeTeam"], "Buffalo Bills")

    def test_swapped_home_away_still_matches(self) -> None:
        from scripts.discover.rebuild_from_tvguide import match_live_game

        events = [{"strHomeTeam": "Temple", "strAwayTeam": "Army"}]
        parsed = {"sport": "American Football", "home": "Army Black Knights", "away": "Temple Owls"}
        matched = match_live_game(parsed, events)
        self.assertIsNotNone(matched)

    def test_unrelated_teams_do_not_match(self) -> None:
        from scripts.discover.rebuild_from_tvguide import match_live_game

        events = [{"strHomeTeam": "Buffalo Bills", "strAwayTeam": "Los Angeles Chargers"}]
        parsed = {"sport": "American Football", "home": "Colo-Colo", "away": "Audax Italiano"}
        self.assertIsNone(match_live_game(parsed, events))

    def test_today_game_rule(self) -> None:
        from scripts.discover.rebuild_from_tvguide import game_is_today

        # Dated on the guide day: listed. Dated elsewhere: dropped.
        self.assertTrue(game_is_today("2026-10-01T17:00:00+00:00", "2026-10-01"))
        self.assertFalse(game_is_today("2026-09-27T17:00:00+00:00", "2026-10-01"))
        self.assertFalse(game_is_today("2025-01-18T00:20:00+00:00", "2026-10-01"))
        # No kickoff: kept, since the source lists it today and an unknown
        # date is not evidence it already played.
        self.assertTrue(game_is_today(None, "2026-10-01"))

    def test_fixture_date_prefers_date_event(self) -> None:
        from scripts.discover.rebuild_from_tvguide import fixture_date

        self.assertEqual(fixture_date({"dateEvent": "2026-10-01", "strTime": "17:00:00"}), "2026-10-01")
        self.assertEqual(fixture_date({}), "")


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
