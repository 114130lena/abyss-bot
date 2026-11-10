"""Guard authenticated Bluebird experimentation from production notifier side effects."""
import ast
import importlib.util
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "bluebird_auth_shadow_probe.py"
WORKFLOW = ROOT / ".github" / "workflows" / "bluebird-auth-shadow-test.yml"


class AuthBluebirdShadowTests(unittest.TestCase):
    def test_only_local_private_cookie_and_no_live_discord(self):
        code = PROBE.read_text(encoding="utf-8")
        ast.parse(code)
        self.assertIn('COOKIES_FILE = Path("/private/x.cookies")', code)
        self.assertIn('DATABASE_FILE = Path("/private/twscraper.db")', code)
        self.assertIn('os.umask(0o077)', code)
        self.assertIn('logger.remove()', code)
        self.assertIn('from core.twscraper import Twscraper, TwscraperRuntime, validate_cookies', code)
        for prohibited in (
            "send_webhook(", "Webhook(", "discord.com",
            "print(cookies)", "print(str(exc))", "TWSCRAPER_COOKIES=",
        ):
            self.assertNotIn(prohibited, code)

    def test_separate_one_shot_container_requires_opt_in(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.user.login == github.repository_owner", workflow)
        self.assertIn("AUTH_SHADOW_NOT_CONFIGURED_NEEDS_LOCAL_COOKIE", workflow)
        self.assertIn('if [ ! -s "$COOKIE" ]; then', workflow)
        self.assertIn('"$COOKIE_MODE" != "600"', workflow)
        self.assertIn("--read-only --cap-drop ALL --security-opt no-new-privileges", workflow)
        self.assertIn("--cycles 1", workflow)
        self.assertIn("--interval 300", workflow)
        self.assertIn("--name abyss-bluebird-auth-shadow", workflow)
        self.assertNotIn("systemctl stop chzzk-bot.service", workflow)
        self.assertNotIn("systemctl restart chzzk-bot.service", workflow)
        self.assertNotIn("DISCORD_WEBHOOK_URL", workflow)
        self.assertNotIn("TWSCRAPER_COOKIES=", workflow)

    def test_one_shot_does_not_wait_300_seconds_after_collection(self):
        spec = importlib.util.spec_from_file_location("bluebird_auth_shadow_probe", PROBE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertFalse(mod.needs_next_cycle(1, 1))
        self.assertTrue(mod.needs_next_cycle(1, 2))
        self.assertFalse(mod.needs_next_cycle(2, 2))
        self.assertTrue(mod.needs_next_cycle(1, 0))
        self.assertTrue(mod.needs_next_cycle(500, 0))
        code = PROBE.read_text(encoding="utf-8")
        self.assertIn("if not needs_next_cycle(cycle, args.cycles):\\n                break".replace("\\n", "\n"), code)

    def test_repost_event_id_and_target_are_separate(self):
        spec = importlib.util.spec_from_file_location("bluebird_auth_shadow_probe", PROBE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        repost = types.SimpleNamespace(
            username="HanayoHiyori", post_id="2120000100000000000",
            created_at=1794330000, is_repost=True, is_reply=False, is_quote=False,
            repost_of=types.SimpleNamespace(post_id="1900000000000000000"),
            media=[types.SimpleNamespace(url="https://video.twimg.com/clip.mp4")],
        )
        original = types.SimpleNamespace(
            username="HanayoHiyori", post_id="2120000200000000000",
            created_at=1794330500, is_repost=False, is_reply=False, is_quote=False,
            repost_of=None, media=[],
        )
        reply = types.SimpleNamespace(
            username="HanayoHiyori", post_id="2120000300000000000",
            created_at=1794330900, is_repost=False, is_reply=True, is_quote=False,
            repost_of=None, media=[],
        )
        context = types.SimpleNamespace(
            username="OtherUser", post_id="2120000400000000000",
            created_at=1794331000, is_repost=False, is_reply=False, is_quote=False,
            repost_of=None, media=[],
        )
        data = mod.summarize(
            types.SimpleNamespace(posts=[repost, original, reply, context], complete=True),
            "HanayoHiyori",
        )
        self.assertEqual(data["posts_count"], 3)
        self.assertEqual(data["originals_count"], 1)
        self.assertEqual(data["reposts_count"], 1)
        self.assertEqual(data["replies_count"], 1)
        self.assertEqual(data["media_posts_count"], 1)
        self.assertEqual(data["items"][0]["id"], repost.post_id)
        self.assertEqual(data["items"][0]["repost_original_id"], "1900000000000000000")
        self.assertEqual(data["items"][0]["video_count"], 1)


if __name__ == "__main__":
    unittest.main()
