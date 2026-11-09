"""Regression tests for provider-scoped X-wide fanart search health."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fanart_global_sources as sources
import fanart_discovery as official


class GlobalSearchHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_patcher = patch.object(sources, "STATE", self.root / "sources.json")
        self.state_patcher.start()

    def tearDown(self):
        self.state_patcher.stop()
        self.tmp.cleanup()

    def test_fx_outage_does_not_turn_artist_fallback_into_success(self):
        state = sources.update("fx_search", {"status": "partial", "groups": {
            "hanayo": {"status": "unavailable"},
            "sakihane": {"status": "unavailable"}}})
        self.assertEqual(state["groups"]["hanayo"]["status"], "global_search_unavailable")
        self.assertFalse(state["groups"]["hanayo"]["coverage_verified"])
        self.assertNotIn("known_artist", json.dumps(state))

    def test_explicit_official_source_enabled_has_independent_group_status(self):
        sources.update("fx_search", {"status": "partial", "groups": {
            "hanayo": {"status": "unavailable"}, "sakihane": {"status": "unavailable"}}})
        state = sources.update("official_x", {"status": "ok", "added": 1, "groups": {
            "hanayo": {"status": "ok", "added": 1},
            "sakihane": {"status": "ok", "added": 0}}})
        self.assertEqual(state["groups"]["hanayo"]["status"], "source_responding")
        self.assertEqual(state["groups"]["sakihane"]["responding_sources"], ["official_x"])
        self.assertFalse(state["groups"]["hanayo"]["coverage_verified"])

    def test_paid_source_never_automatically_enabled(self):
        state = sources.update("official_x", {"status": "disabled"})
        self.assertEqual(state["providers"]["official_x"]["status"], "disabled")
        self.assertEqual(state["groups"]["sakihane"]["status"], "global_search_unavailable")

    def test_no_sensitive_post_fields_enter_status(self):
        state = sources.update("official_x", {
            "status": "api_unavailable", "http": 403, "token": "secret",
            "groups": {"hanayo": {"status": "unavailable", "post_text": "secret"}}})
        self.assertNotIn("secret", json.dumps(state))
        self.assertNotIn("http", json.dumps(state))

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            sources.update("artist_watch", {"status": "ok"})


class OfficialNewArtistTests(unittest.TestCase):
    def test_new_unknown_artist_is_discovered_when_authorized_search_works(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / "queue.json"
            state = root / "state.json"
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "data": [{"id": "1234567890123", "author_id": "123",
                          "text": "art #hiyoriart_", "entities": {"hashtags": [{"tag": "hiyoriart_"}]},
                          "attachments": {"media_keys": ["3_100"]}}],
                "includes": {"users": [{"id": "123", "username": "brandNewArtist"}],
                             "media": [{"media_key": "3_100", "type": "photo",
                                        "url": "https://pbs.twimg.com/media/test.jpg"}]},
                "meta": {}}
            empty = Mock(status_code=200)
            empty.json.return_value = {"data": [], "includes": {}, "meta": {}}
            session = Mock()
            session.get.side_effect = [response, empty]
            with patch.dict("os.environ", {"X_BEARER_TOKEN": "test-bearer"}), \
                 patch.object(official, "QUEUE", queue), \
                 patch.object(official, "STATE", state):
                result = official.discover(session)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["groups"]["hanayo"]["new_authors"], 1)
            self.assertEqual(result["added"], 1)
            item = json.loads(queue.read_text())["hanayo:1234567890123"]
            self.assertEqual(item["artist"], "@brandNewArtist")
            self.assertEqual(item["image_urls"], ["https://pbs.twimg.com/media/test.jpg"])

if __name__ == "__main__":
    unittest.main()
