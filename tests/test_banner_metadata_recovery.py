"""Provenance recovery from Discord audit records and verified local artworks.

All external API responses and files are fake; tests must never touch live guild.
"""
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from PIL import Image
import guild_server_banner as banner

KST = ZoneInfo("Asia/Seoul")


def resp(status, data=None, content=b""):
    result = Mock(status_code=status, content=content)
    result.json.return_value = data or {}
    if status >= 400:
        from requests import HTTPError
        result.raise_for_status.side_effect = HTTPError("fake error")
    return result


def snowflake(dt):
    utc_ms = int(dt.timestamp() * 1000)
    return str((utc_ms - 1420070400000) << 22)


class BannerMetadataRecoveryTests(unittest.TestCase):
    def test_audit_returns_genuine_user_and_time_for_exact_hash(self):
        at = datetime(2026, 11, 7, 17, 43, tzinfo=KST)
        entry = {"id": snowflake(at), "action_type": 1, "user_id": "17",
                 "changes": [{"key": "banner_hash", "old_value": "old", "new_value": "current"}]}
        fetch = Mock(return_value=resp(200, {
            "audit_log_entries": [entry],
            "users": [{"id": "17", "username": "serverstaff", "bot": False}]}))
        record = banner._audit_banner_change("token", "123", "current", get=fetch)
        self.assertEqual(record["status"], "matched")
        self.assertEqual(record["banner_changed_at"], "2026-11-07 17:43 KST")
        self.assertIn("@serverstaff", record["banner_change_method"])
        self.assertEqual(fetch.call_args.kwargs["params"]["action_type"], 1)

    def test_audit_does_not_attribute_unrelated_guild_change(self):
        at = datetime(2026, 11, 7, 17, 43, tzinfo=KST)
        items = [
            {"id": snowflake(at), "action_type": 1, "user_id": "17",
             "changes": [{"key": "name", "new_value": "hello"}]},
            {"id": snowflake(at), "action_type": 1, "user_id": "18",
             "changes": [{"key": "banner_hash", "new_value": "different"}]},
        ]
        record = banner._audit_banner_change("token", "123", "current",
            get=Mock(return_value=resp(200, {"audit_log_entries": items})))
        self.assertEqual(record["status"], "not_found")

    def test_audit_handles_missing_permission_without_inventing_identity(self):
        record = banner._audit_banner_change("token", "123", "current",
            get=Mock(return_value=resp(403)))
        self.assertEqual(record["status"], "permission_denied")
        self.assertNotIn("banner_change_method", record)

    def test_exact_cached_art_recovers_verified_artist_and_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "known.jpg"
            Image.new("RGB", (1920, 1080), (80, 100, 125)).save(source)
            rendered = banner.convert_banner(source)
            buf = io.BytesIO()
            with Image.open(io.BytesIO(rendered)) as art:
                art.save(buf, format="PNG")
            candidate = (source, {"artist": "@real_artist",
                                  "post_url": "https://x.com/real_artist/status/123"})
            with patch.object(banner, "approved_hanayo_art", return_value=[candidate]), \
                 patch.object(banner, "collected_hanayo_art", return_value=[]):
                found = banner._recover_cached_image_source("123", "current",
                    get=Mock(return_value=resp(200, content=buf.getvalue())))
        self.assertEqual(found["status"], "matched")
        self.assertEqual(found["artist"], "@real_artist")
        self.assertEqual(found["post_url"], "https://x.com/real_artist/status/123")

    def test_image_recovery_rejects_an_unrelated_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "known.jpg"
            Image.new("RGB", (1920, 1080), (80, 100, 125)).save(source)
            buf = io.BytesIO()
            Image.new("RGB", (960, 540), (255, 0, 0)).save(buf, format="PNG")
            candidate = (source, {"artist": "@real_artist",
                                  "post_url": "https://x.com/real_artist/status/123"})
            with patch.object(banner, "approved_hanayo_art", return_value=[candidate]), \
                 patch.object(banner, "collected_hanayo_art", return_value=[]):
                found = banner._recover_cached_image_source("123", "current",
                    get=Mock(return_value=resp(200, content=buf.getvalue())))
        self.assertEqual(found["status"], "not_found")

    def test_existing_old_card_backfills_from_discord_audit(self):
        old = {"guild_banner_hash": "current", "artist": "", "post_url": "",
               "banner_info_message_id": "789", "banner_info_updated_hash": "current",
               "banner_info_layout_version": 6}
        audit = {"status": "matched", "banner_change_method": "관리 권한 사용자 변경 (@serverstaff)",
                 "banner_changed_at": "2026-11-07 17:43 KST",
                 "banner_audit_entry_id": "1234567"}
        with patch.dict("os.environ", {
            "DISCORD_BOT_TOKEN": "test", "DISCORD_GUILD_ID": "123",
            "SERVER_BANNER_INFO_MESSAGE_ID": "789",
            "SERVER_BANNER_AUDIT_LOOKUP": "1",
            "SERVER_BANNER_SOURCE_IMAGE_MATCH": "0"}), \
             patch.object(banner, "_current_guild_banner", return_value="current"), \
             patch.object(banner, "read_state", return_value=old), \
             patch.object(banner, "_audit_banner_change", return_value=audit) as lookup, \
             patch.object(banner, "write_state") as save, \
             patch("discord_message_sync.update_original", return_value="updated"):
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "updated")
        lookup.assert_called_once()
        state = save.call_args.args[0]
        self.assertEqual(state["banner_change_method"], audit["banner_change_method"])
        self.assertEqual(state["banner_changed_at"], audit["banner_changed_at"])
        self.assertEqual(state["banner_info_layout_version"], 7)

    def test_first_observed_is_not_falsely_labeled_exact_change_time(self):
        state = {"banner_first_observed_at": "2026-11-08 20:00 KST"}
        card = banner._banner_card("123", "current", state)
        msg = str(card["components"])
        self.assertIn("정확한 변경 시각 미확인", msg)
        self.assertIn("2026-11-08 20:00 KST", msg)

    def test_no_audit_retry_for_same_hash_when_attempt_already_recorded(self):
        old = {"guild_banner_hash": "current",
               "banner_audit_checked_hash": "current",
               "banner_audit_status": "permission_denied",
               "banner_first_observed_at": "2026-11-08 20:00 KST"}
        with patch.dict("os.environ", {
            "DISCORD_BOT_TOKEN": "test", "DISCORD_GUILD_ID": "123",
            "SERVER_BANNER_INFO_MESSAGE_ID": "789",
            "SERVER_BANNER_AUDIT_LOOKUP": "1",
            "SERVER_BANNER_SOURCE_IMAGE_MATCH": "0"}), \
             patch.object(banner, "_current_guild_banner", return_value="current"), \
             patch.object(banner, "read_state", return_value=old), \
             patch.object(banner, "_audit_banner_change") as lookup, \
             patch.object(banner, "write_state"), \
             patch("discord_message_sync.update_original", return_value="updated"):
            banner.sync_banner_info()
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
