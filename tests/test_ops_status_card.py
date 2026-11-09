"""Read-only status formatting never leaks raw source details."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.ops_status_card import render


class StatusCardTests(unittest.TestCase):
    def test_only_aggregated_values_and_fixed_schedule(self):
        secret = "DONT_SHOW_ARTIST_OR_WEBHOOK_SECRET"
        sources = {
            "히요리 X": {"status": "ok", "last_success_age_minutes": 2,
                          "last_post": secret},
            "후야 X": {"status": "stale", "last_success_age_minutes": 40},
        }
        components = {
            "fanart": {"groups": {
                "hanayo": {"posts": 3, "verified_cached_images": 4,
                           "missing_cached_images": 1,
                           "hashtag_source_status": "unavailable",
                           "artist_fallback_status": "ok", "post_url": secret},
                "sakihane": {"posts": 2, "verified_cached_images": 2}}},
            "banner": {"unused_candidates": 2, "total_candidates": 5,
                       "post_url": secret},
            "clip": {"cached_clips": 10, "collected_age_hours": 1,
                     "last_title": secret},
            "discord_uncertain_deliveries": 2,
            "warning_codes": ["fanart_hashtag_search"],
            "raw_exception": secret,
        }
        result = render(sources, components, datetime(2026, 11, 9, 18, 0,
                           tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertIn("08:00 / 18:00 KST", result)
        self.assertIn("전체 검색 이용 불가", result)
        self.assertIn("전송 결과 확인 필요: 2건", result)
        self.assertIn("2026-11-09 18:00 KST", result)
        self.assertNotIn(secret, result)

    def test_empty_state_reports_unverified_not_normal(self):
        result = render({}, {}, datetime(2026, 11, 9, 8, 0,
                        tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertIn("히요리 X: 미확인", result)
        self.assertIn("사용 가능한 미사용 후보: 미확인", result)
        self.assertIn("전송 결과 확인 필요: 0건", result)


if __name__ == "__main__":
    unittest.main()
