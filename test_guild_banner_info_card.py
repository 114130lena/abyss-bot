"""Offline tests: one stable Discord V2 message, retry slots and metadata."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from scripts.guild_banner_info_card import card_payload, upsert_card
from scripts import guild_banner_actions as actions


class CardTests(unittest.TestCase):
    def setUp(self):
        self.state = {"artist": "@PiNA__Art",
                      "post_url": "https://x.com/PiNA__Art/status/2112493940134023358",
                      "updated_at": "2026-11-04T15:21:35+00:00",
                      "candidate_total": 20, "remaining_after": 14}

    def test_card_has_current_image_provenance_and_counts(self):
        payload = card_payload(self.state)
        self.assertEqual(payload["flags"], 32768)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        components = payload["components"][0]["components"]
        self.assertEqual(sum(c.get("type") == 12 for c in components), 1)
        self.assertIn("attachment://current_banner.jpg", str(components))
        self.assertIn("20장", str(components))
        self.assertIn("14장", str(components))
        self.assertIn("@PiNA__Art", str(components))
        self.assertIn("2112493940134023358", str(components))

    def test_existing_card_edited_never_posts_a_new_message(self):
        patched = Mock(status_code=200)
        patched.json.return_value = {"id": "1567562116280360960"}
        with patch("scripts.guild_banner_info_card.check_channel"), \
             patch("scripts.guild_banner_info_card.requests.patch", return_value=patched) as edit, \
             patch("scripts.guild_banner_info_card.requests.post") as create:
            msg, action = upsert_card("token", "123", b"image-data", self.state,
                                      "1567562116280360960")
        self.assertEqual(msg, "1567562116280360960")
        self.assertEqual(action, "edited")
        self.assertEqual(edit.call_count, 1)
        create.assert_not_called()

    def test_deleted_card_recreated_only_on_404(self):
        missing = Mock(status_code=404)
        created = Mock(status_code=200)
        created.json.return_value = {"id": "200"}
        with patch("scripts.guild_banner_info_card.check_channel"), \
             patch("scripts.guild_banner_info_card.requests.patch", return_value=missing), \
             patch("scripts.guild_banner_info_card.requests.post", return_value=created) as post:
            msg, action = upsert_card("token", "123", b"image-data", self.state, "100")
        self.assertEqual((msg, action), ("200", "created"))
        self.assertEqual(post.call_count, 1)

    def test_schedule_retry_maps_to_same_slot(self):
        fixed = datetime(2026, 11, 5, 8, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        class FakeDateTime:
            @staticmethod
            def now(zone):
                return fixed
        with patch.object(actions, "datetime", FakeDateTime), \
             patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule",
                                     "GITHUB_SCHEDULE": "27,47 23 * * *"}):
            self.assertEqual(actions.slot_label(), "2026-11-05 08:00")

    def test_stale_schedule_skips_change(self):
        fixed = datetime(2026, 11, 5, 11, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        class FakeDateTime:
            @staticmethod
            def now(zone):
                return fixed
        with patch.object(actions, "datetime", FakeDateTime), \
             patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule",
                                     "GITHUB_SCHEDULE": "27,47 23 * * *"}):
            self.assertIsNone(actions.slot_label())


if __name__ == "__main__":
    unittest.main()
