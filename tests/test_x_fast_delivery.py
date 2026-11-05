"""Regression tests for fast X media resolution and Discord image preparation."""
import io
import unittest
from unittest.mock import patch

from PIL import Image
import x_post_media
import resilience


class FakeResponse:
    def __init__(self, payload=None, content=None):
        self.payload = payload or {}
        self.content = content or b""
        self.headers = {"Content-Type": "image/png"}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload
    def iter_content(self, size):
        yield self.content


class FastXMediaTests(unittest.TestCase):
    def test_fxtwitter_media_success_single_request(self):
        calls = []
        def get(url, **kwargs):
            calls.append(url)
            return FakeResponse({"tweet": {"media": {"photos": [
                {"url": "https://pbs.twimg.com/media/abc.jpg"}
            ]}}})
        with patch.object(x_post_media.requests, "get", side_effect=get):
            photos, preview, has_video, known = x_post_media.resolve_media(
                {"id": "123", "images": []}, "author")
        self.assertEqual(len(calls), 1)
        self.assertEqual(photos, ["https://pbs.twimg.com/media/abc.jpg"])
        self.assertFalse(has_video)
        self.assertTrue(known)

    def test_unknown_metadata_preserves_rss_photos(self):
        with patch.object(x_post_media.requests, "get", return_value=FakeResponse({"tweet": {}})) as get:
            photos, _, video, known = x_post_media.resolve_media(
                {"id": "123", "images": ["https://pbs.twimg.com/media/rss.jpg"]}, "author")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(photos, ["https://pbs.twimg.com/media/rss.jpg"])
        self.assertFalse(video)
        self.assertFalse(known)

    def test_x_photos_are_attached_in_original_order(self):
        out = io.BytesIO()
        Image.new("RGB", (8, 8)).save(out, format="PNG")
        raw = out.getvalue()
        payload = {"components": [{"type": 17, "components": [
            {"type": 12, "items": [
                {"media": {"url": "https://pbs.twimg.com/media/one"}},
                {"media": {"url": "https://pbs.twimg.com/media/two"}},
            ]}
        ]}]}
        with patch.object(resilience.requests, "get", return_value=FakeResponse(content=raw)) as get:
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(attachments), 2)
        self.assertEqual([a[0] for a in attachments],
                         ["abyss_x_media_0.png", "abyss_x_media_1.png"])
        media = payload["components"][0]["components"][0]["items"]
        self.assertEqual(media[0]["media"]["url"], "attachment://abyss_x_media_0.png")
        self.assertEqual(media[1]["media"]["url"], "attachment://abyss_x_media_1.png")

    def test_failed_photo_keeps_original_url(self):
        payload = {"components": [{"type": 17, "components": [{"type": 12, "items": [
            {"media": {"url": "https://pbs.twimg.com/media/bad"}}
        ]}]}]}
        with patch.object(resilience.requests, "get", side_effect=ValueError("bad image")):
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertFalse(attachments)
        self.assertEqual(payload["components"][0]["components"][0]["items"][0]["media"]["url"],
                         "https://pbs.twimg.com/media/bad")


if __name__ == "__main__":
    unittest.main()
