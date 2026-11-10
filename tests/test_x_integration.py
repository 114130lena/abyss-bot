"""Offline checks for X repost routing, fanart permission, and media classification."""
import unittest
from unittest.mock import patch

import x_repost_notifier as repost
import fanart_url_media as fanart
import x_post_media as media


class RepostRoutingTests(unittest.TestCase):
    def test_repost_title_and_source_link(self):
        self.assertTrue(repost.is_repost("RT by HanayoHiyori: hello"))
        self.assertTrue(repost.is_repost_post({
            "rss_title": "regular", "source_link": "https://x.com/other/status/123"
        }, "HanayoHiyori"))
        self.assertFalse(repost.is_repost_post({
            "rss_title": "a new status", "source_link": "https://x.com/HanayoHiyori/status/123"
        }, "HanayoHiyori"))

    def test_nitter_original_author_is_classified_as_repost(self):
        for mirror in ("nitter.mie.foundation", "nitter.meowing.monster",
                       "nitter.mizuki.st"):
            with self.subTest(mirror=mirror):
                post = {
                    "id": "123456", "rss_title": "an ordinary-looking title",
                    "url": "https://x.com/HanayoHiyori/status/123456",
                    "source_link": f"https://{mirror}/SomeArtist/status/123456#m",
                    "repost": False,
                }
                self.assertTrue(repost.is_repost_post(post, "HanayoHiyori"))
                self.assertEqual(repost.original_status_from_feed(post["source_link"]),
                                 ("SomeArtist", "123456"))

    def test_nitter_creator_original_remains_regular_post(self):
        post = {"rss_title": "original update",
                "source_link": "https://nitter.mie.foundation/HanayoHiyori/status/123"}
        self.assertFalse(repost.is_repost_post(post, "HanayoHiyori"))

    def test_repost_nitter_link_uses_original_x_author_and_thread(self):
        post = {"id": "123", "text": "reshared",
                "url": "https://x.com/HanayoHiyori/status/123",
                "source_link": "https://nitter.mie.foundation/GoodArtist/status/123",
                "images": ["https://pbs.twimg.com/media/example.jpg"],
                "rss_title": "repost"}
        with patch("x_post_media.resolve_media", return_value=(post["images"], "", False, True)), \
             patch("x_repost_delivery_ledger.reserve", return_value=True), \
             patch("x_repost_delivery_ledger.mark_sent"), \
             patch.object(repost, "send_webhook") as sent:
            repost.notify("hanayo", post, "https://discord.com/api/webhooks/111/token")
        url, payload = sent.call_args.args
        self.assertIn("thread_id=1566509366591627344", url)
        self.assertIn("https://x.com/GoodArtist/status/123", str(payload))
        self.assertEqual(payload["allowed_mentions"], {"parse":[]})

    def test_thread_preserves_components_parameter(self):
        url = repost.thread_url("https://discord.com/api/webhooks/123/abc?with_components=true")
        self.assertIn("thread_id=1566509366591627344", url)
        self.assertIn("with_components=true", url)


class FanartPermissionTests(unittest.TestCase):
    def test_attributed_public_art_included_but_blocked_art_excluded(self):
        queue = {
            "a": {"group": "hanayo", "post_url": "https://x.com/artist/status/1",
                  "image_urls": ["https://pbs.twimg.com/media/a"], "review_status": "pending", "artist":"@artist", "hashtag":"#hiyoriart_"},
            "b": {"group": "hanayo", "post_url": "https://x.com/artist/status/2",
                  "image_urls": ["https://pbs.twimg.com/media/b"], "review_status": "blocked", "artist":"@artist", "hashtag":"#hiyoriart_"},
            "c": {"group": "hanayo", "post_url": "https://x.com/artist/status/3",
                  "image_urls": ["https://pbs.twimg.com/media/c"], "review_status": "pending", "artist":"@artist", "hashtag":"#hiyoriart_"},
        }
        with patch.object(fanart, "read", return_value=queue):
            pool = fanart.candidates("hanayo")
        self.assertEqual([p["post_url"] for p in pool],
                         ["https://x.com/artist/status/1", "https://x.com/artist/status/3"])

    def test_native_post_photo_suppresses_fanart(self):
        payload = {"components": [{"type": 17, "components": [
            {"type": 10, "content": "𝕏 X"},
            {"type": 12, "items": [{"media": {"url": "https://pbs.twimg.com/media/photo"}}]},
            {"type": 10, "content": "OFFICIAL LINKS"},
        ]}]}
        self.assertTrue(fanart.has_post_photos(payload))


