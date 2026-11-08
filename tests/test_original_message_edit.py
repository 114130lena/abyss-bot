"""Only the author of the original Discord message may edit it."""
import unittest
from unittest.mock import Mock, patch
import discord_message_sync as sync


def resp(status, data=None):
    r=Mock(status_code=status)
    r.json.return_value=data or {}
    r.raise_for_status.side_effect=RuntimeError("HTTP error") if status>=400 else None
    return r


class OriginalMessageTests(unittest.TestCase):
    def test_bot_owned_original_edits_directly(self):
        edit=Mock(return_value=resp(200, {"id":"333"}))
        with patch.dict("os.environ", {}, clear=True):
            status=sync.update_original("111","333",{"content":"updated"},"token",
                                        patch=edit)
        self.assertEqual(status,"updated")
        self.assertIn("/channels/111/messages/333", edit.call_args.args[0])

    def test_webhook_original_uses_matching_webhook_token(self):
        original=resp(200,{"id":"333","webhook_id":"123"})
        bot_edit=resp(403)
        webhook_edit=resp(200,{"id":"333"})
        editing=Mock(side_effect=[bot_edit, webhook_edit])
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL":
                   "https://discord.com/api/webhooks/123/secret"}, clear=True):
            status=sync.update_original("111","333",{"content":"updated"},"token",
                                        get=Mock(return_value=original),patch=editing)
        self.assertEqual(status,"updated")
        self.assertEqual(editing.call_count,2)
        self.assertIn("/webhooks/123/secret/messages/333", editing.call_args.args[0])

    def test_different_webhook_never_edits_original(self):
        editing=Mock(return_value=resp(403))
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL":
                   "https://discord.com/api/webhooks/999/different"}, clear=True):
            status=sync.update_original("111","333",{}, "token",
                                        get=Mock(return_value=resp(200,{"webhook_id":"123"})),
                                        patch=editing)
        self.assertEqual(status,"webhook_credential_missing")
        self.assertEqual(editing.call_count,1)

    def test_no_credentials_does_not_query_or_post(self):
        edit=Mock(return_value=resp(403))
        lookup=Mock()
        with patch.dict("os.environ",{},clear=True):
            status=sync.update_original("111","333",{}, "token",get=lookup,patch=edit)
        self.assertEqual(status,"webhook_credential_missing")
        lookup.assert_not_called()
        self.assertEqual(edit.call_count,1)


if __name__=="__main__":
    unittest.main()
