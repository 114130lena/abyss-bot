"""Offline policy checks for automated fanart and guild-banner health warnings."""
import unittest
from unittest.mock import patch
import health_monitor


class HealthWarningsTests(unittest.TestCase):
    def test_banner_pool_depletion_and_hashtag_outage(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "SERVER_BANNER_MIN_CANDIDATES": "2",
                                       "FANART_FX_SEARCH_ENABLED": "1"}):
            report = {"banner": {"approved_candidates": 0, "collected_candidates": 1},
                      "fanart": {"hashtag_search": {"hanayo": {"status": "unavailable"},
                                                  "sakihane": {"status": "ok"}},
                                 "search_age_hours": 1}}
            warnings = health_monitor.health_warnings(report)
        self.assertIn("server_banner_pool", warnings)
        self.assertIn("fanart_hashtag_search", warnings)

    def test_healthy_pipeline_does_not_warn(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "FANART_FX_SEARCH_ENABLED": "1"}):
            report = {"banner": {"approved_candidates": 1, "collected_candidates": 3},
                      "fanart": {"hashtag_search": {"hanayo": {"status": "ok"},
                                                  "sakihane": {"status": "ok"}},
                                 "search_age_hours": 0.5}}
            self.assertEqual(health_monitor.health_warnings(report), {})

    def test_stale_clip_data_triggers_warning_but_old_published_card_does_not(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            stale = {"clip": {"configured": True, "state_exists": True,
                              "cached_clips": 9, "collected_age_hours": 5,
                              "published_age_hours": 1}}
            self.assertIn("clip_collection", health_monitor.health_warnings(stale))
            recent = {"clip": {"configured": True, "state_exists": True,
                               "cached_clips": 9, "collected_age_hours": 0.5,
                               "published_age_hours": 11}}
            self.assertNotIn("clip_collection", health_monitor.health_warnings(recent))

    def test_no_clip_file_warns_only_if_configured(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            self.assertEqual(health_monitor.health_warnings(
                {"clip": {"configured": False, "state_exists": False}}), {})
            self.assertIn("clip_collection", health_monitor.health_warnings(
                {"clip": {"configured": True, "state_exists": False}}))

    def test_snapshot_does_not_leak_clip_or_spaces_api_secrets(self):
        import tempfile
        import json
        from pathlib import Path
        from unittest.mock import patch as mock_patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = {"clips": [{"id": "SECRET_PRIVATE_ID_123", "title": "private_title"}],
                     "collected_at": "2026-11-08T19:30:00+09:00",
                     "last_updated": "2026-11-08T18:00:00+09:00",
                     "last_published_slot": "2026-11-08 18"}
            (root / "daily_clip_state.json").write_text(json.dumps(state), encoding="utf-8")
            with mock_patch.object(health_monitor, "ROOT", root), \
                 patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "DUMMY_SECRET_TOKEN",
                                          "CHZZK_CLIP_CHANNEL_ID": "123",
                                          "CHZZK_CLIP_MESSAGE_ID": "456",
                                          "X_BEARER_TOKEN": "DUMMY_X_SECRET",
                                          "HANAYO_X_USER_ID": "17"}), \
                 mock_patch("guild_server_banner.collected_hanayo_art", return_value=[]), \
                 mock_patch("guild_server_banner.approved_hanayo_art", return_value=[]), \
                 mock_patch("delivery_review.count_pending", return_value=0):
                report = health_monitor.snapshot()
        self.assertTrue(report["clip"]["configured"])
        self.assertTrue(report["clip"]["state_exists"])
        self.assertEqual(report["clip"]["cached_clips"], 1)
        self.assertEqual(report["clip"]["last_published_slot"], "2026-11-08 18")
        self.assertTrue(report["spaces"]["configured"])
        serialized = json.dumps(report)
        self.assertNotIn("SECRET_PRIVATE_ID_123", serialized)
        self.assertNotIn("private_title", serialized)
        self.assertNotIn("DUMMY_X_SECRET", serialized)
        self.assertNotIn("DUMMY_SECRET_TOKEN", serialized)

    def test_disabled_features_do_not_create_false_alarm(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            self.assertEqual(health_monitor.health_warnings({"banner": {}, "fanart": {}}), {})


if __name__ == "__main__":
    unittest.main()
