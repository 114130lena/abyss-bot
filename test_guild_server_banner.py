"""Offline tests for isolated Hanayo guild banner feature."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
import guild_server_banner as mod


class BannerTests(unittest.TestCase):
    def test_schedule(self):
        zone = ZoneInfo("Asia/Seoul")
        self.assertEqual(mod.slot(datetime(2026, 11, 4, 8, 0, tzinfo=zone)), "2026-11-04 08:00")
        self.assertEqual(mod.slot(datetime(2026, 11, 4, 18, 0, tzinfo=zone)), "2026-11-04 18:00")
        self.assertIsNone(mod.slot(datetime(2026, 11, 4, 17, 0, tzinfo=zone)))

    def test_ratio(self):
        self.assertEqual(mod.kind(1920, 1080), "16:9")
        self.assertEqual(mod.kind(1920, 1200), "16:10")
        self.assertIsNone(mod.kind(1080, 1080))

    def test_index_filters_other_groups_and_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "creator_media"
            root = base / "fanart" / "hanayo"
            root.mkdir(parents=True)
            good = root / "a.jpg"
            Image.new("RGB", (1920, 1200), "white").save(good)
            digest = hashlib.sha256(good.read_bytes()).hexdigest()
            approved = {"approved": True, "permission_confirmed": True, "hashtag": "#hiyoriart_",
                        "sha256": digest, "original_path": "fanart/hanayo/a.jpg",
                        "artist": "@artist", "post_url": "https://x.com/artist/status/123"}
            data = {"hanayo": [approved, {**approved, "approved": False},
                               {**approved, "original_path": "../other/a.jpg"}],
                    "sakihane": [approved]}
            (base / "fanart_index.json").write_text(json.dumps(data), encoding="utf-8")
            with patch.object(mod, "media_base", return_value=base):
                chosen = mod.approved_hanayo_art()
                self.assertEqual(len(chosen), 1)
                self.assertEqual(chosen[0][0], good.resolve())
                self.assertEqual(Image.open(__import__("io").BytesIO(mod.convert_banner(good))).size,
                                 (960, 540))

    def test_unapproved_collected_cache_is_never_eligible(self):
        with patch.dict("os.environ", {"SERVER_BANNER_USE_COLLECTED": "1"}):
            self.assertEqual(mod.collected_hanayo_art(), [])

    def test_group_collab_repost_never_enter_automatic_hiyori_banner_pool(self):
        from fanart_relevance import eligible_hanayo_banner
        self.assertFalse(eligible_hanayo_banner({
            "text": "RT by @ROFAN_reader_: #toart_ #Rangart_ #hiyoriart_ #maryart_ 어비스 첫 오리지널곡 발매 축하해!"
        }))
        self.assertTrue(eligible_hanayo_banner({"text": "하나요 히요리 팬아트 #hiyoriart_"}))

    def test_rotation_uses_each_candidate_once_and_stops_on_exhaustion(self):
        pool = [(Path(f"/tmp/{c}.jpg"), {"sha256": c}) for c in ("a", "b", "c")]
        history = []
        previous = None
        picks = []
        with patch.object(mod, "approved_hanayo_art", return_value=pool), \
             patch.object(mod, "collected_hanayo_art", return_value=[]):
            for _ in range(3):
                chosen, history, total, remaining = mod.rotation_selection(previous, history)
                previous = chosen[1]["sha256"]
                picks.append(previous)
                self.assertEqual(total, 3)
            self.assertEqual(set(picks), {"a", "b", "c"})
            self.assertEqual(remaining, 0)
            next_one, new_history, _, left = mod.rotation_selection(previous, history)
            self.assertIsNone(next_one)
            self.assertEqual(set(new_history), {"a", "b", "c"})
            self.assertEqual(left, 0)

    def test_rotation_expands_when_new_image_arrives(self):
        pool = [(Path(f"/tmp/{c}.jpg"), {"sha256": c}) for c in ("a", "b", "c")]
        with patch.object(mod, "approved_hanayo_art", return_value=pool), \
             patch.object(mod, "collected_hanayo_art", return_value=[]):
            selected, used, total, remaining = mod.rotation_selection("b", ["a", "b"])
            self.assertEqual(selected[1]["sha256"], "c")
            self.assertEqual(total, 3)
            self.assertEqual(remaining, 0)

    def test_portrait_is_optional_and_preserved_without_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "portrait.jpg"
            Image.new("RGB", (1080, 1920), "white").save(source)
            self.assertIsNone(mod.kind(1080, 1920))
            with patch.dict("os.environ", {"SERVER_BANNER_ALLOW_FIT": "1"}):
                self.assertEqual(mod.kind(1080, 1920), "fit")
                result = mod.convert_banner(source)
                with Image.open(__import__("io").BytesIO(result)) as final:
                    self.assertEqual(final.size, (960, 540))

    def test_dry_run_never_calls_discord(self):
        with patch.object(mod, "select_art", return_value=None), \
             patch.object(mod, "change_guild_banner") as api:
            self.assertEqual(mod.rotate_once(manual=True, dry_run=True)["status"],
                             "no_approved_hanayo_art")
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
