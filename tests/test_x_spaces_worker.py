"""Regression for opt-in X Spaces activation without role mentions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import x_spaces_notifier as spaces


class XSpacesTests(unittest.TestCase):
    def test_live_space_is_sent_without_ping_or_role(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "data": [{"id": "888888888", "creator_id": "17",
                      "state": "live", "title": "방송 스페이스"}]}
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "spaces.json"
            state.write_text("{}", encoding="utf-8")
            with patch.dict("os.environ", {"X_BEARER_TOKEN": "fake-token"}), \
                 patch.object(spaces, "STATE", state), \
                 patch.object(spaces, "ACCOUNT", {"hanayo": "17", "sakihane": ""}), \
                 patch.object(spaces, "WEBHOOK", "https://discord.com/api/webhooks/1/fake"), \
                 patch.object(spaces, "NEXT", 0), \
                 patch.object(spaces.requests, "get", return_value=response), \
                 patch.object(spaces, "get_banner", return_value="https://example.com/banner.jpg"), \
                 patch.object(spaces, "avatar", return_value=""), \
                 patch.object(spaces, "alert", return_value={"components": []}) as render, \
                 patch.object(spaces, "send_webhook") as delivered:
                spaces.check_spaces()
            self.assertEqual(render.call_count, 1)
            self.assertEqual(render.call_args.kwargs["role_id"], "")
            self.assertFalse(render.call_args.kwargs["ping"])
            self.assertEqual(delivered.call_count, 1)

    def test_main_registers_spaces_only_with_api_credentials(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text("utf-8")
        self.assertIn('from x_spaces_notifier import check_spaces', source)
        self.assertIn('workers.append(("X 스페이스", check_spaces))', source)
        self.assertIn('"X_BEARER_TOKEN"', source)


if __name__ == "__main__":
    unittest.main()