class MediaTests(unittest.TestCase):
    def test_x_video_uses_playable_mp4_instead_of_static_poster(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"tweet": {"media": {"videos": [{
                    "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/avc1/640x360/clip.mp4",
                    "thumbnail_url": "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/preview.jpg",
                    "formats": [
                        {"container": "m3u8", "bitrate": 0, "url": "https://video.twimg.com/ext_tw_video/123/pu/pl/master.m3u8"},
                        {"container": "mp4", "bitrate": 100, "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/avc1/320x180/clip.mp4"},
                        {"container": "mp4", "bitrate": 800, "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/avc1/640x360/clip.mp4"}
                    ]
                }]}}}
        with patch.object(media.requests, "get", return_value=Response()):
            photos, playable, is_video, known = media.resolve_media(
                {"id": "2118873316052312139", "images": []}, "SakihaneHuya")
        self.assertEqual(photos, [])
        self.assertTrue(is_video)
        self.assertTrue(known)
        self.assertEqual(playable, "https://video.twimg.com/ext_tw_video/123/pu/vid/avc1/640x360/clip.mp4")
        from discord_v2 import alert
        payload = alert("x", "사키하네 후야", title="🥱",
                        url="https://x.com/SakihaneHuya/status/2118873316052312139",
                        media_url=playable)
        self.assertIn(playable, str(payload))
        self.assertNotIn("preview.jpg", str(payload))

    def test_x_video_fallback_to_poster_for_unplayable_hls(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"tweet": {"media": {"videos": [{
                    "url": "https://video.twimg.com/ext_tw_video/123/pu/pl/master.m3u8",
                    "thumbnail_url": "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/preview.jpg",
                }]}}}
        with patch.object(media.requests, "get", return_value=Response()):
            photos, preview, video, known = media.resolve_media(
                {"id": "123", "images": []}, "SakihaneHuya")
        self.assertEqual((photos, preview, video, known),
                         ([], "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/preview.jpg", True, True))

    def test_x_video_rejects_non_twitter_download_hosts(self):
        self.assertEqual(media._playable_video_url({
            "url": "https://example.com/video.mp4",
            "formats": [{"container": "mp4", "url": "https://example.com/video.mp4"}]
        }), "")

    def test_unknown_media_never_confirmed_text_only(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"tweet": {"media": {}}}
        with patch.object(media.requests, "get", return_value=Response()):
            photos, preview, video, known = media.resolve_media(
                {"id": "123", "images": []}, "HanayoHiyori")
        self.assertFalse(known)
        self.assertEqual(photos, [])
        self.assertFalse(video)

    def test_two_matching_text_only_sources_allow_fanart(self):
        class Response:
            def __init__(self, data): self.data=data
            def raise_for_status(self): pass
            def json(self): return self.data
        fx={"tweet":{"id":"123","author":{"screen_name":"HanayoHiyori"},
                     "text":"새로운 공지", "media":{}}}
        vx={"tweetID":"123","user_screen_name":"HanayoHiyori",
            "text":"새로운 공지","mediaURLs":[],"hasMedia":False}
        with patch.object(media.requests,"get",side_effect=[Response(fx),Response(vx)]) as get:
            photos,preview,video,known=media.resolve_media(
                {"id":"123","images":[]},"HanayoHiyori")
        self.assertEqual((photos,preview,video,known),([], "", False, True))
        self.assertEqual(get.call_count,2)

    def test_second_source_media_blocks_text_only(self):
        class Response:
            def __init__(self, data): self.data=data
            def raise_for_status(self): pass
            def json(self): return self.data
        fx={"tweet":{"id":"123","author":{"screen_name":"HanayoHiyori"},
                     "text":"새로운 공지","media":None}}
        vx={"tweetID":"123","user_screen_name":"HanayoHiyori",
            "text":"새로운 공지","mediaURLs":["https://pbs.twimg.com/media/p.jpg"]}
        with patch.object(media.requests,"get",side_effect=[Response(fx),Response(vx)]):
            photos,_,_,known=media.resolve_media({"id":"123","images":[]},"HanayoHiyori")
        # The secondary source now RECOVERS the photo, rather than leaving it unknown.
        self.assertEqual(photos, ["https://pbs.twimg.com/media/p.jpg"])
        self.assertTrue(known)

    def test_incomplete_or_wrong_secondary_is_not_text_only(self):
        class Response:
            def __init__(self, data): self.data=data
            def raise_for_status(self): pass
            def json(self): return self.data
        fx={"tweet":{"id":"123","author":{"screen_name":"HanayoHiyori"},
                     "text":"새로운 공지","media":{}}}
        vx={"tweetID":"123","user_screen_name":"OtherAccount",
            "text":"새로운 공지","mediaURLs":[]}
        with patch.object(media.requests,"get",side_effect=[Response(fx),Response(vx)]):
            _,_,_,known=media.resolve_media({"id":"123","images":[]},"HanayoHiyori")
        self.assertFalse(known)

    def test_photo_in_rss_never_triggers_secondary_text_only(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"tweet":{"id":"123","author":{"screen_name":"HanayoHiyori"},
                                 "text":"사진 있음","media":{}}}
        with patch.object(media.requests,"get",return_value=Response()) as get:
            photos,_,_,known=media.resolve_media(
                {"id":"123","images":["https://pbs.twimg.com/media/abc.jpg"]},
                "HanayoHiyori")
        self.assertEqual(len(photos),1)
        self.assertFalse(known)
        self.assertEqual(get.call_count,1)

    def test_photo_in_primary_retained(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"tweet":{"media":{"photos":[{"url":"https://pbs.twimg.com/media/abc.jpg"}]}}}
        with patch.object(media.requests,"get",return_value=Response()) as get:
            photos,_,_,known=media.resolve_media({"id":"123","images":[]},"HanayoHiyori")
        self.assertEqual(photos,["https://pbs.twimg.com/media/abc.jpg"])
        self.assertTrue(known)
        self.assertEqual(get.call_count,1)



