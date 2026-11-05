"""Offline behavior regressions for the 18:00 KST CHZZK clip publisher."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo

import chzzk_hiyoriclip_collector as collector
import hiyori_chzzk_daily_publisher as pub
from test_hiyori_chzzk_daily_card import make_card

NOW=datetime(2026,11,5,18,0,15,tzinfo=ZoneInfo("Asia/Seoul"))
UID_A="KvK9UOrTYv"
UID_B="etEcAG0f3q"

def sample(uid,views=10,created=None):
    when=created or NOW-timedelta(hours=2)
    return {"id":uid,"title":"헉","url":"https://chzzk.naver.com/clips/"+uid,
      "created_at":when.isoformat(),"views":views,
      "video_id":"A"*32,"channel_id":collector.CHANNEL,"thumbnail_url":"https://example.org/thumb.jpg"}

def response():
    res=Mock()
    res.raise_for_status.return_value=None
    res.json.return_value={"id":"123456789012345678"}
    return res

class PublicationTests(unittest.TestCase):
    def test_fixed_window_is_previous_18_to_current_18(self):
        a,b=pub.scheduled_window(NOW)
        self.assertEqual((a.hour,b.hour),(18,18))
        self.assertEqual((b-a).total_seconds(),24*3600)
        with self.assertRaises(ValueError):
            pub.scheduled_window(NOW-timedelta(minutes=2))
        with self.assertRaises(ValueError):
            pub.scheduled_window(NOW+timedelta(minutes=13))
    def test_highest_views_and_previous_clip_excluded(self):
        start,end=pub.scheduled_window(NOW)
        rows={UID_A:sample(UID_A,400),UID_B:sample(UID_B,100)}
        self.assertEqual(pub.select_clip(rows,start,end,set())["id"],UID_A)
        self.assertEqual(pub.select_clip(rows,start,end,{UID_A})["id"],UID_B)
        old=sample("old111111",9000,NOW-timedelta(hours=25))
        rows[old["id"]]=old
        self.assertEqual(pub.select_clip(rows,start,end,set())["id"],UID_A)
    def test_card_hides_metrics_and_excludes_mentions(self):
        card=make_card(sample(UID_A,1234),now=NOW,video_url="https://cdn.example.org/test.mp4",preview=False)
        serialized=json.dumps(card,ensure_ascii=False)
        self.assertIn("오늘의 히요리 클립",serialized)
        self.assertIn("18:00 KST",serialized)
        self.assertNotIn("12:00 KST",serialized)
        self.assertNotIn("TODAY'S HIYORI CLIP",serialized)
        for hidden in ("조회수","트로피","재생시간","1위","선정 기준","TEST PREVIEW","1234"):
            self.assertNotIn(hidden,serialized)
        self.assertEqual(card["allowed_mentions"],{"parse":[]})
        self.assertIn("test.mp4",serialized)
    def test_incomplete_scan_must_not_send(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pub,"STATE",Path(folder)/"state.json"),                 patch.object(pub,"destination",return_value="https://discord.com/api/webhooks/fake/fake"):
                sess=Mock()
                with self.assertRaises(RuntimeError):
                    pub.publish(now=NOW,session=sess,collect_fn=lambda **kw:{"complete_24h_scan":False})
                sess.post.assert_not_called()
    def test_published_once_and_never_repost_same_day(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/"state.json"
            idx=Path(folder)/"index.json"
            collector._write(idx,{"clips":{UID_A:sample(UID_A,200),UID_B:sample(UID_B,150)}})
            fake=Mock()
            fake.post.return_value=response()
            calls=[]
            def fetch(**kw):
                calls.append(1)
                return {"complete_24h_scan":True,"safe_for_daily_selection":True,"checked_at":kw["now"].isoformat()}
            with patch.object(pub,"STATE",state),patch.object(collector,"INDEX",idx),                 patch.object(pub,"destination",return_value="https://discord.com/api/webhooks/test"),                 patch("banner_manager.get_banner",return_value=""),                 patch("creator_profiles.avatar",return_value=""):
                result=pub.publish(now=NOW,session=fake,collect_fn=fetch,media_fn=lambda uid:None)
                self.assertEqual(result["status"],"sent")
                self.assertEqual(result["clip_id"],UID_A)
                second=pub.publish(now=NOW,session=fake,collect_fn=fetch,media_fn=lambda uid:None)
                self.assertEqual(second["status"],"already_attempted")
                self.assertEqual(len(calls),1)
                fake.post.assert_called_once()
                _,kwargs=fake.post.call_args
                self.assertNotIn("조회수",json.dumps(kwargs["json"],ensure_ascii=False))
                self.assertEqual(collector.read(state)["days"]["2026-11-05"]["status"],"sent")
    def test_timeout_marked_uncertain_and_no_duplicate_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/"state.json"
            idx=Path(folder)/"idx.json"
            collector._write(idx,{"clips":{UID_A:sample(UID_A,200)}})
            session=Mock()
            session.post.side_effect=__import__("requests").Timeout("uncertain")
            def fetch(**kw):
                return {"complete_24h_scan":True,"safe_for_daily_selection":True,"checked_at":kw["now"].isoformat()}
            with patch.object(pub,"STATE",state),patch.object(collector,"INDEX",idx),                 patch.object(pub,"destination",return_value="https://discord.com/api/webhooks/test"),                 patch("banner_manager.get_banner",return_value=""),                 patch("creator_profiles.avatar",return_value=""):
                with self.assertRaisesRegex(RuntimeError,"manual review"):
                    pub.publish(now=NOW,session=session,collect_fn=fetch,media_fn=lambda uid:None)
                second=pub.publish(now=NOW,session=session,collect_fn=fetch,media_fn=lambda uid:None)
                self.assertEqual(second["status"],"already_attempted")
                session.post.assert_called_once()
                self.assertEqual(collector.read(state)["days"]["2026-11-05"]["status"],"delivery_uncertain")

if __name__=="__main__":
    unittest.main()
