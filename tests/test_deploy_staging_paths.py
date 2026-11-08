"""Catch mismatched staging paths which silently block production deploys."""
import pathlib
import re
import unittest

DEPLOY = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml"

class WorkflowStagingTests(unittest.TestCase):
    def test_new_runtime_files_compile_from_staging(self):
        source = DEPLOY.read_text(encoding="utf-8")
        self.assertIn(".deploy-staging/chzzk_clip_digest.py", source)
        self.assertIn(".deploy-staging/discord_message_sync.py", source)
        self.assertIn("'test_fanart_vision.py' -v", source)
        self.assertNotIn("'test_fanart_vision.py chzzk_clip_digest.py discord_message_sync.py'", source)

    def test_runtime_modules_are_uploaded_and_deployed(self):
        source = DEPLOY.read_text(encoding="utf-8")
        for name in ("chzzk_clip_digest.py", "discord_message_sync.py"):
            self.assertIn(name, source)
            self.assertIn(".deploy-staging/" + name, source)

if __name__ == "__main__":
    unittest.main()
