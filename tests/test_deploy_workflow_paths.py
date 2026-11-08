"""Guard against remote deployment using non-staged Python files.

The bot runs on an Ubuntu host without git pull: new files must be
scp-uploaded, compiled from staging, then copied into production.
"""
import unittest
from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github" / "workflows" / "deploy.yml")


class DeployWorkflowTests(unittest.TestCase):
    def test_every_uploaded_runtime_module_is_compiled_and_activated_from_staging(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        lines = source.splitlines()
        uploaded = next(line.strip() for line in lines
                        if line.strip().startswith("main.py chzzk_replay_notifier.py "))
        remote_check = next(line.strip() for line in lines
                            if line.strip().startswith('"$BOT_PY" -m py_compile .deploy-staging/main.py'))
        copied = next(line.strip() for line in lines
                      if line.strip().startswith("cp .deploy-staging/main.py"))
        modules = [item for item in uploaded.split()
                   if item.endswith(".py")]
        self.assertGreater(len(modules), 20)
        for module in modules:
            with self.subTest(module=module):
                self.assertIn(".deploy-staging/" + module, remote_check.split())
                self.assertIn(".deploy-staging/" + module, copied.split())
        # A command referencing unprefixed *.py runs against the previous live
        # version, which can fail when that module is new.
        self.assertFalse([x for x in remote_check.split()
                          if x.endswith(".py") and not x.startswith(".deploy-staging/")])
        self.assertFalse([x for x in copied.split()
                          if x.endswith(".py") and not x.startswith(".deploy-staging/")])

    def test_shell_run_blocks_never_escape_yaml_indentation(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        inside = False
        for index, line in enumerate(source.splitlines(), 1):
            if line.startswith("        run: |"):
                inside = True
                continue
            if inside and line.startswith("      - name:"):
                inside = False
            if not inside or not line.strip():
                continue
            self.assertTrue(line.startswith("          "),
                            "Workflow shell escaped YAML block at line %d: %r" % (index, line))

    def test_search_regression_is_actually_discovered(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("-p 'test_fanart_fx_search.py' -v", source)
        self.assertNotIn("test_fanart_fx_search.py fanart_weekly_cleanup.py", source)

    def test_production_result_is_published_as_commit_status(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("statuses: write", source)
        self.assertIn("if: always()", source)
        self.assertIn('context="abyss/production-banner"', source)

    def test_live_banner_card_is_verified_after_restart(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertLess(source.index('sudo systemctl restart chzzk-bot'),
                        source.index("BANNER_INFO_LIVE_VERIFIED"))
        self.assertIn('if status not in ("updated", "up_to_date"):', source)


if __name__ == "__main__":
    unittest.main()
