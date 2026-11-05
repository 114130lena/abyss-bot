"""Offline regression tests for safe, non-posting Hiyori clip metadata ingestion."""
import json
import os
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import hiyoriclip_collector as clip

DATE=datetime(2026,11,5,1,0,tzinfo=timezone.utc)

def payload(post_id="2116841881615446466",text="오늘의 클립 #hiyoriclip_"):
    return {"id":post_id,"text":text,"created_timestamp":DATE.timestamp(),
      "author":{"screen_name":"33ammi"},
      "media":{"videos":[{"id":"vid1","url":"https://video.twimg.com/ext_tw_video/foo.mp4"}]},
      "likes":44,"retweets":7,"views":1200}

class CollectorTests(unittest.TestCase):
    def test_validate_status_links(self):
        self.assertEqual(clip.parse_url("https://x.com/33ammi/status/2116841881615446466?lang=ko"),("33ammi","2116841881615446466"))
        self.assertIsNone(clip.parse_url("https://evil.com/33ammi/status/2116841881615446466"))
        self.assertIsNone(clip.parse_url("https://x.com/33ammi/status/not-a-number"))
    def test_accept_known_untampered_video_post(self):
        row=clip.normalize(payload(),"33ammi","2116841881615446466","supplied_url")
        self.assertEqual(row["views"],1200)
        self.assertEqual(row["likes"],44)
        self.assertEqual(row["reposts"],7)
        self.assertEqual(len(row["video_urls"]),1)
    def test_reject_photo_only_and_wrong_hashtag(self):
        a=payload()
        a["media"]={"photos":[{"url":"https://pbs.twimg.com/media/a.jpg"}]}
        self.assertIsNone(clip.normalize(a,"33ammi",a["id"],"supplied_url"))
        a=payload(text="일반 영상 #hiyoriart_")
        self.assertIsNone(clip.normalize(a,"33ammi",a["id"],"supplied_url"))
    def test_wrong_author_rejected(self):
        self.assertIsNone(clip.normalize(payload(),"someone_else","2116841881615446466","supplied_url"))
    def test_stable_merge_does_not_replace_known_metrics_with_missing(self):
        old=clip.normalize(payload(),"33ammi","2116841881615446466","supplied_url")
        newer=dict(old,views=None,discovered_via=["profile:33ammi"])
        joined=clip.merged(old,newer)
        self.assertEqual(joined["views"],1200)
        self.assertEqual(joined["discovered_via"],["profile:33ammi","supplied_url"])
    def test_html_profile_rss_parsed_with_provenance(self):
        xml="""<?xml version="1.0"?><rss><channel>
        <title>33ammi</title><item><link>https://x.com/33ammi/status/2116841881615446466</link>
        <description>&lt;p&gt;#hiyoriclip_&lt;/p&gt;</description></item></channel></rss>"""
        resp=Mock(content=xml.encode())
        resp.raise_for_status.return_value=None
        session=Mock()
        session.get.return_value=resp
        self.assertEqual(clip.account_feed(session,"33ammi"),[("33ammi","2116841881615446466")])
    def test_incomplete_free_search_never_claims_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.object(clip,"DEST",base),patch.object(clip,"INDEX",base/"index.json"),\
                 patch.object(clip,"STATUS",base/"state.json"),patch.object(clip,"ACCOUNTS",base/"accounts.txt"),\
                 patch.object(clip,"URLS",base/"links.txt"),patch.dict(os.environ,{"X_BEARER_TOKEN":""}):
                (base/"links.txt").write_text("https://x.com/33ammi/status/2116841881615446466",encoding="utf-8")
                with patch.object(clip,"fx_post",return_value=payload()):
                    result=clip.collect(session=Mock(),now=DATE+timedelta(hours=1))
                    self.assertEqual(result["new"],1)
                    self.assertFalse(result["global_hashtag_search_confirmed"])
                    self.assertEqual(result["status"],"partial_profile_or_manual_only")
                    self.assertEqual(result["within_24h"],1)
                    second=clip.collect(session=Mock(),now=DATE+timedelta(hours=1))
                    self.assertEqual(second["new"],0)
                    self.assertEqual(second["updated"],1)
                    self.assertEqual(len(json.loads((base/"index.json").read_text())["posts"]),1)

if __name__=="__main__":
    unittest.main()