class XVideoAttachmentTests(unittest.TestCase):
    def test_small_mp4_is_embedded_as_discord_attachment(self):
        import resilience
        class Response:
            status_code = 200
            headers = {"Content-Type": "video/mp4", "Content-Length": "6"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def raise_for_status(self): pass
            def iter_content(self, size): yield b"MP4DATA"
        payload = {"components":[{"type":17,"components":[
            {"type":12,"items":[{"media":{"url":"https://video.twimg.com/ext_tw_video/123/clip.mp4"}}]}
        ]}]}
        with patch.object(resilience.requests,"get",return_value=Response()):
            uploads = resilience._collect_x_video_attachments(payload)
        self.assertEqual(uploads[0][0], "abyss_x_video.mp4")
        self.assertEqual(uploads[0][2], "video/mp4")
        self.assertEqual(payload["components"][0]["components"][0]["items"][0]["media"]["url"],
                         "attachment://abyss_x_video.mp4")

    def test_large_x_video_retains_remote_link(self):
        import resilience
        class Response:
            status_code = 200
            headers = {"Content-Type": "video/mp4", "Content-Length": "20000000"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def raise_for_status(self): pass
        url = "https://video.twimg.com/ext_tw_video/123/clip.mp4"
        payload = {"components":[{"type":17,"components":[
            {"type":12,"items":[{"media":{"url":url}}]}
        ]}]}
        with patch.object(resilience.requests,"get",return_value=Response()):
            self.assertEqual(resilience._collect_x_video_attachments(payload),[])
        self.assertEqual(payload["components"][0]["components"][0]["items"][0]["media"]["url"],url)

    def test_untrusted_video_origin_never_downloaded(self):
        import resilience
        payload={"components":[{"type":17,"components":[
            {"type":12,"items":[{"media":{"url":"https://example.com/video.mp4"}}]}
        ]}]}
        with patch.object(resilience.requests,"get") as get:
            self.assertEqual(resilience._collect_x_video_attachments(payload),[])
        get.assert_not_called()


class XProviderBackoffTests(unittest.TestCase):
    def setUp(self):
        import x_feed_health
        self.feed = x_feed_health
        self.feed._until.clear()
        self.feed._host_until.clear()
        self.feed._failures.clear()

    def tearDown(self):
        self.feed._until.clear()
        self.feed._host_until.clear()
        self.feed._failures.clear()

    def test_one_creator_timeout_pauses_other_creator_on_same_host(self):
        with patch.object(self.feed.time, "monotonic", return_value=1000):
            source = "https://fxtwitter.com/HanayoHiyori/feed.xml"
            second = "https://fxtwitter.com/SakihaneHuya/feed.xml"
            other = "https://nitter.mie.foundation/HanayoHiyori/rss"
            self.feed.note_failure(source, TimeoutError("feed timeout"))
            self.assertFalse(self.feed.active(source))
            self.assertFalse(self.feed.active(second))
            self.assertTrue(self.feed.active(other))
        with patch.object(self.feed.time, "monotonic", return_value=1350):
            self.assertTrue(self.feed.active(second))

    def test_provider_retry_after_and_snapshot_reflect_shared_outage(self):
        u1 = "https://fxtwitter.com/HanayoHiyori/feed.xml"
        u2 = "https://fxtwitter.com/SakihaneHuya/feed.xml"
        u3 = "https://nitter.mizuki.st/SakihaneHuya/rss"
        with patch.object(self.feed.time, "monotonic", return_value=100), \
             patch.object(self.feed.random, "randint", return_value=0):
            self.feed.note_failure(u1, TimeoutError())
            self.assertEqual(self.feed.retry_after([u1,u2]), 90)
            self.assertEqual(self.feed.retry_after([u2,u3]), 0)
            self.assertEqual(self.feed.source_snapshot([u2])["fxtwitter.com"]["cooldown_seconds"], 90)

    def test_429_is_thirty_minute_shared_provider_pause(self):
        import requests
        error = requests.HTTPError("rate limited")
        error.response = type("Response", (), {"status_code": 429})()
        with patch.object(self.feed.time, "monotonic", return_value=2000):
            self.feed.note_failure("https://fxtwitter.com/A/feed.xml", error)
            self.assertFalse(self.feed.active("https://fxtwitter.com/B/feed.xml"))
        with patch.object(self.feed.time, "monotonic", return_value=3799):
            self.assertFalse(self.feed.active("https://fxtwitter.com/B/feed.xml"))
        with patch.object(self.feed.time, "monotonic", return_value=3850):
            self.assertTrue(self.feed.active("https://fxtwitter.com/B/feed.xml"))

    def test_repeated_timeout_expands_cooldown(self):
        url = "https://nitter.mie.foundation/A/rss"
        with patch.object(self.feed.time, "monotonic", return_value=100):
            self.feed.note_failure(url, TimeoutError())
        with patch.object(self.feed.time, "monotonic", return_value=290):
            self.feed.note_failure(url, TimeoutError())
            self.assertFalse(self.feed.active(url))
        with patch.object(self.feed.time, "monotonic", return_value=950):
            self.assertTrue(self.feed.active(url))



if __name__ == "__main__":
    unittest.main()
