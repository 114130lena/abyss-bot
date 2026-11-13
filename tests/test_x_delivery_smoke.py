"""Offline delivery-path checks. No real Discord webhook is used."""
import unittest
from unittest.mock import patch

import twitter_notifier
import twitter_huya_notifier
import x_repost_notifier


WEBHOOK = "https://discord.com/api/webhooks/123/token"

def gallery_urls(payload):
    return [
        item.get("media", {}).get("url", "")
        for box in payload.get("components", [])
        if box.get("type") == 17
        for block in box.get("components", [])
        if block.get("type") == 12
        for item in block.get("items", [])
    ]


class DeliverySmokeTests(unittest.TestCase):
    def test_repost_to_dedicated_thread_without_role_mentions(self):
        post = {
            "id": "123", "rss_title": "RT by streamer",
            "url": "https://x.com/HanayoHiyori/status/123",
            "source_link": "https://x.com/original/status/456",
            "text": "original photo", "images": [], "source": "test",
        }
        original = "https://pbs.twimg.com/media/original.jpg"
        with patch("x_post_media.resolve_media", return_value=([original], "", False, True)) as resolve, \
             patch.object(x_repost_notifier, "send_webhook") as sent, \
             patch.object(x_repost_notifier, "get_banner", return_value=""), \
             patch("x_repost_delivery_ledger.reserve", return_value=True), \
             patch("x_repost_delivery_ledger.mark_sent"):
            x_repost_notifier.notify("hanayo", post, WEBHOOK)
        self.assertEqual(resolve.call_args.args[1], "original")
        self.assertEqual(resolve.call_args.args[0]["id"], "456")
        destination, payload = sent.call_args.args
        self.assertIn("thread_id=1566509366591627344", destination)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertNotIn("<@&", str(payload))
        self.assertIn(original, gallery_urls(payload))
        self.assertIn("https://x.com/original/status/456", str(payload))

    def test_original_post_uses_normal_webhook_and_photo(self):
        post = {"id": "987", "url": "https://x.com/HanayoHiyori/status/987",
                "text": "photo update", "images": []}
        image = "https://pbs.twimg.com/media/post.jpg"
        with patch("x_post_media.resolve_media", return_value=([image], "", False, True)), \
             patch.object(twitter_notifier, "send_webhook") as sent, \
             patch.object(twitter_notifier, "get_banner", return_value=""):
            twitter_notifier._notify(post)
        url, payload = sent.call_args.args
        self.assertNotIn("thread_id=", url)
        self.assertIn(image, gallery_urls(payload))
        self.assertFalse(payload.get("_abyss_photo_unknown", False))

    def test_huya_video_keeps_original_watch_link(self):
        post = {"id": "777", "url": "https://x.com/SakihaneHuya/status/777",
                "text": "video update", "images": []}
        with patch("x_post_media.resolve_media",
                   return_value=([], "https://pbs.twimg.com/media/preview.jpg", True, True)), \
             patch.object(twitter_huya_notifier, "send_webhook") as sent, \
             patch.object(twitter_huya_notifier, "get_banner", return_value=""):
            twitter_huya_notifier._notify(post)
        url, payload = sent.call_args.args
        self.assertNotIn("thread_id=", url)
        self.assertTrue(payload["_abyss_has_video"])
        self.assertIn("원본 X 영상 보기", str(payload))
        self.assertIn("https://x.com/SakihaneHuya/status/777", str(payload))

    def test_unknown_x_media_never_authorizes_fanart(self):
        post = {"id": "222", "url": "https://x.com/HanayoHiyori/status/222",
                "text": "unknown attachments", "images": []}
        with patch("x_post_media.resolve_media", return_value=([], "", False, False)), \
             patch.object(twitter_notifier, "send_webhook") as sent, \
             patch.object(twitter_notifier, "get_banner", return_value=""):
            twitter_notifier._notify(post)
        payload = sent.call_args.args[1]
        self.assertTrue(payload["_abyss_photo_unknown"])
        self.assertNotIn("_abyss_verified_text_only", payload)


    def test_space_link_keeps_original_post_and_blocks_fanart_both_creators(self):
        text = ("[https://nitter.gq/t.co/iKf8fwZuyb]"
                "(https://x.com/HanayoHiyori/status/2121241811914195289)")
        for module in (twitter_notifier, twitter_huya_notifier):
            with self.subTest(module=module.__name__):
                post = {"id": "2121241811914195289", "text": text,
                        "url": "https://x.com/HanayoHiyori/status/2121241811914195289",
                        "images": [], "source": "bluebird-twscraper"}
                with patch("x_post_media.resolve_media",
                           return_value=([], "", False, True)), \
                     patch("x_post_media.describe_x_link_post",
                           return_value=("🎙️ X 스페이스 공유",
                               "🎙️ [스페이스 입장하기](https://x.com/i/spaces/1qKVmyYgzgLxB)")), \
                     patch.object(module, "send_webhook") as sent, \
                     patch.object(module, "get_banner", return_value=""):
                    module._notify(post)
                destination, payload = sent.call_args.args
                self.assertNotIn("thread_id=", destination)
                self.assertTrue(payload["_abyss_photo_unknown"])
                self.assertNotIn("_abyss_verified_text_only", payload)
                rendered = str(payload)
                self.assertIn("스페이스", rendered)
                self.assertIn("https://x.com/i/spaces/1qKVmyYgzgLxB", rendered)
                self.assertNotIn("nitter.gq", rendered)
                self.assertEqual(payload["_abyss_event_key"],
                    "x-original:" + module.USERNAME.lower() + ":" + post["id"])

    def test_unknown_tco_link_does_not_masquerade_as_plain_text(self):
        post = {"id": "2121241811914195289",
                "url": "https://x.com/HanayoHiyori/status/2121241811914195289",
                "text": "[https://nitter.gq/t.co/iKf8fwZuyb]"
                        "(https://x.com/HanayoHiyori/status/2121241811914195289)",
                "images": []}
        with patch("x_post_media.resolve_media", return_value=([], "", False, True)), \
             patch("x_post_media.requests.head",
                   side_effect=__import__("requests").Timeout), \
             patch.object(twitter_notifier, "send_webhook") as sent, \
             patch.object(twitter_notifier, "get_banner", return_value=""):
            twitter_notifier._notify(post)
        payload = sent.call_args.args[1]
        self.assertTrue(payload["_abyss_photo_unknown"])
        self.assertIn("X 링크 게시물", str(payload))
        self.assertNotIn("nitter.gq", str(payload))



if __name__ == "__main__":
    unittest.main()
