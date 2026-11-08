"""Offline tests for opt-in visual scoring. Never call a real paid API."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import fanart_vision as vision
import guild_server_banner as banner
import fanart_usage


class VisionTests(unittest.TestCase):
    def test_not_configured_does_not_call_paid_api(self):
        with patch.dict("os.environ", {"SERVER_BANNER_VISION_ENABLED": "0", "OPENAI_API_KEY": ""}):
            self.assertFalse(vision.ready())
            with patch.object(vision.requests, "post") as api:
                self.assertEqual(vision.scan(), {"status": "off", "analyzed": 0})
                api.assert_not_called()

    def test_enabled_without_key_is_explicitly_unavailable(self):
        with patch.dict("os.environ", {"SERVER_BANNER_VISION_ENABLED": "1", "OPENAI_API_KEY": ""}):
            self.assertFalse(vision.ready())
            with patch.object(vision.requests, "post") as api:
                self.assertEqual(vision.scan()["status"], "missing_api_key")
                api.assert_not_called()

    def test_visual_score_excludes_characterless_banner_even_if_hashtag(self):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "has_character": False, "hiyori_present": False,
            "hiyori_prominence": 0, "banner_composition": 0.9,
            "confidence": 0.95})}}]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sample.jpg"
            Image.new("RGB", (1920, 1080), "white").save(path)
            with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}), \
                 patch.object(vision.requests, "post", return_value=response):
                result = vision._analyze(path, "https://yt3.googleusercontent.com/ref")
        self.assertFalse(result["banner_eligible"])
        self.assertFalse(result["has_character"])

    def test_group_art_with_hiyori_is_valid_despite_other_tags(self):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "has_character": True, "hiyori_present": True,
            "hiyori_prominence": 0.83, "banner_composition": 0.94,
            "confidence": 0.93})}}]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sample.jpg"
            Image.new("RGB", (1920, 1080), "blue").save(path)
            with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}), \
                 patch.object(vision.requests, "post", return_value=response):
                result = vision._analyze(path, "https://yt3.googleusercontent.com/ref")
                body = vision.requests.post.call_args.kwargs["json"]
        self.assertTrue(result["banner_eligible"])
        self.assertEqual(len(body["messages"][0]["content"]), 3)

    def test_visual_mode_uses_cached_decision_not_text_exclusions(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "creator_media"
            folder = base / "fanart_candidates" / "hanayo"
            folder.mkdir(parents=True)
            path = folder / "1.jpg"
            Image.new("RGB", (1920, 1080), "red").save(path)
            data = {"hanayo:123": {
                "group": "hanayo", "post_id": "123", "post_url": "https://x.com/artist/status/123",
                "artist": "@artist", "hashtag": "#hiyoriart_",
                "review_status": "pending", "decision": "unknown",
                "text": "RT by @artist #toart_ #hiyoriart_ #maryart_",
                "cached_files": ["fanart_candidates/hanayo/1.jpg"]}}
            (base / "fanart_discovery.json").write_text(json.dumps(data))
            verdict = {"banner_eligible": True, "score": 88}
            with patch.dict("os.environ", {"SERVER_BANNER_VISION_ENABLED": "1", "SERVER_BANNER_USE_COLLECTED": "1"}), \
                 patch.object(banner, "media_base", return_value=base), \
                 patch("fanart_discovery.BASE", base), \
                 patch("fanart_discovery.QUEUE", base / "fanart_discovery.json"), \
                 patch.object(fanart_usage, "BASE", base), \
                 patch.object(vision, "enabled", return_value=True), \
                 patch.object(vision, "classification", return_value=verdict):
                found = banner.collected_hanayo_art()
                reserved = fanart_usage.server_reserved_post_ids("hanayo")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][1]["vision_score"], 88)
            self.assertEqual(reserved, {"123"})

    def test_visual_mode_with_no_verdict_does_not_pick_image(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "creator_media"
            folder = base / "fanart_candidates" / "hanayo"
            folder.mkdir(parents=True)
            Image.new("RGB", (1920, 1080), "white").save(folder / "1.jpg")
            data = {"h:123": {"group": "hanayo", "post_url": "https://x.com/artist/status/123",
                "artist": "@artist", "hashtag": "#hiyoriart_", "text": "히요리",
                "cached_files": ["fanart_candidates/hanayo/1.jpg"]}}
            (base / "fanart_discovery.json").write_text(json.dumps(data))
            with patch.dict("os.environ", {"SERVER_BANNER_VISION_ENABLED": "1"}), \
                 patch.object(banner, "media_base", return_value=base), \
                 patch("fanart_discovery.QUEUE", base / "fanart_discovery.json"), \
                 patch.object(vision, "classification", return_value=None):
                self.assertEqual(banner.collected_hanayo_art(), [])


if __name__ == "__main__":
    unittest.main()
