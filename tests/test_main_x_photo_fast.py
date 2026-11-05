"""Offline regression checks for the shared Discord X photo upload path."""
import io
import unittest
from unittest.mock import patch

from PIL import Image
import resilience


class ImageResponse:
    headers = {"Content-Type": "image/png"}
    def __init__(self, content):
        self.content = content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        pass
    def iter_content(self, size):
        yield self.content


class DiscordImageTests(unittest.TestCase):
    def make_payload(self, urls):
        return {"components": [{"type": 17, "components": [
            {"type": 12, "items": [{"media": {"url": url}} for url in urls]}
        ]}]}

    def test_concurrent_photo_upload_preserves_order(self):
        image = io.BytesIO()
        Image.new("RGB", (4, 4)).save(image, format="PNG")
        payload = self.make_payload([
            "https://pbs.twimg.com/media/one",
            "https://pbs.twimg.com/media/two"
        ])
        with patch.object(resilience.requests, "get", return_value=ImageResponse(image.getvalue())) as get:
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertEqual(get.call_count, 2)
        self.assertEqual([file[0] for file in attachments],
                         ["abyss_x_media_0.png", "abyss_x_media_1.png"])
        items = payload["components"][0]["components"][0]["items"]
        self.assertEqual(items[0]["media"]["url"], "attachment://abyss_x_media_0.png")
        self.assertEqual(items[1]["media"]["url"], "attachment://abyss_x_media_1.png")

    def test_failed_photo_leaves_original_url(self):
        url = "https://pbs.twimg.com/media/failed"
        payload = self.make_payload([url])
        with patch.object(resilience.requests, "get", side_effect=ValueError("invalid")):
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertEqual(attachments, [])
        self.assertEqual(payload["components"][0]["components"][0]["items"][0]["media"]["url"], url)

    def test_non_x_media_never_downloaded(self):
        payload = self.make_payload(["https://example.com/image.png"])
        with patch.object(resilience.requests, "get") as get:
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertEqual(attachments, [])
        get.assert_not_called()

    def test_only_first_four_photos_downloaded(self):
        image = io.BytesIO()
        Image.new("RGB", (4, 4)).save(image, format="PNG")
        payload = self.make_payload([f"https://pbs.twimg.com/media/{i}" for i in range(5)])
        with patch.object(resilience.requests, "get", return_value=ImageResponse(image.getvalue())) as get:
            attachments = resilience._collect_x_image_attachments(payload)
        self.assertEqual(len(attachments), 4)
        self.assertEqual(get.call_count, 4)


if __name__ == "__main__":
    unittest.main()
