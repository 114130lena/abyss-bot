"""No-network tests for shared guild-banner ownership and 08/18 clip verification."""
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import unittest

import guild_banner_timer_runner as backup
import guild_banner_healthcheck as banner_health
import clip_healthcheck as clip_health

KST = ZoneInfo("Asia/Seoul")


def at(day, hour, minute):
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


class SchedulerCoordinationTests(unittest.TestCase):
    def test_legacy_timer_is_retired_and_github_backup_uses_new_runner(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        deploy = (root / ".github/workflows/deploy.yml").read_text("utf-8")
        backup_source = (root / ".github/workflows/guild-banner-schedule.yml").read_text("utf-8")
        health_source = (root / ".github/workflows/guild-banner-health-monitor.yml").read_text("utf-8")
        clip_source = (root / ".github/workflows/clip-schedule-health.yml").read_text("utf-8")
        self.assertIn("sudo systemctl disable --now abyss-guild-banner.timer", deploy)
        self.assertIn("BANNER_CANONICAL_POOL eligible=", deploy)
        self.assertIn("guild_banner_timer_runner.py", backup_source)
        self.assertNotIn(".guild_banner_auth.env", backup_source)
        self.assertIn("guild_banner_healthcheck.py", health_source)
        self.assertIn("clip_healthcheck.py", clip_source)
        self.assertIn("25 23,9 * * *", clip_source)

    def test_backup_only_uses_its_current_kst_hour(self):
        self.assertEqual(backup.target_slot(at(8, 8, 27)), at(8, 8, 0))
        self.assertEqual(backup.target_slot(at(8, 8, 59)), at(8, 8, 0))
        self.assertEqual(backup.target_slot(at(8, 18, 47)), at(8, 18, 0))
        for when in (at(8, 7, 59), at(8, 11, 34),
                     at(8, 17, 59), at(8, 21, 34),
                     at(9, 1, 6), at(9, 6, 12)):
            with self.subTest(when=when):
                self.assertIsNone(backup.target_slot(when))

    def test_stale_github_job_cannot_change_banner_overnight(self):
        with patch("guild_server_banner.rotate_once") as rotate:
            result = backup.run(at(9, 1, 6))
        self.assertEqual(result["status"], "outside_schedule")
        self.assertIsNone(result["target_slot"])
        rotate.assert_not_called()

    def test_backup_uses_canonical_rotate_once_with_same_slot(self):
        with patch("guild_server_banner.rotate_once", return_value={"status": "already_rotated"}) as rotate:
            result = backup.run(at(8, 8, 34))
        self.assertEqual(result["status"], "already_rotated")
        rotate.assert_called_once_with(now=at(8, 8, 0))

    def test_health_reads_single_canonical_state_not_legacy_timer(self):
        state = {"last_slot": "2026-11-08 08:00",
                 "guild_banner_hash": "newhash",
                 "banner_info_updated_hash": "newhash",
                 "candidate_total": 6}
        with patch("guild_server_banner.read_state", return_value=state):
            result = banner_health.check(at(8, 8, 45), current_hash="newhash")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate_total"], 6)
        with patch("guild_server_banner.read_state", return_value={**state, "last_slot": None}):
            missed = banner_health.check(at(8, 8, 45), current_hash="newhash")
        self.assertEqual(missed["status"], "missing_slot")


class ClipPublishingTests(unittest.TestCase):
    def test_clip_collection_is_independent_from_08_18_publication(self):
        now = at(9, 8, 25)
        saved = {"last_published_slot": "2026-11-09 08",
                 "last_updated": "2026-11-09T08:02:00+09:00",
                 "clips": [{"id": "redacted"}]}
        response = Mock()
        response.json.return_value = {"id": "200", "flags": 32768,
                                      "components": [{"type": 17}],
                                      "edited_timestamp": "2026-11-08T23:02:00+00:00"}
        response.raise_for_status.return_value = None
        with patch.dict("os.environ", {
                "DISCORD_BOT_TOKEN": "fake",
                "CHZZK_CLIP_CHANNEL_ID": "100",
                "CHZZK_CLIP_MESSAGE_ID": "200"}), \
             patch.object(clip_health, "load_json", return_value=saved):
            result = clip_health.check(now=now, get=Mock(return_value=response))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["cached_clips"], 1)

    def test_old_clip_slot_cannot_pass_if_data_was_collected_recently(self):
        now = at(9, 8, 25)
        saved = {"last_published_slot": "2026-11-08 18",
                 "last_updated": "2026-11-08T18:02:00+09:00",
                 "collected_at": "2026-11-09T08:00:00+09:00"}
        with patch.object(clip_health, "load_json", return_value=saved):
            result = clip_health.check(now=now)
        self.assertEqual(result["status"], "missing_published_slot")

    def test_metadata_success_cannot_mask_old_discord_message(self):
        saved = {"last_published_slot": "2026-11-09 08",
                 "last_updated": "2026-11-09T08:02:00+09:00"}
        response = Mock()
        response.json.return_value = {"id": "200", "flags": 32768,
                                      "components": [{"type": 17}],
                                      "edited_timestamp": "2026-11-08T09:00:00+00:00"}
        with patch.dict("os.environ", {
                "DISCORD_BOT_TOKEN": "fake",
                "CHZZK_CLIP_CHANNEL_ID": "100",
                "CHZZK_CLIP_MESSAGE_ID": "200"}), \
             patch.object(clip_health, "load_json", return_value=saved):
            result = clip_health.check(now=at(9, 8, 25), get=Mock(return_value=response))
        self.assertEqual(result["status"], "original_not_edited_this_slot")


if __name__ == "__main__":
    unittest.main()
