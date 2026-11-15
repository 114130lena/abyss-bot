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
        self.assertIn("검증된 기념일을 등록하면", text)
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
        hub.record_transition(channel, opened, opened, NOW + timedelta(minutes=10))
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(minutes=90))
        hub.record_transition(channel, opened, {"status": "CLOSE"},
                              NOW + timedelta(minutes=92))
        stats = hub._stats(NOW + timedelta(hours=2))
        self.assertEqual(stats["hanayo"]["count"], 1)
        self.assertEqual(stats["hanayo"]["seconds"], 5400)

    def test_explicit_existing_message_is_edited_not_posted(self):
        with patch.dict("os.environ", {
                "DISCORD_BOT_TOKEN": "dummy", "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
                "COMMUNITY_DASHBOARD_MESSAGE_ID": "222"}), \
             patch.object(hub, "update_original", return_value="updated") as edit:
            post = Mock()
            result = hub.sync_once(NOW, post=post)
            self.assertEqual(result["message_id"], "222")
            post.assert_not_called()
            edit.assert_called_once()

    def test_failed_discovery_never_creates_duplicate(self):
        with patch.dict("os.environ", {
                "DISCORD_BOT_TOKEN": "dummy", "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
                "COMMUNITY_DASHBOARD_MESSAGE_ID": ""}):
            post = Mock()
            result = hub.sync_once(NOW, get=Mock(return_value=response(403, {})),
                                   post=post)
            self.assertEqual(result["status"], "discovery_unavailable")
            post.assert_not_called()

    def test_reuses_discovered_message(self):
        stored = {"id": "333", "components": [{"type": 10,
                  "content": hub.MARKER}]}
        with patch.dict("os.environ", {
                "DISCORD_BOT_TOKEN": "dummy", "COMMUNITY_DASHBOARD_CHANNEL_ID": "111",
                "COMMUNITY_DASHBOARD_MESSAGE_ID": ""}), \
             patch.object(hub, "update_original", return_value="updated"):
            post = Mock()
            result = hub.sync_once(NOW, get=Mock(return_value=response(200, [stored])),
                                   post=post)
            self.assertEqual(result["message_id"], "333")
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
