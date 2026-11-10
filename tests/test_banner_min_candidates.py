"""Scheduled guild banner rotation resumes after a one-time manual edit."""
import unittest
from unittest.mock import patch
import guild_server_banner as banner

class BannerMinimumTests(unittest.TestCase):
    def test_hiyori_is_preferred_when_both_creators_have_unused_art(self):
        available = {
            "hiyori": ("h.jpg", {"sha256": "hiyori", "group": "hanayo",
                                  "banner_format": "square_fit", "vision_score": 60}),
            "huya": ("u.jpg", {"sha256": "huya", "group": "sakihane",
                               "banner_format": "16:9", "vision_score": 99}),
        }
        with patch.object(banner, "banner_inventory", return_value={
                "available": available, "total": 2, "unused": 2}), \
             patch("fanart_vision.enabled", return_value=True), \
             patch.object(banner.random, "random", return_value=0.5):
            selected, history, _, _ = banner.rotation_selection(state={})
        self.assertEqual(selected[1]["group"], "hanayo")
        self.assertIn("hiyori", history)

    def test_huya_retains_occasional_share(self):
        available = {
            "hiyori": ("h.jpg", {"sha256": "hiyori", "group": "hanayo",
                                  "banner_format": "16:9"}),
            "huya": ("u.jpg", {"sha256": "huya", "group": "sakihane",
                               "banner_format": "16:9"}),
        }
        with patch.object(banner, "banner_inventory", return_value={
                "available": available, "total": 2, "unused": 2}), \
             patch("fanart_vision.enabled", return_value=False), \
             patch.object(banner.random, "random", return_value=0.95):
            selected, _, _, _ = banner.rotation_selection(state={})
        self.assertEqual(selected[1]["group"], "sakihane")

    def test_huya_is_fallback_if_no_fresh_hiyori_remains(self):
        available = {
            "huya": ("u.jpg", {"sha256": "huya", "group": "sakihane",
                               "banner_format": "16:9"}),
        }
        with patch.object(banner, "banner_inventory", return_value={
                "available": available, "total": 1, "unused": 1}), \
             patch("fanart_vision.enabled", return_value=False), \
             patch.object(banner.random, "random") as chance:
            selected, _, _, _ = banner.rotation_selection(state={})
        self.assertEqual(selected[1]["group"], "sakihane")
        chance.assert_not_called()

    def test_square_art_can_fill_banner_without_crop_as_fallback(self):
        import json
        import tempfile
        from pathlib import Path
        from PIL import Image
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "creator_media"
            folder = base / "fanart_candidates" / "hanayo"
            folder.mkdir(parents=True)
            Image.new("RGB", (1920, 1080), "red").save(folder / "wide.jpg")
            Image.new("RGB", (900, 900), "blue").save(folder / "square.jpg")
            entries = {}
            for i, name in enumerate(("wide", "square")):
                entries["hanayo:" + str(i+12345)] = {
                    "group": "hanayo",
                    "post_id": str(i+12345),
                    "artist": "@known_artist",
                    "post_url": "https://x.com/known_artist/status/" + str(i+12345),
                    "hashtag": "#hiyoriart_",
                    "text": "히요리 #hiyoriart_",
                    "cached_files": ["fanart_candidates/hanayo/" + name + ".jpg"],
                }
            (base / "fanart_discovery.json").write_text(json.dumps(entries))
            with patch.dict("os.environ", {
                    "SERVER_BANNER_ALLOW_FIT": "1",
                    "SERVER_BANNER_USE_COLLECTED": "1",
                    "SERVER_BANNER_VISION_ENABLED": "0"}), \
                 patch.object(banner, "media_base", return_value=base), \
                 patch("fanart_discovery.QUEUE", base / "fanart_discovery.json"):
                pool = banner.collected_hanayo_art()
                self.assertEqual(len(pool), 2)
                self.assertEqual({p[1]["banner_format"] for p in pool},
                                 {"16:9", "square_fit"})
                selected, _, total, _ = banner.rotation_selection()
                self.assertEqual(total, 2)
                self.assertEqual(selected[1]["banner_format"], "16:9")
                image = banner.convert_banner(folder / "square.jpg")
                from io import BytesIO
                with Image.open(BytesIO(image)) as converted:
                    self.assertEqual(converted.size, (960, 540))

    def test_wider_horizontal_resolution_is_included_without_loosening_nonreuse(self):
        from pathlib import Path
        import json
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "creator_media"
            folder = base / "fanart_candidates" / "hanayo"
            folder.mkdir(parents=True)
            Image.new("RGB", (1200, 430), "blue").save(folder / "long.jpg")
            state = {"hanayo:54321": {
                "group": "hanayo", "hashtag": "#hiyoriart_",
                "artist": "@artist", "post_url": "https://x.com/artist/status/54321",
                "text": "#hiyoriart_",
                "cached_files": ["fanart_candidates/hanayo/long.jpg"],
            }}
            (base / "fanart_discovery.json").write_text(json.dumps(state))
            with patch.object(banner, "media_base", return_value=base), \
                 patch("fanart_discovery.QUEUE", base / "fanart_discovery.json"), \
                 patch.dict("os.environ", {"SERVER_BANNER_USE_COLLECTED": "1",
                                           "SERVER_BANNER_VISION_ENABLED": "0"}):
                pool = banner.collected_hanayo_art()
                self.assertEqual(len(pool), 1)
                self.assertEqual(pool[0][1]["banner_format"], "landscape_fit")
                available = banner.banner_inventory()
                self.assertEqual(available["unused"], 1)
                used = banner.banner_inventory(state={
                    "all_used_post_ids": ["54321"]})
                self.assertEqual(used["unused"], 0)

    def test_single_fresh_candidate_allowed_after_previous_automatic_rotation(self):
        selected = ("image.jpg", {"sha256": "fresh", "artist": "@artist",
                                  "post_url": "https://x.com/artist/status/123"})
        state = {"banner_change_method": "봇 자동 교체", "last_sha256": "old",
                 "guild_banner_hash": "old_hash"}
        with patch.object(banner, "slot", return_value="2026-11-10 08:00"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "rotation_selection",
                          return_value=(selected, ["old", "fresh"], 1, 0)), \
             patch.object(banner, "convert_banner", return_value=b"jpeg"), \
             patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "DISCORD_BOT_TOKEN": "token",
                                       "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "change_guild_banner", return_value="new_hash") as change, \
             patch.object(banner, "write_state"), \
             patch.object(banner, "sync_banner_info"):
            result = banner.rotate_once(manual=False,
                                        now=__import__("datetime").datetime(
                                            2026, 11, 10, 8, 0,
                                            tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Seoul")))
        self.assertEqual(result["status"], "changed")
        change.assert_called_once()

    def test_single_fresh_candidate_rotates_after_external_manual_change(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        selected = ("image.jpg", {"sha256": "new", "artist": "@artist",
                                  "post_url": "https://x.com/artist/status/5678"})
        state = {"banner_change_method": "외부 변경 감지",
                 "guild_banner_hash": "manually_set_hash", "last_sha256": "old"}
        with patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "rotation_selection",
                          return_value=(selected, ["old", "new"], 1, 0)), \
             patch.object(banner, "convert_banner", return_value=b"jpeg"), \
             patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "DISCORD_BOT_TOKEN": "token",
                                       "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "change_guild_banner", return_value="new_hash") as change, \
             patch.object(banner, "write_state") as save, \
             patch.object(banner, "sync_banner_info"):
            result = banner.rotate_once(
                now=datetime(2026, 11, 10, 18, 0,
                             tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(result["status"], "changed")
        change.assert_called_once()
        self.assertEqual(save.call_args.args[0]["last_slot"], "2026-11-10 18:00")
        self.assertEqual(save.call_args.args[0]["banner_change_method"], "봇 자동 교체")

    def test_used_candidate_still_does_not_rotate(self):
        with patch.object(banner, "slot", return_value="2026-11-10 18:00"), \
             patch.object(banner, "read_state", return_value={
                 "banner_change_method": "외부 변경 감지"}), \
             patch.object(banner, "rotation_selection",
                          return_value=(None, ["old"], 1, 0)), \
             patch.object(banner, "change_guild_banner") as change:
            result = banner.rotate_once(manual=False)
        self.assertEqual(result["status"], "exhausted_candidates")
        change.assert_not_called()
