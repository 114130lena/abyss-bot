"""Offline checks: no Discord token/network access required."""
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
import server_banner_scheduler as scheduler


class ServerBannerTests(unittest.TestCase):
    def test_only_twice_daily_in_korea(self):
        kst = ZoneInfo("Asia/Seoul")
        self.assertEqual(scheduler.slot_key(datetime(2026, 11, 4, 8, 0, tzinfo=kst)),
                         "2026-11-04 08:00")
        self.assertEqual(scheduler.slot_key(datetime(2026, 11, 4, 18, 0, tzinfo=kst)),
                         "2026-11-04 18:00")
        self.assertIsNone(scheduler.slot_key(datetime(2026, 11, 4, 8, 1, tzinfo=kst)))

    def test_ratios(self):
        self.assertEqual(scheduler.ratio_type(1920, 1080), "16:9")
        self.assertEqual(scheduler.ratio_type(1920, 1200), "16:10")
        self.assertIsNone(scheduler.ratio_type(1080, 1080))
        self.assertIsNone(scheduler.ratio_type(1080, 1920))
        self.assertIsNone(scheduler.ratio_type(800, 450))

    def test_16_10_is_preserved_with_blurred_sidebars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hanayo.jpg"
            Image.new("RGB", (1600, 1000), (50, 100, 180)).save(path)
            payload = scheduler.render(path)
            with Image.open(io.BytesIO(payload)) as rendered:
                self.assertEqual(rendered.size, (960, 540))

    def test_only_approved_local_files_are_considered(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new("RGB", (1920, 1080)).save(folder / "valid.png")
            Image.new("RGB", (1080, 1080)).save(folder / "square.png")
            (folder / "random.txt").write_text("not an image")
            found = scheduler.candidates(folder)
            self.assertEqual([x.name for x in found], ["valid.png"])

    def test_same_slot_does_not_repeat(self):
        kst = ZoneInfo("Asia/Seoul")
        now = datetime(2026, 11, 4, 8, 0, tzinfo=kst)
        with patch.object(scheduler, "load_state", return_value={"last_slot": "2026-11-04 08:00"}):
            self.assertFalse(scheduler.run_once(now))


if __name__ == "__main__":
    unittest.main()
