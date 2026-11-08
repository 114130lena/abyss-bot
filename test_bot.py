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

class CrossPlatformTitleLinkTests(unittest.TestCase):
    """Offline regressions: inspect the exact Discord title URLs without sending messages."""

    def test_chzzk_live_uses_channel_id_not_numeric_live_id(self):
        from unittest.mock import Mock
        cid = "36ddb9bb4f17593b60f1b63cec86611d"
        response = Mock()
        response.json.return_value = {"code": 200, "content": {
            "liveId": 1234567890, "status": "OPEN", "liveTitle": "방송 제목",
            "channel": {"channelName": "사키하네 후야"}}}
        with patch.object(main.session, "get", return_value=response):
            live = main.get_live_info(cid)
        self.assertEqual(live["url"], "https://chzzk.naver.com/live/" + cid)
        self.assertEqual(live["live_id"], "1234567890")
        payload = unified_alert("chzzk", "사키하네 후야",
                                title=live["title"], url=live["url"])
        parts = payload["components"][0]["components"]
        self.assertTrue(any("](https://chzzk.naver.com/live/" + cid + ")" in p.get("content", "")
                            for p in parts))

    def test_chzzk_live_state_still_tracks_live_id(self):
        from unittest.mock import Mock
        cid = "36ddb9bb4f17593b60f1b63cec86611d"
        state = {cid: {"status": "OPEN", "live_id": "101", "open_date": "2026-11-05"}}
        live = {"channel_id": cid, "live_id": "102", "url": "https://chzzk.naver.com/live/" + cid,
                "status": "OPEN", "title": "New Live", "name": "사키하네 후야",
                "open_date": "2026-11-05"}
        with patch.object(main, "load_channels", return_value=[cid]), \
             patch.object(main, "get_live_info", return_value=live), \
             patch.object(main, "send_discord_notification") as sent, \
             patch.object(main, "save_json"):
            main.check_chzzk_once(state)
        sent.assert_called_once()
        self.assertEqual(state[cid]["live_id"], "102")

    def test_link_destinations_all_platform_titles_and_footer(self):
        destinations = (
            ("chzzk", "https://chzzk.naver.com/live/49d2c3cf8f0b2843d934ec15e82bb54c", False),
            ("chzzk", "https://chzzk.naver.com/video/12345678", True),
            ("cafe", "https://cafe.naver.com/f-e/cafes/29424353/articles/1125873?menuid=431", False),
            ("x", "https://x.com/HanayoHiyori/status/2116841881615446466", False),
            ("youtube", "https://www.youtube.com/watch?v=example123", False),
        )
        for platform, url, replay_flag in destinations:
            with self.subTest(platform=platform, replay=replay_flag):
                payload = unified_alert(platform, "하나요 히요리", title="링크 확인",
                                        url=url, replay=replay_flag)
                parts = payload["components"][0]["components"]
                title_link = next(p["content"] for p in parts if p.get("content", "").startswith("## ["))
                self.assertIn("](" + url + ")", title_link)
                buttons = next(p["components"] for p in parts if p.get("type") == 1)
                self.assertEqual(len(buttons), 4)
                self.assertTrue(all(b["url"].startswith("https://") for b in buttons))
                chzzk_button = next(b["url"] for b in buttons if b["label"] == "CHZZK")
                self.assertRegex(chzzk_button, r"^https://chzzk[.]naver[.]com/[0-9a-f]{32}$")


class DiscordRoleMentionReceiptTests(unittest.TestCase):
    def test_expected_role_is_present_in_v2_component(self):
        from resilience import _role_mention_expectations
        payload = unified_alert("chzzk", "하나요 히요리",
                                role_id="1563499860135772211",
                                title="새 방송", url="https://chzzk.naver.com/live/test")
        self.assertEqual(_role_mention_expectations(payload), ["1563499860135772211"])

    def test_retweet_without_role_never_requires_mention(self):
        from resilience import _role_mention_expectations
        payload = unified_alert("x", "하나요 히요리", role_id="",
                                title="리트윗", url="https://x.com/example/status/123")
        self.assertEqual(_role_mention_expectations(payload), [])

    def test_receipt_audit_recognizes_and_detects_missing_role(self):
        from resilience import _audit_role_mention
        from unittest.mock import Mock
        payload = unified_alert("youtube", "사키하네 후야",
                                role_id="1564279305171640361", title="영상",
                                url="https://www.youtube.com/watch?v=abc")
        response = Mock(status_code=200)
        response.json.return_value = {
            "id": "123", "flags": 32768, "mention_roles": ["1564279305171640361"]
        }
        with self.assertLogs(level="INFO") as logs:
            _audit_role_mention(response, payload)
        self.assertIn("DISCORD_ROLE_MENTION_RECOGNIZED", "\\n".join(logs.output))
        response.json.return_value["mention_roles"] = []
        with self.assertLogs(level="WARNING") as logs:
            _audit_role_mention(response, payload)
        self.assertIn("DISCORD_ROLE_MENTION_MISSING", "\\n".join(logs.output))

    def test_webhook_asks_for_message_receipt_without_extra_send(self):
        from resilience import send_webhook
        from unittest.mock import Mock
        response = Mock(status_code=200)
        response.json.return_value = {"id": "200", "flags": 32768,
                                      "mention_roles": ["1563499860135772211"]}
        payload = unified_alert("chzzk", "하나요 히요리",
                                role_id="1563499860135772211",
                                title="방송", url="https://chzzk.naver.com/live/a")
        with patch("resilience.requests.post", return_value=response) as sent:
            send_webhook("https://discord.com/api/webhooks/12/token?thread_id=456",
                         payload)
        self.assertEqual(sent.call_count, 1)
        url = sent.call_args.args[0]
        self.assertIn("thread_id=456", url)
        self.assertIn("with_components=true", url)
        self.assertIn("wait=true", url)


