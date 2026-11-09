"""Media visibility regression tests for X reposts and unified platform cards."""
import unittest
from unittest.mock import Mock, patch

import discord_v2
import x_repost_notifier
import cafe_notifier


def galleries(payload):
    return [part for c in payload.get("components", []) for part in c.get("components", [])
            if part.get("type") == 12]


class MediaVisibilityTests(unittest.TestCase):
    def test_repost_uses_original_author_and_photo_in_thread(self):
        post = {
            "id": "123456789", "rss_title": "Reposted by HanayoHiyori",
            "source_link": "https://nitter.example/Artist/status/123456789",
            "url": "https://x.com/HanayoHiyori/status/123456789",
            "text": "사진", "images": []}
        with patch("x_post_media.resolve_media", return_value=(
                    ["https://pbs.twimg.com/media/repost.jpg"], "", False, True)) as resolver, \
             patch.object(x_repost_notifier, "send_webhook") as send, \
             patch.object(x_repost_notifier, "get_banner", return_value=""), \
             patch.object(x_repost_notifier, "avatar", return_value=""), \
             patch.object(x_repost_notifier, "handle", return_value="@HanayoHiyori"), \
             patch("x_repost_delivery_ledger.reserve", return_value=True), \
             patch("x_repost_delivery_ledger.mark_sent"):
            x_repost_notifier.notify("hanayo", post, "https://discord.com/api/webhooks/123/token")
        self.assertEqual(resolver.call_args.args[1], "Artist")
        webhook, payload = send.call_args.args
        self.assertIn("thread_id=1566509366591627344", webhook)
        self.assertIn("https://pbs.twimg.com/media/repost.jpg",
                      str(galleries(payload)))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_photo_cards_for_x_and_cafe(self):
        photo = "https://pbs.twimg.com/media/original.jpg"
        for kind in ("x", "cafe"):
            with self.subTest(kind=kind):
                card = discord_v2.alert(kind, "하나요 히요리",
                                        images=[photo], url="https://example.com/post")
                self.assertIn(photo, str(galleries(card)))

    def test_youtube_thumbnail_and_chzzk_live_thumbnail(self):
        for platform in ("youtube", "chzzk"):
            with self.subTest(platform=platform):
                card = discord_v2.alert(
                    platform, "하나요 히요리", media_url="https://i.ytimg.com/vi/example/hqdefault.jpg",
                    url="https://example.com/video")
                self.assertIn("hqdefault.jpg", str(galleries(card)))

    def test_cafe_parser_preserves_multiple_original_photos(self):
        parser = cafe_notifier._CafeHtmlParser()
        parser.feed('<div><img src="https://example.com/first.jpg">'
                    '<img data-src="https://example.com/second.jpg"></div>')
        self.assertEqual(len(parser.images), 2)


if __name__ == "__main__":
    unittest.main()
