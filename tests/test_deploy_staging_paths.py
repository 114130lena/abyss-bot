"""운영 배포 경로와 스테이징 목록의 단일화 회귀 검사."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github/workflows/deploy.yml"


class WorkflowStagingTests(unittest.TestCase):
    def test_new_runtime_files_compile_from_staging(self):
        source = DEPLOY.read_text(encoding="utf-8")
        names = (ROOT / "runtime_files.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("chzzk_clip_digest.py", names)
        self.assertIn("discord_message_sync.py", names)
        self.assertIn('staged_py+=(".deploy-staging/\u0024file")', source)
        self.assertIn("'test_fanart_vision.py' -v", source)

    def test_runtime_modules_are_uploaded_and_deployed(self):
        source = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('"\u0024{runtime_files[@]}" runtime_files.txt', source)
        self.assertIn('for file in "\u0024{runtime_files[@]}"; do cp ".deploy-staging/\u0024file" .; done', source)


if __name__ == "__main__":
    unittest.main()
