"""Avoid replacing a manually selected guild banner from a 1-image pool."""
import unittest
from unittest.mock import patch
import guild_server_banner as banner

class BannerMinimumTests(unittest.TestCase):
    def test_wide_and_fit_candidates_are_both_available_when_only_one_is_wide(self):
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
                self.assertEqual({p[1]["server_aspect"] for p in pool}, {"wide", "fit"})
                _, _, total, _ = banner.rotation_selection()
                self.assertGreaterEqual(total, 2)

    def test_single_candidate_does_not_replace_guild_on_schedule(self):
        selected = ("image.jpg", {"sha256": "a", "artist": "@artist"})
        with patch.object(banner, "slot", return_value="2026-11-08 18:00"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection", return_value=(selected, ["a"], 1, 0)), \
             patch.object(banner, "change_guild_banner") as change:
            result = banner.rotate_once(manual=False)
        self.assertEqual(result["status"], "insufficient_candidates")
        change.assert_not_called()
