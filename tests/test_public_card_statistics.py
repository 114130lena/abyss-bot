"""Public banner/clip inventory must match production selection and real timestamps."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import fanart_public_stats as stats
import guild_server_banner as banner
import chzzk_clip_digest as clips
from schedule_display import format_saved_time

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=KST)


class PublicFanartStatsTests(unittest.TestCase):
    def test_counts_eligible_candidates_and_checked_times_using_real_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "creator_media"
            root_h = base / "fanart_candidates" / "hanayo"
            root_f = base / "fanart_candidates" / "sakihane"
            root_h.mkdir(parents=True)
            root_f.mkdir(parents=True)
            for target in (root_h / "a.jpg", root_h / "b.jpg", root_f / "f.jpg"):
                target.write_bytes(b"image")
            queue = {
                "hanayo:101": {"group": "hanayo", "post_url": "https://x.com/a/status/101",
                    "discovered_at": "2026-11-08T12:00:00+00:00",
                    "cached_files": ["fanart_candidates/hanayo/a.jpg",
                                     "fanart_candidates/hanayo/b.jpg"]},
                "hanayo:102": {"group": "hanayo", "post_url": "https://x.com/b/status/102",
                    "cached_files": ["fanart_candidates/hanayo/missing.jpg"]},
                "sakihane:103": {"group": "sakihane", "post_url": "https://x.com/f/status/103",
                    "discovered_at": "2026-11-08T13:00:00+00:00",
                    "cached_files": ["fanart_candidates/sakihane/f.jpg"]}}
            (base / "fanart_discovery.json").write_text(json.dumps(queue), encoding="utf-8")
            (base / "fanart_profile_state.json").write_text(json.dumps({
                "checked_at": "2026-11-08T14:00:00+00:00"}), encoding="utf-8")
            (base / "fanart_fx_search_state.json").write_text(json.dumps({
                "checked_at": "2026-11-08T14:30:00+00:00"}), encoding="utf-8")
            def posts(group):
                if group == "hanayo":
                    return [{"post_url": "https://x.com/a/status/101"},
                            {"post_url": "https://x.com/a/status/101"}]
                return [{"post_url": "https://x.com/f/status/103"},
                        {"post_url": "https://x.com/g/status/104"}]
            with patch.object(stats, "BASE", base), \
                 patch.object(stats, "QUEUE", base / "fanart_discovery.json"), \
                 patch("fanart_url_media.candidates", side_effect=posts) as selectors, \
                 patch.object(banner, "approved_hanayo_art", return_value=[
                     ("old.jpg", {"sha256": "same"}),
                     ("old2.jpg", {"sha256": "same"})]), \
                 patch.object(banner, "collected_hanayo_art", return_value=[
                     ("new.jpg", {"sha256": "another"})]):
                result = stats.snapshot()
            self.assertTrue(result["available"])
            self.assertEqual(result["groups"]["hanayo"]["posts"], 2)
            self.assertEqual(result["groups"]["sakihane"]["posts"], 1)
            self.assertEqual(result["groups"]["hanayo"]["cached_images"], 2)
            self.assertEqual(result["groups"]["sakihane"]["cached_images"], 1)
            self.assertEqual(result["groups"]["hanayo"]["notification_posts"], 1)
            self.assertEqual(result["groups"]["sakihane"]["notification_posts"], 2)
            self.assertEqual(result["server_banner_images"], 2)
            self.assertEqual(result["checked_at"], "2026-11-08T14:30:00+00:00")
            self.assertEqual(result["last_new_post_at"], "2026-11-08T13:00:00+00:00")
            self.assertEqual(selectors.call_count, 2)
            displayed = stats.card_text(result)
            self.assertIn("수집 게시글**　히요리 2건 · 후야 1건", displayed)
            self.assertIn("저장된 이미지**　히요리 2장 · 후야 1장", displayed)
            self.assertIn("서버 배너용 후보**　히요리 2장", displayed)
            self.assertIn("알림 배너·팬아트 후보**　히요리 1건 · 후야 2건", displayed)
            self.assertIn("마지막 수집 점검", displayed)
            self.assertIn("마지막 신규 게시글 발견", displayed)
            self.assertEqual(stats.checksum(result), stats.checksum(dict(result)))

    def test_missing_inventory_is_explicitly_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(stats, "BASE", Path(folder)), \
                 patch.object(stats, "QUEUE", Path(folder) / "missing.json"):
                report = stats.snapshot()
        self.assertFalse(report["available"])
        self.assertIsNone(report["groups"]["hanayo"]["posts"])
        self.assertIn("수집 데이터 확인 불가", stats.card_text(report))

    def test_older_or_invalid_time_is_not_falsely_represented(self):
        self.assertEqual(format_saved_time(""), "기록 없음")
        self.assertEqual(format_saved_time("2026-11-08 22:00"), "기록 없음")
        self.assertEqual(format_saved_time("invalid"), "기록 없음")
        self.assertEqual(format_saved_time(NOW), f"<t:{int(NOW.timestamp())}:f>")


class PublicCardSyncTests(unittest.TestCase):
    def test_fanart_count_change_edits_same_original_banner_message_once(self):
        report = {"available": True,
                  "groups": {"hanayo": {"posts": 2, "cached_images": 3, "notification_posts": 1},
                             "sakihane": {"posts": 1, "cached_images": 2, "notification_posts": 1}},
                  "server_banner_images": 2, "checked_at": "2026-11-08T14:00:00+00:00",
                  "last_new_post_at": ""}
        current = {"guild_banner_hash": "samehash",
                   "banner_info_updated_hash": "samehash", "banner_info_message_id": "789",
                   "banner_info_layout_version": 10,
                   "banner_info_stats_checksum": "old-value",
                   "banner_changed_at": "2026-11-08 18:00 KST",
                   "banner_change_method": "봇 자동 교체"}
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "token", "DISCORD_GUILD_ID": "123",
                                      "SERVER_BANNER_INFO_MESSAGE_ID": "789"}), \
             patch.object(banner, "INFO_CHANNEL_ID", "456"), \
             patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch("fanart_public_stats.snapshot", return_value=report), \
             patch("discord_message_sync.update_original", return_value="updated") as edit, \
             patch.object(banner, "write_state") as saved, \
             patch.object(banner, "read_state", return_value=current):
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(edit.call_count, 1)
        self.assertEqual(edit.call_args.args[1], "789")
        self.assertIn("히요리 2건 · 후야 1건", str(edit.call_args.args[2]))
        new_state = saved.call_args.args[0]
        self.assertEqual(new_state["banner_info_stats_checksum"], stats.checksum(report))
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "token", "DISCORD_GUILD_ID": "123",
                                      "SERVER_BANNER_INFO_MESSAGE_ID": "789"}), \
             patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch("fanart_public_stats.snapshot", return_value=report), \
             patch.object(banner, "read_state", return_value=new_state), \
             patch("discord_message_sync.update_original") as no_edit:
            again = banner.sync_banner_info()
        self.assertEqual(again["status"], "up_to_date")
        no_edit.assert_not_called()

    def test_clip_card_distinguishes_collection_and_publication_times(self):
        clip = {"id": "clip123456", "title": "히요리 클립",
                "url": "https://chzzk.naver.com/clips/clip123456",
                "thumbnail": "", "views": 50, "date": NOW.isoformat()}
        collected = "2026-11-08T17:00:00+09:00"
        payload = clips._render([clip], {}, now=NOW, collected_at=collected)
        text = " ".join(str(x.get("content", ""))
                        for x in payload["components"][0]["components"])
        self.assertIn("수집 주기 · 2시간마다", text)
        self.assertIn("마지막 수집 ·", text)
        self.assertIn(format_saved_time(collected), text)
        self.assertIn("메시지 갱신 ·", text)
        self.assertIn(format_saved_time(NOW), text)
        self.assertIn("매일 08:00 / 18:00 KST", text)


if __name__ == "__main__":
    unittest.main()
