from __future__ import annotations

import unittest

from scripts.lib.pipeline import (
    language_exclusion_reason,
    load_language_filter,
    load_quality_filter,
    name_quality,
    quality_exclusion_reason,
)
from scripts.lib.catalog import normalize_name, strip_quality

ROOT = "."


class LanguageFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_language_filter(ROOT)

    def _reason(self, cid, cats, languages, english):
        return language_exclusion_reason(cid, "Some Channel", cats, languages, english, self.rules)

    def test_non_english_is_removed_from_listed_categories(self) -> None:
        for category in ("documentary", "entertainment", "kids", "movies", "music", "news", "sports"):
            with self.subTest(category=category):
                reason = self._reason("some-id", [category], ["rus"], False)
                self.assertTrue(reason.startswith("language:"))

    def test_english_is_kept(self) -> None:
        for category in ("documentary", "entertainment", "kids", "movies", "music", "news", "sports"):
            with self.subTest(category=category):
                self.assertEqual(self._reason("some-id", [category], ["eng"], True), "")

    def test_english_alongside_another_language_is_kept(self) -> None:
        # A bilingual channel is watchable in English, so it stays.
        self.assertEqual(self._reason("some-id", ["news"], ["eng", "ara"], True), "")

    def test_unknown_language_is_kept(self) -> None:
        """Never delete a channel merely because no data was found for it."""
        self.assertEqual(self._reason("some-id", ["news"], [], None), "")

    def test_categories_outside_the_filter_are_untouched(self) -> None:
        for category in ("soccer", "nfl", "nba", "ufc", "sky-sports", "tsn"):
            with self.subTest(category=category):
                self.assertEqual(self._reason("some-id", [category], ["rus"], False), "")

    def test_bein_sports_is_excepted(self) -> None:
        # beIN is Arabic/French but was explicitly requested as a keeper.
        self.assertEqual(self._reason("bein-1", ["bein-sports"], ["ara"], False), "")

    def test_named_channels_are_excepted(self) -> None:
        for channel_id in ("tbc1", "tbc2", "dodoma-tv"):
            with self.subTest(channel_id=channel_id):
                # Swahili, and entertainment is a filtered category.
                self.assertEqual(self._reason(channel_id, ["entertainment"], ["swa"], False), "")

    def test_reason_names_the_language(self) -> None:
        self.assertEqual(self._reason("x", ["news"], ["rus"], False), "language:rus")


class QualityFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_quality_filter(ROOT)

    def _reason(self, name, cats):
        return quality_exclusion_reason(name, cats, self.rules)

    def test_high_definition_music_is_dropped(self) -> None:
        for name in ("30A Music (720p)", "360TuneBox (1080p)", "Some Channel (2160p)"):
            with self.subTest(name=name):
                self.assertTrue(self._reason(name, ["music"]).startswith("quality:"))

    def test_standard_definition_music_is_kept(self) -> None:
        for name in ("ACW UG TV (480p)", "Some Radio (360p)", "Unlabelled Music"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, ["music"]), "")

    def test_not_24_7_music_is_dropped(self) -> None:
        self.assertEqual(self._reason("4 Fun TV (576i) [Not 24/7]", ["music"]), "not-24/7")

    def test_rule_only_applies_to_configured_categories(self) -> None:
        # An HD news channel is not affected by the music quality rule.
        self.assertEqual(self._reason("CNN HD (1080p)", ["news"]), "")

    def test_name_quality_parsing(self) -> None:
        self.assertEqual(name_quality("Channel (1080p)"), (1080, False))
        self.assertEqual(name_quality("Channel (480p)"), (480, False))
        self.assertEqual(name_quality("Channel HD"), (720, False))
        self.assertEqual(name_quality("Channel FHD"), (1080, False))
        self.assertEqual(name_quality("Channel"), (None, False))
        self.assertEqual(name_quality("X [Not 24/7]"), (None, True))


class QualityWordMergeTests(unittest.TestCase):
    """A quality spelled into the name must not create a second record."""

    def test_trailing_quality_word_is_ignored(self) -> None:
        pairs = [
            ("Trace Urban HD (1080p)", "Trace Urban"),
            ("Al Jazeera English HD (576p)", "Al Jazeera English"),
            ("8XM HD", "8XM (576p)"),
            ("That's 70s SD (576p)", "That's 70s (720p)"),
        ]
        for decorated, plain in pairs:
            with self.subTest(decorated=decorated):
                self.assertEqual(normalize_name(decorated), normalize_name(plain))

    def test_strip_quality_removes_both_forms(self) -> None:
        self.assertEqual(strip_quality("Trace Urban HD (1080p)"), "Trace Urban")
        self.assertEqual(strip_quality("Trace Urban"), "Trace Urban")

    def test_quality_word_inside_a_name_is_preserved(self) -> None:
        # "1HD" is part of the brand, not a trailing quality marker.
        self.assertEqual(strip_quality("1HD Music Television"), "1HD Music Television")
        self.assertEqual(normalize_name("1HD Music Television"), "1hd music television")


if __name__ == "__main__":
    unittest.main()
