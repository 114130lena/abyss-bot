"""Banner information synchronization regression tests (no Discord network calls)."""
import unittest
from unittest.mock import patch, Mock
import guild_server_banner as banner


def response(data, status=200):
    item = Mock()
    item.status_code = status
    item.json.return_value = data
    item.raise_for_status.side_effect = (
        RuntimeError("bad status") if status >= 400 else None
    )
    return item


class BannerInfoTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "DISCORD_BOT_TOKEN": "dummy",
            "DISCORD_GUILD_ID": "123456",
            "SERVER_BANNER_INFO_MESSAGE_ID": "789",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.channel = patch.object(banner, "INFO_CHANNEL_ID", "1567561255764500551")
        self.channel.start()
        self.addCleanup(self.channel.stop)

    def test_manual_change_clears_stale_artist_and_edits_same_message(self):
        previous = {"guild_banner_hash": "oldhash", "artist": "@oldartist",
                    "post_url": "https://x.com/artist/status/123",
                    "banner_info_message_id": "789", "banner_info_updated_hash": "oldhash"}
        with patch.object(banner, "_current_guild_banner", return_value="newhash"), \
             patch.object(banner, "read_state", return_value=previous), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["external"])
        payload = edit.call_args.kwargs["json"]
        self.assertNotIn("@oldartist", str(payload["components"]))
        self.assertIn("newhash", str(payload["components"]))
        self.assertEqual(save.call_args.args[0]["banner_info_message_id"], "789")

    def test_unchanged_banner_skips_discord_message_write(self):
        state = {"guild_banner_hash": "samehash", "banner_info_updated_hash": "samehash",
                 "banner_info_message_id": "789", "banner_info_layout_version": 5}
        with patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner.requests, "patch") as edit:
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "up_to_date")
        edit.assert_not_called()

    def test_missing_original_id_never_creates_duplicate(self):
        with patch.dict("os.environ", {"SERVER_BANNER_INFO_MESSAGE_ID": ""}), \
             patch.object(banner, "_current_guild_banner", return_value="hash"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner.requests, "post") as create, \
             patch.object(banner.requests, "patch") as edit:
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "original_message_id_required")
        create.assert_not_called()
        edit.assert_not_called()

    def test_uneditable_original_never_creates_duplicate(self):
        with patch.object(banner, "_current_guild_banner", return_value="hash"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner.requests, "post") as create, \
             patch.object(banner.requests, "patch", return_value=response({}, 403)):
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "original_message_uneditable")
        create.assert_not_called()

    def test_attribution_remains_for_confirmed_bot_rotation(self):
        state = {"guild_banner_hash": "bot_hash", "artist": "@realartist",
                 "post_url": "https://x.com/realartist/status/123",
                 "banner_info_message_id": "789"}
        with patch.object(banner, "_current_guild_banner", return_value="bot_hash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "write_state"), \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            banner.sync_banner_info()
        self.assertIn("@realartist", str(edit.call_args.kwargs["json"]["components"]))


if __name__ == "__main__":
    unittest.main()
