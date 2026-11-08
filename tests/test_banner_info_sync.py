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

    def test_rotation_saves_patch_returned_banner_hash_not_stale_get(self):
        selected = ("cached.jpg", {"sha256": "fresh", "artist": "@artist",
                                   "post_url": "https://x.com/artist/status/123"})
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1", "DISCORD_BOT_TOKEN": "bot",
                                       "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "slot", return_value="2026-11-08 18:00"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection", return_value=(selected, ["fresh"], 2, 1)), \
             patch.object(banner, "convert_banner", return_value=b"jpeg"), \
             patch.object(banner, "change_guild_banner", return_value="new_banner_hash"), \
             patch.object(banner, "_current_guild_banner") as stale_get, \
             patch.object(banner, "write_state") as saved, \
             patch.object(banner, "sync_banner_info"):
            result = banner.rotate_once(manual=False)
        self.assertEqual(result["status"], "changed")
        stale_get.assert_not_called()
        self.assertEqual(saved.call_args.args[0]["guild_banner_hash"], "new_banner_hash")
        self.assertEqual(saved.call_args.args[0]["artist"], "@artist")

    def test_unchanged_banner_skips_discord_message_write(self):
        state = {"guild_banner_hash": "samehash", "banner_info_updated_hash": "samehash",
                 "banner_info_message_id": "789", "banner_info_layout_version": 10,
                 "banner_info_stats_checksum": "fixed-stats",
                 "banner_first_observed_at": "2026-11-08 18:00 KST"}
        with patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch("fanart_public_stats.snapshot", return_value={"available": False}), \
             patch("fanart_public_stats.checksum", return_value="fixed-stats"), \
             patch.object(banner.requests, "patch") as edit:
            result = banner.sync_banner_info()
        self.assertEqual(result["status"], "up_to_date")
        edit.assert_not_called()

    def test_uninitialized_hash_keeps_existing_artist_and_source(self):
        state = {"artist": "@realartist", "post_url": "https://x.com/realartist/status/123",
                 "banner_changed_at": "2026-11-08 08:00 KST"}
        with patch.object(banner, "_current_guild_banner", return_value="firsthash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            result = banner.sync_banner_info()
        self.assertFalse(result["external"])
        self.assertEqual(save.call_args.args[0]["artist"], "@realartist")
        self.assertEqual(save.call_args.args[0]["guild_banner_hash"], "firsthash")
        self.assertIn("@realartist", str(edit.call_args.kwargs["json"]))

    def test_forced_slot_sync_updates_unchanged_message(self):
        state = {"guild_banner_hash": "samehash", "banner_info_updated_hash": "samehash",
                 "banner_info_message_id": "789", "banner_info_layout_version": 5}
        with patch.object(banner, "_current_guild_banner", return_value="samehash"), \
             patch.object(banner, "read_state", return_value=state), \
             patch.object(banner, "write_state"), \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            result = banner.sync_banner_info(force=True)
        self.assertEqual(result["status"], "updated")
        edit.assert_called_once()

    def test_card_always_shows_all_fields_and_current_image(self):
        card = banner._banner_card("123456", "livehash", {})
        parts = card["components"][0]["components"]
        full = "\n".join(p.get("content", "") for p in parts)
        for label in ("현재 적용된 서버 배너", "변경 방식", "변경 시각",
                      "원본 작가", "원본 게시물"):
            self.assertIn(label, full)
        self.assertIn("확인되지 않음", full)
        self.assertIn("기록 없음", full)
        galleries = [p for p in parts if p.get("type") == 12]
        self.assertEqual(len(galleries), 1)
        self.assertIn("/banners/123456/livehash.png",
                      galleries[0]["items"][0]["media"]["url"])
        self.assertEqual(card["flags"], 32768)

    def test_card_shows_link_and_button_for_verified_source(self):
        source = "https://x.com/artist/status/12345"
        card = banner._banner_card("123456", "livehash", {
            "artist": "@artist", "post_url": source,
            "banner_changed_at": "2026-11-08 18:00 KST",
            "banner_change_method": "봇 자동 교체"})
        parts = card["components"][0]["components"]
        full = "\n".join(p.get("content", "") for p in parts)
        self.assertIn("**변경 방식** · 봇 자동 교체", full)
        self.assertIn("**변경 시각** · 2026-11-08 18:00 KST", full)
        self.assertIn("@artist", full)
        self.assertIn(source, full)
        buttons = [p for p in parts if p.get("type") == 1]
        self.assertEqual(buttons[0]["components"][0]["url"], source)
        self.assertEqual(buttons[0]["components"][0]["label"], "원본 게시물")
        self.assertLess(next(i for i,p in enumerate(parts) if p.get("type")==10 and
                             "원본 작가" in p.get("content", "")),
                        next(i for i,p in enumerate(parts) if p.get("type")==12))

    def test_no_banner_still_has_source_fields_without_inventing_an_artist(self):
        card = banner._banner_card("123456", "", {"artist": "@old",
            "post_url": "https://x.com/old/status/123"})
        parts = card["components"][0]["components"]
        full = "\n".join(p.get("content", "") for p in parts)
        self.assertIn("**원본 작가** · 확인되지 않음", full)
        self.assertIn("**원본 게시물** · 확인되지 않음", full)
        self.assertFalse(any(p.get("type")==12 for p in parts))
        self.assertFalse(any(p.get("type")==1 for p in parts))

    def test_exact_banner_hash_recovers_verified_previous_artist_and_link(self):
        prior = {
            "guild_banner_hash": "newerhash", "artist": "@newer",
            "post_url": "https://x.com/newer/status/999",
            "banner_hash_history": {
                "olderhash": {"artist": "@actual", "post_url": "https://x.com/actual/status/123",
                              "changed_at": "2026-11-05 08:00 KST",
                              "method": "봇 자동 교체"}}}
        with patch.object(banner, "_current_guild_banner", return_value="olderhash"), \
             patch.object(banner, "read_state", return_value=prior), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            result = banner.sync_banner_info()
        self.assertTrue(result["external"])
        saved = save.call_args.args[0]
        self.assertEqual(saved["artist"], "@actual")
        self.assertEqual(saved["post_url"], "https://x.com/actual/status/123")
        self.assertEqual(saved["banner_changed_at"], "")
        self.assertEqual(saved["banner_source_recorded_at"], "2026-11-05 08:00 KST")
        self.assertIn("이전 자동 배너 재사용 감지", str(edit.call_args.kwargs["json"]))

    def test_external_unknown_hash_drops_stale_artist_but_keeps_history(self):
        prior = {"guild_banner_hash": "olderhash", "artist": "@old",
                 "post_url": "https://x.com/old/status/111",
                 "banner_hash_history": {
                     "olderhash": {"artist": "@old", "post_url": "https://x.com/old/status/111"}}}
        with patch.object(banner, "_current_guild_banner", return_value="foreignhash"), \
             patch.object(banner, "read_state", return_value=prior), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner.requests, "patch", return_value=response({"id": "789"})) as edit:
            banner.sync_banner_info()
        saved = save.call_args.args[0]
        self.assertEqual(saved["artist"], "")
        self.assertEqual(saved["post_url"], "")
        self.assertEqual(saved["banner_change_method"], "외부 변경 감지")
        self.assertEqual(saved["banner_changed_at"], "")
        self.assertRegex(saved["banner_first_observed_at"], r"\d{4}-\d{2}-\d{2}")
        self.assertIn("olderhash", saved["banner_hash_history"])
        self.assertNotIn("@old", str(edit.call_args.kwargs["json"]))

    def test_rotation_records_source_hash_history_for_future_recovery(self):
        selected = ("cached.jpg", {"sha256": "fresh", "artist": "@artist",
                                   "post_url": "https://x.com/artist/status/123"})
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1", "DISCORD_BOT_TOKEN": "bot",
                                       "DISCORD_GUILD_ID": "123"}), \
             patch.object(banner, "slot", return_value="2026-11-08 18:00"), \
             patch.object(banner, "read_state", return_value={}), \
             patch.object(banner, "rotation_selection", return_value=(selected, ["fresh"], 2, 1)), \
             patch.object(banner, "convert_banner", return_value=b"jpeg"), \
             patch.object(banner, "change_guild_banner", return_value="new_banner_hash"), \
             patch.object(banner, "write_state") as save, \
             patch.object(banner, "sync_banner_info"):
            result = banner.rotate_once(manual=False)
        self.assertEqual(result["status"], "changed")
        saved = save.call_args.args[0]
        self.assertEqual(saved["banner_change_method"], "봇 자동 교체")
        self.assertEqual(saved["banner_hash_history"]["new_banner_hash"]["artist"], "@artist")
        self.assertEqual(saved["banner_hash_history"]["new_banner_hash"]["post_url"],
                         "https://x.com/artist/status/123")
        self.assertEqual(saved["guild_banner_hash"], "new_banner_hash")

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
