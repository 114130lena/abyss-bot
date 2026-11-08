"""Regression tests for FxTwitter media extraction and safe fallback."""
import unittest
from unittest.mock import patch

import x_post_media


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FxMediaTests(unittest.TestCase):
    POST = {"id": "123456789", "images": []}

    def test_v2_photo(self):
        data = {"status": {"media": {"photos": [
            {"type": "photo", "url": "https://pbs.twimg.com/media/example.jpg"}
        ]}}}
        with patch("x_post_media.requests.get", return_value=FakeResponse(data)):
            photos, video, has_video, known = x_post_media.resolve_media(self.POST, "Example")
        self.assertEqual(photos, ["https://pbs.twimg.com/media/example.jpg"])
        self.assertEqual(video, "")
        self.assertFalse(has_video)
        self.assertTrue(known)

    def test_v2_video(self):
        data = {"status": {"media": {"videos": [
            {"type": "video", "formats": [
                {"container": "mp4", "bitrate": 256000, "url": "https://video.twimg.com/example.mp4"}
            ]}
        ]}}}
        with patch("x_post_media.requests.get", return_value=FakeResponse(data)):
            photos, url, has_video, known = x_post_media.resolve_media(self.POST, "Example")
        self.assertEqual(photos, [])
        self.assertEqual(url, "https://video.twimg.com/example.mp4")
        self.assertTrue(has_video)
        self.assertTrue(known)

    def test_gif_in_photos_uses_video(self):
        data = {"tweet": {"media": {"photos": [
            {"type": "gif", "url": "https://pbs.twimg.com/media/animation.jpg",
             "transcode_url": "https://video.twimg.com/animation.mp4"}
        ]}}}
        with patch("x_post_media.requests.get", return_value=FakeResponse(data)):
            photos, url, has_video, known = x_post_media.resolve_media(self.POST, "Example")
        self.assertEqual(photos, [])
        self.assertEqual(url, "https://video.twimg.com/animation.mp4")
        self.assertTrue(has_video)
        self.assertTrue(known)

    def test_empty_media_is_not_confirmed_text_only(self):
        data = {"tweet": {"media": {"all": [], "photos": [], "videos": []}}}
        with patch("x_post_media.requests.get", return_value=FakeResponse(data)):
            photos, _, has_video, known = x_post_media.resolve_media(self.POST, "Example")
        self.assertEqual(photos, [])
        self.assertFalse(has_video)
        self.assertFalse(known)


if __name__ == "__main__":
    unittest.main()
