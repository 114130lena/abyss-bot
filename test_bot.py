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
from discord_v2 import COLORS, alert as unified_alert

class RoutingTests(unittest.TestCase):
    def test_x_rss_description_preserves_paragraphs(self):
        import xml.etree.ElementTree as ET
        item = ET.fromstring(
            "<item><title>collapsed text</title>"
            "<description><![CDATA[첫 문장<br>둘째 문장<br><br>새 문단"
            "<p>다음 문단</p><img src='https://example.org/photo.jpg'>]]></description>"
            "</item>"
        )
        body = twitter._post_body(item)
        self.assertIn("첫 문장\n둘째 문장\n\n새 문단", body)
        self.assertIn("다음 문단", body)
        self.assertNotIn("<img", body)
        self.assertNotIn("\n\n\n\n", body)

    def test_x_rss_description_falls_back_to_title(self):
        import xml.etree.ElementTree as ET
        item = ET.fromstring("<item><title>제목만 있음</title><description></description></item>")
        self.assertEqual(twitter._post_body(item), "제목만 있음")

    def test_premium_x_image_gallery_variants(self):
        from test_alert import make_premium_payload
        for count in (0, 1, 2, 4):
            with self.subTest(image_count=count), patch("test_alert.get_banner", return_value=""):
                payload = make_premium_payload("hanayo", "x", image_count=count)
            components = payload["components"][0]["components"]
            galleries = [part for part in components if part["type"] == 12]
            self.assertEqual(len(galleries), 1 if count else 0)
            if count:
                self.assertEqual(len(galleries[0]["items"]), count)
            self.assertIn("𝕏 **X** · 하나요 히요리 · **NEW POST**", str(components))
            self.assertIn("원문 보기 (테스트)", str(components))
            self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_premium_chzzk_live_card_layout(self):
        from test_alert import make_premium_payload
        with patch("test_alert.get_banner", return_value="https://yt3.googleusercontent.com/banner"):
            payload = make_premium_payload("hanayo", "chzzk")
        parts = payload["components"][0]["components"]
        self.assertIn("🟢 **CHZZK** · 하나요 히요리 · **LIVE**", parts[1]["content"])
        self.assertTrue(parts[3]["content"].startswith("## ["))
        self.assertIn("https://chzzk.naver.com/", parts[3]["content"])
        self.assertIn("Just Chatting · 2026.10.29 21:30", parts[-1]["content"])
        self.assertNotIn("방송 시청하기", str(payload))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_premium_preview_never_mentions_roles(self):
        from test_alert import make_premium_payload
        for platform in ("chzzk", "youtube", "x", "cafe", "replay"):
            with patch("test_alert.get_banner", return_value=""):
                payload = make_premium_payload("hanayo", platform)
            self.assertEqual(payload["username"], "정덕삼")
            self.assertEqual(payload["allowed_mentions"].get("parse"), [])
            self.assertEqual(payload["allowed_mentions"].get("roles", []), [])
            self.assertEqual(payload["flags"], 32768)
            self.assertNotIn("<@&", str(payload))

    def test_unified_title_link_escapes_markdown(self):
        payload = unified_alert(
            "youtube", "하나요 히요리",
            title="제목 [특별편] (1화)", url="https://www.youtube.com/watch?v=test",
            ping=False)
        parts = payload["components"][0]["components"]
        link = next(p["content"] for p in parts if p.get("content", "").startswith("## ["))
        self.assertIn("제목 \\[특별편\\] \\(1화\\)", link)
        self.assertIn("](https://www.youtube.com/watch?v=test)", link)

    def test_x_unified_body_preserves_blank_paragraph(self):
        payload = unified_alert("x", "하나요 히요리", body="첫 문단\\n\\n둘째 문단",
                                ping=False)
        parts = payload["components"][0]["components"]
        self.assertTrue(any(p.get("content") == "첫 문단\\n\\n둘째 문단" for p in parts))

    def test_unified_alert_formats(self):
        cases = [("chzzk", "LIVE"), ("youtube", "NEW VIDEO"),
                 ("x", "NEW POST"), ("cafe", "NEW POST")]
        for platform, status in cases:
            with self.subTest(platform=platform):
                payload = unified_alert(
                    platform, "하나요 히요리", "https://example.com/banner.jpg",
                    title="테스트 제목" if platform != "x" else "",
                    body="첫째\n\n둘째" if platform == "x" else "",
                    url="https://example.com/test",
                    images=["https://example.com/image.jpg"] if platform == "x" else [],
                    date="2026-10-29T12:00:00Z", ping=False)
                parts = payload["components"][-1]["components"]
                self.assertIn(status, parts[1]["content"])
                self.assertTrue(any(p.get("type") == 14 for p in parts))
                self.assertTrue(any(p.get("type") == 12 for p in parts))
                self.assertIn("2026.10.29 21:00", str(parts))
                self.assertEqual(payload["allowed_mentions"]["roles"], [])

    def test_unified_youtube_manual_preview(self):
        from test_alert import make_premium_payload
        with patch("test_alert.get_banner", return_value=""):
            p = make_premium_payload("hanayo", "youtube")
        components = p["components"][0]["components"]
        self.assertIn("YOUTUBE", components[0]["content"])
        self.assertTrue(any("## [" in item.get("content", "") for item in components))
        self.assertTrue(any("hqdefault.jpg" in str(item) for item in components))
        self.assertEqual(p["allowed_mentions"], {"parse": [], "roles": []})

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
             patch.object(yt, "youtube_thread_webhook_url", return_value="https://discord.com/api/webhooks/mock/token?thread_id=1565296193725141064"), \
             patch.object(yt, "send_webhook") as sent:
            yt._notify(video)
        payload = sent.call_args.args[1]
        self.assertEqual(payload["flags"], 32768)
        self.assertIn("1563499860135772211", payload["components"][0]["content"])
        parts = payload["components"][1]["components"]
        self.assertIn("test", parts[0]["items"][0]["media"]["url"])
        self.assertTrue(any("hqdefault.jpg" in str(part) for part in parts))
        self.assertTrue(any("## [" in part.get("content", "") for part in parts))

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
        self.assertTrue(any(live["thumbnail"] in str(part) for part in payload["components"][1]["components"]))

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

    def test_x_failed_delivery_keeps_post_unread(self):
        post = {"id": "900", "text": "new", "url": "https://x.com/test/status/900",
                "source": "test", "pub_date": ""}
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "seen.json"
            state_file.write_text("[]", encoding="utf-8")
            with patch.object(twitter, "SEEN_FILE", state_file), \
                 patch.object(twitter, "WEBHOOK_URL", "https://discord.com/api/webhooks/mock/token"), \
                 patch.object(twitter, "_fetch_merged_posts", return_value=[post]), \
                 patch.object(twitter, "_notify", side_effect=ValueError("send failed")), \
                 patch.object(twitter, "warn_failure"), \
                 patch.object(twitter, "next_check", 0):
                twitter.check_twitter()
            self.assertEqual(state_file.read_text(encoding="utf-8"), "[]")
    def test_x_rss_merges_backup_posts_without_duplicates(self):
        newest = {"id": "200", "text": "new", "url": "https://x.com/HanayoHiyori/status/200",
                  "source": "backup", "pub_date": ""}
        previous = {"id": "100", "text": "old", "url": "https://x.com/HanayoHiyori/status/100",
                    "source": "primary", "pub_date": ""}
        def fake_fetch(url):
            return [previous] if url == twitter.FEED_URL else [newest, previous]
        with patch.object(twitter, "ALL_FEEDS", [twitter.FEED_URL, "https://backup.example/rss"]), \
             patch.object(twitter, "_fetch_posts", side_effect=fake_fetch):
            posts = twitter._fetch_merged_posts(True)
        self.assertEqual([p["id"] for p in posts], ["100", "200"])
        self.assertEqual(posts[-1]["source"], "backup")

    def test_x_rss_every_source_checked_each_cycle(self):
        urls = ["https://first.example/rss", "https://second.example/rss",
                "https://third.example/rss"]
        with patch.object(twitter, "ALL_FEEDS", urls), \
             patch.object(twitter, "_fetch_posts", return_value=[]) as fetched:
            self.assertEqual(twitter._fetch_merged_posts(), [])
        self.assertCountEqual([c.args[0] for c in fetched.call_args_list], urls)

    def test_x_rss_runs_sources_concurrently(self):
        from threading import Barrier
        barrier = Barrier(3)
        urls = ["https://first.example/rss", "https://second.example/rss",
                "https://third.example/rss"]
        def synchronized_fetch(source):
            barrier.wait(timeout=2)
            return [{"id": str(urls.index(source) + 100), "text": "test",
                     "source": source, "url": "https://x.com/test/status/100", "pub_date": ""}]
        with patch.object(twitter, "ALL_FEEDS", urls), \
             patch.object(twitter, "_fetch_posts", side_effect=synchronized_fetch):
            posts = twitter._fetch_merged_posts()
        self.assertEqual(len(posts), 3)
    def test_x_rss_primary_failure_tries_backup_immediately(self):
        def fake_fetch(url):
            if url == twitter.FEED_URL:
                raise ValueError("primary unavailable")
            return [{"id": "300", "text": "new", "url": "https://x.com/HanayoHiyori/status/300",
                     "source": url, "pub_date": ""}]
        with patch.object(twitter, "BACKUP_FEEDS", ["https://backup.example/rss"]), \
             patch.object(twitter, "_fetch_posts", side_effect=fake_fetch):
            posts = twitter._fetch_merged_posts(False)
        self.assertEqual([p["id"] for p in posts], ["300"])

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
             patch.object(replay, "thread_webhook_url", return_value="https://discord.com/api/webhooks/test/token?thread_id=1565293200225206342"), \
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
