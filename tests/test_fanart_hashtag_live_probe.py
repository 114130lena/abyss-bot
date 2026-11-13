"""Read-only fanart search probe must verify original media without leaking IDs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import fanart_hashtag_live_probe as live_probe


def fx_result(posts, status=200, code=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = {"code": code, "results": posts}
    return result


def artist_post(group, post_id, artist):
    return {"type": "status", "id": post_id,
            "author": {"screen_name": artist},
            "text": "팬아트 #" + ("hiyoriart_" if group == "hanayo" else "huyart"),
            "media": {"photos": [{
                "type": "photo",
                "url": "https://pbs.twimg.com/media/fixture.jpg"}]}}


class LiveProbeTests(unittest.TestCase):
    def test_read_only_reports_new_artist_and_existing_posts_without_raw_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = Path(folder) / "fanart_discovery.json"
            original = json.dumps({
                "hanayo:1234567890": {
                    "group": "hanayo", "artist": "@existing"}}, ensure_ascii=False)
            queue.write_text(original, encoding="utf-8")
            client = Mock()
            client.get.side_effect = [
                fx_result([
                    artist_post("hanayo", "1234567890", "existing"),
                    artist_post("hanayo", "1234567891", "new_artist")]),
                fx_result([artist_post("sakihane", "2234567890", "new_huya")]),
            ]
            with patch.object(live_probe, "QUEUE", queue):
                result = live_probe.probe(client)
            self.assertEqual(queue.read_text(encoding="utf-8"), original)
            self.assertTrue(result["read_only"])
            self.assertFalse(result["sent_discord"])
            self.assertFalse(result["search_coverage_verified"])
            self.assertEqual(result["groups"]["hanayo"]["valid_photo_posts"], 2)
            self.assertEqual(result["groups"]["hanayo"]["queued_valid_posts"], 1)
            self.assertEqual(result["groups"]["hanayo"]["unknown_artists_found"], 1)
            self.assertEqual(result["groups"]["sakihane"]["unknown_artists_found"], 1)
            output = json.dumps(result)
            self.assertNotIn("new_artist", output)
            self.assertNotIn("1234567891", output)
            self.assertNotIn("pbs.twimg.com", output)

    def test_empty_and_provider_error_are_distinct(self):
        client = Mock()
        failure = fx_result([], status=404, code=404)
        failure.raise_for_status.side_effect = RuntimeError("unavailable")
        # requests.HTTPError is caught, generic faults should not hide.
        import requests
        failure.raise_for_status.side_effect = requests.HTTPError("not found")
        client.get.side_effect = [fx_result([]), failure]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(live_probe, "QUEUE", Path(folder) / "queue.json"):
                result = live_probe.probe(client)
        self.assertEqual(result["groups"]["hanayo"]["status"], "empty_or_not_eligible")
        self.assertEqual(result["groups"]["sakihane"]["status"], "unavailable")
        self.assertEqual(result["groups"]["sakihane"]["http_status"], 404)
