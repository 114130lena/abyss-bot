"""Candidate screening regression tests (no network or Discord calls)."""
import unittest
from datetime import datetime, timedelta, timezone
from hiyoriclip_video_screen import inspect

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

def post(id, age=1, *, video=True, mp4=True, views=300, likes=15, reposts=3):
    return {
        "id": str(id), "author": "@a", "url": "https://x.com/a/status/"+str(id),
        "created_at": (NOW-timedelta(hours=age)).isoformat(),
        "text": "#hiyoriclip_",
        "video_ids": ["vid-"+str(id)] if video else [],
        "video_urls": ["https://video.twimg.com/ext_tw_video/"+str(id)+".mp4"] if video and mp4 else [],
        "views":views,"likes":likes,"reposts":reposts,"discovered_via":["profile:a"]}

class ScreenTest(unittest.TestCase):
    def test_recent_video_with_public_mp4(self):
        result=inspect([post(1)],now=NOW)
        self.assertEqual(result["metrics"]["direct_mp4"],1)
        self.assertEqual(result["metrics"]["ranking_ready"],1)
        self.assertEqual(result["video_posts"],1)
    def test_missing_direct_url_marked_not_discarded(self):
        result=inspect([post(1,mp4=False)],now=NOW)
        self.assertEqual(result["metrics"]["needs_video_url"],1)
        self.assertEqual(result["video_posts"],1)
    def test_no_video_and_old_post_excluded(self):
        result=inspect([post(1,video=False),post(2,age=25)],now=NOW)
        self.assertEqual(result["video_posts"],0)
        self.assertEqual(result["metrics"]["no_video_metadata"],1)
        self.assertEqual(result["metrics"]["outside_window"],1)
    def test_missing_views_not_fabricated(self):
        result=inspect([post(1,views=None)],now=NOW)
        self.assertEqual(result["metrics"]["views_missing"],1)
        self.assertEqual(result["metrics"]["ranking_ready"],1)
        self.assertIsNone(result["sample_recent"][0]["views"])
    def test_missing_likes_not_ranking_ready(self):
        result=inspect([post(1,likes=None)],now=NOW)
        self.assertEqual(result["metrics"]["missing_core_metrics"],1)
        self.assertNotIn("ranking_ready", result["metrics"])
    def test_bad_mp4_host_rejected(self):
        item=post(1)
        item["video_urls"]=["https://evil.test/clip.mp4"]
        result=inspect([item],now=NOW)
        self.assertEqual(result["metrics"]["needs_video_url"],1)
    def test_duplicate_ids_deduplicated(self):
        result=inspect([post(1),post(1)],now=NOW)
        self.assertEqual(result["video_posts"],1)
        self.assertEqual(result["metrics"]["duplicate_post_ids"],1)

if __name__=="__main__":
    unittest.main()
