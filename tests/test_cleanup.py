from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.lib.catalog import strip_quality
from scripts.lib.pipeline import event_exclusion_reason, load_event_filter, name_quality

ROOT = "."


class DisplayNameTests(unittest.TestCase):
    """Resolution is kept as data and must not appear in the public name."""

    def test_parenthesised_resolution_is_removed_anywhere(self) -> None:
        cases = [
            ("Trace Urban HD (1080p)", "Trace Urban"),
            ("9Gem (720p) [Geo-blocked]", "9Gem [Geo-blocked]"),
            ("100% Auto Moto TV (406p) [Not 24/7]", "100% Auto Moto TV [Not 24/7]"),
            ("Al Jazeera English HD (576p)", "Al Jazeera English"),
            ("That's 70s SD (576p)", "That's 70s"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(strip_quality(raw), expected)

    def test_no_bitrate_markers_survive(self) -> None:
        self.assertEqual(strip_quality("Channel (1080p)"), "Channel")
        self.assertEqual(strip_quality("Channel FHD"), "Channel")

    def test_quality_is_still_readable_from_the_name(self) -> None:
        self.assertEqual(name_quality("Channel (1080p)"), (1080, False))
        self.assertEqual(name_quality("X [Not 24/7]"), (None, True))


class EventExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_event_filter(ROOT)

    def _at(self, hours_from_start: float) -> datetime:
        start = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
        return start + timedelta(hours=hours_from_start)

    def test_event_is_kept_before_kickoff(self) -> None:
        record = {"event_start": "2026-09-25T20:00:00+00:00", "event_sport": "Soccer"}
        self.assertEqual(event_exclusion_reason(record, self.rules, self._at(-1)), "")

    def test_event_is_kept_during_play(self) -> None:
        record = {"event_start": "2026-09-25T20:00:00+00:00", "event_sport": "Soccer"}
        # Soccer window is 2 hours, so 1.5h in is still live.
        self.assertEqual(event_exclusion_reason(record, self.rules, self._at(1.5)), "")

    def test_event_is_dropped_after_its_window(self) -> None:
        record = {"event_start": "2026-09-25T20:00:00+00:00", "event_sport": "Soccer"}
        reason = event_exclusion_reason(record, self.rules, self._at(3))
        self.assertTrue(reason.startswith("event-ended:"))

    def test_american_football_gets_a_longer_window(self) -> None:
        record = {"event_start": "2026-09-25T20:00:00+00:00", "event_sport": "American Football"}
        # 3h in: past a soccer window, still inside the 3.5h football one.
        self.assertEqual(event_exclusion_reason(record, self.rules, self._at(3)), "")
        self.assertTrue(
            event_exclusion_reason(record, self.rules, self._at(4)).startswith("event-ended:")
        )

    def test_unresolved_event_is_never_removed(self) -> None:
        """No kickoff means no evidence the event finished."""
        for record in ({}, {"event_start": None}, {"event_start": ""}):
            with self.subTest(record=record):
                self.assertEqual(event_exclusion_reason(record, self.rules, self._at(99)), "")

    def test_malformed_kickoff_is_ignored(self) -> None:
        record = {"event_start": "not-a-timestamp"}
        self.assertEqual(event_exclusion_reason(record, self.rules, self._at(99)), "")

    def test_naive_timestamp_is_treated_as_utc(self) -> None:
        record = {"event_start": "2026-09-25T20:00:00", "event_sport": "Soccer"}
        self.assertTrue(event_exclusion_reason(record, self.rules, self._at(3)).startswith("event-ended:"))


class FixtureParsingTests(unittest.TestCase):
    def test_event_name_is_parsed_into_sport_and_teams(self) -> None:
        from scripts.events.fixture_schedule import parse_event

        cases = {
            "[Liga MX] América vs Monterrey | TUDN": ("Soccer", "América", "Monterrey"),
            "[American Football] Army Black Knights vs. Temple Owls | English": (
                "American Football", "Army Black Knights", "Temple Owls",
            ),
            "[Basketball Euroleague] Fenerbahce vs Virtus Bologna 1 (FAWA)": (
                "Basketball", "Fenerbahce", "Virtus Bologna 1",
            ),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                parsed = parse_event(name)
                self.assertIsNotNone(parsed)
                self.assertEqual(
                    (parsed["sport"], parsed["home"], parsed["away"]), expected
                )

    def test_non_event_name_is_ignored(self) -> None:
        from scripts.events.fixture_schedule import parse_event

        self.assertIsNone(parse_event("CNN"))
        self.assertIsNone(parse_event("[American Football] No Opponent Here"))

    def test_time_formats_vary_by_sport(self) -> None:
        from scripts.events.fixture_schedule import event_start

        # Some sports return seconds, some do not, some return no time at all.
        self.assertEqual(
            event_start({"dateEvent": "2026-09-25", "strTime": "00:15:00"}),
            "2026-09-25T00:15:00+00:00",
        )
        self.assertEqual(
            event_start({"dateEvent": "2026-09-25", "strTime": "16:00"}),
            "2026-09-25T16:00:00+00:00",
        )
        self.assertEqual(
            event_start({"dateEvent": "2026-09-25"}),
            "2026-09-25T00:00:00+00:00",
        )


class LogoSourceTests(unittest.TestCase):
    def test_preferred_source_wins(self) -> None:
        from scripts.lib.pipeline import LogoIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "primary.json").write_text(
                json.dumps({"logos": [{"name": "CNN", "path": "TV:US/CNN.png",
                                       "country": "US", "kind": "channel"}]}),
                encoding="utf-8",
            )
            (root / "fallback.json").write_text(
                json.dumps({"logos": [
                    {"name": "CNN", "url": "https://gradetv.net/logos/CNN.us",
                     "country": "US", "kind": "channel", "source": "gradetv"},
                    {"name": "CNN", "url": "https://upload.wikimedia.org/x.png",
                     "country": "US", "kind": "channel", "source": "wikipedia"},
                ]}),
                encoding="utf-8",
            )
            index = LogoIndex.load_many([root / "primary.json", root / "fallback.json"], "https://base")
            # The K-yzu index stores a path and builds a raw URL; the
            # fallbacks carry a ready-made URL and must not win.
            self.assertEqual(index.find("CNN", "US"), "https://base/TV:US/CNN.png")

    def test_fallback_is_used_when_primary_misses(self) -> None:
        from scripts.lib.pipeline import LogoIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "primary.json").write_text(json.dumps({"logos": []}), encoding="utf-8")
            (root / "fallback.json").write_text(
                json.dumps({"logos": [
                    {"name": "9Gem", "url": "https://gradetv.net/logos/9Gem.in",
                     "kind": "channel", "source": "gradetv"},
                ]}),
                encoding="utf-8",
            )
            index = LogoIndex.load_many([root / "primary.json", root / "fallback.json"], "https://base")
            self.assertEqual(index.find("9Gem"), "https://gradetv.net/logos/9Gem.in")

    def test_wikipedia_never_outranks_gradetv(self) -> None:
        from scripts.lib.pipeline import LogoIndex

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fallback.json").write_text(
                json.dumps({"logos": [
                    {"name": "X", "url": "https://upload.wikimedia.org/a.png",
                     "kind": "channel", "source": "wikipedia"},
                    {"name": "X", "url": "https://gradetv.net/logos/x",
                     "kind": "channel", "source": "gradetv"},
                ]}),
                encoding="utf-8",
            )
            index = LogoIndex.load(root / "fallback.json", "https://base")
            self.assertEqual(index.find("X"), "https://gradetv.net/logos/x")

    def test_missing_files_are_tolerated(self) -> None:
        from scripts.lib.pipeline import LogoIndex

        index = LogoIndex.load_many([Path("/nonexistent/a.json")], "https://base")
        self.assertEqual(index.find("Anything"), "")


