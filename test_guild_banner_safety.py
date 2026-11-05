"""Unit tests for crash-safe guild banner application and state backups."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import guild_server_banner as banner
from scripts import guild_banner_safety as safety


class SafetyTests(unittest.TestCase):
    def test_pending_reconciles_remote_change_without_repeat_patch(self):
        states=[]
        fake={"hash":"original"}
        def write(value):
            states.append(json.loads(json.dumps(value)))
        def get(_t,_g): return fake["hash"]
        def change(image,token,guild):fake["hash"]="new"
        with patch.object(safety,"guild_banner",side_effect=get):
            completed=safety.run_change(
                {},slot="2026-11-05 18:00",
                info={"sha256":"a","artist":"@a","post_url":"https://x.com/a/status/1",
                      "rotation_used":["a"],"candidate_total":5,"remaining_after":4},
                payload=b"jpeg",token="token",guild="123",write=write,change=change)
        self.assertEqual(states[0]["pending_banner"]["before_hash"],"original")
        self.assertNotIn("pending_banner",completed)
        self.assertEqual(completed["last_slot"],"2026-11-05 18:00")
        self.assertEqual(completed["verified_banner_hash"],"new")
        self.assertTrue(completed["info_card_pending"])

    def test_timeout_after_applied_restores_slot_without_new_patch(self):
        pending={"slot":"2026-11-05 08:00","sha256":"abc",
                 "before_hash":"old","artist":"@abc",
                 "started_at":datetime.now(timezone.utc).isoformat()}
        with patch.object(safety,"guild_banner",return_value="new"):
            result,status=safety.reconcile({"pending_banner":pending},"t","1",lambda _:None)
        self.assertEqual(status,"reconciled_changed")
        self.assertEqual(result["last_sha256"],"abc")

    def test_inflight_request_does_not_repeat_change(self):
        pending={"slot":"2026-11-05 08:00","sha256":"abc",
                 "before_hash":"old",
                 "started_at":datetime.now(timezone.utc).isoformat()}
        with patch.object(safety,"guild_banner",return_value="old"):
            result,status=safety.reconcile({"pending_banner":pending},"t","1",lambda _:None)
        self.assertEqual(status,"pending_grace")
        self.assertIn("pending_banner",result)

    def test_old_unapplied_intent_is_cleared(self):
        pending={"slot":"2026-11-05 08:00","sha256":"abc",
                 "before_hash":"old",
                 "started_at":(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()}
        with patch.object(safety,"guild_banner",return_value="old"):
            result,status=safety.reconcile({"pending_banner":pending},"t","1",lambda _:None)
        self.assertEqual(status,"not_applied")
        self.assertNotIn("pending_banner",result)

    def test_valid_backup_recovers_corrupted_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/"creator_media"
            with patch.object(banner,"media_base",return_value=root):
                banner.write_state({"last_slot":"first"})
                banner.write_state({"last_slot":"second"})
                self.assertEqual(banner.read_state()["last_slot"],"second")
                banner.state_path().write_text("{bad",encoding="utf-8")
                self.assertEqual(banner.read_state()["last_slot"],"first")
                self.assertEqual((root/"guild_banner_rotation_state.json.bak").stat().st_mode & 0o777,0o600)

if __name__=="__main__":
    unittest.main()
