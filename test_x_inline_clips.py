"""X image, video and GIF routing without posting live Discord messages."""
import unittest
from unittest.mock import Mock, patch

import x_post_media
import discord_v2


def fake_fx(tweet):
    reply=Mock()
    reply.raise_for_status.return_value=None
    reply.json.return_value={"tweet":tweet}
    return reply


class NativeClipTests(unittest.TestCase):
    def test_public_original_mp4_is_playable_gallery_media(self):
        direct="https://video.twimg.com/ext_tw_video/123/pu/vid/720x1280/clip.mp4?tag=12"
        thumbnail="https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/clip.jpg"
        tweet={"media":{"videos":[{"url":direct,"thumbnail_url":thumbnail,
                                    "type":"video","format":"video/mp4"}]}}
        with patch.object(x_post_media.requests,"get",return_value=fake_fx(tweet)):
            photos,preview,has_video,known,url=x_post_media.resolve_media_with_video(
                {"id":"123","images":[]},"HanayoHiyori")
        self.assertEqual(photos,[])
        self.assertEqual(preview,thumbnail)
        self.assertEqual(url,direct)
        self.assertTrue(has_video)
        self.assertTrue(known)
        card=discord_v2.alert("x","하나요 히요리",role_id="",url="https://x.com/HanayoHiyori/status/123",
                              body="영상 확인",media_url=url,ping=False)
        gallery=[part for container in card["components"]
                 for part in container.get("components",[]) if part.get("type")==12]
        self.assertTrue(any(i["media"]["url"]==direct for box in gallery for i in box["items"]))
        self.assertEqual(card["allowed_mentions"]["parse"],[])

    def test_animated_gif_mp4_is_playable(self):
        video="https://video.twimg.com/tweet_video/example.mp4"
        with patch.object(x_post_media.requests,"get",return_value=fake_fx(
            {"media":{"gifs":[{"url":video,"type":"gif"}]}})):
            _,_,has_video,known,url=x_post_media.resolve_media_with_video(
                {"id":"124"},"HanayoHiyori")
        self.assertTrue(has_video)
        self.assertTrue(known)
        self.assertEqual(url,video)

    def test_no_unsafe_third_party_or_hls_stream(self):
        for url in ("https://evil.test/video.mp4","http://video.twimg.com/foo.mp4",
                    "https://video.twimg.com/video.m3u8",
                    "https://video.twimg.com.evil.test/movie.mp4"):
            self.assertFalse(x_post_media._direct_video_url(url))

    def test_photo_post_does_not_add_video(self):
        picture="https://pbs.twimg.com/media/test.jpg?name=orig"
        with patch.object(x_post_media.requests,"get",return_value=fake_fx(
            {"media":{"photos":[{"url":picture}]}})):
            photos,preview,has_video,known,url=x_post_media.resolve_media_with_video(
                {"id":"125"},"SakihaneHuya")
        self.assertEqual(photos,[picture])
        self.assertFalse(has_video)
        self.assertTrue(known)
        self.assertFalse(url)

    def test_api_missing_metadata_remains_unverified(self):
        with patch.object(x_post_media.requests,"get",return_value=fake_fx(
            {"media":None})),patch.object(x_post_media.time,"sleep"):
            photos,preview,has_video,known,url=x_post_media.resolve_media_with_video(
                {"id":"126"},"HanayoHiyori")
        self.assertFalse(known)
        self.assertFalse(has_video)
        self.assertEqual(url,"")
        self.assertEqual(x_post_media.resolve_media({"id":"oops"},"HanayoHiyori"),( [], "", False, False))

    def test_legacy_four_tuple_is_maintained(self):
        with patch.object(x_post_media.requests,"get",return_value=fake_fx(
            {"media":{"all":[]}})):
            old=x_post_media.resolve_media({"id":"127"},"HanayoHiyori")
            self.assertEqual(len(old),4)
            self.assertTrue(old[3])


if __name__=="__main__":
    unittest.main()
