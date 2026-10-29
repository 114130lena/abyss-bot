"""Offline regression tests: never post to a live Discord webhook."""
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

import creator_groups
import main
import youtube_notifier as yt
import cafe_notifier as cafe
import twitter_notifier as twitter
import banner_manager as banners
import chzzk_replay_notifier as replay
from discord_v2 import COLORS

class RoutingTests(unittest.TestCase):
    def test_creator_roles(self):
        self.assertEqual(creator_groups.role_for("hanayo"), "1563499860135772211")
        self.assertEqual(creator_groups.role_for("sakihane"), "1564279305171640361")
        self.assertEqual(creator_groups.youtube_group("UCHAr9pWGS30akL4MQCQT4uQ"), "hanayo")
        self.assertEqual(creator_groups.youtube_group("UC0YQnenKBCu5sGb7H61n6HA"), "sakihane")
        self.assertEqual(creator_groups.youtube_group("test-replay", "test-replay"), "hanayo")
        self.assertEqual(creator_groups.cafe_group("431"), "hanayo")
        self.assertEqual(creator_groups.cafe_group("382"), "sakihane")
        self.assertEqual(creator_groups.x_group("HanayoHiyori"), "hanayo")

    def test_youtube_alert_has_top_banner_thumbnail_and_role(self):
        video = {"id": "abc123", "title": "테스트 영상", "url": "https://youtube.com/watch?v=abc123",
                 "channel": "하나요", "channel_id": "UCHAr9pWGS30akL4MQCQT4uQ", "published": ""}
        with patch.object(yt, "get_banner", return_value="https://yt3.googleusercontent.com/test"), \
             patch.object(yt, "send_webhook") as sent:
            yt._notify(video)
        payload = sent.call_args.args[1]
        self.assertEqual(payload["flags"], 32768)
        self.assertIn("1563499860135772211", payload["components"][0]["content"])
        parts = payload["components"][1]["components"]
        self.assertIn("test", parts[0]["items"][0]["media"]["url"])
        self.assertIn("hqdefault.jpg", parts[2]["items"][0]["media"]["url"])

    def test_chzzk_alert_has_top_banner_thumbnail_role(self):
        live = {"name": "사키하네 후야", "channel_id": "36ddb9bb4f17593b60f1b63cec86611d",
                "title": "테스트 방송", "url": "https://chzzk.naver.com/live/test",
                "category": "게임", "thumbnail": "https://example.com/live.png", "avatar": "",
                "open_date": "2026-10-29"}
        with patch.object(main, "get_banner", return_value="https://yt3.googleusercontent.com/test"), \
             patch.object(main, "send_webhook") as sent:
            main.send_discord_notification(live)
        payload = sent.call_args.args[1]
        self.assertEqual(payload["flags"], 32768)
        self.assertIn("1564279305171640361", payload["components"][0]["content"])
        self.assertEqual(payload["components"][1]["accent_color"], COLORS["chzzk"])
        self.assertEqual(payload["components"][1]["components"][2]["items"][0]["media"]["url"], live["thumbnail"])

    def test_cafe_and_x_roles(self):
        post = {"title": "테스트", "url": "https://cafe.naver.com/123", "summary": "",
                "author": "테스터"}
        with patch.object(cafe, "get_banner", return_value="https://yt3.googleusercontent.com/banner"), \
             patch.object(cafe, "send_webhook") as sent:
            cafe.notify(post, None, "382", "sakihane")
        self.assertIn("1564279305171640361", sent.call_args.args[1]["components"][0]["content"])
        with patch.object(twitter, "get_banner", return_value="https://yt3.googleusercontent.com/banner"), \
             patch.object(twitter, "send_webhook") as sent:
            twitter._notify({"text": "테스트", "url": "https://x.com/a/status/1"})
        self.assertIn("1563499860135772211", sent.call_args.args[1]["components"][0]["content"])

    def test_youtube_failure_does_not_block_other_channel(self):
        channels = ["bad", "good"]
        def fake_fetch(ch):
            if ch == "bad":
                raise ValueError("temporary RSS outage")
            return [{"id": "new", "title": "test", "url": "https://youtube.com/watch?v=new",
                     "channel": "good", "channel_id": "good", "published": ""}]
        with patch.object(yt, "_load_seen", return_value={"bad": [], "good": []}), \
             patch.object(yt, "_fetch_uploads", side_effect=fake_fetch), \
             patch.object(yt, "_notify") as sent, \
             patch.object(yt, "_save_seen"), \
             patch.object(yt, "warn_failure"):
            yt._check_youtube_channels(channels)
        self.assertEqual(sent.call_count, 1)

    def test_youtube_rss_accepts_id_without_uc_prefix(self):
        xml = (b'<feed xmlns="http://www.w3.org/2005/Atom" '
               b'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
               b'<title>Test Channel</title>'
               b'<yt:channelId>0YQnenKBCu5sGb7H61n6HA</yt:channelId>'
               b'</feed>')
        from unittest.mock import Mock
        response = Mock()
        response.content = xml
        response.raise_for_status.return_value = None
        with patch.object(yt.session, "get", return_value=response):
            result = yt._fetch_uploads("UC0YQnenKBCu5sGb7H61n6HA")
        self.assertEqual(result, [])

    def test_youtube_rss_rejects_unrelated_channel(self):
        xml = (b'<feed xmlns="http://www.w3.org/2005/Atom" '
               b'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
               b'<yt:channelId>some-other-channel</yt:channelId>'
               b'</feed>')
        from unittest.mock import Mock
        response = Mock()
        response.content = xml
        response.raise_for_status.return_value = None
        with patch.object(yt.session, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "channel mismatch"):
                yt._fetch_uploads("UC0YQnenKBCu5sGb7H61n6HA")

    def test_replay_posts_to_thread_without_mention(self):
        item = {"id": "12345", "title": "새 다시보기",
                "thumbnail": "https://example.com/thumbnail.jpg",
                "url": "https://chzzk.naver.com/video/12345"}
        with patch.object(replay, "get_banner", return_value="https://yt3.googleusercontent.com/banner"), \
             patch.object(replay, "send_webhook") as sent:
            replay.notify(item)
        url, message = sent.call_args.args
        self.assertIn("thread_id=1565293200225206342", url)
        self.assertEqual(message["flags"], 32768)
        self.assertEqual(message["allowed_mentions"], {"parse": []})
        self.assertNotIn("1563499860135772211", str(message))
        self.assertIn("12345", str(message))

    def test_replay_only_accepts_replay_type(self):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {"code": 200, "content": {"data": [
            {"videoNo": 123, "videoType": "CLIP", "videoTitle": "클립"},
            {"videoNo": 456, "videoType": "REPLAY", "videoTitle": "다시보기"}
        ]}}
        with patch.object(replay._SESSION, "get", return_value=response):
            items = replay.fetch_replays("test-channel")
        self.assertEqual([v["id"] for v in items], ["456"])

    def test_banner_url_validation(self):
        self.assertTrue(banners._valid("https://yt3.googleusercontent.com/image"))
        self.assertFalse(banners._valid("https://evil.example.com/image"))

if __name__ == "__main__":
    unittest.main()
