"""Offline reference dataset tests; no model, network, or Discord calls."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fanart_reference_dataset as dataset


class ReferenceDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / "fanart_candidates" / "hanayo"
        folder.mkdir(parents=True)
        (folder / "first.jpg").write_bytes(b"image1")
        (folder / "second.jpg").write_bytes(b"image2")
        queue = {
            "hanayo:123": {
                "post_id": "123", "group": "hanayo",
                "post_url": "https://x.com/artist/status/123",
                "cached_files": [
                    "fanart_candidates/hanayo/first.jpg",
                    "fanart_candidates/hanayo/second.jpg"],
            }
        }
        (self.root / "fanart_discovery.json").write_text(json.dumps(queue))

    def test_each_post_image_is_independent_and_unverified(self):
        result = dataset.build(self.root)
        self.assertEqual(result["scanned"], 2)
        records = json.loads((self.root / "fanart_reference_dataset.json").read_text())["records"]
        self.assertEqual(set(records), {"123:0", "123:1"})
        self.assertTrue(all(x["label"] == "uncertain" for x in records.values()))

    def test_labels_survive_rebuild_only_for_unchanged_image(self):
        dataset.build(self.root)
        dataset.label("123", 0, "hanayo", self.root)
        dataset.label("123", 1, "other", self.root)
        self.assertEqual(dataset.summary(self.root)["hanayo"], 1)
        image = self.root / "fanart_candidates" / "hanayo" / "second.jpg"
        image.write_bytes(b"image-replaced")
        dataset.build(self.root)
        records = json.loads((self.root / "fanart_reference_dataset.json").read_text())["records"]
        self.assertEqual(records["123:0"]["label"], "hanayo")
        self.assertEqual(records["123:1"]["label"], "uncertain")
        with self.assertRaises(ValueError):
            dataset.label("123", 5, "other", self.root)

    def test_rejects_path_escape_and_unindexed_posts(self):
        record = json.loads((self.root / "fanart_discovery.json").read_text())
        record["hanayo:123"]["cached_files"] += ["../../secret.jpg"]
        (self.root / "fanart_discovery.json").write_text(json.dumps(record))
        self.assertEqual(dataset.build(self.root)["scanned"], 2)
        with self.assertRaises(ValueError):
            dataset.label("999", 0, "hanayo", self.root)


if __name__ == "__main__":
    unittest.main()
