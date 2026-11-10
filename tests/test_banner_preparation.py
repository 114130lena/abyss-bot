"""Offline tests for ahead-of-time Discord guild banner preparation."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import guild_server_banner as banner

KST = ZoneInfo("Asia/Seoul")


class BannerPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch_root = patch.object(banner, "media_base", return_value=self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def test_prepare_window_starts_before_both_publish_times(self):
        for hour, minute, target in ((7, 44, None), (7, 45, 8),
                                     (17, 45, 18), (18, 0, None)):
            now = datetime(2026, 11, 11, hour, minute, tzinfo=KST)
            selected = banner._upcoming_banner_slot(now)
            self.assertEqual(selected.hour if selected else None, target)

    def test_prepared_image_is_applied_without_reencoding(self):
        now = datetime(2026, 11, 11, 7, 50, tzinfo=KST)
        meta = {"sha256": "fresh_sha", "artist": "@artist",
                "group": "hanayo",
                "post_url": "https://x.com/artist/status/123456",
                "banner_format": "16:9"}
        with patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection",
                          return_value=(("source.jpg", meta), ["fresh_sha"], 1, 0)), \
             patch.object(banner, "convert_banner", return_value=b"jpeg"):
            self.assertEqual(banner.prepare_next_banner(now)["status"], "prepared")
        with patch("fanart_discovery.read", return_value={}), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection") as selection, \
             patch.object(banner, "convert_banner") as converter, \
             patch.object(banner, "change_guild_banner", return_value="newhash") as apply, \
             patch.object(banner, "write_state"), \
             patch.object(banner, "sync_banner_info"), \
             patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "DISCORD_BOT_TOKEN": "token",
                                       "DISCORD_GUILD_ID": "123"}):
            result = banner.rotate_once(
                now=datetime(2026, 11, 11, 8, 0, tzinfo=KST))
        self.assertEqual(result["status"], "changed")
        selection.assert_not_called()
        converter.assert_not_called()
        apply.assert_called_once_with(b"jpeg", "token", "123")

    def test_prepared_image_cannot_reuse_notification_post(self):
        manifest, image = banner._prepared_banner_paths()
        image.write_bytes(b"jpg")
        manifest.write_text(json.dumps({
            "slot": "2026-11-11 08:00", "sha256": "fresh_sha",
            "post_url": "https://x.com/artist/status/123456",
            "jpeg_sha256": hashlib.sha256(b"jpg").hexdigest()}))
        with patch("fanart_discovery.read", return_value={
                "hanayo": {"https://x.com/artist/status/123456": "used"}}):
            self.assertIsNone(banner._load_prepared_banner("2026-11-11 08:00", {}))

    def test_manual_rotation_does_not_consume_prepared_slot(self):
        selected = ("source.jpg", {"sha256": "fresh_sha", "artist": "@artist",
                                   "post_url": "https://x.com/artist/status/123456"})
        with patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection",
                          return_value=(selected, ["fresh_sha"], 1, 0)), \
             patch.object(banner, "convert_banner", return_value=b"jpg"), \
             patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "DISCORD_BOT_TOKEN": "token",
                                       "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "change_guild_banner", return_value="newhash"), \
             patch.object(banner, "write_state") as saved, \
             patch.object(banner, "sync_banner_info"):
            result = banner.rotate_once(manual=True)
        self.assertEqual(result["status"], "changed")
        self.assertIsNone(saved.call_args.args[0].get("last_slot"))


if __name__ == "__main__":
    unittest.main()