class DeliveryReviewTests(unittest.TestCase):
    def test_unknown_outcome_records_no_message_body_or_webhook(self):
        import delivery_review
        import json
        with TemporaryDirectory() as temp:
            path = Path(temp) / "pending.jsonl"
            payload = {"content": "private text", "components": [{"content": "𝕏 X"}]}
            with patch.object(delivery_review, "JOURNAL", path):
                token = delivery_review.record(payload, "ReadTimeout")
                self.assertEqual(delivery_review.count_pending(), 1)
            data = path.read_text(encoding="utf-8")
            self.assertEqual(len(token), 20)
            self.assertNotIn("private text", data)
            self.assertEqual(json.loads(data)["status"], "needs_manual_review")

    def test_health_snapshot_counts_pending_review(self):
        import delivery_review
        import health_monitor
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "creator_media").mkdir()
            path = root / "discord_delivery_uncertain.jsonl"
            path.write_text('{"status":"needs_manual_review"}\\n', encoding="utf-8")
            with patch.object(delivery_review, "JOURNAL", path), \
                 patch.object(health_monitor, "ROOT", root), \
                 patch("guild_server_banner.collected_hanayo_art", return_value=[]):
                status = health_monitor.snapshot()
            self.assertEqual(status["discord_uncertain_deliveries"], 1)
            self.assertEqual(status["banner"]["collected_candidates"], 0)


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
            parts = payload["components"][0]["components"]
            galleries = [part for part in parts if part["type"] == 12]
            self.assertEqual(len(galleries), 1 if count else 0)
            self.assertIn("## [", str(parts))
            self.assertNotIn("StelLive", str(parts))
            self.assertEqual(payload["allowed_mentions"], {"parse": [], "roles": []})

    def test_premium_chzzk_live_card_layout(self):
        from test_alert import make_premium_payload
        with patch("test_alert.get_banner", return_value="https://yt3.googleusercontent.com/banner"):
            payload = make_premium_payload("hanayo", "chzzk")
        parts = payload["components"][0]["components"]
        self.assertEqual(parts[-1]["type"], 1)
        self.assertIn("하나요 히요리", str(parts))
        self.assertTrue(any(p.get("content", "").startswith("## [") for p in parts))
        self.assertNotIn("StelLive", str(parts))
        self.assertNotIn("NEW POST", str(parts))

    def test_premium_preview_never_mentions_roles(self):
        from test_alert import make_premium_payload
        for platform in ("chzzk", "youtube", "x", "cafe", "replay"):
            with patch("test_alert.get_banner", return_value=""):
                payload = make_premium_payload("hanayo", platform)
            self.assertEqual(payload["username"], "정덕삼")
            self.assertEqual(payload["allowed_mentions"]["roles"], [])
            self.assertEqual(payload["flags"], 32768)
            self.assertNotIn("<@&", str(payload))

    def test_unified_title_link_escapes_markdown(self):
        payload = unified_alert("youtube", "하나요 히요리",
            title="제목 [특별편] (1화)", url="https://www.youtube.com/watch?v=test", ping=False)
        parts = payload["components"][0]["components"]
        link = next(p["content"] for p in parts if p.get("content", "").startswith("## ["))
        self.assertIn("제목 " + chr(92) + "[특별편" + chr(92) + "] " + chr(92) + "(1화" + chr(92) + ")", link)
        self.assertIn("](https://www.youtube.com/watch?v=test)", link)

    def test_x_unified_body_preserves_blank_paragraph(self):
        payload = unified_alert("x", "하나요 히요리",
            body="첫 문단\n\n둘째 문단", url="https://x.com/test/status/1", ping=False)
        parts = payload["components"][0]["components"]
        self.assertTrue(any("## [첫 문단]" in p.get("content", "") for p in parts))
        self.assertTrue(any(p.get("content") == "둘째 문단" for p in parts))

    def test_unified_alert_formats(self):
        cases = ("chzzk", "youtube", "x", "cafe")
        for platform in cases:
            with self.subTest(platform=platform):
                payload = unified_alert(platform, "하나요 히요리",
                    "https://example.com/banner.jpg",
                    title="테스트 제목" if platform != "x" else "",
                    body="첫째\n\n둘째" if platform == "x" else "",
                    url="https://example.com/test",
                    images=["https://example.com/image.jpg"] if platform == "x" else [],
                    date="2026-10-29T12:00:00Z", ping=False,
                    handle="@HanayoHiyori", avatar="https://example.com/avatar.jpg")
                parts = payload["components"][-1]["components"]
                # Current layout keeps the official banner above shortcut buttons.
                self.assertEqual(parts[-1]["type"], 1)
                labels = [button["label"] for button in parts[-1]["components"]]
                self.assertIn("YouTube", labels)
                self.assertIn("X", labels)
                self.assertIn("CAFE", labels)
                self.assertTrue(any(p.get("type") == 12 and
                    any(item.get("media", {}).get("url") == "https://example.com/banner.jpg"
                        for item in p.get("items", [])) for p in parts))
                self.assertEqual(parts[0]["type"], 9)
                self.assertEqual(parts[0]["accessory"]["type"], 11)
                self.assertNotIn("StelLive", str(parts))
                self.assertTrue(any(p.get("type") == 14 for p in parts))
                self.assertTrue(platform == "chzzk" or "2026.10.29 21:00" in str(parts))
                self.assertEqual(payload["allowed_mentions"]["roles"], [])

    def test_unified_youtube_manual_preview(self):
        from test_alert import make_premium_payload
        with patch("test_alert.get_banner", return_value=""):
            p = make_premium_payload("hanayo", "youtube")
        components = p["components"][0]["components"]
        self.assertTrue(any("YOUTUBE" in item.get("content", "") for item in components))
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
        self.assertTrue(any(p.get("type") == 12 and "test" in str(p) for p in parts))
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

    def test_cafe_detail_extracts_body_and_images(self):
        from unittest.mock import Mock
        html = "<div>후하요~<br>오늘 방송 안내</div><p>두 번째 문단</p><img src='https://example.com/post.jpg'>"
        response = Mock()
        response.json.return_value = {"message": {"result": {"article": {"contentHtml": html}}}}
        with patch.object(cafe.session, "get", return_value=response):
            body, images = cafe.article_detail({"id": "123", "summary": "짧은 미리보기", "images": []})
        self.assertIn("후하요~\n오늘 방송 안내", body)
        self.assertIn("두 번째 문단", body)
        self.assertEqual(images, ["https://example.com/post.jpg"])

    def test_cafe_detail_failure_falls_back_to_preview(self):
        with patch.object(cafe.session, "get", side_effect=cafe.requests.Timeout):
            body, images = cafe.article_detail({"id": "123", "summary": "미리보기", "images": ["https://example.com/thumb.jpg"]})
        self.assertEqual(body, "미리보기")
        self.assertEqual(images, ["https://example.com/thumb.jpg"])

    def test_cafe_long_post_shows_more_and_original_link(self):
        url = "https://cafe.naver.com/f-e/cafes/29424353/articles/123"
        payload = unified_alert("cafe", "사키하네 후야", title="방송 공지",
                                body="공지" * 1800, url=url,
                                images=["https://example.com/post.jpg"], ping=False)
        parts = payload["components"][0]["components"]
        self.assertTrue(any("전체 글 보기" in p.get("content", "") for p in parts))
        self.assertTrue(any(p.get("type") == 12 and
                            "post.jpg" in str(p) for p in parts))

    def test_long_cafe_content_keeps_metadata_separator(self):
        post = "공지 내용입니다.\\n" * 240
        result = unified_alert("cafe", "사키하네 후야",
                               body=post, date="2026-10-31T08:00:00Z",
                               banner="https://example.com/banner.jpg", ping=False)
        parts = result["components"][0]["components"]
        footer_index = next(i for i, part in enumerate(parts)
                            if "작성 · " in part.get("content", ""))
        self.assertEqual(parts[footer_index - 1]["type"], 14)
        self.assertEqual(parts[-1]["type"], 1)
        self.assertGreaterEqual(sum(p.get("type") == 10 for p in parts), 4)

    def test_cafe_footer_visible_without_timestamp(self):
        result = unified_alert("cafe", "사키하네 후야", body="긴 공지 " * 600, ping=False)
        parts = result["components"][0]["components"]
        position = next(i for i, part in enumerate(parts)
                        if part.get("content") == "작성 시간 정보 없음")
        self.assertEqual(parts[position - 1]["type"], 14)

    def test_cafe_unix_timestamp_formatting(self):
        from discord_v2 import _korean_datetime
        self.assertEqual(_korean_datetime(1761897600000),
                         _korean_datetime(1761897600))

    def test_schedule_highlight_uses_explicit_facts(self):
        from discord_v2 import _schedule_facts
        facts = _schedule_facts("10월 31일 방송 일정", "본 방송: 10월 31일 오후 7시")
        self.assertIn("📅 날짜 · 10월 31일", facts)
        self.assertIn("🕒 언급된 시간 · 오후 7시", facts)

    def test_schedule_highlight_does_not_guess_between_times(self):
        from discord_v2 import _schedule_facts
        facts = _schedule_facts("방송 일정", "방송은 오후 6시, 대회는 오후 7시")
        self.assertEqual(facts, [])

    def test_alert_buttons_and_live_heading(self):
        live = unified_alert("chzzk", "하나요 히요리",
                             title="방송 시작", url="https://chzzk.naver.com/live/test")
        parts = live["components"][0]["components"]
        self.assertTrue(any("CHZZK · LIVE" in p.get("content", "") for p in parts))
        self.assertTrue(any(p.get("type") == 1 for p in parts))
        cafe_card = unified_alert("cafe", "사키하네 후야", title="10월 31일 방송 일정",
                                  body="오후 7시 방송합니다", url="https://cafe.naver.com/example")
        parts = cafe_card["components"][0]["components"]
        self.assertFalse(any("📌 공지 핵심 정보" in p.get("content", "") for p in parts))
        self.assertTrue(any(p.get("type") == 1 for p in parts))

    def test_cafe_and_x_keep_full_source_without_summary_card(self):
        for platform in ("cafe", "x"):
            card = unified_alert(platform, "하나요 히요리",
                                 title="오늘 방송 일정", body="오후 8시 방송합니다",
                                 url="https://example.com/post", date="2026-11-01T10:00:00Z",
                                 ping=False)
            parts = card["components"][0]["components"]
            self.assertNotIn("공지 핵심 정보", str(parts))
            self.assertIn("오후 8시 방송합니다", str(parts))
            self.assertIn("방송 예고", str(parts))

    def test_notice_type_classifies_explicit_announcements(self):
        from discord_v2 import _notice_type
        cases = (
            ("오늘 방송 일정", "오후 8시에 방송합니다", "📺 방송 예고"),
            ("휴방 공지", "오늘은 휴방입니다", "🌙 휴방 안내"),
            ("방송 시간 변경", "오후 8시로 변경", "🔄 일정 변경 안내"),
            ("방송 취소", "방송을 취소합니다", "🚫 방송 취소 안내"),
            ("그냥 인사", "좋은 아침이에요", None),
        )
        for title, body, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(_notice_type(title, body), expected)

    def test_notice_does_not_classify_negated_break(self):
        from discord_v2 import _notice_type
        self.assertIsNone(_notice_type("안내", "오늘 휴방 아닙니다"))

    def test_x_and_cafe_notice_labels_and_buttons(self):
        for platform in ("cafe", "x"):
            with self.subTest(platform=platform):
                payload = unified_alert(platform, "하나요 히요리",
                                        title="휴방 공지", body="오늘은 휴방입니다",
                                        url="https://example.com/post", ping=False)
                components = payload["components"][0]["components"]
                self.assertTrue(any("🌙 휴방 안내" in item.get("content", "")
                                    for item in components))
                self.assertTrue(any(item.get("type") == 1 for item in components))

    def test_collab_and_casual_post_classification(self):
        from discord_v2 import _notice_type
        self.assertEqual(_notice_type("", "저녁에는 어비스 합방이 있어욥!"), "🎮 합방 예고")
        self.assertEqual(_notice_type("방송", "이따 방송에서 만나요!"), "📺 방송 예고")
        self.assertIsNone(_notice_type("", "오늘도 또 만나요 우리!"))
        self.assertIsNone(_notice_type("", "오늘 휴방 아닙니다"))

    def test_compact_collab_subtitle_no_today(self):
        card = unified_alert("x", "하나요 히요리",
                             "https://example.com/banner.jpg",
                             role_id="1563499860135772211",
                             title="오늘 메리랑 합방", body="저녁에 메리랑 합방 있어요",
                             date="2026-11-01T00:00:00Z",
                             url="https://x.com/HanayoHiyori/status/1",
                             handle="@HanayoHiyori", ping=True)
        self.assertEqual(card["components"][0]["content"], "<@&1563499860135772211>")
        self.assertEqual(card["allowed_mentions"]["roles"], ["1563499860135772211"])
        parts = card["components"][1]["components"]
        self.assertIn("HANAYO HIYORI", str(parts[0]))
        self.assertNotIn("TODAY", str(parts))
        self.assertIn("-# 🎮 합방 예고", str(parts))
        self.assertIn("메리와 함께", str(parts))
        self.assertEqual(parts[-1]["type"], 1)
        footer_index = next(i for i, part in enumerate(parts)
                            if "작성 · " in part.get("content", ""))
        self.assertEqual(parts[footer_index - 1]["type"], 14)
        other = unified_alert("cafe", "사키하네 후야", handle="@Sakihanechannel", ping=False)
        self.assertIn("SAKIHANE HUYA", str(other["components"][0]["components"][0]))

    def test_conservative_schedule_tracking_and_health(self):
        import notice_intelligence as ni
        from pathlib import Path
        with TemporaryDirectory() as folder:
            schedule = Path(folder) / "schedule.json"
            health = Path(folder) / "health.json"
            with patch.object(ni, "SCHEDULE_FILE", schedule), patch.object(ni, "HEALTH_FILE", health):
                created = ni.record_schedule(
                    "hanayo", "2026-11-03 방송 일정", "오후 8시 방송 예정",
                    "2026-11-01T08:00:00Z", "https://example.com/1")
                self.assertEqual(created["time"], "20:00")
                changed = ni.record_schedule(
                    "hanayo", "2026-11-03 방송 시간 변경", "오후 9시로 변경",
                    "2026-11-02T08:00:00Z", "https://example.com/2")
                self.assertEqual(changed["time"], "21:00")
                self.assertEqual(len(ni._read(schedule)), 1)
                self.assertIsNone(ni.record_schedule(
                    "hanayo", "11월 3일 방송 일정", "오후 10시 방송 예정",
                    "2026-11-02T08:00:00Z", "https://example.com/other"))
                self.assertEqual(ni._read(schedule)["hanayo:2026-11-03:unspecified"]["time"], "21:00")
                self.assertEqual(ni._event_key("11월 3일 방송", "", "2026-11-02T08:00:00Z"), "2026-11-03")
                self.assertIsNone(ni.record_schedule(
                    "hanayo", "오늘 방송", "오후 9시 방송 예정",
                    "2026-11-02T08:00:00Z", "https://example.com/3"))
                ni.report_source("히요리 X", False, "RSS unavailable")
                self.assertEqual(ni._read(health)["히요리 X"]["consecutive_failures"], 1)
                ni.report_source("히요리 X", True)
                self.assertEqual(ni._read(health)["히요리 X"]["consecutive_failures"], 0)

    def test_schedule_topic_separation_and_clock_normalization(self):
        import notice_intelligence as ni
        self.assertEqual(ni._clock_key("오후 8시"), "20:00")
        self.assertEqual(ni._clock_key("20:00"), "20:00")
        self.assertEqual(ni._clock_key("오후 8시 / 오후 9시"), None)
        with TemporaryDirectory() as directory:
            with patch.object(ni, "SCHEDULE_FILE", Path(directory) / "schedule.json"):
                a = ni.record_schedule("hanayo", "2026-11-03 마인크래프트 방송 일정",
                                       "오후 8시 방송합니다", "2026-11-01T08:00:00Z", "https://example.com/a")
                b = ni.record_schedule("hanayo", "2026-11-03 발헤임 방송 일정",
                                       "오후 9시 방송합니다", "2026-11-01T08:00:00Z", "https://example.com/b")
                self.assertIsNotNone(a)
                self.assertIsNotNone(b)
                self.assertEqual(len(ni._read(ni.SCHEDULE_FILE)), 2)

    def test_collab_name_requires_collab_context(self):
        from notice_intelligence import participants
        self.assertEqual(participants("", "저녁에 메리랑 합방해요!"), ["메리"])
        self.assertEqual(participants("", "메리랑 밥 먹었어요"), [])
        self.assertEqual(participants("", "오늘 합방해요"), [])

    def test_calendar_dates_and_ambiguous_event_dates(self):
        from notice_intelligence import _event_key
        stamp = "2026-11-01T10:00:00Z"
        self.assertEqual(_event_key("11월 3일 방송", "", stamp), "2026-11-03")
        self.assertEqual(_event_key("2026/11/03 방송", "", stamp), "2026-11-03")
        self.assertEqual(_event_key("2026-11-03 방송", "", stamp), "2026-11-03")
        self.assertIsNone(_event_key("11월 3일과 11월 4일 방송", "", stamp))

    def test_collab_aliases_and_no_casual_name_detection(self):
        from notice_intelligence import participants
        self.assertIn("시부키", participants("시부키랑 합방", "오늘 함께해요"))
        self.assertIn("메리", participants("메리와 합방", "방송"))
        self.assertEqual(participants("메리 일상", "오늘 같이 식사했어요"), [])
        self.assertEqual(participants("히요리랑 합방", "", creator="히요리"), [])

    def test_admin_recovery_after_sustained_collection_failure(self):
        import notice_intelligence as ni
        from pathlib import Path
        with TemporaryDirectory() as folder, \
             patch.object(ni, "HEALTH_FILE", Path(folder) / "health.json"), \
             patch("resilience.ADMIN_WEBHOOK", "https://discord.com/api/webhooks/test/token"), \
             patch("resilience.send_webhook") as delivered:
            for _ in range(3):
                ni.report_source("후야 X", False, "rss unavailable")
            delivered.assert_not_called()
            ni.report_source("후야 X", True)
            self.assertEqual(delivered.call_count, 1)
            self.assertIn("복구", str(delivered.call_args.args[1]))
            ni.report_source("후야 X", True)
            self.assertEqual(delivered.call_count, 1)

    def test_discord_card_limits_keep_role_footer_and_banner(self):
        for platform in ("chzzk", "x", "cafe", "youtube"):
            card = unified_alert(platform, "하나요 히요리", "https://example.com/banner.png",
                                 role_id="1563499860135772211",
                                 title="메리랑 합방", body="오늘 저녁 메리랑 합방 있어요",
                                 category="게임", date="2026-11-01T10:00:00Z",
                                 url="https://example.com/post",
                                 images=["https://example.com/image.png"], ping=True)
            parts = card["components"][1]["components"]
            self.assertLessEqual(len(parts), 10)
            self.assertEqual(parts[-1]["type"], 1)
            self.assertIn("<@&1563499860135772211>", str(card))
            self.assertNotIn("TODAY", str(parts))
            self.assertNotIn("공지 핵심 정보", str(parts))

    def test_connection_error_does_not_duplicate_discord_mentions(self):
        import requests
        from resilience import send_webhook
        with patch("resilience.requests.post", side_effect=requests.ConnectionError("lost")) as post:
            with self.assertRaises(requests.ConnectionError):
                send_webhook("https://discord.com/api/webhooks/test/token",
                             {"content": "<@&123>"}, attempts=3)
        self.assertEqual(post.call_count, 1)

    def test_connect_timeout_can_retry_safely(self):
        import requests
        from unittest.mock import Mock
        from resilience import send_webhook
        accepted = Mock()
        accepted.status_code = 204
        with patch("resilience.requests.post", side_effect=[requests.ConnectTimeout(), accepted]) as post, \
             patch("resilience.time.sleep"):
            send_webhook("https://discord.com/api/webhooks/test/token", {"content": "test"})
        self.assertEqual(post.call_count, 2)

    def test_image_card_preview_isolated_and_no_mentions(self):
        import test_image_card as preview
        from unittest.mock import patch as local_patch
        for group in ("hanayo", "sakihane"):
            with local_patch("test_image_card.get_banner", return_value="https://example.com/banner.png"):
                payload, png = preview.make_preview(group)
            self.assertEqual(png[:8], bytes([137,80,78,71,13,10,26,10]))
            self.assertEqual(payload["allowed_mentions"], {"parse": []})
            self.assertEqual(payload["flags"], 32768)
            parts = payload["components"][0]["components"]
            self.assertEqual(parts[0]["type"], 12)
            self.assertEqual(parts[0]["items"][0]["media"]["url"], "attachment://abyss_preview.png")
            self.assertEqual(parts[1]["type"], 14)
            self.assertEqual(parts[2]["type"], 10)
            self.assertEqual(parts[2]["content"], "**𝕏**")
            self.assertNotIn("H I Y O R I", str(parts[0]))
            self.assertNotIn("H U Y A", str(parts[0]))
            self.assertNotIn("\\\\[테스트\\\\]", str(parts))
            self.assertNotIn("𝕏 X", str(parts))
            self.assertEqual(parts[-1]["type"], 1)
            self.assertLessEqual(len(parts), 10)
            self.assertIn("합방 예고", str(parts))
            self.assertNotIn("TODAY", str(parts))

    def test_integrated_v2_comparison_has_no_uploaded_image_or_pings(self):
        import test_image_card as preview
        from unittest.mock import patch as local_patch, Mock
        with local_patch("test_image_card.get_banner", return_value="https://example.com/banner.png"):
            payload = preview.make_integrated_preview("hanayo")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(payload["flags"], 32768)
        parts = payload["components"][0]["components"]
        self.assertNotIn("attachment://", str(parts))
        self.assertIn("통합형 테스트", str(parts))
        self.assertIn("HANAYO HIYORI", str(parts))
        self.assertNotIn("TODAY", str(parts))
        self.assertNotIn("공지 핵심 정보", str(parts))
        self.assertEqual(parts[-1]["type"], 1)
        response = Mock()
        with patch("test_image_card.requests.post", return_value=response) as sent:
            preview.send_integrated_preview("https://discord.com/api/webhooks/123/token", payload)
        self.assertIn("thread_id=1566335914500816988", sent.call_args.args[0])
        self.assertIn("with_components=true", sent.call_args.args[0])
        self.assertEqual(sent.call_args.kwargs["json"]["allowed_mentions"], {"parse": []})

    def test_image_card_upload_multipart(self):
        import test_image_card as preview
        from unittest.mock import Mock
        response = Mock()
        with patch("test_image_card.requests.post", return_value=response) as sent:
            preview.send_preview("https://discord.com/api/webhooks/123/token",
                                 {"allowed_mentions": {"parse": []}, "flags": 32768},
                                 b"png")
        self.assertIn("with_components=true", sent.call_args.args[0])
        self.assertIn("thread_id=1566335914500816988", sent.call_args.args[0])
        self.assertEqual(sent.call_args.kwargs["files"]["files[0]"][0], "abyss_preview.png")
        self.assertIn("payload_json", sent.call_args.kwargs["data"])

    def test_read_timeout_does_not_retry_uncertain_discord_delivery(self):
        import requests
        from resilience import send_webhook
        with patch("resilience.requests.post", side_effect=requests.ReadTimeout()) as post:
            with self.assertRaises(requests.ReadTimeout):
                send_webhook("https://discord.com/api/webhooks/test/token", {"content": "test"})
        self.assertEqual(post.call_count, 1)

    def test_huya_x_routing_and_presentation(self):
        import twitter_huya_notifier as huya
        self.assertEqual(creator_groups.x_group("SakihaneHuya"), "sakihane")
        self.assertEqual(huya.GROUP, "sakihane")
        self.assertNotEqual(huya.SEEN_FILE, twitter.SEEN_FILE)
        with patch.object(huya, "get_banner", return_value=""), \
             patch.object(huya, "send_webhook") as sent:
            huya._notify({"id": "123", "text": "오늘 합방 있습니다",
                          "url": "https://x.com/SakihaneHuya/status/123",
                          "pub_date": "", "images": []})
        payload = sent.call_args.args[1]
        self.assertNotIn("content", payload["components"][0])
        self.assertEqual(payload["allowed_mentions"]["roles"], [])
        self.assertIn("SAKIHANE HUYA", str(payload))

    def test_huya_x_initializes_without_historical_notifications(self):
        import twitter_huya_notifier as huya
        posts = [{"id": "123", "text": "기존 공지", "url": "https://x.com/SakihaneHuya/status/123"}]
        with patch.object(huya, "WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"), \
             patch.object(huya, "_fetch_merged_posts", return_value=posts), \
             patch.object(huya, "_load_seen", return_value=None), \
             patch.object(huya, "_save_seen") as saved, \
             patch.object(huya, "_notify") as sent, \
             patch.object(huya, "next_check", 0):
            huya.check_huya_x()
        sent.assert_not_called()
        self.assertEqual(saved.call_args.args[0], {"123"})

    def test_huya_x_failure_keeps_post_unread(self):
        import twitter_huya_notifier as huya
        post = {"id": "900", "text": "새 게시글", "url": "https://x.com/SakihaneHuya/status/900"}
        with patch.object(huya, "WEBHOOK_URL", "https://discord.com/api/webhooks/test/token"), \
             patch.object(huya, "_fetch_merged_posts", return_value=[post]), \
             patch.object(huya, "_load_seen", return_value=set()), \
             patch.object(huya, "_save_seen") as saved, \
             patch.object(huya, "_notify", side_effect=ValueError("failed")), \
             patch.object(huya, "warn_failure"), \
             patch.object(huya, "next_check", 0):
            huya.check_huya_x()
        saved.assert_not_called()

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
        self.assertEqual(sent.call_args.args[1]["allowed_mentions"]["roles"], [])

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

    def test_youtube_failed_rss_uses_long_backoff_but_other_channels_continue(self):
        yt._channel_failures.clear()
        yt._channel_retry_at.clear()
        def fake_fetch(ch):
            if ch == "bad":
                raise ValueError("RSS unavailable")
            return []
        with patch.object(yt, "_load_seen", return_value={"bad": [], "good": []}), \
             patch.object(yt, "_fetch_uploads", side_effect=fake_fetch) as fetched, \
             patch.object(yt, "_save_seen"), \
             patch.object(yt, "warn_failure"), \
             patch.object(yt.time, "monotonic", return_value=100):
            yt._check_youtube_channels(["bad", "good"])
            self.assertGreaterEqual(yt._channel_retry_at["bad"], 3700)
            yt._check_youtube_channels(["bad", "good"])
            self.assertEqual([call.args[0] for call in fetched.call_args_list],
                             ["bad", "good", "good"])
        yt._channel_retry_at.clear()
        yt._channel_failures.clear()

    def test_health_snapshot_shows_fanart_counts_without_tokens(self):
        import health_monitor
        import json
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "creator_media").mkdir()
            (root / "creator_media" / "fanart_discovery.json").write_text(
                json.dumps({"h": {"group":"hanayo","cached_files":["a","b"]},
                            "s":{"group":"sakihane","cached_files":["c"]}}), encoding="utf-8")
            with patch.object(health_monitor, "ROOT", root):
                status = health_monitor.snapshot()
            self.assertEqual(status["fanart"]["hanayo"]["posts"], 1)
            self.assertEqual(status["fanart"]["hanayo"]["cached_images"], 2)
            self.assertEqual(status["fanart"]["sakihane"]["cached_images"], 1)
            self.assertTrue(status["states"]["youtube_seen"]["missing"])
            self.assertNotIn("secret", str(status).lower())

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

    def test_replay_posts_to_thread_with_hanayo_role(self):
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
        self.assertEqual(message["allowed_mentions"], {"parse": [], "roles": ["1563499860135772211"]})
        self.assertIn("<@&1563499860135772211>", str(message))
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

    def test_avatar_high_resolution_validation(self):
        from unittest.mock import Mock
        import creator_profiles as profiles
        original = "https://yt3.googleusercontent.com/avatar=s88-c"
        high = "https://yt3.googleusercontent.com/avatar=s512-c"
        self.assertEqual(profiles._high_res_avatar(original), high)
        ok = Mock()
        ok.headers = {"Content-Type": "image/jpeg"}
        with patch.object(profiles.requests, "head", return_value=ok) as head:
            self.assertEqual(profiles._verified_avatar_url(original), high)
            head.assert_called_once()
        failure = Mock()
        failure.raise_for_status.side_effect = profiles.requests.HTTPError("404")
        with patch.object(profiles.requests, "head", return_value=failure):
            self.assertEqual(profiles._verified_avatar_url(original), original)

    def test_banner_url_validation(self):
        self.assertTrue(banners._valid("https://yt3.googleusercontent.com/image"))
        self.assertFalse(banners._valid("https://evil.example.com/image"))

