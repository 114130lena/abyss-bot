"""Offline checks for the restored cached guild banner and safe weekly cleanup."""
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import fanart_discovery
import fanart_usage
import fanart_url_media
import fanart_weekly_cleanup
import guild_server_banner


class BannerAndCacheRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "creator_media"
        self.base.mkdir(parents=True)
        self.cached = self.base / "fanart_candidates" / "hanayo" / "source.jpg"
        self.cached.parent.mkdir(parents=True)
        Image.new("RGB", (1920, 1080), "white").save(self.cached)
        self.digest = hashlib.sha256(self.cached.read_bytes()).hexdigest()
        self.source = "https://x.com/artist/status/12345"
        self.queue = {"hanayo:12345": {
            "group": "hanayo", "hashtag": "#hiyoriart_", "artist": "@artist",
            "post_url": self.source, "review_status": "pending",
            "decision": "unknown", "text": "히요리 일러스트 #hiyoriart_", "image_urls": ["https://pbs.twimg.com/media/pic.jpg"],
            "cached_files": ["fanart_candidates/hanayo/source.jpg"]}}
        self.save_queue()
        self.patches = [
            patch.object(fanart_discovery, "BASE", self.base),
            patch.object(fanart_discovery, "QUEUE", self.base / "fanart_discovery.json"),
            patch.object(guild_server_banner, "media_base", return_value=self.base),
            patch.object(fanart_usage, "BASE", self.base),
            patch.object(fanart_url_media, "BASE", self.base),
            patch.object(fanart_url_media, "QUEUE", self.base / "fanart_discovery.json"),
            patch.object(fanart_weekly_cleanup, "BASE", self.base),
            patch.object(fanart_weekly_cleanup, "CACHE", (self.base / "fanart_candidates").resolve()),
            patch.object(fanart_weekly_cleanup, "QUEUE", self.base / "fanart_discovery.json")
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def save_queue(self):
        (self.base / "fanart_discovery.json").write_text(json.dumps(self.queue), encoding="utf-8")

    def test_cached_banner_candidate_rotates_and_stays_out_of_notification(self):
        with patch.dict(os.environ, {"SERVER_BANNER_USE_COLLECTED": "1",
                                     "SERVER_BANNER_ENABLED": "1",
                                     "DISCORD_BOT_TOKEN": "test-token",
                                     "DISCORD_GUILD_ID": "12345"}), \
             patch.object(guild_server_banner, "change_guild_banner") as change:
            pool = guild_server_banner.collected_hanayo_art()
            self.assertEqual(len(pool), 1)
            self.assertEqual(pool[0][1]["post_url"], self.source)
            result = guild_server_banner.rotate_once(manual=True)
            self.assertEqual(result["status"], "changed")
            change.assert_called_once()
        state = guild_server_banner.read_state()
        self.assertIn("12345", state["all_used_post_ids"])
        self.assertFalse(fanart_usage.notification_allowed("hanayo", self.queue["hanayo:12345"]))
        self.assertEqual(fanart_url_media.candidates("hanayo"), [])

    def test_future_server_banner_candidates_remain_available_to_notifications(self):
        # Prospective guild art cannot monopolize the entire notification pool.
        self.assertEqual(fanart_usage.server_reserved_post_ids("hanayo"), {"12345"})
        self.assertTrue(fanart_usage.notification_allowed("hanayo", self.queue["hanayo:12345"]))
        self.assertEqual(len(fanart_url_media.candidates("hanayo")), 1)

    def test_notification_used_art_cannot_later_become_guild_banner(self):
        used = {"hanayo": {self.source: "2026-11-10T00:00:00+00:00"}}
        (self.base / "fanart_url_used.json").write_text(
            json.dumps(used), encoding="utf-8")
        with patch.dict(os.environ, {"SERVER_BANNER_USE_COLLECTED": "1"}):
            inventory = guild_server_banner.banner_inventory(state={})
        self.assertEqual(inventory["unused"], 0)

    def test_notification_fanart_keeps_artist_credit_and_only_source_button(self):
        from discord_v2 import alert
        with patch.object(fanart_usage, "server_reserved_post_ids", return_value=set()):
            card = alert("chzzk", "하나요 히요리", "https://example.com/banner.jpg",
                         title="Test", url="https://chzzk.naver.com/live/123",
                         avatar="https://example.com/avatar.jpg", ping=False)
            changed, chosen = fanart_url_media.apply(card)
        self.assertIsNotNone(chosen)
        parts = next(x["components"] for x in changed["components"] if x["type"] == 17)
        credit = next(x["content"] for x in parts if x.get("type") == 10 and "FAN ART" in x.get("content", ""))
        self.assertIn("출처: @artist", credit)
        self.assertNotIn("원본 X 게시물", credit)
        row = next(x for x in parts if x.get("type") == 1)
        self.assertEqual([b["label"] for b in row["components"]],
                         ["원본 게시물", "CHZZK", "CAFE", "X", "YouTube"])

    def test_prohibited_cached_art_is_never_used_as_guild_banner(self):
        self.queue["hanayo:12345"]["text"] = "사용 금지"
        self.save_queue()
        with patch.dict(os.environ, {"SERVER_BANNER_USE_COLLECTED": "1"}):
            self.assertEqual(guild_server_banner.collected_hanayo_art(), [])

    def test_group_art_or_retweet_with_hiyori_tag_not_server_eligible(self):
        import fanart_relevance
        multi = {"text": "RT by @artist: #toart_ #Rangart_ #hiyoriart_ #maryart_ 어비스 첫 오리지널곡 발매 축하해!"}
        self.assertFalse(fanart_relevance.eligible_hanayo_banner(multi))
        self.assertFalse(fanart_relevance.eligible_hanayo_banner(
            {"text": "#hiyoriart_ 배경 그림"}))
        self.assertTrue(fanart_relevance.eligible_hanayo_banner(
            {"text": "#hiyoriart_"}))
        self.assertTrue(fanart_relevance.eligible_hanayo_banner(
            {"text": "오늘의 낙서 #hiyoriart_"}))
        self.assertFalse(fanart_relevance.eligible_hanayo_banner(
            {"text": "히요리 팬아트 #huyart"}))
        self.assertTrue(fanart_relevance.eligible_hanayo_banner(
            {"text": "히요리 팬아트 #hiyoriart_"}))
        self.assertFalse(fanart_relevance.eligible_hanayo_banner(
            {"text": "히요리 팬아트 #hiyoriart_", "server_banner_excluded": True}))

    def test_tag_only_horizontal_cached_post_enters_guild_pool(self):
        self.queue["hanayo:12345"]["text"] = "#hiyoriart_"
        self.save_queue()
        with patch.dict(os.environ, {"SERVER_BANNER_USE_COLLECTED": "1",
                                     "SERVER_BANNER_VISION_ENABLED": "0"}):
            self.assertEqual(len(guild_server_banner.collected_hanayo_art()), 1)

    def test_non_hiyori_caption_remains_notification_candidate_not_reserved(self):
        self.queue["hanayo:12345"]["text"] = "풍경 일러스트 #hiyoriart_"
        self.save_queue()
        with patch.dict(os.environ, {"SERVER_BANNER_USE_COLLECTED": "1"}):
            self.assertEqual(guild_server_banner.collected_hanayo_art(), [])
        self.assertTrue(fanart_usage.notification_allowed("hanayo", self.queue["hanayo:12345"]))

    def test_weekly_cleanup_deletes_only_old_unreferenced_copies(self):
        orphan = self.cached.parent / "orphan.jpg"
        Image.new("RGB", (600, 400)).save(orphan)
        old = time.time() - 10 * 86400
        os.utime(self.cached, (old, old))
        os.utime(orphan, (old, old))
        result = fanart_weekly_cleanup.cleanup(now=time.time())
        self.assertEqual(result["deleted_or_would_delete"], 1)
        self.assertTrue(self.cached.is_file())
        self.assertFalse(orphan.exists())
        self.assertIn("hanayo:12345", json.loads((self.base / "fanart_discovery.json").read_text()))

    def test_legacy_cache_retired_does_not_block_public_x_preview(self):
        self.queue["hanayo:12345"]["cache_retired"] = True
        self.save_queue()
        # Isolate the legacy retirement behavior from the separate
        # rule reserving landscape art for the Discord guild banner.
        with patch.object(fanart_usage, "server_reserved_post_ids", return_value=set()):
            entries = fanart_url_media.candidates("hanayo")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["post_url"], self.source)


if __name__ == "__main__":
    unittest.main()
