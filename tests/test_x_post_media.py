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

    def test_vx_recovers_photo_when_fxtwitter_status_has_no_media(self):
        primary = {"code": 200, "status": {"id": "123456789", "author": {"username": "Artist"}, "media": {}}}
        backup = {"tweetID": "123456789", "user_screen_name": "Artist", "mediaURLs": [
            "https://pbs.twimg.com/media/repost.jpg"], "text": "사진"}
        with patch("x_post_media.requests.get", side_effect=[
            FakeResponse(primary), FakeResponse(backup)]):
            photos, _, video, known = x_post_media.resolve_media(
                self.POST, "Artist")
        self.assertEqual(photos, ["https://pbs.twimg.com/media/repost.jpg"])
        self.assertFalse(video)
        self.assertTrue(known)

    def test_vx_rejects_wrong_account(self):
        primary = {"code": 503}
        backup = {"tweetID": "123456789", "user_screen_name": "Imposter", "mediaURLs": [
            "https://pbs.twimg.com/media/wrong.jpg"], "text": "사진"}
        with patch("x_post_media.requests.get", side_effect=[
            FakeResponse(primary), FakeResponse(backup)]):
            photos, _, _, known = x_post_media.resolve_media(self.POST, "Artist")
        self.assertEqual(photos, [])
        self.assertFalse(known)

    def test_rss_photo_survives_failed_metadata_sources(self):
        post = {"id": "123456789", "images": ["https://pbs.twimg.com/media/rss.jpg"]}
        with patch("x_post_media.requests.get", side_effect=ValueError("down")):
            photos, _, _, known = x_post_media.resolve_media(post, "Artist")
        self.assertEqual(photos, post["images"])
        self.assertFalse(known)

    def test_empty_media_is_not_confirmed_text_only(self):
        data = {"tweet": {"media": {"all": [], "photos": [], "videos": []}}}
        with patch("x_post_media.requests.get", return_value=FakeResponse(data)):
            photos, _, has_video, known = x_post_media.resolve_media(self.POST, "Example")
        self.assertEqual(photos, [])
        self.assertFalse(has_video)
        self.assertFalse(known)


if __name__ == "__main__":
    unittest.main()