class YouTubeFeedFallbackTests(unittest.TestCase):
    @staticmethod
    def response(status, channel_id="UCHAr9pWGS30akL4MQCQT4uQ"):
        import requests
        class Result:
            content = ('<feed xmlns="http://www.w3.org/2005/Atom" '
                       'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                       '<yt:channelId>' + channel_id + '</yt:channelId>'
                       '<title>Creator</title>'
                       '<entry><yt:videoId>abc123</yt:videoId><title>New upload</title></entry>'
                       '</feed>').encode()
            def raise_for_status(self):
                if status >= 400:
                    err = requests.HTTPError(str(status))
                    err.response = self
                    raise err
            status_code = status
        return Result()

    def test_404_uses_verified_upload_playlist_feed(self):
        channel = "UCHAr9pWGS30akL4MQCQT4uQ"
        with patch.object(yt.session,"get", side_effect=[
            self.response(404), self.response(200)]) as requested:
            uploads = yt._fetch_uploads(channel)
        self.assertEqual([x["id"] for x in uploads], ["abc123"])
        self.assertEqual(requested.call_count, 2)
        self.assertEqual(requested.call_args_list[1].kwargs["params"],
                         {"playlist_id": "UU" + channel[2:]})

    def test_fallback_wrong_channel_fails_closed(self):
        channel = "UCHAr9pWGS30akL4MQCQT4uQ"
        with patch.object(yt.session,"get", side_effect=[
            self.response(404), self.response(200, "UC0YQnenKBCu5sGb7H61n6HA")]):
            with self.assertRaises(ValueError):
                yt._fetch_uploads(channel)

    def test_good_primary_feed_never_requests_fallback(self):
        with patch.object(yt.session,"get", return_value=self.response(200)) as requested:
            yt._fetch_uploads("UCHAr9pWGS30akL4MQCQT4uQ")
        requested.assert_called_once()


