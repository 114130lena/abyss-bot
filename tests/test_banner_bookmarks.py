import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import banner_bookmarks as bookmarks
import banner_bookmark_interactions as interactions


class BannerBookmarkTests(unittest.TestCase):
    def test_dedup_and_public_catalog_omits_user_id(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bookmarks, "FILE", Path(directory) / "saved.json"):
                state = {"last_sha256": "a" * 64, "group": "hanayo",
                         "artist": "@Artist_1", "post_url": "https://x.com/Artist_1/status/123"}
                self.assertEqual(bookmarks.keep_current(state, "123")["status"], "saved")
                self.assertEqual(bookmarks.keep_current(state, "123")["status"], "already_saved")
                self.assertEqual(len(bookmarks.public_catalog()), 1)
                self.assertNotIn("saved_by", bookmarks.public_catalog()[0])

    def test_invalid_signature_is_rejected(self):
        with patch.dict("os.environ", {"DISCORD_APPLICATION_PUBLIC_KEY": "a" * 64}):
            status, _ = interactions.process(b'{"type":1}', "00" * 64, "1234")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
