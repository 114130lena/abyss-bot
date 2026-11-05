"""Hourly CHZZK editor regression tests: same ID, fresh complete scan, safe first POST."""
import json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
import chzzk_hiyoriclip_collector as collector
import hiyori_hourly_clip_updater as h

NOW=datetime(2026,11,5,16,5,0,tzinfo=ZoneInfo("Asia/Seoul"))
UID1="KvK9UOrTYv"
UID2="etEcAG0f3q"
BOT="1567554486908493854"
MID="234567890123456789"

def clip(uid,views):
    return {"id":uid,"title":"재생 테스트","views":views,"url":"https://chzzk.naver.com/clips/"+uid,
       "created_at":(NOW-timedelta(hours=1)).isoformat(),"channel_id":collector.CHANNEL,
       "video_id":"A"*32,"thumbnail_url":"https://example.org/p.jpg"}
def resp(status,body):
    r=Mock(status_code=status)
    r.raise_for_status.side_effect=None
    r.json.return_value=body
    return r
def fake_card_message(mid):
    return {"id":mid,"author":{"id":BOT},
      "components":[{"type":17,"components":[{"type":10,"content":"🎬 오늘의 히요리 클립"}]}]}
def session(existing=None):
    s=Mock()
    s.get.side_effect=lambda u,**k:(resp(200,{"id":BOT}) if u.endswith("/users/@me") else
      resp(200,{"guild_id":h.GUILD,"type":0}) if u.endswith("/channels/"+h.CHANNEL) else
      resp(200,fake_card_message(existing)) if existing and u.endswith("/messages/"+existing) else
      resp(200,[]))
    s.post.return_value=resp(200,fake_card_message(MID))
    s.patch.return_value=resp(200,fake_card_message(MID))
    return s
def fetch(**kwargs):
    return {"complete_24h_scan":True,"safe_for_daily_selection":True,"checked_at":kwargs["now"].isoformat()}
class HourlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/"state.json"
        self.idx=Path(self.tmp.name)/"index.json"
        collector._write(self.idx,{"clips":{UID1:clip(UID1,100),UID2:clip(UID2,150)}})
        self.p1=patch.object(h,"STATE",self.state)
        self.p2=patch.object(collector,"INDEX",self.idx)
        self.p1.start();self.p2.start()
        self.addCleanup(self.p1.stop);self.addCleanup(self.p2.stop)
    def test_overnight_and_morning_skip_without_collection(self):
        s=session()
        for hour in (0,2,4,6,8,10,14,18,22,23):
            result=h.refresh(now=NOW.replace(hour=hour),session=s,collect_fn=lambda **kw: self.fail("must not collect outside active period"))
            self.assertEqual(result["status"],"outside_active_hours")
        s.post.assert_not_called()
        s.patch.assert_not_called()
    def test_top_ten_and_archived_featured_links(self):
        now=NOW
        clips={f"clip{idx:06d}":clip(f"clip{idx:06d}",idx) for idx in range(1,19)}
        ranked=h.ranked(clips,now)
        self.assertEqual(len(ranked),18)
        self.assertEqual(h.SHOW_TOP,10)
        body=h._extra_links(ranked,[{"id":"previous987","title":"지난 영상"}])
        self.assertEqual(body.count("https://chzzk.naver.com/clips/"),11)
        self.assertIn("이전에 소개한 클립",body)
        self.assertNotIn("조회수",body)
        self.assertNotIn("🏆",body)
    def test_select_top_24h_and_hidden_metrics(self):
        best=h.choose(collector.read(self.idx)["clips"],NOW)
        self.assertEqual(best["id"],UID2)
        with patch.object(h,"_identity",return_value=BOT):
            s=session()
            r=h.refresh(now=NOW,session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/x.mp4")
        self.assertEqual(r["status"],"created")
        self.assertEqual(r["message_id"],MID)
        payload=s.post.call_args.kwargs["json"]
        body=json.dumps(payload,ensure_ascii=False)
        self.assertIn("12:05 · 16:05 · 20:05 갱신",body)
        self.assertIn("영상 목록",body)
        self.assertIn("example.org/x.mp4",body)
        self.assertEqual(len([i for i in payload["components"][0]["components"] if i["type"]==12]),1)
        self.assertEqual(len([i for i in payload["components"][0]["components"] if i["type"]==12][0]["items"]),2)
        for invalid in ("조회수","재생시간","🏆","1위","TODAY'S HIYORI CLIP","<@&"):
            self.assertNotIn(invalid,body)
        self.assertEqual(payload["allowed_mentions"],{"parse":[]})
        self.assertNotIn("username",payload)
    def test_same_card_updated_next_four_hour_slot_only(self):
        collector._write(self.state,{"message_id":MID,"channel_id":h.CHANNEL})
        s=session(existing=MID)
        with patch.object(h,"_identity",return_value=BOT):
            first=h.refresh(now=NOW,session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
            again=h.refresh(now=NOW,session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
            next_hour=h.refresh(now=NOW+timedelta(hours=4),session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
        self.assertEqual((first["status"],again["status"],next_hour["status"]),
                         ("updated","already_updated_this_hour","updated"))
        s.post.assert_not_called()
        self.assertEqual(s.patch.call_count,2)
        self.assertEqual(first["message_id"],next_hour["message_id"])
    def test_create_failure_keeps_pending_and_no_second_post(self):
        s=session()
        import requests
        s.post.side_effect=requests.Timeout()
        with patch.object(h,"_identity",return_value=BOT):
            with self.assertRaises(requests.Timeout):
                h.refresh(now=NOW,session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
            result=h.refresh(now=NOW+timedelta(hours=4),session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
        self.assertEqual(result["status"],"pending_first_post_manual_reconciliation")
        self.assertTrue(collector.read(self.state)["create_pending"])
        s.post.assert_called_once()
    def test_incomplete_scan_avoids_writing(self):
        s=session()
        with self.assertRaisesRegex(RuntimeError,"incomplete"):
            h.refresh(now=NOW,session=s,collect_fn=lambda **kw:{"complete_24h_scan":False})
        s.post.assert_not_called()
        s.patch.assert_not_called()
    def test_no_recent_candidate_keeps_prior(self):
        collector._write(self.idx,{"clips":{UID1:{**clip(UID1,100),
            "created_at":(NOW-timedelta(hours=26)).isoformat()}}})
        result=h.refresh(now=NOW,session=session(),collect_fn=fetch)
        self.assertEqual(result["status"],"no_eligible_recent_clips_keep_previous")
    def test_recover_bot_message_without_known_state(self):
        s=session()
        def get(url,**kwargs):
            if url.endswith("/messages") and kwargs.get("params",{}).get("limit")==100:
                return resp(200,[fake_card_message(MID)])
            return session().get(url,**kwargs)
        s.get.side_effect=get
        with patch.object(h,"_identity",return_value=BOT):
            result=h.refresh(now=NOW,session=s,collect_fn=fetch,video_fn=lambda uid:"https://example.org/"+uid+".mp4")
        self.assertEqual(result["status"],"updated")
        s.post.assert_not_called()
        s.patch.assert_called_once()
if __name__=="__main__":
    unittest.main()
