"""Offline checks for X repost routing, fanart permission, and media classification."""
import unittest
from unittest.mock import patch

import x_repost_notifier as repost
import fanart_url_media as fanart
import x_post_media as media


class RepostRoutingTests(unittest.TestCase):
    def test_repost_title_and_source_link(self):
        self.assertTrue(repost.is_repost("RT by HanayoHiyori: hello"))
        self.assertTrue(repost.is_repost_post({
            "rss_title": "regular", "source_link": "https://x.com/other/status/123"
        }, "HanayoHiyori"))
        self.assertFalse(repost.is_repost_post({
            "rss_title": "a new status", "source_link": "https://x.com/HanayoHiyori/status/123"
        }, "HanayoHiyori"))

    def test_thread_preserves_components_parameter(self):
        url = repost.thread_url("https://discord.com/api/webhooks/123/abc?with_components=true")
        self.assertIn("thread_id=1566509366591627344", url)
        self.assertIn("with_components=true", url)


class FanartPermissionTests(unittest.TestCase):
    def test_attributed_public_art_included_but_blocked_art_excluded(self):
        queue = {
            "a": {"group": "hanayo", "post_url": "https://x.com/artist/status/1",
                  "image_urls": ["https://pbs.twimg.com/media/a"], "review_status": "pending", "artist":"@artist", "hashtag":"#hiyoriart_"},
            "b": {"group": "hanayo", "post_url": "https://x.com/artist/status/2",
                  "image_urls": ["https://pbs.twimg.com/media/b"], "review_status": "blocked", "artist":"@artist", "hashtag":"#hiyoriart_"},
            "c": {"group": "hanayo", "post_url": "https://x.com/artist/status/3",
                  "image_urls": ["https://pbs.twimg.com/media/c"], "review_status": "pending", "artist":"@artist", "hashtag":"#hiyoriart_"},
        }
        with patch.object(fanart, "read", return_value=queue):
            pool = fanart.candidates("hanayo")
        self.assertEqual([p["post_url"] for p in pool],
                         ["https://x.com/artist/status/1", "https://x.com/artist/status/3"])

    def test_native_post_photo_suppresses_fanart(self):
        payload = {"components": [{"type": 17, "components": [
            {"type": 10, "content": "𝕏 X"},
            {"type": 12, "items": [{"media": {"url": "https://pbs.twimg.com/media/photo"}}]},
            {"type": 10, "content": "OFFICIAL LINKS"},
        ]}]}
        self.assertTrue(fanart.has_post_photos(payload))


class MediaTests(unittest.TestCase):
    def test_unknown_media_never_confirmed_text_only(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"tweet": {"media": {}}}
        with patch.object(media.requests, "get", return_value=Response()):
            photos, preview, video, known = media.resolve_media(
                {"id": "123", "images": []}, "HanayoHiyori")
        self.assertFalse(known)
        self.assertEqual(photos, [])
        self.assertFalse(video)


if __name__ == "__main__":
    unittest.main()
