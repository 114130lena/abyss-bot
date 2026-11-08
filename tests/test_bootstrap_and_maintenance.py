"""Catch silent startup regressions in configuration and fanart maintenance."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartupIntegrityTests(unittest.TestCase):
    def test_dotenv_loaded_before_importing_modules_that_cache_settings(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertLess(source.index('load_dotenv(Path(os.getenv("BOT_DATA_DIR"'),
                        source.index("from cafe_notifier import check_cafe"))
        self.assertLess(source.index('load_dotenv(Path(os.getenv("BOT_DATA_DIR"'),
                        source.index("from resilience import load_json"))

    def test_orphan_cleanup_starts_under_worker_supervision(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"FANART_WEEKLY_CLEANUP_ENABLED"', source)
        self.assertIn('args=("팬아트 캐시 정리", cleanup_fanart_cache, 86400)', source)


if __name__ == "__main__":
    unittest.main()
