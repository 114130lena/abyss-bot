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
        self.assertNotIn("@oldartist", payload["embeds"][0]["description"])
        self.assertIn("newhash", payload["embeds"][0]["image"]["url"])
        self.assertEqual(save.call_args.args[0]["banner_info_message_id"], "789")

    def test_unchanged_banner_skips_discord_message_write(self):
        state = {"guild_banner_hash": "samehash", "banner_info_updated_hash": "samehash",
                 "banner_info_message_id": "789"}
        with patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner.requests, "patch") as edit:
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "up_to_date")
        edit.assert_not_called()

    def test_new_card_is_persisted_only_on_success(self):
        with patch.object(banner, "_current_guild_banner", return_value="hash"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "_find_existing_banner_info", return_value=""), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner.requests, "post", return_value=response({"id": "999"})):
            result = banner.sync_banner_info()
        self.assertEqual(result["message_id"], "999")
        self.assertEqual(save.call_args.args[0]["banner_info_updated_hash"], "hash")

    def test_attribution_remains_for_confirmed_bot_rotation(self):
        state = {"guild_banner_hash": "bot_hash", "artist": "@realartist",
                 "post_url": "https://x.com/realartist/status/123",
                 "banner_info_message_id": "789"}
        with patch.object(banner, "_current_guild_banner", return_value="bot_hash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "write_state"), \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            banner.sync_banner_info()
        self.assertIn("@realartist", edit.call_args.kwargs["json"]["embeds"][0]["description"])


if __name__ == "__main__":
    unittest.main()
