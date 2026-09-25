from __future__ import annotations

import unittest

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

    def test_country_and_quality_normalization(self) -> None:
        _, entries = parse_m3u_text(
            '#EXTM3U\n#EXTINF:-1 tvg-id="mtv.uk" tvg-name="MTV (HD)" '
            'tvg-country="UK" group-title="Entertainment",MTV (HD)\n'
            'https://example.com/mtv.m3u8\n'
        )
        self.assertEqual(entries[0].clean_name(), "MTV")
        self.assertEqual(entries[0].inferred_country(), "GB")


if __name__ == "__main__":
    unittest.main()