class LogoLookupHeuristicsTests(unittest.TestCase):
    def test_annotations_are_stripped_for_lookup(self) -> None:
        from scripts.logos.enrich_index import title_variants

        self.assertEqual(title_variants("9Gem (720p) [Geo-blocked]"), ["9Gem (720p) [Geo-blocked]", "9Gem"])

    def test_vector_lead_image_is_accepted_as_logo(self) -> None:
        from scripts.logos.enrich_index import looks_like_logo

        self.assertTrue(looks_like_logo("60-minutes.svg", "60 Minutes"))
        self.assertTrue(looks_like_logo("CNN_Logo_(2014).svg", "CNN"))

    def test_photograph_is_not_accepted(self) -> None:
        from scripts.logos.enrich_index import looks_like_logo

        self.assertFalse(looks_like_logo("Cbs-building.jpg", "CBS"))

    def test_wikipedia_chrome_is_rejected(self) -> None:
        from scripts.logos.enrich_index import looks_like_logo

        for junk in ("Commons-logo.svg", "Wiktionary-logo-en-v2.svg", "Portal-logo.png"):
            with self.subTest(junk=junk):
                self.assertFalse(looks_like_logo(junk, "ITV"))

    def test_underscore_normalisation_matches(self) -> None:
        from scripts.logos.enrich_index import file_key

        self.assertEqual(
            file_key("File:Fox_News_Channel_logo.svg"),
            file_key("File:Fox News Channel logo.svg"),
        )


if __name__ == "__main__":
    unittest.main()
