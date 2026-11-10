"""Offline regression tests for low-latency X original/repost delivery."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import twitter_notifier as hi
import twitter_huya_notifier as huya


WEBHOOK = "https://discord.com/api/webhooks/123/token"


class FastFeedTests(unittest.TestCase):
    def test_fx_feed_starts_even_when_three_nitter_requests_wait(self):
        for watcher in (hi, huya):
            with self.subTest(watcher=watcher.__name__):
                fx_started = threading.Event()

                def slow_nitter(_url):
                    # Fx must not be queued behind all three slow Nitter feeds.
                    if not fx_started.wait(0.8):
                        raise AssertionError("FxTwitter was queued behind Nitter")
                    return []

                def fx_feed(_username, _common):
                    fx_started.set()
                    return []

                feed_attr = "ALL_FEEDS" if watcher is hi else "FEEDS"
                with patch.object(watcher, feed_attr, ["a", "b", "c"]), \
                     patch.object(watcher, "_fetch_posts", side_effect=slow_nitter), \
                     patch.object(watcher.feed_health, "active", return_value=True), \
                     patch.object(watcher.feed_health, "timed_fetch",
                                  side_effect=lambda func, url: func(url)), \
                     patch("x_fx_feed.fetch_creator_originals", side_effect=fx_feed):
                    result = (watcher._fetch_merged_posts(True) if watcher is hi
                              else watcher._fetch_merged_posts())
                self.assertEqual(result, [])
                self.assertTrue(fx_started.is_set())

    def test_callback_fires_before_slower_mirror_completes(self):
        for watcher in (hi, huya):
            with self.subTest(watcher=watcher.__name__):
                release = threading.Event()
                slow_done = threading.Event()
                callback_before_slow_done = []
                item = {"id": "100", "text": "hello", "source": "fast", "images": []}

                def get_feed(url):
                    if url == "slow":
                        release.wait(1.0)
                        slow_done.set()
                        return []
                    return [item]

                def callback(posts):
                    callback_before_slow_done.append(not slow_done.is_set())
                    self.assertEqual(posts, [item])
                    release.set()

                feed_attr = "ALL_FEEDS" if watcher is hi else "FEEDS"
                with patch.object(watcher, feed_attr, ["fast", "slow"]), \
                     patch.object(watcher, "_fetch_posts", side_effect=get_feed), \
                     patch.object(watcher.feed_health, "active",
                                  side_effect=lambda url: "fxtwitter" not in url), \
                     patch.object(watcher.feed_health, "timed_fetch",
                                  side_effect=lambda func, url: func(url)):
                    result = (watcher._fetch_merged_posts(True, on_batch=callback)
                              if watcher is hi else watcher._fetch_merged_posts(on_batch=callback))
                self.assertEqual([p["id"] for p in result], ["100"])
                self.assertEqual(callback_before_slow_done, [True])


class EarlyDeliveryTests(unittest.TestCase):
    def test_hanayo_original_delivered_in_callback_not_again_in_final_merge(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "x_repost_seen_posts.json").write_text("[]")
            post = {"id": "123", "text": "hello", "images": [], "rss_title": "hello",
                    "url": "https://x.com/HanayoHiyori/status/123",
                    "source_link": "https://nitter.gq/HanayoHiyori/status/123",
                    "source": "https://nitter.gq/HanayoHiyori/rss"}

            def fake_fetch(_include_backups=True, on_batch=None):
                self.assertIsNotNone(on_batch)
                on_batch([post])
                self.assertEqual(notify.call_count, 1)
                return [post]

            with patch.object(hi, "BASE_DIR", Path(root)), \
                 patch.object(hi, "_load_seen", return_value=set()), \
                 patch.object(hi, "_save_seen") as saved, \
                 patch.object(hi, "_notify") as notify, \
                 patch.object(hi, "_fetch_merged_posts", side_effect=fake_fetch), \
                 patch.object(hi, "WEBHOOK_URL", WEBHOOK), \
                 patch.object(hi, "next_check", 0.0), \
                 patch.object(hi, "failures", 0), \
                 patch("notice_intelligence.report_source"):
                hi.check_twitter()
            notify.assert_called_once()
            saved.assert_called_once_with({"123"})

    def test_huya_repost_delivered_once_before_merge_and_never_as_original(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "x_huya_repost_seen_posts.json"
            file.write_text("[]")
            post = {"id": "456", "text": "RT @artist: illustration",
                    "images": [], "rss_title": "RT @artist: illustration",
                    "url": "https://x.com/SakihaneHuya/status/456",
                    "source_link": "https://nitter.gq/artist/status/333",
                    "source": "https://nitter.gq/SakihaneHuya/rss"}

            def fake_fetch(on_batch=None):
                self.assertIsNotNone(on_batch)
                on_batch([post])
                self.assertEqual(repost.call_count, 1)
                return [post]

            with patch.object(huya, "BASE_DIR", Path(root)), \
                 patch.object(huya, "_load_seen", return_value=set()), \
                 patch.object(huya, "_notify") as original, \
                 patch.object(huya, "_fetch_merged_posts", side_effect=fake_fetch), \
                 patch.object(huya, "WEBHOOK_URL", WEBHOOK), \
                 patch.object(huya, "next_check", 0.0), \
                 patch.object(huya, "failures", 0), \
                 patch("x_repost_notifier.notify") as repost, \
                 patch("notice_intelligence.report_source"):
                huya.check_huya_x()
            repost.assert_called_once()
            original.assert_not_called()
            self.assertEqual(json.loads(file.read_text()), ["456"])

    def test_ambiguous_original_waits_for_complete_merge(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "x_repost_seen_posts.json").write_text("[]")
            post = {"id": "789", "text": "hello", "rss_title": "hello",
                    "images": [], "url": "https://x.com/HanayoHiyori/status/789",
                    "source": "https://nitter.gq/HanayoHiyori/rss"}

            def fake_fetch(_include_backups=True, on_batch=None):
                on_batch([post])
                notify.assert_not_called()
                return [post]

            with patch.object(hi, "BASE_DIR", Path(root)), \
                 patch.object(hi, "_load_seen", return_value=set()), \
                 patch.object(hi, "_save_seen"), \
                 patch.object(hi, "_notify") as notify, \
                 patch.object(hi, "_fetch_merged_posts", side_effect=fake_fetch), \
                 patch.object(hi, "WEBHOOK_URL", WEBHOOK), \
                 patch.object(hi, "next_check", 0.0), \
                 patch.object(hi, "failures", 0), \
                 patch("notice_intelligence.report_source"):
                hi.check_twitter()
            notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
