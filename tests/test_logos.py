from __future__ import annotations

import unittest

from scripts.lib.pipeline import LogoIndex


class LogoIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = LogoIndex(
            [
                {
                    "name": "CNN",
                    "path": "TV:US/CNN.png",
                    "url": "https://raw.githubusercontent.com/K-yzu/Logos/main/TV:US/CNN.png",
                    "country": "US",
                    "kind": "channel",
                },
                {
                    "name": "CNN",
                    "path": "TV:UK/CNN.png",
                    "url": "https://raw.githubusercontent.com/K-yzu/Logos/main/TV:UK/CNN.png",
                    "country": "GB",
                    "kind": "channel",
                },
            ],
            "https://raw.githubusercontent.com/K-yzu/Logos/main",
        )

    def test_country_specific_raw_link_is_preferred(self) -> None:
        self.assertEqual(
            self.index.find("CNN", "US"),
            "https://raw.githubusercontent.com/K-yzu/Logos/main/TV:US/CNN.png",
        )
        self.assertEqual(
            self.index.find("CNN", "GB"),
            "https://raw.githubusercontent.com/K-yzu/Logos/main/TV:UK/CNN.png",
        )

    def test_unknown_country_does_not_borrow_another_country_logo(self) -> None:
        self.assertEqual(self.index.find("CNN", "CA"), "")


if __name__ == "__main__":
    unittest.main()
