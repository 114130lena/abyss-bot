"""Mocked CHZZK clips API paging, eligibility and disk persistence checks."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
import chzzk_hiyoriclip_collector as c

NOW=datetime(2026,11,5,12,tzinfo=ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)

def clip(uid,age=1,views=20,adult=False):
    return {"clipUID":uid,"ownerChannelId":c.CHANNEL,"videoId":"68334BD1AE1A2290D1670C6D1B1808F8CCED",
            "clipTitle":"히요리 예시","createdDate":(NOW-timedelta(hours=age)).astimezone(c.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "readCount":views,"duration":20,"thumbnailImageUrl":"https://example.org/a.jpg",
            "adult":adult,"blindType":None}

def mock_response(data,cursor):
    response=Mock()
    response.raise_for_status.return_value=None
    response.json.return_value={"code":200,"content":{"data":data,"page":{"next":cursor}}}
    return response

class ClipTests(unittest.TestCase):
    def test_reads_two_pages_and_filters_past_day(self):
        session=Mock()
        session.get.side_effect=[
            mock_response([clip("AAA111")],{"clipUID":"AAA111","readCount":20}),
            mock_response([clip("BBB222",age=23),clip("CCC333",age=25)],None)]
        rows,status=c.fetch_recent(session=session,now=NOW,max_pages=5)
        self.assertEqual(set(rows),{"AAA111","BBB222"})
        self.assertTrue(status["complete_24h_scan"])
        self.assertEqual(status["pages"],2)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["clipUID"],"AAA111")
    def test_unfinished_scan_is_never_complete(self):
        session=Mock()
        session.get.return_value=mock_response([clip("AAA111")],{"clipUID":"AAA111","readCount":20})
        _,state=c.fetch_recent(session=session,now=NOW,max_pages=1)
        self.assertFalse(state["complete_24h_scan"])
    def test_drops_adult_blinded_and_other_channel(self):
        item=clip("AAA111",adult=True)
        self.assertIsNone(c.clip_row(item,now=NOW))
        item=clip("AAA111")
        item["blindType"]="BLIND"
        self.assertIsNone(c.clip_row(item,now=NOW))
        item=clip("AAA111")
        item["ownerChannelId"]="someone_else"
        self.assertIsNone(c.clip_row(item,now=NOW))
    def test_non_video_or_missing_views_not_accepted(self):
        item=clip("AAA111",views=None)
        self.assertIsNone(c.clip_row(item,now=NOW))
        item=clip("AAA111")
        item.pop("videoId")
        self.assertIsNone(c.clip_row(item,now=NOW))
    def test_created_kst(self):
        self.assertEqual(c.parse_created("2026-11-05 11:00:00"),datetime(2026,11,5,2,tzinfo=timezone.utc))
    def test_persist_and_repeat_without_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(c,"INDEX",base/"clips.json"),patch.object(c,"STATUS",base/"state.json"):
                session=Mock()
                session.get.return_value=mock_response([clip("AAA111")],None)
                one=c.collect(session=session,now=NOW)
                two=c.collect(session=session,now=NOW)
                self.assertEqual(one["new_clips"],1)
                self.assertEqual(two["new_clips"],0)
                self.assertEqual(one["stored_7_days"],1)
                self.assertFalse(two["discord_publishing_enabled"])
    def test_bad_api_shape_rejected_without_overwriting_cache(self):
        session=Mock()
        session.get.return_value=Mock(json=Mock(return_value={"code":200,"content":{}}))
        with self.assertRaises(ValueError):
            c.fetch_recent(session=session,now=NOW)
    def test_incomplete_scan_not_marked_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(c,"INDEX",base/"clips.json"),patch.object(c,"STATUS",base/"state.json"),\
                 patch.object(c,"fetch_recent",return_value=({"AAA111":c.clip_row(clip("AAA111"),now=NOW)},
                    {"pages":12,"complete_24h_scan":False,"recent_video_count":1})):
                res=c.collect(now=NOW)
                self.assertFalse(res["safe_for_daily_selection"])

if __name__=="__main__":
    unittest.main()
