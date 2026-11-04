"""Offline tests for isolated Hanayo guild banner feature."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
import guild_server_banner as mod


class BannerTests(unittest.TestCase):
    def test_schedule(self):
        zone = ZoneInfo("Asia/Seoul")
        self.assertEqual(mod.slot(datetime(2026, 11, 4, 8, 0, tzinfo=zone)), "2026-11-04 08:00")
        self.assertEqual(mod.slot(datetime(2026, 11, 4, 18, 0, tzinfo=zone)), "2026-11-04 18:00")
        self.assertIsNone(mod.slot(datetime(2026, 11, 4, 17, 0, tzinfo=zone)))

    def test_ratio(self):
        self.assertEqual(mod.kind(1920, 1080), "16:9")
        self.assertEqual(mod.kind(1920, 1200), "16:10")
        self.assertIsNone(mod.kind(1080, 1080))

    def test_index_filters_other_groups_and_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "creator_media"
            root = base / "fanart" / "hanayo"
            root.mkdir(parents=True)
            good = root / "a.jpg"
            Image.new("RGB", (1920, 1200), "white").save(good)
            digest = hashlib.sha256(good.read_bytes()).hexdigest()
            approved = {"approved": True, "permission_confirmed": True, "hashtag": "#hiyoriart_",
                        "sha256": digest, "original_path": "fanart/hanayo/a.jpg",
                        "artist": "@artist", "post_url": "https://x.com/artist/status/123"}
            data = {"hanayo": [approved, {**approved, "approved": False},
                               {**approved, "original_path": "../other/a.jpg"}],
                    "sakihane": [approved]}
            (base / "fanart_index.json").write_text(json.dumps(data), encoding="utf-8")
            with patch.object(mod, "media_base", return_value=base):
                chosen = mod.approved_hanayo_art()
                self.assertEqual(len(chosen), 1)
                self.assertEqual(chosen[0][0], good.resolve())
                self.assertEqual(Image.open(__import__("io").BytesIO(mod.convert_banner(good))).size,
                                 (960, 540))

    def test_dry_run_never_calls_discord(self):
        with patch.object(mod, "select_art", return_value=None), \
             patch.object(mod, "change_guild_banner") as api:
            self.assertEqual(mod.rotate_once(manual=True, dry_run=True)["status"],
                             "no_approved_hanayo_art")
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
