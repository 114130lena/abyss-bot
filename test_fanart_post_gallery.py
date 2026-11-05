"""Offline checks: fanart footer picks whole X posts, not individual photos."""
import copy
import unittest
from unittest.mock import patch
import fanart_url_media as media


def sample_card():
    return {
        "components": [{
            "type": 17,
            "components": [
                {"type": 9, "components": [{"type": 10, "content": "## 사키하네 후야 SAKIHANE HUYA"}],
                 "accessory": {"type": 11, "media": {"url": "https://example.com/avatar.png"}}},
                {"type": 10, "content": "**𝕏 X**"},
                {"type": 10, "content": "-# 공식 채널 · OFFICIAL LINKS"},
                {"type": 12, "items": [{"media": {"url": "https://example.com/banner.jpg"}}]},
                {"type": 1, "components": [
                    {"type": 2, "style": 5, "label": label, "url": "https://example.com/" + label}
                    for label in ("CHZZK", "CAFE", "X", "YouTube")
                ]},
            ],
        }],
    }


class FanartGalleryTests(unittest.TestCase):
    def test_one_post_three_photos_is_one_candidate(self):
        images = ["https://pbs.twimg.com/media/abc%d.jpg" % i for i in (1, 2, 3)]
        records = {
            "sakihane:2116843682917961768": {
                "group": "sakihane", "review_status": "pending",
                "post_url": "https://x.com/Haayung00/status/2116843682917961768",
                "artist": "@Haayung00", "image_urls": images,
            },
            "other": {
                "group": "hanayo", "post_url": "https://x.com/other/status/123",
                "image_urls": ["https://pbs.twimg.com/media/else.jpg"],
            }
        }
        with patch.object(media, "read", return_value=records):
            pool = media.candidates("sakihane")
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["images"], images)

    def test_three_images_preserve_buttons_and_credit_once(self):
        originals = ["https://pbs.twimg.com/media/p%d.jpg" % i for i in range(3)]
        picked = {"artist": "@Haayung00",
                  "post_url": "https://x.com/Haayung00/status/2116843682917961768",
                  "images": originals}
        card = sample_card()
        with patch.object(media, "candidates", return_value=[picked]), \
             patch.object(media, "read", return_value={}), \
             patch.object(media.random.SystemRandom, "choice", side_effect=lambda seq: seq[0]):
            processed, selection = media.apply(card)
        parts = processed["components"][0]["components"]
        self.assertEqual([x["media"]["url"] for x in parts[3]["items"]], originals)
        self.assertIn("3장", parts[2]["content"])
        self.assertEqual([b["label"] for b in parts[4]["components"]],
                         ["원본 게시물", "CHZZK", "CAFE", "X", "YouTube"])
        self.assertEqual(selection["post_url"], picked["post_url"])
        self.assertEqual(card, sample_card())  # Original X notification payload unchanged.

    def test_only_one_photo_is_one_gallery_item(self):
        picked = {"artist": "@artist", "post_url": "https://x.com/artist/status/456",
                  "images": ["https://pbs.twimg.com/media/a.jpg"]}
        with patch.object(media, "candidates", return_value=[picked]), \
             patch.object(media, "read", return_value={}):
            processed, _ = media.apply(sample_card())
        self.assertEqual(len(processed["components"][0]["components"][3]["items"]), 1)

    def test_post_photos_keep_original_gallery(self):
        card = sample_card()
        card["components"][0]["components"].insert(2, {
            "type": 12, "items": [{"media": {"url": "https://pbs.twimg.com/media/post.jpg"}}]})
        with patch.object(media, "candidates") as candidates:
            processed, chosen = media.apply(card)
        self.assertIsNone(chosen)
        self.assertEqual(processed, card)
        candidates.assert_not_called()


if __name__ == "__main__":
    unittest.main()
