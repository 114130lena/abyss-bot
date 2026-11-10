"""Offline regression for wide-only Huya guild banners and global nonreuse."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import fanart_discovery
import fanart_relevance
import fanart_usage
import fanart_public_stats
import guild_server_banner as banner


class HuyaGuildBannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "creator_media"
        self.root = self.base / "fanart_candidates" / "sakihane"
        self.root.mkdir(parents=True)
        self.queue = {}
        self.add("101", (1600, 900), "#huyart", "wide")
        self.add("102", (1400, 420), "후야 팬아트 #huyart", "panorama")
        self.add("103", (900, 900), "#huyart", "square")
        self.add("104", (1600, 900), "#huyart #hiyoriart_", "mixed")
        self.add("105", (1600, 900), "풍경 그림 #huyart", "background")
        self.add("106", (1600, 900), "RT by @reader: 후야 #huyart", "repost")
        self.add("107", (1600, 900), "사용 금지 #huyart", "blocked")
        self.qpath = self.base / "fanart_discovery.json"
        self.qpath.write_text(json.dumps(self.queue), encoding="utf-8")
        self.patches = [
            patch.object(fanart_discovery, "QUEUE", self.qpath),
            patch.object(fanart_discovery, "BASE", self.base),
            patch.object(fanart_usage, "BASE", self.base),
            patch.object(fanart_public_stats, "BASE", self.base),
            patch.object(fanart_public_stats, "QUEUE", self.qpath),
            patch.object(banner, "media_base", return_value=self.base),
            patch.dict("os.environ", {"SERVER_BANNER_USE_COLLECTED": "1",
                                      "SERVER_BANNER_VISION_ENABLED": "0"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def add(self, post_id, size, caption, basename):
        path = self.root / (basename + ".jpg")
        Image.new("RGB", size, color=(int(post_id), 70, 80)).save(path)
        self.queue["sakihane:" + post_id] = {
            "group": "sakihane", "hashtag": "#huyart",
            "post_url": "https://x.com/artist/status/" + post_id,
            "artist": "@artist", "text": caption,
            "cached_files": ["fanart_candidates/sakihane/" + basename + ".jpg"]}

    def test_huya_is_wide_only_and_uses_correct_group(self):
        pool = banner.collected_huya_art()
        self.assertEqual(len(pool), 2)
        self.assertEqual({x[1]["post_url"].rsplit("/", 1)[-1] for x in pool},
                         {"101", "102"})
        self.assertEqual({x[1]["group"] for x in pool}, {"sakihane"})
        self.assertEqual({x[1]["banner_format"] for x in pool},
                         {"16:9", "landscape_fit"})

    def test_explicit_blocked_art_and_wrong_group_do_not_enter(self):
        self.assertTrue(fanart_relevance.eligible_huya_banner(
            {"text": "#huyart"}))
        self.assertFalse(fanart_relevance.eligible_huya_banner(
            {"text": "RT by @any: #huyart"}))
        self.assertFalse(fanart_relevance.eligible_huya_banner(
            {"text": "#huyart #hiyoriart_"}))
        self.assertFalse(fanart_relevance.eligible_huya_banner(
            {"text": "풍경 일러스트 #huyart"}))
        self.assertFalse(fanart_relevance.eligible_huya_banner(
            {"text": "후야 #huyart", "server_banner_excluded": True}))

    def test_huya_and_hiyori_share_banner_use_history(self):
        with patch.object(banner, "approved_hanayo_art", return_value=[]), \
             patch.object(banner, "collected_hanayo_art", return_value=[]):
            pool = banner.banner_inventory(state={})
            self.assertEqual(pool["total_by_group"]["sakihane"], 2)
            self.assertEqual(pool["unused_by_group"]["sakihane"], 2)
            selected, _history, count, _remaining = banner.rotation_selection(state={})
            self.assertEqual(count, 2)
            self.assertEqual(selected[1]["group"], "sakihane")
            pool = banner.banner_inventory(state={
                "all_used_post_ids": ["101"],
                "all_used_sha256": [selected[1]["sha256"]]})
            self.assertEqual(pool["unused"], 0 if selected[1]["post_url"].endswith("/102") else 1)
        # Future eligible guild artwork remains available to notifications;
        # only posts actually used for guild banners are prohibited there.
        self.assertEqual(fanart_usage.server_reserved_post_ids("sakihane"),
                         {"101", "102"})
        self.assertTrue(fanart_usage.notification_allowed(
            "sakihane", self.queue["sakihane:101"]))
        self.assertTrue(fanart_usage.notification_allowed(
            "sakihane", self.queue["sakihane:102"]))

    def test_prior_huya_banner_post_stays_excluded_after_restart(self):
        (self.base / "guild_banner_rotation_state.json").write_text(
            json.dumps({"last_sha256": "old-huya-sha",
                        "rotation_used": ["old-huya-sha"],
                        "all_used_post_ids": ["101"]}), encoding="utf-8")
        hashes, posts = fanart_usage.excluded_notification_art("sakihane")
        self.assertIn("old-huya-sha", hashes)
        self.assertIn("101", posts)
        self.assertFalse(fanart_usage.notification_allowed(
            "sakihane", self.queue["sakihane:101"]))

    def test_info_card_labels_huya_and_separate_group_counts(self):
        report = {"available": True, "server_banner_images": 3,
                  "server_banner_total": 5,
                  "server_banner_by_group": {"hanayo": 1, "sakihane": 2},
                  "groups": {
                      "hanayo": {"posts": 10, "cached_images": 20,
                                 "notification_posts": 3},
                      "sakihane": {"posts": 11, "cached_images": 22,
                                   "notification_posts": 4}},
                  "checked_at": "", "last_new_post_at": ""}
        card = banner._banner_card("123", "bannerhash", {
            "group": "sakihane", "artist": "@artist",
            "post_url": "https://x.com/artist/status/101",
            "banner_change_method": "봇 자동 교체",
            "banner_changed_at": "2026-11-09 18:00 KST"}, fanart_stats=report)
        text = "\n".join(
            str(x.get("content", "")) for x in card["components"][0]["components"])
        self.assertIn("SAKIHANE HUYA", text)
        self.assertIn("히요리 1장 · 후야 2장", text)
        self.assertEqual(card["flags"], 32768)


if __name__ == "__main__":
    unittest.main()
