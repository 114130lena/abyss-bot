"""Video shortlist: duplicate original media, current window and privacy."""
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hiyoriclip_video_candidates as mod
NOW=datetime(2026,11,5,12,tzinfo=timezone.utc)

def make(i, media=None, age=1, likes=4):
    return {"id":str(i),"author":"@test","url":f"https://x.com/test/status/{i}",
      "created_at":(NOW-timedelta(hours=age)).isoformat(),
      "text":"test #hiyoriclip_","video_ids":[media or f"v{i}"],
      "video_urls":[f"https://video.twimg.com/ext_tw_video/{media or i}.mp4"],
      "views":20,"likes":likes,"reposts":2,"discovered_via":["profile:test"]}
class CandidatesTest(unittest.TestCase):
    def test_dedup_same_video_preserves_more_reacted_upload(self):
        result=mod.shortlist({"posts":{"1":make(1,"shared"),"2":make(2,"shared",likes=10)}},now=NOW)
        self.assertEqual(result["video_posts_before_dedup"],2)
        self.assertEqual(result["duplicate_video_reposts"],1)
        self.assertEqual(result["unique_videos"],1)
        self.assertEqual(result["candidates"][0]["id"],"2")
    def test_old_items_not_mixed_with_recent(self):
        result=mod.shortlist({"posts":{"1":make(1,age=30),"2":make(2)}},now=NOW)
        self.assertEqual(result["unique_videos"],1)
    def test_only_direct_verified_mp4_is_exported(self):
        item=make(1)
        item["video_urls"]=["https://evil.example/clip.mp4"]
        result=mod.shortlist({"posts":{"1":item}},now=NOW)
        self.assertIsNone(result["candidates"][0]["mp4_url"])
    def test_private_atomic_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"candidates.json"
            mod.save_atomic(path,{"a":1})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.read_text(),"{"+"\n  \"a\": 1\n}")
if __name__=="__main__": unittest.main()
