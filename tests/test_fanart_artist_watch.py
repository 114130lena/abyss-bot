"""Test known-artist fallback without external network access."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import fanart_artist_watch as watch


def json_response(results):
    response = Mock()
    response.json.return_value = {"code": 200, "results": results}
    return response


def artist_post(id, artist, tag):
    return {"type": "status", "id": id,
            "author": {"screen_name": artist},
            "text": "히요리 팬아트 #" + tag,
            "media": {"photos": [{"type": "photo",
                                  "url": "https://pbs.twimg.com/media/" + id + ".jpg"}]}}


class ArtistWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.qpath = self.base / "fanart_discovery.json"
        self.spath = self.base / "fanart_artist_watch_state.json"
        self.qpath.write_text(json.dumps({
            "hanayo:111": {"group": "hanayo", "artist": "@fan_artist",
                           "post_id": "111", "post_url": "https://x.com/fan_artist/status/111"}
        }), encoding="utf-8")
        self.patches = [patch.object(watch, "QUEUE", self.qpath),
                        patch.object(watch, "STATE", self.spath)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_existing_artist_media_brings_new_tagged_post_without_repost(self):
        session = Mock()
        session.get.return_value = json_response([
            artist_post("987654321", "fan_artist", "hiyoriart_"),
            artist_post("987654322", "different_artist", "hiyoriart_"),
            artist_post("987654323", "fan_artist", "unrelated"),
        ])
        result = watch.discover(session=session)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["groups"]["hanayo"]["known_artists"], 1)
        queue = json.loads(self.qpath.read_text("utf-8"))
        self.assertIn("hanayo:987654321", queue)
        self.assertEqual(queue["hanayo:987654321"]["artist"], "@fan_artist")
        self.assertEqual(queue["hanayo:987654321"]["source"], "fx_artist_media_api")
        self.assertNotIn("hanayo:987654322", queue)
        self.assertEqual(len(queue["hanayo:987654321"]["image_urls"]), 1)

    def test_json_timeout_falls_back_to_documented_rss_media(self):
        rss = ('<rss><channel><item>'
               '<link>https://x.com/fan_artist/status/987654321</link>'
               '<title>히요리 #hiyoriart_</title>'
               '<description><![CDATA[<img src="https://pbs.twimg.com/media/known.jpg">]]></description>'
               '</item></channel></rss>')
        response = Mock()
        response.content = rss.encode("utf-8")
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.side_effect = [requests.ReadTimeout("failed"), response]
        result = watch.discover(session=session)
        self.assertEqual(result["added"], 1)
        queue = json.loads(self.qpath.read_text("utf-8"))
        self.assertEqual(queue["hanayo:987654321"]["source"], "fx_artist_media_rss")
        self.assertEqual(session.get.call_count, 2)

    def test_failure_preserves_previous_entries_and_rotates_pointers(self):
        session = Mock()
        session.get.side_effect = requests.ReadTimeout("network")
        result = watch.discover(session=session)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["groups"]["hanayo"]["status"], "partial")
        self.assertIn("hanayo:111", json.loads(self.qpath.read_text("utf-8")))
        self.assertEqual(session.get.call_count, 2)

    def test_empty_artist_catalog_does_not_search(self):
        self.qpath.write_text("{}")
        session = Mock()
        result = watch.discover(session=session)
        self.assertEqual(result["added"], 0)
        session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
