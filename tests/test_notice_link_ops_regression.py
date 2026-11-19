import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import discord_v2
import notice_intelligence as notice
import x_post_media

class RegressionTests(unittest.TestCase):
    def test_link_and_caption(self):
        source = "[https://nitter.gq/t.co/XesSFAUhV8](https://x.com/SakihaneHuya/status/2123322278360592681)\n후야 급히 오다"
        with patch.object(x_post_media, "_expand_space_shortlink", return_value=""):
            label, body = x_post_media.describe_x_link_post(source)
        self.assertIn("링크 게시물", label)
        self.assertIn("후야 급히 오다", body)
        self.assertNotIn("nitter.gq", body)
        self.assertEqual(discord_v2._x_headline(source)[0], "후야 급히 오다")

    def test_dated_secondary_break(self):
        title = "11월 19일 방송 일정!"
        body = "오늘은 GTA6를 합니다!\n11월 20일은 휴방입니다!"
        self.assertEqual(discord_v2._notice_type(title, body), "📺 방송 예고")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "schedule.json"
            with patch.object(notice, "SCHEDULE_FILE", state):
                result = notice.record_schedule("sakihane", title, body,
                    "2026-11-19T16:37:00+09:00", "https://cafe.naver.com/test/123")
                self.assertEqual(result["status"], "scheduled")
                rows = notice._read(state)
                self.assertEqual(rows["sakihane:2026-11-20:unspecified"]["status"], "break")
                self.assertEqual(result["time_note"], "시간 미정")

if __name__ == "__main__":
    unittest.main()
