from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.epg.import_epgshare import match_channels, update_catalog
from scripts.lib.pipeline import write_json


class EpgMappingTests(unittest.TestCase):
    def test_source_id_is_preferred_and_dummy_ids_are_ignored(self) -> None:
        epg = [
            {"id": "CNN.HD.us2", "names": [("en", "CNN")]},
            {"id": "CNN.Dummy.us2", "names": [("en", "CNN")]},
        ]
        catalog = {
            "version": 1,
            "channels": {
                "cnn": {
                    "name": "CNN",
                    "aliases": ["CNN"],
                    "countries": ["US"],
                    "source_refs": {"core": ["CNN.HD.us2"]},
                },
                "local-cnn": {
                    "name": "Local CNN",
                    "aliases": ["Local CNN"],
                    "countries": ["US"],
                    "source_refs": {"core": ["Local.CNN.us_locals1"]},
                },
            },
        }
        matches, stats = match_channels(epg, catalog)
        self.assertEqual(matches["cnn"]["epg_id"], "CNN.HD.us2")
        self.assertEqual(matches["cnn"]["method"], "source-id")
        self.assertNotIn("local-cnn", matches)
        self.assertEqual(stats["matched"], 1)

    def test_catalog_update_sets_and_clears_epg_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "channels.json"
            write_json(
                path,
                {
                    "version": 1,
                    "channels": {
                        "cnn": {"name": "CNN", "epg_id": "old"},
                        "other": {"name": "Other", "epg_id": "keep"},
                    },
                },
            )
            update_catalog(path, {"cnn": {"epg_id": "CNN.HD.us2", "method": "source-id"}}, {"other": "manual"})
            import json

            records = json.loads(path.read_text())["channels"]
            self.assertEqual(records["cnn"]["epg_id"], "CNN.HD.us2")
            self.assertEqual(records["other"]["epg_id"], "manual")


if __name__ == "__main__":
    unittest.main()
