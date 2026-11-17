"""Community hub regression tests: schedule, milestones, analytics, no duplicates."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import community_dashboard as hub

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 16, 18, 0, tzinfo=KST)


def response(status, data):
    r = Mock(status_code=status)
    r.json.return_value = data
    return r


class CommunityDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(hub, "STATE", root / "card.json"),
            patch.object(hub, "SESSIONS", root / "sessions.json"),
            patch.object(hub, "MILESTONES", root / "milestones.json"),
            patch.object(hub, "SCHEDULE", root / "schedules.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_three_sections_and_no_fake_data(self):
        payload = hub.render(NOW)
        parts = payload["components"][0]["components"]
        text = "\n".join(p.get("content", "") for p in parts)
        self.assertIn("방송 일정", text)
        self.assertIn("기념일", text)
        self.assertIn("활동 통계", text)
        self.assertIn("확인된 향후 일정이 없습니다", text)
        self.assertIn("생일", text)
        self.assertIn("데뷔", text)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(payload["flags"], 32768)

    def test_verified_cafe_schedule_is_shown(self):
        hub.save_json(hub.SCHEDULE, {
            "hanayo:2026-11-17:unspecified": {
                "time": "21:00", "status": "scheduled",
                "source": "https://cafe.naver.com/example"},
            "sakihane:2026-11-18:unspecified": {
                "time": "", "status": "break", "source": ""}})
        text = str(hub.render(NOW))
        self.assertIn("11/17 21:00", text)
        self.assertIn("11/18 시간 미정", text)

    def test_live_session_counted_once(self):
        channel = "49d2c3cf8f0b2843d934ec15e82bb54c"
        old = {"status": "CLOSE"}
        opened = {"status": "OPEN", "live_id": "abc"}
        hub.record_transition(channel, old, opened, NOW)
        hub.record_transition(channel, opened, opened, NOW + timedelta(minutes=89, seconds=50))
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(minutes=90, seconds=10))
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(minutes=92))
        stats = hub._stats(NOW + timedelta(hours=2))
        self.assertEqual(stats["hanayo"]["count"], 1)
        self.assertEqual(stats["hanayo"]["seconds"], 5400)

    def test_verified_anniversaries_and_annual_rollover(self):
        upcoming = hub._milestones(NOW)
        labels = [(group, title, years) for days, group, title, md, years, source in upcoming]
        self.assertIn(("hanayo", "데뷔", 1), labels)
        self.assertIn(("sakihane", "데뷔", 2), labels)
        self.assertIn(("hanayo", "생일", None), labels)
        self.assertIn(("sakihane", "생일", None), labels)
        self.assertTrue(all(source.startswith("https://") for _, _, _, _, _, source in upcoming))

    def test_milestone_groups_and_compact_stats(self):
        payload = hub.render(NOW)
        parts = payload["components"][0]["components"]
        card = next(p["content"] for p in parts if p.get("content", "").startswith("### 🎉"))
        self.assertIn("**하나요 히요리**", card)
        self.assertIn("**사키하네 후야**", card)
        self.assertLess(card.index("첫 커버곡"), card.index("**사키하네 후야**"))
        self.assertIn("ABYSS 첫 싱글 발매", card)
        self.assertIn("3D 데뷔", card)
        stats = next(p["content"] for p in parts if p.get("content", "").startswith("### 📊"))
        self.assertIn("수집된 종료 방송 기준", stats)
        self.assertNotIn("종료 감지 오차 범위", stats)

    def test_api_start_time_and_bounded_end_are_stored(self):
        channel = "49d2c3cf8f0b2843d934ec15e82bb54c"
        opened = {"status": "OPEN", "live_id": "newstream",
                  "open_date": "2026-11-16 17:00:00"}
        hub.record_transition(channel, {}, opened, NOW)
        hub.record_transition(channel, opened, opened, NOW + timedelta(seconds=10))
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(seconds=20))
        rows = hub._read(hub.SESSIONS)["sessions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["started"], "2026-11-16T17:00:00+09:00")
        self.assertEqual(rows[0]["start_source"], "api_open_date")
        self.assertEqual(rows[0]["seconds_min"], 3610)
        self.assertEqual(rows[0]["seconds_max"], 3620)
        self.assertNotIn("17:00:00", str(hub.render(NOW + timedelta(minutes=1))))

    def test_network_gap_over_five_minutes_not_counted_as_precise_duration(self):
        channel = "36ddb9bb4f17593b60f1b63cec86611d"
        opened = {"status": "OPEN", "live_id": "huya1",
                  "open_date": "2026-11-16 17:00:00"}
        hub.record_transition(channel, {}, opened, NOW)
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(minutes=20))
        self.assertEqual(hub._stats(NOW + timedelta(minutes=21))["sakihane"]["count"], 0)

    def test_unknown_status_does_not_end_live(self):
        channel = "49d2c3cf8f0b2843d934ec15e82bb54c"
        opened = {"status": "OPEN", "live_id": "abc",
                  "open_date": "2026-11-16 17:00:00"}
        hub.record_transition(channel, {}, opened, NOW)
        hub.record_transition(channel, opened, {"status": "UNKNOWN"}, NOW + timedelta(seconds=10))
        self.assertIn(channel, hub._read(hub.SESSIONS)["active"])

    def test_reuses_original_clip_message_even_if_old_state_points_elsewhere(self):
        hub.save_json(hub.STATE, {"message_id": "999"})
        env = {"DISCORD_BOT_TOKEN": "dummy", "CHZZK_CLIP_ENABLED": "0",
               "CHZZK_CLIP_CHANNEL_ID": "111", "CHZZK_CLIP_MESSAGE_ID": "222",
               "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
               "COMMUNITY_DASHBOARD_MESSAGE_ID": "222"}
        with patch.dict("os.environ", env), \
             patch.object(hub, "update_original", return_value="updated") as edit:
            post = Mock()
            result = hub.sync_once(NOW, post=post)
        self.assertEqual(result["message_id"], "222")
        self.assertEqual(hub._read(hub.STATE)["message_id"], "222")
        self.assertEqual(edit.call_args.args[:2], ("111", "222"))
        post.assert_not_called()

    def test_cannot_create_or_edit_if_original_message_not_configured(self):
        env = {"DISCORD_BOT_TOKEN": "dummy", "CHZZK_CLIP_ENABLED": "0",
               "CHZZK_CLIP_CHANNEL_ID": "111", "CHZZK_CLIP_MESSAGE_ID": "222",
               "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
               "COMMUNITY_DASHBOARD_MESSAGE_ID": ""}
        with patch.dict("os.environ", env), patch.object(hub, "update_original") as edit:
            post = Mock()
            result = hub.sync_once(NOW, post=post)
        self.assertEqual(result["status"], "not_configured_or_clip_active")
        post.assert_not_called()
        edit.assert_not_called()

    def test_content_change_refreshes_without_timestamp_only_edits(self):
        env = {"DISCORD_BOT_TOKEN": "dummy", "CHZZK_CLIP_ENABLED": "0",
               "CHZZK_CLIP_CHANNEL_ID": "111", "CHZZK_CLIP_MESSAGE_ID": "222",
               "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
               "COMMUNITY_DASHBOARD_MESSAGE_ID": "222"}
        with patch.dict("os.environ", env), patch.object(
                hub, "update_original", return_value="updated") as edit:
            self.assertEqual(hub.sync_once(NOW, force=False)["status"], "updated")
            self.assertEqual(hub.sync_once(NOW + timedelta(seconds=5),
                                          force=False)["status"], "up_to_date")
            self.assertEqual(edit.call_count, 1)
            hub.save_json(hub.SCHEDULE, {"hanayo:2026-11-17:unspecified": {
                "time": "21:00", "status": "scheduled", "source": ""}})
            self.assertEqual(hub.sync_once(NOW + timedelta(seconds=10),
                                          force=False)["status"], "updated")
            self.assertEqual(edit.call_count, 2)

    def test_failed_refresh_retries_same_data(self):
        env = {"DISCORD_BOT_TOKEN": "dummy", "CHZZK_CLIP_ENABLED": "0",
               "CHZZK_CLIP_CHANNEL_ID": "111", "CHZZK_CLIP_MESSAGE_ID": "222",
               "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
               "COMMUNITY_DASHBOARD_MESSAGE_ID": "222"}
        with patch.dict("os.environ", env), patch.object(
                hub, "update_original", side_effect=["failed", "updated"]) as edit:
            self.assertEqual(hub.sync_once(NOW, force=False)["status"], "update_failed")
            self.assertEqual(hub.sync_once(NOW, force=False)["status"], "updated")
            self.assertEqual(edit.call_count, 2)

    def test_clip_worker_must_be_paused(self):
        env = {"DISCORD_BOT_TOKEN": "dummy", "CHZZK_CLIP_ENABLED": "1",
               "CHZZK_CLIP_CHANNEL_ID": "111", "CHZZK_CLIP_MESSAGE_ID": "222",
               "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
               "COMMUNITY_DASHBOARD_MESSAGE_ID": "222"}
        with patch.dict("os.environ", env), patch.object(hub, "update_original") as edit:
            result = hub.sync_once(NOW)
        self.assertEqual(result["status"], "not_configured_or_clip_active")
        edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
