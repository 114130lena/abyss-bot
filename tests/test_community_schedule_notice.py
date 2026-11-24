"""Regression tests for advertised Cafe broadcast times in the community panel."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import community_dashboard as dashboard
import notice_intelligence as notice

KST = ZoneInfo("Asia/Seoul")
POST = "2026-11-16T16:30:00+09:00"
URL = "https://cafe.naver.com/f-e/cafes/29424353/articles/123?menuid=431"


class CafeScheduleTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "notice_schedule_state.json"
        self.patches = [
            patch.object(notice, "SCHEDULE_FILE", self.state),
            patch.object(dashboard, "SCHEDULE", self.state),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_negated_break_same_day_nine_pm_from_afternoon_notice(self):
        result = notice.record_schedule(
            "hanayo", "수요일은 ! !",
            "휴방이 아닌\n9시 방송 입니다 !\n이따가 생방에서 바요 ~",
            POST, URL)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["time"], "21:00")
        card = str(dashboard.render(datetime(2026, 11, 16, 17, tzinfo=KST)))
        self.assertIn("11/16 21:00 KST", card)

    def test_same_day_saturday_evening_cafe_notice(self):
        result = notice.record_schedule(
            "hanayo", "토요일 ! !",
            "하요링 ! !\n저녁 먹으면서 일을 끝내구있습니닷\n토요일 방송은\n9:00 이에요 ! ! !\n곧 방송에서 만나요",
            "2026-11-21T19:38:00+09:00",
            "https://cafe.naver.com/f-e/cafes/29424353/articles/1143080?menuid=431")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["time"], "21:00")
        card = str(dashboard.render(datetime(2026, 11, 21, 20, tzinfo=KST)))
        self.assertIn("11/21 21:00 KST", card)

    def test_tomorrow_with_explicit_evening_clock(self):
        result = notice.record_schedule(
            "sakihane", "내일 방송 공지", "내일 오후 8시 30분 방송 예정",
            POST, URL)
        self.assertEqual(result["time"], "20:30")
        self.assertIn("sakihane:2026-11-17", str(notice._read(self.state)))

    def test_month_day_and_24_hour_time(self):
        result = notice.record_schedule(
            "hanayo", "방송 일정", "11월 17일 21:00 방송 시작", POST, URL)
        self.assertEqual(result["time"], "21:00")

    def test_bare_clock_is_ambiguous_when_posted_before_noon(self):
        result = notice.record_schedule(
            "hanayo", "내일 방송 공지", "내일 9시 방송", POST, URL)
        self.assertEqual(result["time"], "")
        self.assertIn("오전/오후 미확인", result["time_note"])
        card = str(dashboard.render(datetime(2026, 11, 16, 17, tzinfo=KST)))
        self.assertIn("9시 (오전/오후 미확인)", card)

    def test_multiple_conflicting_times_dont_guess(self):
        result = notice.record_schedule(
            "hanayo", "오늘 방송 공지", "오늘 오후 8시 혹은 9시 방송",
            POST, URL)
        self.assertIsNone(result)
        # A conflicting 20:00 / 9시 announcement must not be guessed.

    def test_changed_old_time_ignored(self):
        result = notice.record_schedule(
            "hanayo", "일정 변경 안내",
            "내일 방송\n변경 전 20:00\n변경 후 21:00",
            POST, URL)
        self.assertEqual(result["time"], "21:00")

    def test_newer_notice_updates_time_without_change_keyword(self):
        notice.record_schedule("hanayo", "오늘 방송", "오늘 오후 8시 방송", POST, URL)
        result = notice.record_schedule(
            "hanayo", "오늘 방송", "오늘 오후 9시 방송",
            "2026-11-16T17:30:00+09:00", URL + "&new=1")
        self.assertEqual(result["time"], "21:00")
        notice.record_schedule("hanayo", "오늘 방송", "오늘 오후 8시 방송", POST, URL)
        self.assertEqual(next(iter(notice._read(self.state).values()))["time"], "21:00")

    def test_missing_publication_date_never_invents_today(self):
        self.assertIsNone(notice.record_schedule(
            "hanayo", "오늘 방송", "오후 9시 방송", "", URL))

    def test_reconcile_recent_seen_cafe_notice_without_notifying(self):
        article = {"id": "123", "title": "오늘 방송",
                   "date": POST, "url": URL}
        boards = [{"menu_id": "431", "group": "hanayo"}]
        with patch("cafe_notifier.BOARDS", boards), \
             patch("cafe_notifier.get_articles", return_value=[article]), \
             patch("cafe_notifier.article_detail", return_value=("오늘 오후 9시 방송합니다", [])):
            count = notice.reconcile_recent_cafe_schedules(
                datetime(2026, 11, 16, 17, tzinfo=KST))
        self.assertEqual(count, 1)
        self.assertTrue(self.state.exists())


if __name__ == "__main__":
    unittest.main()
