"""Persistent scheduled guild banner attempts without real Discord calls."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import guild_server_banner as banner

KST = ZoneInfo("Asia/Seoul")

def when(hour, minute):
    return datetime(2026, 11, 9, hour, minute, tzinfo=KST)


class PendingBannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pending = Path(self.tmp.name) / "pending.json"
        self.p = patch.object(banner, "_pending_banner_path", return_value=self.pending)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_persists_failure_then_retries_after_restart(self):
        old = {"last_slot": "2026-11-09 08:00", "guild_banner_hash": "oldhash"}
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "test", "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "read_state", return_value=old), \
             patch.object(banner, "rotate_once", side_effect=[
                 {"status": "retryable_error"}, {"status": "changed"}]) as rotate, \
             patch.object(banner, "_current_guild_banner", return_value="oldhash"):
            first = banner.retry_scheduled_banner_once(when(18, 0))
            self.assertEqual(first["status"], "retryable_error")
            self.assertTrue(self.pending.exists())
            self.assertEqual(banner.retry_scheduled_banner_once(when(18, 1))["status"],
                             "retry_waiting")
            # Simulated process restart: only JSON on disk retains pending.
            second = banner.retry_scheduled_banner_once(when(18, 4))
        self.assertEqual(second["status"], "changed")
        self.assertEqual(rotate.call_count, 2)
        self.assertEqual(rotate.call_args.kwargs["now"], when(18, 0))
        self.assertEqual(rotate.call_args.kwargs["apply_deadline"], when(18, 0).replace(
            day=10, hour=8))
        self.assertEqual(json.loads(self.pending.read_text()), {})

    def test_external_change_cancels_pending_before_any_retry(self):
        old = {"last_slot": "2026-11-09 08:00", "guild_banner_hash": "oldhash"}
        with patch.object(banner, "read_state", side_effect=[
                old, {"last_slot": "2026-11-09 08:00",
                      "guild_banner_hash": "newmanual"}]), \
             patch.object(banner, "rotate_once", return_value={"status": "retryable_error"}) as rotate:
            banner.retry_scheduled_banner_once(when(18, 0))
            result = banner.retry_scheduled_banner_once(when(18, 4))
        self.assertEqual(result["status"], "external_change_recorded")
        self.assertEqual(rotate.call_count, 1)
        self.assertEqual(banner._pending_banner_read(), {})

    def test_next_slot_supersedes_old_request(self):
        self.pending.write_text(json.dumps({
            "slot": "2026-11-08 18:00", "attempts": 4,
            "baseline_hash": "hash"}))
        with patch.object(banner, "read_state", return_value={"last_slot": "2026-11-08 08:00"}), \
             patch.object(banner, "rotate_once", return_value={"status": "changed"}) as rotate:
            result = banner.retry_scheduled_banner_once(when(8, 30))
        self.assertEqual(result["status"], "superseded_slot")
        rotate.assert_not_called()
        self.assertEqual(banner._pending_banner_read(), {})

    def test_candidate_exhaustion_waits_for_new_art_without_reset(self):
        with patch.object(banner, "read_state", return_value={
                "last_slot": "2026-11-09 08:00", "guild_banner_hash": "x"}), \
             patch.object(banner, "rotate_once", return_value={
                 "status": "exhausted_candidates"}):
            result = banner.retry_scheduled_banner_once(when(18, 0))
        self.assertEqual(result["status"], "exhausted_candidates")
        self.assertEqual(banner._pending_banner_read()["slot"], "2026-11-09 18:00")
        self.assertEqual(banner._pending_banner_read()["next_attempt_at"],
                         when(18, 20).isoformat())

    def test_late_start_within_ten_minutes_creates_current_slot(self):
        with patch.object(banner, "read_state", return_value={
                "last_slot": "2026-11-09 08:00",
                "guild_banner_hash": "oldhash"}), \
             patch.object(banner, "rotate_once", return_value={"status": "changed"}) as rotate:
            result = banner.retry_scheduled_banner_once(when(18, 6))
        self.assertEqual(result["status"], "changed")
        self.assertEqual(rotate.call_args.kwargs["now"], when(18, 0))
        self.assertEqual(banner._pending_banner_read(), {})

    def test_late_start_after_grace_does_not_create_slot(self):
        with patch.object(banner, "read_state", return_value={
                "last_slot": "2026-11-09 08:00"}), \
             patch.object(banner, "rotate_once") as rotate:
            result = banner.retry_scheduled_banner_once(when(18, 11))
        self.assertEqual(result["status"], "no_pending_slot")
        rotate.assert_not_called()

    def test_success_does_not_restart_same_slot(self):
        with patch.object(banner, "read_state", return_value={
                "last_slot": "2026-11-09 18:00"}), \
             patch.object(banner, "rotate_once") as rotate:
            result = banner.retry_scheduled_banner_once(when(18, 0))
        self.assertEqual(result["status"], "already_rotated")
        rotate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
