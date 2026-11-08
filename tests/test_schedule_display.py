"""08:00 / 18:00 KST Discord schedule + auto-updating relative time tests."""
import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule_display
import guild_server_banner as banners
import chzzk_clip_digest as clips

KST = ZoneInfo("Asia/Seoul")


def kst(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


class ScheduleDisplayTests(unittest.TestCase):
    def test_today_morning_and_evening_boundary(self):
        self.assertEqual(schedule_display.next_update(kst(8, 7, 59)), kst(8, 8))
        self.assertEqual(schedule_display.next_update(kst(8, 8)), kst(8, 18))
        self.assertEqual(schedule_display.next_update(kst(8, 17, 59)), kst(8, 18))
        self.assertEqual(schedule_display.next_update(kst(8, 18)), kst(9, 8))
        self.assertEqual(schedule_display.next_update(kst(8, 21, 46)), kst(9, 8))
        self.assertEqual(schedule_display.next_update(kst(9, 0)), kst(9, 8))

    def test_absolute_and_relative_discord_timestamps_use_same_unix_second(self):
        now = kst(8, 21, 46)
        stamp = int(kst(9, 8).timestamp())
        label = schedule_display.schedule_label(now)
        self.assertIn("매일 08:00 / 18:00 KST", label)
        self.assertIn(f"<t:{stamp}:F>", label)
        self.assertIn(f"<t:{stamp}:R>", label)
        self.assertEqual(len(re.findall(r"<t:\d+:[FR]>", label)), 2)

    def test_banner_card_shows_schedule_but_not_internal_audit_error(self):
        item = banners._banner_card("123", "hash", {
            "banner_change_method": "기존 기록 · 변경 주체 미상",
            "banner_audit_status": "permission_denied",
            "artist": "@artist",
            "post_url": "https://x.com/artist/status/123"})
        text = str(item["components"])
        self.assertIn("정기 업데이트", text)
        self.assertIn("다음 업데이트", text)
        self.assertIn("업데이트까지", text)
        self.assertIn("이전 배너 · 변경 방식 미확인", text)
        self.assertNotIn("Discord 감사 로그 권한 없음", text)
        self.assertIn("원본 게시물", text)
        self.assertEqual(item["flags"], 32768)

    def test_clip_card_shows_same_schedule_for_publishing_not_collection(self):
        now = kst(8, 18)
        clip = {"id": "clip123456", "title": "오늘의 클립",
                "url": "https://chzzk.naver.com/clips/clip123456",
                "thumbnail": "", "views": 1, "date": now.isoformat()}
        item = clips._render([clip], {}, now=now)
        content = " ".join(str(p.get("content", ""))
                           for p in item["components"][0]["components"])
        stamp = int(kst(9, 8).timestamp())
        self.assertIn(f"<t:{stamp}:R>", content)
        self.assertIn(f"<t:{stamp}:F>", content)
        self.assertIn("매일 08:00 / 18:00 KST", content)
        self.assertNotIn("2시간마다 게시", content)
        self.assertEqual(item["flags"], 32768)


if __name__ == "__main__":
    unittest.main()
