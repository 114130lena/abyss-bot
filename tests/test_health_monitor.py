"""Offline policy checks for automated fanart and guild-banner health warnings."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import health_monitor
from scripts import operational_audit


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

    def test_verify_file_presence_in_cached_gallery_refs(self):
        import tempfile
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / "creator_media"
            cached = base / "fanart_candidates" / "hanayo"
            cached.mkdir(parents=True)
            (cached / "present.jpg").write_bytes(b"sample")
            (base / "fanart_discovery.json").write_text(json.dumps({
                "h1": {"group": "hanayo", "text": "히요리 #hiyoriart_",
                       "cached_files": [
                          "fanart_candidates/hanayo/present.jpg",
                          "fanart_candidates/hanayo/missing.jpg",
                          "../../untrusted.jpg"]}}), encoding="utf-8")
            with patch.object(health_monitor, "ROOT", root), \
                 patch("guild_server_banner.collected_hanayo_art", return_value=[]), \
                 patch("guild_server_banner.approved_hanayo_art", return_value=[]), \
                 patch("delivery_review.count_pending", return_value=0):
                report = health_monitor.snapshot()
        self.assertEqual(report["fanart"]["hanayo"]["cached_images"], 3)
        self.assertEqual(report["fanart"]["hanayo"]["verified_cached_images"], 1)
        self.assertEqual(report["fanart"]["hanayo"]["missing_cached_images"], 2)
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            warnings = health_monitor.health_warnings(report)
        self.assertIn("fanart_cache_integrity", warnings)

    def test_disabled_features_do_not_create_false_alarm(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            self.assertEqual(health_monitor.health_warnings({"banner": {}, "fanart": {}}), {})

    def test_operational_audit_uses_poll_success_not_post_state_file(self):
        now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
        state = {
            "히요리 X": {
                "last_check": "2026-11-09T11:59:30+09:00",
                "last_success": "2026-11-09T11:59:30+09:00",
                "consecutive_failures": 0,
            },
            "후야 X": {
                "last_check": "2026-11-09T11:59:00+09:00",
                "last_success": "2026-11-09T11:45:00+09:00",
                "consecutive_failures": 4,
                "reason": "SENSITIVE_EXCEPTION_TEXT",
            },
        }
        observed = operational_audit.source_check_summary(state=state, now=now)
        self.assertEqual(observed["히요리 X"]["status"], "ok")
        self.assertEqual(observed["후야 X"]["status"], "repeated_failures")
        self.assertEqual(observed["네이버 카페"]["status"], "unverified")
        self.assertNotIn("SENSITIVE_EXCEPTION_TEXT", str(observed))

    def test_operational_audit_sanitizes_component_snapshot(self):
        report = {
            "fanart": {
                "hanayo": {"posts": 5, "verified_cached_images": 4,
                           "missing_cached_images": 1},
                "sakihane": {"posts": 2, "verified_cached_images": 2},
                "hashtag_search": {
                    "hanayo": {"status": "ok", "secret": "DO_NOT_LEAK"},
                    "sakihane": {"status": "unavailable"}},
            },
            "banner": {"bot_rotation_enabled": True, "legacy_timer_active": False,
                       "total_eligible_candidates": 7, "unused_candidates": 4,
                       "vision_enabled": True, "vision_ready": True,
                       "post_url": "https://x.com/DO_NOT_LEAK"},
            "clip": {"configured": True, "state_exists": True,
                     "cached_clips": 7, "collected_age_hours": 0.5,
                     "published_age_hours": 12,
                     "title": "DO_NOT_LEAK"},
            "spaces": {"configured": False},
            "discord_uncertain_deliveries": 0,
        }
        with patch("health_monitor.snapshot", return_value=report), \\
             patch("health_monitor.health_warnings",
                   return_value={"fanart_cache_integrity": "DO_NOT_LEAK"}):
            summarized = operational_audit.component_check_summary()
        self.assertEqual(summarized["banner"]["unused_candidates"], 4)
        self.assertEqual(summarized["fanart"]["groups"]["hanayo"]["missing_cached_images"], 1)
        self.assertEqual(summarized["warning_codes"], ["fanart_cache_integrity"])
        self.assertNotIn("DO_NOT_LEAK", str(summarized))


if __name__ == "__main__":
    unittest.main()
