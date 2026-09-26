from __future__ import annotations

import unittest

from scripts.build.merge_playlists import ALL_HEADER
from scripts.lib.m3u import M3UEntry, parse_m3u_text, render_m3u


class M3UTests(unittest.TestCase):
    def test_parser_keeps_directives_and_commas(self) -> None:
        text = (
            '#EXTM3U url-tvg="https://example.com/epg.xml"\n'
            '#EXTINF:-1 tvg-id="news.us" tvg-name="Example, News" '
            'tvg-logo="https://example.com/logo.png" group-title="News",Example, News\n'
            '#EXTVLCOPT:http-user-agent=Example Agent\n'
            '#EXTVLCOPT:http-referrer=https://example.com/\n'
            'https://example.com/live.m3u8\n'
        )
        header, entries = parse_m3u_text(text)
        self.assertIn("url-tvg", header)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "Example, News")
        self.assertEqual(entry.attrs["tvg-id"], "news.us")
        self.assertEqual(entry.group, "News")
        self.assertEqual(len(entry.extra_lines), 2)
        self.assertEqual(entry.url, "https://example.com/live.m3u8")

    def test_render_round_trip_preserves_source_headers(self) -> None:
        entry = M3UEntry(
            duration="-1",
            attrs={"tvg-id": "example", "group-title": "News"},
            title="Example",
            url="https://example.com/live.m3u8",
            extra_lines=["#EXTVLCOPT:http-referrer=https://example.com"],
        )
        rendered = render_m3u('#EXTM3U url-tvg="https://example.com/epg.xml"', [entry])
        header, parsed = parse_m3u_text(rendered)
        self.assertEqual(header, '#EXTM3U url-tvg="https://example.com/epg.xml"')
        self.assertEqual(parsed[0].url, entry.url)
        self.assertEqual(parsed[0].extra_lines, entry.extra_lines)

    def test_leading_comments_survive_a_round_trip(self) -> None:
        # The published playlist credits its upstreams in a comment block after
        # the #EXTM3U line. apply_logos rewrites every playlist in place, so
        # those comments are only durable if the parser hands them back.
        entry = M3UEntry(
            duration="-1",
            attrs={"tvg-id": "example", "group-title": "News"},
            title="Example",
            url="https://example.com/live.m3u8",
        )
        header = '#EXTM3U url-tvg="https://example.com/epg.xml"\n# thank you, upstream'
        header, parsed = parse_m3u_text(render_m3u(header, [entry]))
        self.assertIn("# thank you, upstream", header)
        self.assertTrue(header.startswith("#EXTM3U"))
        self.assertEqual(len(parsed), 1)
        # A second pass must not accumulate or drop anything.
        self.assertEqual(parse_m3u_text(render_m3u(header, parsed))[0], header)

    def test_channel_directives_are_not_mistaken_for_a_comment_block(self) -> None:
        # #EXTVLCOPT belongs to the entry it follows, not to the header.
        text = (
            "#EXTM3U\n"
            "#EXTINF:-1 tvg-id=\"a\" group-title=\"News\",A\n"
            "#EXTVLCOPT:http-referrer=https://example.com/\n"
            "https://example.com/a.m3u8\n"
        )
        header, entries = parse_m3u_text(text)
        self.assertEqual(header, "#EXTM3U")
        self.assertEqual(entries[0].extra_lines, ["#EXTVLCOPT:http-referrer=https://example.com/"])

    def test_country_and_quality_normalization(self) -> None:
        _, entries = parse_m3u_text(
            '#EXTM3U\n#EXTINF:-1 tvg-id="mtv.uk" tvg-name="MTV (HD)" '
            'tvg-country="UK" group-title="Entertainment",MTV (HD)\n'
            'https://example.com/mtv.m3u8\n'
        )
        self.assertEqual(entries[0].clean_name(), "MTV")
        self.assertEqual(entries[0].inferred_country(), "GB")


class CreditsHeaderTests(unittest.TestCase):
    """A playlist URL is often the only thing a user ever sees, so the
    published file names the projects that actually publish the streams."""

    def test_header_still_opens_with_extm3u(self) -> None:
        self.assertTrue(ALL_HEADER.startswith("#EXTM3U"))
        self.assertIn("url-tvg", ALL_HEADER.splitlines()[0])

    def test_header_credits_every_upstream(self) -> None:
        for project in (
            "iptv-org",
            "doms9/iptv",
            "CDN Live TV",
            "Grade TV",
            "Pluto TV",
            "TVivu",
            "K-yzu/Logos",
            "EPGShare01",
            "TheSportsDB",
            "Wikipedia",
        ):
            with self.subTest(project=project):
                self.assertIn(project, ALL_HEADER)

    def test_header_states_nothing_is_hosted_here(self) -> None:
        self.assertIn("No stream here is hosted by this project", ALL_HEADER)

    def test_every_comment_line_is_a_comment(self) -> None:
        for line in ALL_HEADER.splitlines()[1:]:
            self.assertTrue(line.startswith("#"), line)

    def test_header_keeps_the_first_line_stable_for_parsers(self) -> None:
        # A reader that only understands the first line must still get a usable
        # directive rather than a comment.
        self.assertNotIn("\n", ALL_HEADER.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
