"""Keep Bluebird shadow mode isolated from the live X/Discord pipeline."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BluebirdShadowSafetyTests(unittest.TestCase):
    def test_probe_is_valid_python_and_uses_upstream_adapters(self):
        code = (ROOT / "scripts" / "bluebird_shadow_probe.py").read_text(encoding="utf-8")
        ast.parse(code)
        self.assertIn("from core.bettertwitfix import BetterTwitFix", code)
        self.assertIn("from core.carryfeed import CarryFeed", code)
        self.assertIn("from core.fxembed import FxEmbed", code)
        for forbidden in ("Webhook(", "send_webhook(", "discord.com", "TWSCRAPER_COOKIES"):
            self.assertNotIn(forbidden, code)

    def test_container_is_owner_gated_and_non_notifying(self):
        workflow = (ROOT / ".github" / "workflows" / "bluebird-shadow-test.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.issue.user.login == github.repository_owner", workflow)
        self.assertIn("--read-only --cap-drop ALL --security-opt no-new-privileges", workflow)
        self.assertIn("--entrypoint python", workflow)
        self.assertIn("--memory 384m", workflow)
        self.assertIn("--no-healthcheck", workflow)
        self.assertIn("sudo docker rm -f abyss-bluebird-shadow", workflow)
        self.assertIn("--interval 60", workflow)
        self.assertNotIn("DISCORD_WEBHOOK_URL", workflow)
        self.assertNotIn("-e TWSCRAPER_COOKIES=", workflow)
        self.assertNotIn("systemctl restart chzzk-bot", workflow)
        self.assertNotIn("systemctl stop chzzk-bot", workflow)


if __name__ == "__main__":
    unittest.main()
