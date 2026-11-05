"""Deterministic tests for 21:00 KST 24-hour daily clip selection."""
import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from daily_hiyoriclip_selector import select_one, rank_clips, DAILY_RECOMMENDATION_HOUR_KST

NOW=datetime(2026,11,5,12,0,tzinfo=ZoneInfo("Asia/Seoul"))

def clip(id, age=1, views=500, likes=30, reposts=5, tag="#hiyoriclip_", video=True):
    return {"id":str(id),"text":"테스트 "+tag,
            "created_timestamp":(NOW-timedelta(hours=age)).timestamp(),
            "author":{"screen_name":"clipper"},
            "media":{"videos":[{"id":str(id),"url":"https://video.twimg.com/movie"+str(id)+".mp4"}] if video else []},
            "views":views,"likes":likes,"reposts":reposts}

class ClipTests(unittest.TestCase):
    def test_daily_publication_at_noon(self):
        self.assertEqual(DAILY_RECOMMENDATION_HOUR_KST,12)
        self.assertEqual(NOW.hour,12)

    def test_selects_highest_overall(self):
        rows=[clip(1,views=100,likes=1,reposts=0),clip(2,views=1000,likes=200,reposts=100)]
        self.assertEqual(select_one(rows,now=NOW)["id"],"2")
    def test_excludes_outside_24_hours_and_future(self):
        self.assertIsNone(select_one([clip(1,age=24.01),clip(2,age=-0.001)],now=NOW))
        self.assertEqual(select_one([clip(3,age=23.99)],now=NOW)["id"],"3")
    def test_requires_video_not_only_hashtag_or_photo(self):
        self.assertIsNone(select_one([clip(1,video=False),clip(2,tag="#hiyoriart_")],now=NOW))
    def test_case_insensitive_exact_tag(self):
        self.assertIsNotNone(select_one([clip(1,tag="#HIYORICLIP_")],now=NOW))
        self.assertIsNone(select_one([clip(2,tag="#hiyoriclip_bad")],now=NOW))
    def test_prior_winner_excluded(self):
        self.assertEqual(select_one([clip(1),clip(2)],now=NOW,used_ids=["1"])["id"],"2")
    def test_missing_all_views_still_ranks_by_likes_and_reposts(self):
        selected=select_one([clip(1,views=None,likes=100,reposts=10),
                            clip(2,views=None,likes=1,reposts=1)],now=NOW)
        self.assertIsNone(selected["views"])
        self.assertEqual(selected["id"],"1")
    def test_missing_metrics_not_fabricated_as_zero(self):
        self.assertIsNone(select_one([clip(1,likes=None)],now=NOW))
    def test_duplicate_video_is_not_double_counted(self):
        a,b=clip(1),clip(2)
        b["media"]["videos"][0]["id"]="1"
        self.assertEqual(len(rank_clips([a,b],now=NOW)),1)
    def test_video_repost_not_selected(self):
        row=clip(1)
        row["type"]="repost"
        self.assertIsNone(select_one([row],now=NOW))
    def test_scores_are_0_to_100(self):
        for row in rank_clips([clip(1),clip(2,views=5000,likes=100,reposts=50)],now=NOW):
            self.assertTrue(0<=row["score"]<=100)
if __name__=="__main__":
    unittest.main()
