"""Free multi-source discovery, no duplicates, safe media, no paid API calls."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import json

import fanart_free_search as free


def rss(links):
    root = ET.Element("rss")
    channel = ET.SubElement(root, "channel")
    for link in links:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "link").text = link
    return ET.tostring(root)


def response(data=b"", json_value=None):
    r = Mock()
    r.content = data
    r.json.return_value = json_value
    return r


def post(post_id, tag, author="newArtist"):
    return {"tweet": {"type": "status", "id": post_id,
        "author": {"screen_name": author}, "text": "새 팬아트 #" + tag,
        "media": {"photos": [{"type": "photo",
                              "url": "https://pbs.twimg.com/media/test.jpg"}]}}}


class FreeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patchers = [
            patch.object(free, "STATE", base / "state.json"),
            patch.object(free, "QUEUE", base / "queue.json"),
            patch("fanart_free_search._nitter_feeds", return_value=[])]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp.cleanup()

    def test_same_x_post_from_two_indexes_adds_only_once(self):
        link = "https://x.com/newArtist/status/1234567890"
        rss_data = rss([link])
        session = Mock()
        # hiyori Bing, verified status, hiyori Google, huya Bing, huya Google
        session.get.side_effect = [
            response(rss_data), response(json_value=post("1234567890", "hiyoriart_")),
            response(rss_data), response(rss([])), response(rss([]))]
        result = free.discover(session=session)
        queue = json.loads(free.QUEUE.read_text())
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue["hanayo:1234567890"]["artist"], "@newArtist")
        self.assertEqual(queue["hanayo:1234567890"]["image_urls"],
                         ["https://pbs.twimg.com/media/test.jpg"])
        self.assertEqual(result["groups"]["hanayo"]["bing_index"]["added"], 1)
        self.assertEqual(result["groups"]["hanayo"]["google_news_index"]["added"], 0)
        self.assertEqual(session.get.call_count, 5)
        self.assertFalse(result["coverage_verified"])

    def test_no_photo_or_wrong_tag_never_added(self):
        link = "https://x.com/newArtist/status/1234567890"
        session = Mock()
        session.get.side_effect = [
            response(rss([link])),
            response(json_value=post("1234567890", "unrelated")),
            response(rss([])), response(rss([])), response(rss([]))]
        free.discover(session=session)
        self.assertFalse(free.QUEUE.exists())

    def test_no_untrusted_redirect_urls(self):
        self.assertIsNone(free._x_post_url("https://evil.example/redirect?to=https://x.com/a/status/1234"))
        self.assertIsNone(free._x_post_url("http://x.com/a/status/1234"))
        self.assertEqual(free._x_post_url(
            "https://nitter.mie.foundation/newArtist/status/123456",
            "nitter.mie.foundation"), ("newArtist", "123456"))

    def test_two_hour_interval_skips_all_network_calls(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        free.STATE.write_text(json.dumps({"checked_at": (now - timedelta(minutes=45)).isoformat()}))
        client = Mock()
        self.assertEqual(free.discover(client, now=now)["status"], "rate_limited")
        client.get.assert_not_called()

    def test_provider_failure_does_not_remove_existing_records(self):
        existing = {"hanayo:42": {"post_id": "42", "artist": "@old"}}
        free.QUEUE.write_text(json.dumps(existing))
        client = Mock()
        client.get.side_effect = Exception("TEST")
        # Generic exception deliberately not swallowed: real request errors
        # are swallowed, but unexpected programming faults should surface.
        with self.assertRaises(Exception):
            free.discover(client)
        self.assertEqual(json.loads(free.QUEUE.read_text()), existing)


if __name__ == "__main__":
    unittest.main()
