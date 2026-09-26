from __future__ import annotations

import unittest

from scripts.lib.geo import (
    country_from_name,
    country_from_reference,
    record_countries,
    region_base_name,
)
from scripts.lib.pipeline import (
    country_exclusion_reason,
    load_country_filter,
    load_local_filter,
    load_radio_filter,
    load_region_filter,
    local_exclusion_reason,
    radio_exclusion_reason,
    region_duplicates,
)

ROOT = "."


def record(name: str, **extra) -> dict:
    return {"name": name, **extra}


class CountryFromReferenceTests(unittest.TestCase):
    """iptv-org, Grade TV and TVivu all encode the country in the source id."""

    def test_country_is_read_before_the_variant(self) -> None:
        cases = {
            "PlutoTVParanormal.de.ES": "DE",
            "ParanormalState.us.UK": "US",
            "1001Noites.br.SD": "BR",
            "TerraMaterWILD.de.German": "DE",
        }
        for reference, expected in cases.items():
            with self.subTest(reference=reference):
                self.assertEqual(country_from_reference(reference), expected)

    def test_two_segment_reference_is_the_country(self) -> None:
        self.assertEqual(country_from_reference("DodomaTV.tz"), "TZ")
        self.assertEqual(country_from_reference("30AMusic.us"), "US")

    def test_variant_marker_on_the_country_is_understood(self) -> None:
        self.assertEqual(country_from_reference("Animal.Planet.HD.us2"), "US")
        self.assertEqual(country_from_reference("WPIX-DT.us_locals1"), "US")

    def test_a_name_is_never_mistaken_for_a_country(self) -> None:
        # "DodomaTV" must not be read as "DO", and a quality segment is not a
        # country even when it happens to be two letters.
        self.assertEqual(country_from_reference("DodomaTV"), "")
        self.assertEqual(country_from_reference("CNN.International.us2"), "US")

    def test_a_brand_token_is_not_a_country(self) -> None:
        self.assertEqual(country_from_reference("ACC_Network"), "")
        self.assertEqual(country_from_reference(""), "")

    def test_uk_is_normalised_to_the_iso_code(self) -> None:
        self.assertEqual(country_from_reference("Channel4.uk"), "GB")


class CountryFromNameTests(unittest.TestCase):
    def test_country_in_brackets(self) -> None:
        self.assertEqual(country_from_name("Arte (France)"), "FR")
        self.assertEqual(country_from_name("Pluto TV Paranormal (United States)"), "US")

    def test_resolution_is_not_read_as_a_country(self) -> None:
        self.assertEqual(country_from_name("Some Channel (1080p)"), "")
        self.assertEqual(country_from_name("Some Channel"), "")


class RecordCountryTests(unittest.TestCase):
    def test_explicit_country_wins_over_the_source_id(self) -> None:
        value = record("Angel TV Nepal", countries=["NP"], source_refs={"iptv-org": ["AngelTV.in.Nepal"]})
        self.assertEqual(record_countries(value), {"NP"})

    def test_source_id_is_the_fallback(self) -> None:
        self.assertEqual(record_countries(record("TBC2", source_refs={"gradetv": ["TBC2.tz"]})), {"TZ"})

    def test_display_name_is_the_last_resort(self) -> None:
        self.assertEqual(record_countries(record("Arte (France)")), {"FR"})

    def test_nothing_known_is_empty(self) -> None:
        self.assertEqual(record_countries(record("Some Channel")), set())


class CountryFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_country_filter(ROOT)

    def _reason(self, name, countries, categories=None):
        value = record(name, countries=[code for code in countries.split(",") if code])
        return country_exclusion_reason(name, categories or ["entertainment"], value, self.rules)

    def test_allowed_countries_are_kept(self) -> None:
        for code in ("us", "US", "CA", "GB", "NZ", "AU", "uk"):
            with self.subTest(code=code):
                self.assertEqual(self._reason("Some Channel", code), "")

    def test_other_countries_are_removed(self) -> None:
        for code in ("de", "fr", "in", "br", "es"):
            with self.subTest(code=code):
                self.assertEqual(self._reason("Some Channel", code), f"country:{code.upper()}")

    def test_unknown_country_is_removed(self) -> None:
        self.assertEqual(self._reason("Some Channel", ""), "country:unknown")

    def test_a_channel_from_an_allowed_country_survives(self) -> None:
        self.assertEqual(self._reason("Some Channel", "us,de"), "")

    def test_the_wanted_brands_are_excepted(self) -> None:
        cases = {
            "beIN SPORTS 1": "qa",
            "Trace Africa": "fr",
            "XITE Hits": "nl",
            "Vevo Rock": "us",
        }
        for name, code in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, code), "")

    def test_a_brand_word_inside_another_name_is_not_an_exemption(self) -> None:
        self.assertEqual(self._reason("Tracing the News", "de"), "country:DE")

    def test_tanzania_is_excepted(self) -> None:
        self.assertEqual(self._reason("TBC1", "tz", categories=["tanzania"]), "")
        # The same channel outside its own category is not protected.
        self.assertEqual(self._reason("TBC1", "tz", categories=["entertainment"]), "country:TZ")

    def test_the_reason_names_the_country(self) -> None:
        self.assertEqual(self._reason("Arte (France)", ""), "country:FR")


class RegionBaseNameTests(unittest.TestCase):
    WORDS = ["arabia", "america", "asia", "europe", "china", "chinese", "indo china"]

    def test_parenthesised_country_and_trailing_code(self) -> None:
        self.assertEqual(
            region_base_name("Pluto TV Paranormal (Germany) ES", self.WORDS),
            "Pluto TV Paranormal",
        )

    def test_parenthesised_country_on_its_own(self) -> None:
        self.assertEqual(region_base_name("Avatar (Sweden)", self.WORDS), "Avatar")

    def test_trailing_country_code(self) -> None:
        self.assertEqual(region_base_name("48 Hours CA", self.WORDS), "48 Hours")

    def test_trailing_region_word(self) -> None:
        for name in ("Angel TV Arabia", "Angel TV Indo-China", "CCTV-4 America"):
            with self.subTest(name=name):
                self.assertEqual(region_base_name(name, self.WORDS), name.rsplit(" ", 1)[0])

    def test_tv_is_never_a_region(self) -> None:
        # "TV" is Tuvalu's code, but it ends almost every channel name.
        self.assertEqual(region_base_name("A&E TV", self.WORDS), "A&E TV")
        self.assertEqual(region_base_name("111 TV", self.WORDS), "111 TV")

    def test_a_lowercase_word_is_not_a_region_code(self) -> None:
        self.assertEqual(region_base_name("Watch It", self.WORDS), "Watch It")

    def test_an_unqualified_name_is_never_trimmed(self) -> None:
        for name in ("CNN", "Angel TV", "Trace Africa"):
            with self.subTest(name=name):
                self.assertEqual(region_base_name(name, self.WORDS), name)


class RegionDuplicateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_region_filter(ROOT)

    def _fold(self, names):
        records = {f"id-{index}": record(name) for index, name in enumerate(names)}
        published = set(records)
        return {
            records[channel_id]["name"]: winner
            for channel_id, winner in region_duplicates(records, published, self.rules).items()
        }

    def test_every_variant_folds_into_the_unqualified_channel(self) -> None:
        folded = self._fold(
            [
                "Pluto TV Paranormal",
                "Pluto TV Paranormal (United States)",
                "Pluto TV Paranormal (United States) CA",
            ]
        )
        self.assertEqual(folded, {"Pluto TV Paranormal (United States)": "id-0",
                                  "Pluto TV Paranormal (United States) CA": "id-0"})

    def test_a_brand_keeps_exactly_one_region(self) -> None:
        names = ["Angel TV", "Angel TV Arabia", "Angel TV Australia", "Angel TV Chinese"]
        folded = self._fold(names)
        self.assertEqual(sorted(folded), sorted(names[1:]))
        self.assertEqual(set(folded.values()), {"id-0"})

    def test_distinct_channels_are_never_folded(self) -> None:
        # "Trace Africa" and "Trace Naija" share a brand, not a channel, and
        # there is no channel named "Trace" for them to fold into.
        self.assertEqual(self._fold(["Trace Africa", "Trace Naija", "Trace Sport Stars"]), {})

    def test_only_published_channels_take_part(self) -> None:
        records = {
            "plain": record("Betty White"),
            "variant": record("Betty White CA"),
        }
        # The unqualified record is already excluded by another rule, so the
        # variant must be left alone rather than point at a dead winner.
        self.assertEqual(region_duplicates(records, {"variant"}, self.rules), {})

    def test_the_unqualified_name_is_preferred_over_an_allowed_country(self) -> None:
        records = {
            "plain": record("NatureTime"),
            "au": record("NatureTime AU", countries=["AU"]),
        }
        self.assertEqual(region_duplicates(records, set(records), self.rules), {"au": "plain"})

    def test_two_variants_with_no_unqualified_name_are_both_kept(self) -> None:
        # Neither "NatureTime CA" nor "NatureTime UK" is "NatureTime", and
        # there is no such channel, so they are two feeds rather than one.
        records = {
            "ca": record("NatureTime CA", countries=["CA"]),
            "uk": record("NatureTime UK", countries=["GB"]),
        }
        self.assertEqual(region_duplicates(records, set(records), self.rules), {})


class LocalFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_local_filter(ROOT)

    def _reason(self, name, **extra):
        return local_exclusion_reason(name, record(name, **extra), self.rules)

    def test_us_call_signs_are_removed(self) -> None:
        for name in ("ABC KATC", "ABC KERO-TV", "ABC KGUN-TV", "CBS KCCI", "Create WMPT", "KBEV"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=["US"]), "local")

    def test_a_call_sign_is_read_in_the_country_that_issues_them(self) -> None:
        # "WILD" and "WEST" look like call signs but belong to a German and a
        # Peruvian channel, so they are dropped by the country filter instead.
        for name, country in (("Terra Mater WILD", "DE"), ("TV-WEST", "NG"), ("WEST", "PE")):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=[country]), "")

    def test_a_call_sign_is_a_station_wherever_the_source_files_it(self) -> None:
        # iptv-org files KQSL, a Tulare station, under Canada.
        self.assertEqual(self._reason("TLN KQSL", countries=["CA"]), "local")

    def test_an_ordinary_word_is_never_a_call_sign(self) -> None:
        for name in ("Wildcat", "Warner Bros TV", "Wild 'N Out", "Wild West Chronicles"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=["US"]), "")

    def test_australian_regional_feeds_are_removed(self) -> None:
        for name in ("ABC TV NT", "ABC TV QLD", "ABC TV VIC", "ABC TV WA", "ABC TV ACT"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=["AU"]), "local")

    def test_a_leading_state_abbreviation_is_kept(self) -> None:
        # "SA Bulo Bulo" is a Bolivian football club, not an Australian feed.
        self.assertEqual(self._reason("[Bolivia Copa] SA Bulo Bulo vs Real Potosi"), "")

    def test_municipal_and_community_channels_are_removed(self) -> None:
        for name in (
            "City of Fort Pierce",
            "Kern County TV",
            "Government TV 16",
            "CAN TV19",
            "Beach TV Key West & Florida Keys",
        ):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=["US"]), "local")

    def test_a_local_source_id_is_enough(self) -> None:
        value = record("CW", source_refs={"core": ["WPIX-DT.us_locals1"]})
        self.assertEqual(local_exclusion_reason("CW", value, self.rules), "local")

    def test_national_channels_survive(self) -> None:
        for name in ("CBS", "NBC", "ITV1", "Channel 4", "ABC TV", "ABC TV Plus", "CW"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, countries=["US"]), "")


class RadioFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_radio_filter(ROOT)

    def _reason(self, name, categories):
        return radio_exclusion_reason(name, categories, self.rules)

    def test_radio_stations_are_removed_from_music(self) -> None:
        for name in ("Power Hit Radio", "Radio Italia Trend TV", "Melody FM", "CKNO-FM", "X 102.7 FM"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, ["music"]), "radio")

    def test_music_video_channels_survive(self) -> None:
        for name in ("Vevo Rock", "Music Box Hits", "Clubbing TV", "MCM Top", "Trace Muzika"):
            with self.subTest(name=name):
                self.assertEqual(self._reason(name, ["music"]), "")

    def test_the_station_suffix_is_matched_case_sensitively(self) -> None:
        # "I Am ..." must not be read as an AM station.
        self.assertEqual(self._reason("I Am Famous", ["music"]), "")

    def test_other_categories_are_untouched(self) -> None:
        self.assertEqual(self._reason("Power Hit Radio", ["news"]), "")


if __name__ == "__main__":
    unittest.main()