class WebSubSafetyTests(unittest.TestCase):
    def test_websub_and_rss_share_seen_ledger(self):
        import youtube_notifier as notifier
        channel = "UCHAr9pWGS30akL4MQCQT4uQ"
        xml = (f'<feed xmlns="http://www.w3.org/2005/Atom" '
               f'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
               f'<yt:channelId>{channel}</yt:channelId><title>Creator</title>'
               f'<entry><yt:channelId>{channel}</yt:channelId>'
               f'<yt:videoId>newvideo001</yt:videoId><title>New upload</title>'
               f'</entry></feed>').encode()
        seen = {channel: []}
        with patch.object(notifier, "load_channel_ids", return_value=[channel]), patch.object(notifier, "_load_seen", return_value=seen), patch.object(notifier, "_save_seen") as saved, patch.object(notifier, "_notify") as sent:
            notifier.receive_websub_feed(xml)
            notifier.receive_websub_feed(xml)
        sent.assert_called_once()
        self.assertEqual(seen[channel], ["newvideo001"])
        self.assertEqual(saved.call_count, 1)

    def test_websub_rejects_unknown_channel(self):
        import youtube_notifier as notifier
        xml = (b'<feed xmlns="http://www.w3.org/2005/Atom" '
               b'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
               b'<yt:channelId>UC0000000000000000000000</yt:channelId></feed>')
        with patch.object(notifier,"load_channel_ids",return_value=["UCHAr9pWGS30akL4MQCQT4uQ"]):
            with self.assertRaises(ValueError):
                notifier.receive_websub_feed(xml)


if __name__ == "__main__":
    unittest.main()
