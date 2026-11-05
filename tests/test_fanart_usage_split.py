"""Regression tests for notification fanart restoration and guild banner exclusion."""
import unittest
from unittest.mock import patch

import fanart_url_media
import fanart_usage
from discord_v2 import alert


def review_entry(id_, *, group="hanayo", approved=True):
    return {
        "group": group, "review_status": "approved" if approved else "pending",
        "permission_confirmed": True, "artist": "@example",
        "post_url": "https://x.com/example/status/" + str(id_),
        "image_urls": ["https://pbs.twimg.com/media/art" + str(id_) + ".jpg"]
    }


class FanartSeparationTests(unittest.TestCase):
    def test_used_server_banner_excluded_from_url_gallery(self):
        index = {"hanayo": [{
            "sha256": "server-art-hash",
            "post_url": "https://x.com/example/status/101",
            "approved": True, "permission_confirmed": True
        }]}
        state = {"last_sha256": "server-art-hash",
                 "rotation_used": ["server-art-hash"]}
        queue = {"one": review_entry(101), "two": review_entry(202)}
        def usage_read(path):
            if path.name == "fanart_index.json":
                return index
            if path.name == "guild_banner_rotation_state.json":
                return state
            return {}
        with patch.object(fanart_usage, "read", side_effect=usage_read), \\
             patch.object(fanart_url_media, "read", return_value=queue):
            result = fanart_url_media.candidates("hanayo")
        self.assertEqual([p["post_url"] for p in result],
                         ["https://x.com/example/status/202"])

    def test_explicit_server_only_art_excluded_before_use(self):
        index = {"hanayo": [{
            "sha256": "dedicated-art-hash",
            "post_url": "https://x.com/example/status/303",
            "use_for_server_banner": True
        }]}
        with patch.object(fanart_usage, "read", side_effect=lambda p: index if p.name == "fanart_index.json" else {}):
            self.assertFalse(fanart_usage.notification_allowed("hanayo", review_entry(303)))
            self.assertTrue(fanart_usage.notification_allowed("hanayo", review_entry(404)))

    def test_unapproved_and_nonconsented_art_not_restored(self):
        entries = {"pending": review_entry(1, approved=False),
                   "unlicensed": dict(review_entry(2), permission_confirmed=False),
                   "approved": review_entry(3)}
        with patch.object(fanart_usage, "read", return_value={}), \\
             patch.object(fanart_url_media, "read", return_value=entries):
            result = fanart_url_media.candidates("hanayo")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["post_url"], "https://x.com/example/status/3")

    def test_approved_art_replaces_existing_footer_with_original_button(self):
        original = alert(
            "chzzk", "하나요 히요리", "https://example.org/banner.jpg",
            title="Test", url="https://chzzk.naver.com/live/123",
            avatar="https://example.org/avatar.jpg", ping=False)
        queue = {"approved": review_entry(555)}
        with patch.object(fanart_usage, "read", return_value={}), \\
             patch.object(fanart_url_media, "read", side_effect=lambda path: queue if path == fanart_url_media.QUEUE else {}):
            changed, choice = fanart_url_media.apply(original)
        self.assertIsNotNone(choice)
        parts = next(x["components"] for x in changed["components"] if x["type"] == 17)
        self.assertTrue(any("FAN ART" in x.get("content", "") for x in parts))
        row = next(x for x in parts if x["type"] == 1)
        self.assertEqual([x["label"] for x in row["components"]],
                         ["원본 게시물", "CHZZK", "CAFE", "X", "YouTube"])
        self.assertTrue(any("art555" in str(x) for x in parts))


if __name__ == "__main__":
    unittest.main()
