"""Avoid replacing a manually selected guild banner from a 1-image pool."""
import unittest
from unittest.mock import patch
import guild_server_banner as banner

class BannerMinimumTests(unittest.TestCase):
    def test_single_candidate_does_not_replace_guild_on_schedule(self):
        selected = ("image.jpg", {"sha256": "a", "artist": "@artist"})
        with patch.object(banner, "slot", return_value="2026-11-08 18:00"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection", return_value=(selected, ["a"], 1, 0)), \
             patch.object(banner, "change_guild_banner") as change:
            result = banner.rotate_once(manual=False)
        self.assertEqual(result["status"], "insufficient_candidates")
        change.assert_not_called()
