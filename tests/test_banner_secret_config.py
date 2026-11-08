"""Regression checks for secure deploy-time banner configuration."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "configure_banner_env.py"
spec = importlib.util.spec_from_file_location("configure_banner_env", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def config(token="bot-token", guild="123456"):
    return {
        "DISCORD_BOT_TOKEN": token,
        "DISCORD_GUILD_ID": guild,
        "SERVER_BANNER_ENABLED": "1",
        "SERVER_BANNER_USE_COLLECTED": "1",
        "SERVER_BANNER_ALLOW_FIT": "1",
        "FANART_CACHE_CANDIDATES": "1",
        "FANART_DISCOVERY_ENABLED": "1",
        "SERVER_BANNER_INFO_CHANNEL_ID": "1567561255764500551",
        "SERVER_BANNER_INFO_MESSAGE_ID": "1567562116280360960",
        "SERVER_BANNER_INFO_POLL_SECONDS": "60",
        "SERVER_BANNER_AUDIT_LOOKUP": "1",
        "SERVER_BANNER_SOURCE_IMAGE_MATCH": "1",
        "CHZZK_CLIP_CHANNEL_ID": "1567760518977167501",
        "CHZZK_CLIP_MESSAGE_ID": "1567763296151539875",
    }


class BannerSecretConfigTests(unittest.TestCase):
    def test_preserves_other_settings_and_updates_values_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OTHER_SETTING=keep\nDISCORD_BOT_TOKEN=old\n"
                            "DISCORD_BOT_TOKEN=duplicate\n", encoding="utf-8")
            module.update_env(config(), path)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertIn("OTHER_SETTING=keep", lines)
            self.assertIn("DISCORD_BOT_TOKEN=bot-token", lines)
            self.assertEqual(sum(line.startswith("DISCORD_BOT_TOKEN=") for line in lines), 1)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            module.update_env(config("new-token"), path)
            self.assertIn("DISCORD_BOT_TOKEN=new-token", path.read_text())

    def test_rejects_incomplete_or_multiline_secrets_without_changing_env(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("OTHER=keep\n", encoding="utf-8")
            for bad in (config("", "123"), config("line\nINJECTED=1", "123"), config("ok", "abc")):
                with self.assertRaises(ValueError):
                    module.update_env(bad, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "OTHER=keep\n")


if __name__ == "__main__":
    unittest.main()
