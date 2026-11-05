"""Offline restoration checks: previously approved local fanart and live-style delivery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import fanart_url_media as fanart
import fanart_usage as usage
import resilience
from discord_v2 import alert


class FakeResponse:
    status_code = 204
    def raise_for_status(self):
        return None


class RestoreLocalFanartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.file = self.base / "fanart_banners" / "hanayo" / "safe.jpg"
        self.file.parent.mkdir(parents=True)
        Image.new("RGB", (64, 64)).save(self.file, format="JPEG")
        self.entries = {"hanayo": [{
            "approved": True, "permission_confirmed": True,
            "sha256": "safe", "artist": "@artist",
            "path": "fanart_banners/hanayo/safe.jpg",
            "post_url": "https://x.com/artist/status/42"
        }]}
        (self.base / "fanart_index.json").write_text(json.dumps(self.entries))
        (self.base / "fanart_discovery.json").write_text("{}")
        self.p1 = patch.object(fanart, "BASE", self.base)
        self.p2 = patch.object(fanart, "QUEUE", self.base / "fanart_discovery.json")
        self.p3 = patch.object(fanart, "HISTORY", self.base / "history.json")
        self.p4 = patch.object(usage, "BASE", self.base)
        for p in (self.p1, self.p2, self.p3, self.p4):
            p.start()

    def tearDown(self):
        for p in (self.p1, self.p2, self.p3, self.p4):
            p.stop()
        self.temp.cleanup()

    def make_card(self):
        return alert(
            "chzzk", "하나요 히요리", "https://example.org/banner.jpg",
            title="테스트 라이브", url="https://chzzk.naver.com/live/123",
            avatar="https://example.org/profile.jpg", ping=False)

    def test_existing_local_approved_art_is_selected(self):
        entries = fanart.candidates("hanayo")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["local_path"], str(self.file.resolve()))
        payload, chosen = fanart.apply(self.make_card())
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["local_path"], str(self.file.resolve()))
        self.assertIn("attachment://abyss_notification_fanart.jpg", str(payload))
        row = next(x for x in payload["components"][-1]["components"] if x["type"] == 1)
        self.assertEqual([x["label"] for x in row["components"]],
                         ["원본 게시물", "CHZZK", "CAFE", "X", "YouTube"])

    def test_server_banner_already_used_never_reappears(self):
        path = self.base / "guild_banner_rotation_state.json"
        path.write_text(json.dumps({"rotation_used": ["safe"]}))
        self.assertEqual(fanart.candidates("hanayo"), [])

    def test_notification_sends_actual_local_attachment(self):
        captured = {}
        def fake_post(url, **kwargs):
            captured.update(kwargs)
            self.assertIn("files", kwargs)
            self.assertTrue(any(value[0] == "abyss_notification_fanart.jpg"
                                for value in kwargs["files"].values()))
            data = json.loads(kwargs["data"]["payload_json"])
            self.assertIn("attachment://abyss_notification_fanart.jpg", str(data))
            return FakeResponse()
        with patch.object(resilience.requests, "post", side_effect=fake_post), \
             patch.object(resilience, "_collect_x_image_attachments", return_value=[]), \
             patch("fanart_discovery.BASE", self.base), \
             patch.dict("os.environ", {"ABYSS_NOTIFICATION_FANART": "1",
                                      "ABYSS_RANDOM_MEDIA": "0"}):
            resilience.send_webhook("https://discord.com/api/webhooks/1/abc", self.make_card())
        self.assertIn("files", captured)


if __name__ == "__main__":
    unittest.main()
