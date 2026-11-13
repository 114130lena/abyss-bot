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


    def test_direct_space_url_is_classified_without_network(self):
        value = "https://x.com/i/spaces/1qKVmyYgzgLxB"
        with patch("x_post_media.requests.head",
                   side_effect=AssertionError("direct Space needs no network")):
            label, body = x_post_media.describe_x_link_post(value)
        self.assertIn("스페이스", label)
        self.assertIn(value, body)

    def test_nitter_short_link_reconstructs_real_tco_and_expands_space(self):
        source = ("[https://nitter.gq/t.co/iKf8fwZuyb]"
                  "(https://x.com/HanayoHiyori/status/2121241811914195289)")
        class Redirect:
            status_code = 302
            headers = {"Location": "https://x.com/i/spaces/1qKVmyYgzgLxB"}
            def close(self):
                pass
        with patch("x_post_media.requests.head", return_value=Redirect()) as head:
            label, body = x_post_media.describe_x_link_post(source)
        self.assertIn("스페이스", label)
        self.assertIn("https://x.com/i/spaces/1qKVmyYgzgLxB", body)
        self.assertNotIn("nitter.gq", body)
        self.assertEqual(head.call_args.args[0], "https://t.co/iKf8fwZuyb")
        self.assertFalse(head.call_args.kwargs["allow_redirects"])

    def test_unresolved_short_link_is_not_falsely_labeled_space(self):
        source = ("[https://nitter.gq/t.co/iKf8fwZuyb]"
                  "(https://x.com/HanayoHiyori/status/2121241811914195289)")
        with patch("x_post_media.requests.head",
                   side_effect=__import__("requests").Timeout):
            label, body = x_post_media.describe_x_link_post(source)
        self.assertEqual(label, "🔗 X 링크 게시물")
        self.assertNotIn("nitter.gq", body)

    def test_shortener_head_rejection_tries_one_safe_get(self):
        class Response:
            def __init__(self, status, destination=""):
                self.status_code = status
                self.headers = {"Location": destination}
            def close(self):
                pass
        with patch("x_post_media.requests.head",
                   return_value=Response(405)) as head, \
             patch("x_post_media.requests.get",
                   return_value=Response(301, "https://x.com/i/spaces/1qKVmyYgzgLxB")) as get:
            label, body = x_post_media.describe_x_link_post("https://t.co/abcde12345")
        self.assertIn("스페이스", label)
        self.assertIn("https://x.com/i/spaces/1qKVmyYgzgLxB", body)
        self.assertEqual(head.call_count, 1)
        self.assertEqual(get.call_count, 1)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_non_space_redirect_does_not_follow_arbitrary_url(self):
        class Redirect:
            status_code = 302
            headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
            def close(self):
                pass
        with patch("x_post_media.requests.head", return_value=Redirect()) as head:
            label, _ = x_post_media.describe_x_link_post("https://t.co/abcde12345")
        self.assertEqual(label, "🔗 X 링크 게시물")
        self.assertEqual(head.call_count, 1)

    def test_normal_text_with_link_is_not_modified(self):
        with patch("x_post_media.requests.head",
                   side_effect=AssertionError("not link-only")):
            value = x_post_media.describe_x_link_post(
                "오늘 방송합니다! https://t.co/abcde12345")
        self.assertIsNone(value)



if __name__ == "__main__":
    unittest.main()
