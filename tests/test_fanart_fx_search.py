"""Offline regression for FxEmbed public hashtag search with post-level provenance."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import fanart_fx_search as search


def response(results, bottom=None):
    r = Mock()
    r.json.return_value = {"code": 200, "results": results,
                           "cursor": {"bottom": bottom}}
    return r


def tweet(uid, handle, tag, photos=None):
    return {"type": "status", "id": uid,
            "author": {"screen_name": handle},
            "text": "히요리 팬아트 #" + tag,
            "media": {"photos": photos if photos is not None else [
                {"type": "photo", "url": "https://pbs.twimg.com/media/" + uid + ".jpg"},
                {"type": "photo", "url": "https://pbs.twimg.com/media/" + uid + "b.jpg"}]}}


class FxHashtagSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [
            patch.object(search, "QUEUE", self.base / "fanart_discovery.json"),
            patch.object(search, "STATE", self.base / "fanart_fx_search_state.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_multiple_authors_all_images_and_pagination(self):
        session = Mock()
        session.get.side_effect = [
            response([tweet("123456789", "fanArtist", "hiyoriart_")], "page2"),
            response([tweet("234567890", "anotherArtist", "hiyoriart_")]),
            response([tweet("345678901", "huyaArtist", "huyart")])]
        summary = search.discover(session)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["added"], 3)
        queue = json.loads(search.QUEUE.read_text("utf-8"))
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue["hanayo:123456789"]["artist"], "@fanArtist")
        self.assertEqual(len(queue["hanayo:123456789"]["image_urls"]), 2)
        self.assertEqual(queue["hanayo:123456789"]["post_url"],
                         "https://x.com/fanArtist/status/123456789")
        self.assertEqual(queue["sakihane:345678901"]["hashtag"], "#huyart")
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["cursor"], "page2")
        search.discover(Mock(get=Mock(side_effect=[
            response([tweet("123456789", "fanArtist", "hiyoriart_")]),
            response([tweet("345678901", "huyaArtist", "huyart")])])))
        self.assertEqual(len(json.loads(search.QUEUE.read_text("utf-8"))), 3)

    def test_untrusted_images_or_unrelated_tags_not_collected(self):
        session = Mock()
        session.get.side_effect = [
            response([
                tweet("123456789", "fanArtist", "wrongtag"),
                tweet("234567890", "fanArtist", "hiyoriart_", [
                    {"type": "photo", "url": "https://evil.example/media/234567890.jpg"}]),
                tweet("345678901", "fanArtist", "hiyoriart_", [
                    {"type": "video", "url": "https://pbs.twimg.com/media/video.mp4"}])]),
            response([])]
        result = search.discover(session)
        self.assertEqual(result["added"], 0)
        self.assertEqual(search.QUEUE.exists(), False)

    def test_empty_page_is_not_valid_fanart_success(self):
        client = Mock()
        client.get.side_effect = [response([]), response([])]
        summary = search.discover(client)
        self.assertEqual(summary["status"], "ok")  # HTTP 200, not coverage
        self.assertFalse(summary["coverage_verified"])
        for group in ("hanayo", "sakihane"):
            result = summary["groups"][group]
            self.assertEqual(result["status"], "empty")
            self.assertEqual(result["matched"], 0)
            self.assertEqual(result["added"], 0)
            self.assertEqual(result["pages_fetched"], 1)

    def test_new_artist_saved_once_with_metrics(self):
        search.QUEUE.write_text(json.dumps({
            "hanayo:42": {"group": "hanayo", "artist": "@existing"}
        }))
        client = Mock()
        client.get.side_effect = [
            response([
                tweet("1000000001", "new_artist", "hiyoriart_"),
                tweet("1000000002", "existing", "hiyoriart_"),
                tweet("1000000003", "new_artist", "hiyoriart_"),
                tweet("1000000004", "other_artist", "wrong_tag"),
            ]),
            response([tweet("2000000001", "huya_new", "huyart")]),
        ]
        summary = search.discover(client)
        self.assertEqual(summary["groups"]["hanayo"]["new_artists"], 1)
        self.assertEqual(summary["groups"]["hanayo"]["matched"], 3)
        self.assertEqual(summary["groups"]["hanayo"]["rejected_without_valid_photo_or_tag"], 1)
        self.assertEqual(summary["groups"]["sakihane"]["new_artists"], 1)
        self.assertEqual(summary["added"], 4)
        self.assertEqual(len(json.loads(search.QUEUE.read_text())), 5)
        again = Mock()
        again.get.side_effect = [
            response([tweet("1000000001", "new_artist", "hiyoriart_")]),
            response([tweet("2000000001", "huya_new", "huyart")]),
        ]
        second = search.discover(again)
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["groups"]["hanayo"]["duplicate_posts"], 1)
        self.assertEqual(second["groups"]["hanayo"]["new_artists"], 0)
        self.assertEqual(second["groups"]["hanayo"]["matched"], 1)
        self.assertTrue(second["groups"]["hanayo"]["last_new_artist_at"])

    def test_second_page_failure_reports_partial_and_preserves_first(self):
        client = Mock()
        client.get.side_effect = [
            response([tweet("1000000001", "a", "hiyoriart_")], "next"),
            requests.Timeout("provider offline"),
            response([]),
        ]
        summary = search.discover(client)
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["groups"]["hanayo"]["status"], "partial")
        self.assertEqual(summary["groups"]["hanayo"]["pages_fetched"], 1)
        self.assertEqual(summary["groups"]["hanayo"]["added"], 1)
        self.assertEqual(summary["groups"]["sakihane"]["status"], "empty")
        self.assertIn("hanayo:1000000001", json.loads(search.QUEUE.read_text()))

    def test_provider_outage_preserves_collected_posts(self):
        old = {"hanayo:123456789": {"post_url": "https://x.com/a/status/123456789"}}
        search.QUEUE.write_text(json.dumps(old))
        session = Mock()
        session.get.side_effect = requests.Timeout("outage")
        result = search.discover(session)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(json.loads(search.QUEUE.read_text()), old)
        health = json.loads(search.STATE.read_text())
        self.assertEqual(health["groups"]["hanayo"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
