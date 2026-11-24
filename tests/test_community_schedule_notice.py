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

    def test_real_huya_nov24_notice_separates_tomorrow_break(self):
        result = notice.record_schedule(
            "sakihane", "11월 24일 방송 일정!",
            "오늘 방송 6시 부터 시작합니다!\n어제 하려고했던 좀비게임 제드 존 해보려고해용\n솔플임!\n25일은 휴방입니다~\n우에엥 졸린다~ 다들 이따보아요!\n게임 재밌겠다!",
            1795504478853, URL)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["time"], "18:00")
        state = notice._read(self.state)
        self.assertEqual(state["sakihane:2026-11-25:unspecified"]["status"], "break")

    def test_real_hiyori_space_repairs_wrong_next_day(self):
        notice._save(self.state, {"hanayo:2026-11-24:unspecified": {
            "status": "scheduled", "time": "23:00", "source": URL}})
        result = notice.record_schedule(
            "hanayo", "월요일은 . . !",
            "내일 낮일정을 위해 방송을 쉬어갑니다아..!!\n오늘은 대신 밤 11시 스페로 만나요!!!",
            1795438230447, URL)
        self.assertEqual(result["status"], "break")
        self.assertNotIn("hanayo:2026-11-24:unspecified", notice._read(self.state))
        self.assertIn("hanayo:2026-11-23:unspecified", notice._read(self.state))
        space = notice._read(self.state)["hanayo:2026-11-23:x_space"]
        self.assertEqual(space["status"], "space")
        self.assertEqual(space["platform"], "x")
        self.assertEqual(space["time"], "23:00")
        card = str(dashboard.render(datetime(2026, 11, 23, 22, tzinfo=KST)))
        self.assertIn("X 스페이스", card)
        self.assertNotIn("X 스페이스", str(dashboard.render(datetime(2026, 11, 24, 10, tzinfo=KST))))

    def test_space_only_notice_is_not_chzzk_broadcast(self):
        events = notice.analyze_schedule("오늘 공지", "오늘 밤 11시 스페이스 진행합니다", POST)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "space")
        self.assertEqual(events[0]["time"], "23:00")

    def test_real_friday_late_notice_ignores_yesterday_morning(self):
        events = notice.analyze_schedule(
            "금요일 ! !", "11:30 방송 입니다!!\n어제 아침에 방종하구 자고\n평소보단 짧방이 될 것 같아요\n오늘 넘 늦게 안자도록 할께요! 이따 방송에서 만나요",
            "2026-11-20T20:52:00+09:00")
        self.assertEqual(events[0]["time"], "23:30")
        self.assertEqual(events[0]["play_type"], "짧은 방송")

    def test_tomorrow_colon_clock_is_ambiguous(self):
        events = notice.analyze_schedule("내일 방송 공지", "내일 9:30 방송", POST)
        self.assertEqual(events[0]["time"], "")
        self.assertIn("오전/오후 미확인", events[0]["time_note"])

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
