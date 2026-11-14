"""운영 배포 파일 목록을 단일 기준으로 관리하는지 검사합니다."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
MANIFEST = ROOT / "runtime_files.txt"


class DeployWorkflowTests(unittest.TestCase):
    def test_manifest_covers_required_runtime_files(self):
        files = [x.strip() for x in MANIFEST.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(len(files), len(set(files)))
        self.assertGreater(len(files), 40)
        for name in files:
            self.assertRegex(name, r"^[a-z][a-z0-9_]*\.(?:py|txt)$")
            self.assertTrue((ROOT / name).is_file(), name)
        for name in ("main.py", "discord_event_dedup.py", "x_repost_delivery_ledger.py",
                     "guild_server_banner.py", "chzzk_clip_digest.py",
                     "discord_message_sync.py", "requirements.txt", "youtube_channels.txt"):
            self.assertIn(name, files)

    def test_all_deployment_phases_use_manifest(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("mapfile -t runtime_files < runtime_files.txt", source)
        self.assertIn("mapfile -t runtime_files < .deploy-staging/runtime_files.txt", source)
        self.assertIn('"\u0024{runtime_files[@]}" runtime_files.txt', source)
        self.assertIn('"\u0024{staged_py[@]}"', source)
        self.assertIn('cp ".deploy-staging/\u0024file" .', source)
        self.assertIn('cp "\u0024backup_dir/\u0024file" .', source)
        self.assertIn("rollback_on_error", source)
        self.assertIn('test -f ".deploy-staging/\u0024file"', source)

    def test_shell_run_blocks_never_escape_yaml_indentation(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        inside = False
        for index, line in enumerate(source.splitlines(), 1):
            if line.startswith("        run: |"):
                inside = True
                continue
            if inside and line.startswith("      - name:"):
                inside = False
            if inside and line.strip():
                self.assertTrue(line.startswith("          "),
                                "Workflow shell escaped YAML block at line %d: %r" % (index, line))

    def test_search_regression_is_actually_discovered(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("-p 'test_fanart_fx_search.py' -v", source)

    def test_production_result_is_published_as_commit_status(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("statuses: write", source)
        self.assertIn("if: always()", source)
        self.assertIn('context="abyss/production-banner"', source)
        self.assertIn('context="abyss/banner-provenance/\u0024provenance_status"', source)

    def test_production_health_probe_uses_actual_dotenv_settings(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        health = source.split("Capture redacted runtime health after deployment", 1)[1]
        health = health.split("Upload redacted health report", 1)[0]
        self.assertIn('load_dotenv("/home/ubuntu/chzzk-bot/.env")', health)
        self.assertLess(health.index("load_dotenv("), health.index("from health_monitor import snapshot"))

    def test_live_banner_card_is_verified_after_restart(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertLess(source.index('sudo systemctl restart chzzk-bot'),
                        source.index("BANNER_INFO_LIVE_VERIFIED"))
        self.assertIn('if status not in ("updated", "up_to_date"):', source)


if __name__ == "__main__":
    unittest.main()
