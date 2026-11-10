"""Regression tests for the Bluebird authenticated bridge to existing X workers."""
import importlib.util
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts/bluebird_live_collector.py"
WORKFLOW = ROOT / ".github/workflows/bluebird-live-bridge-activate.yml"

spec = importlib.util.spec_from_file_location("bluebird_live_collector", PRODUCER)
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


def post(username, sid, created_at, *, repost_of=None, reply=False, media=()):
    return types.SimpleNamespace(
        username=username, post_id=str(sid), created_at=created_at,
        text="artist media!", media=tuple(types.SimpleNamespace(url=url) for url in media),
        is_repost=repost_of is not None, is_reply=reply, is_quote=False,
        repost_of=(types.SimpleNamespace(username=repost_of[0], post_id=str(repost_of[1]))
                   if repost_of else None),
    )


class BluebirdLiveBridgeTests(unittest.TestCase):
    def test_producer_first_scan_baselines_with_zero_events_and_persists_seen(self):
        now=1794333000
        state={"version": 1, "users": {}}
        a=post("HanayoHiyori", 2120220000000000000,now-10)
        first,new=producer._update(state,"HanayoHiyori",types.SimpleNamespace(posts=[a],complete=True),now)
        self.assertTrue(first)
        self.assertEqual(new,0)
        self.assertEqual(len(state["users"]["hanayohiyori"]["seen"]),1)
        self.assertEqual(state["users"]["hanayohiyori"]["events"],[])
        self.assertIsNone(producer._snapshot(state,now))
        second,new=producer._update(state,"SakihaneHuya",types.SimpleNamespace(posts=[
            post("SakihaneHuya",2120220000000000001,now-10)
        ],complete=True),now)
        self.assertTrue(second)
        self.assertEqual(new,0)
        self.assertEqual(set(producer._snapshot(state,now)["users"]),{"hanayohiyori","sakihanehuya"})
        self.assertEqual(producer._snapshot(state,now)["users"]["hanayohiyori"],[])

    def test_repost_event_is_distinct_from_original_and_deduplicated(self):
        from x_repost_delivery_ledger import _keys
        from x_repost_notifier import is_repost_post, thread_url, THREAD_ID
        now=1794334000
        state={"version":1,"users":{}}
        producer._update(state,"HanayoHiyori",types.SimpleNamespace(
            posts=[post("HanayoHiyori",2120210000000000000,now-60)],complete=True),now)
        repost=post("HanayoHiyori",2120220000000000000,now+3,
                    repost_of=("Artist123",1900000000000000000),
                    media=("https://pbs.twimg.com/media/FAKE123.jpg",
                           "https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/720x1280/a.mp4"))
        first,new=producer._update(state,"HanayoHiyori",types.SimpleNamespace(
            posts=[repost],complete=True),now+10)
        self.assertFalse(first)
        self.assertEqual(new,1)
        item=state["users"]["hanayohiyori"]["events"][0]
        self.assertEqual(item["id"],"2120220000000000000")
        self.assertEqual(item["source_link"],"https://x.com/Artist123/status/1900000000000000000")
        self.assertEqual(item["url"],"https://x.com/HanayoHiyori/status/2120220000000000000")
        self.assertTrue(item["repost"])
        self.assertFalse(item["bluebird_media_verified"])
        self.assertEqual(item["images"],["https://pbs.twimg.com/media/FAKE123.jpg"])
        self.assertTrue(item["bluebird_video_url"].endswith(".mp4"))
        self.assertTrue(is_repost_post(item,"HanayoHiyori"))
        self.assertIn("original:hanayo:artist123:1900000000000000000",_keys("hanayo",item))
        self.assertIn("thread_id="+THREAD_ID,thread_url("https://discord.com/api/webhooks/1/fake"))
        _, count=producer._update(state,"HanayoHiyori",types.SimpleNamespace(
            posts=[repost],complete=True),now+15)
        self.assertEqual(count,0)
        self.assertEqual(len(state["users"]["hanayohiyori"]["events"]),1)

    def test_age_and_reply_suppression(self):
        now=1794334000
        state={"version":1,"users":{}}
        producer._update(state,"SakihaneHuya",types.SimpleNamespace(
            posts=[post("SakihaneHuya",2120200000000000000,now-60)],complete=True),now)
        first,new=producer._update(state,"SakihaneHuya",types.SimpleNamespace(posts=[
            post("SakihaneHuya",2120100000000000000,now-7300),
            post("SakihaneHuya",2120210000000000000,now-10,reply=True),
            post("SakihaneHuya",2120220000000000000,now-12),
        ],complete=True),now)
        self.assertFalse(first)
        self.assertEqual(new,1)
        self.assertEqual(len(state["users"]["sakihanehuya"]["events"]),1)
        self.assertEqual(state["users"]["sakihanehuya"]["events"][0]["id"],"2120220000000000000")

    def test_corrupt_baseline_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/"state.json"
            f.write_text("{broken",encoding="utf-8")
            with mock.patch.object(producer,"STATE_FILE",f):
                with self.assertRaises(ValueError):
                    producer._read_state()
            f.write_text('{"version":9,"users":{}}',encoding="utf-8")
            with mock.patch.object(producer,"STATE_FILE",f):
                with self.assertRaises(ValueError):
                    producer._read_state()

    def test_snapshot_reader_validates_account_date_source_and_dedup(self):
        import x_fx_feed
        now=1794334000
        state={"version":1,"users":{}}
        producer._update(state,"HanayoHiyori",types.SimpleNamespace(
            posts=[post("HanayoHiyori",2120200000000000000,now-60)],complete=True),now)
        producer._update(state,"SakihaneHuya",types.SimpleNamespace(
            posts=[post("SakihaneHuya",2120200000000000001,now-60)],complete=True),now)
        p=post("HanayoHiyori",2120220000000000000,now-10,
                repost_of=("Artist123",1900000000000000000))
        producer._update(state,"HanayoHiyori",types.SimpleNamespace(posts=[p],complete=True),now)
        snap=producer._snapshot(state,now)
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/"bridge.json"
            target.write_text(json.dumps(snap),encoding="utf-8")
            with mock.patch.object(x_fx_feed,"BLUEBIRD_BRIDGE_PATH",target),mock.patch.dict(os.environ,{"X_BLUEBIRD_BRIDGE_ENABLED":"1"}):
                posts=x_fx_feed.fetch_bluebird_events("HanayoHiyori",now=now)
                self.assertEqual(len(posts),1)
                self.assertEqual(posts[0]["source"],"bluebird-twscraper")
                self.assertTrue(posts[0]["repost"])
                self.assertEqual(x_fx_feed.fetch_bluebird_events("SakihaneHuya",now=now),[])
                self.assertIsNone(x_fx_feed.fetch_bluebird_events("HanayoHiyori",now=now+300))
                with mock.patch.dict(os.environ,{"X_BLUEBIRD_BRIDGE_ENABLED":"0"}):
                    self.assertIsNone(x_fx_feed.fetch_bluebird_events("HanayoHiyori",now=now))
                # An off-account wrapper must never be passed to the Discord worker.
                snap["users"]["hanayohiyori"][0]["url"]="https://x.com/Artist123/status/2120220000000000000"
                target.write_text(json.dumps(snap),encoding="utf-8")
                self.assertEqual(x_fx_feed.fetch_bluebird_events("HanayoHiyori",now=now),[])

    def test_authenticated_media_avoids_public_lookup(self):
        from x_post_media import resolve_media
        with mock.patch("x_post_media.requests.get",side_effect=AssertionError("network blocked")):
            pictures,preview,has_video,known=resolve_media({
                "source":"bluebird-twscraper","id":"2120220000000000000",
                "images":["https://pbs.twimg.com/media/FAKE.jpg"],
                "bluebird_video_url":"https://video.twimg.com/ext_tw_video/test.mp4",
                "bluebird_media_verified":True,
            },"HanayoHiyori")
        self.assertEqual(len(pictures),1)
        self.assertTrue(preview.endswith(".mp4"))
        self.assertTrue(has_video)
        self.assertTrue(known)

    def test_optional_reader_in_existing_worker_with_all_rss_cooling(self):
        import twitter_notifier
        import twitter_huya_notifier
        sample={"id":"2120220000000000000","source":"bluebird-twscraper"}
        batches=[]
        with mock.patch("x_fx_feed.fetch_bluebird_events",return_value=[sample]),mock.patch("x_feed_health.active",return_value=False):
            h=twitter_notifier._fetch_merged_posts(on_batch=batches.append)
            u=twitter_huya_notifier._fetch_merged_posts(on_batch=batches.append)
        self.assertEqual(h,[sample])
        self.assertEqual(u,[sample])
        self.assertEqual(batches,[[sample],[sample]])

    def test_workflow_has_no_discord_or_other_platform_mutations(self):
        workflow=WORKFLOW.read_text(encoding="utf-8")
        source=PRODUCER.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.user.login == github.repository_owner",workflow)
        self.assertIn("--name abyss-bluebird-x-bridge",workflow)
        self.assertIn("--interval 90",workflow)
        self.assertIn("--read-only --cap-drop ALL --security-opt no-new-privileges",workflow)
        for forbidden in ("DISCORD_WEBHOOK_URL","send_webhook(","Webhook(","discord.com/api/"):
            self.assertNotIn(forbidden,source)
        for forbidden in ("systemctl restart chzzk-bot","systemctl stop chzzk-bot","DISCORD_BOT_TOKEN"):
            self.assertNotIn(forbidden,workflow)


if __name__=="__main__":
    unittest.main()
