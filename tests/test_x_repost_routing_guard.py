"""Regression tests: no repost may reach ordinary broadcast webhook."""
import unittest
from unittest.mock import patch
import x_repost_notifier as repost
import twitter_notifier as hanayo
import twitter_huya_notifier as huya


class RepostRoutingSafetyTests(unittest.TestCase):
    def test_rss_repost_variants(self):
        for title in ("RT @someone: fan art", "Reposted by HanayoHiyori",
                      "Retweet by HanayoHiyori", "🔁 RT @someone"):
            with self.subTest(title=title):
                self.assertTrue(repost.is_repost(title))

    def test_off_author_status_is_repost_even_without_label(self):
        self.assertTrue(repost.is_repost_post(
            {"source_link": "https://nitter.example/other/status/123"}, "HanayoHiyori"
        ))

    def test_normal_notify_is_fail_closed_for_reposts(self):
        item = {"id": "123", "repost": True, "rss_title": "RT @other",
                "url": "https://x.com/HanayoHiyori/status/123", "text": "RT"}
        for notifier in (hanayo, huya):
            with self.subTest(notifier=notifier.__name__), \
                 patch.object(notifier, "send_webhook") as send:
                with self.assertRaises(ValueError):
                    notifier._notify(item)
                send.assert_not_called()

    def test_thread_webhook_overrides_parent_channel(self):
        url = repost.thread_url("https://discord.com/api/webhooks/123/token")
        self.assertIn("thread_id=1566509366591627344", url)


if __name__ == "__main__":
    unittest.main()
