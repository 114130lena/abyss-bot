"""Prevent guild banner repeats; recycle notification art only after exhaustion."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import guild_server_banner as guild
import fanart_url_media as url_art
import random_media as local
from discord_v2 import alert


def gallery_card():
    return alert("chzzk", "하나요 히요리", "https://example.com/banner.jpg",
                 title="테스트", url="https://chzzk.naver.com/live/123",
                 avatar="https://example.com/avatar.jpg", ping=False)


def guild_art(count):
    return [("image%d.jpg" % i,
             {"sha256": "sha_%d" % i,
              "artist": "@fanartist", "post_url": "https://x.com/fanartist/status/%d" % (i + 101)})
            for i in range(count)]


def notification_posts(count):
    return [{"post_url": "https://x.com/fanartist/status/%d" % (i + 201),
             "artist": "@fanartist",
             "images": ["https://pbs.twimg.com/media/pic%d.jpg" % i]}
            for i in range(count)]


class GuildBannerNoRepeatTests(unittest.TestCase):
    def test_used_images_are_removed_permanently_and_new_art_unlocks_rotation(self):
        pool = guild_art(3)
        with patch.object(guild, "approved_hanayo_art", return_value=pool), \
             patch.object(guild, "collected_hanayo_art", return_value=[]), \
             patch.object(guild.random, "choice", side_effect=lambda items: items[0]):
            state = {}
            picked_order = []
            for remaining_expected in (2, 1, 0):
                picked, history, total, remaining = guild.rotation_selection(
                    state.get("last_sha256"), state.get("rotation_used", []), state=state)
                self.assertIsNotNone(picked)
                sha = picked[1]["sha256"]
                self.assertNotIn(sha, picked_order)
                picked_order.append(sha)
                self.assertEqual(total, 3)
                self.assertEqual(remaining, remaining_expected)
                state = {"rotation_used": history, "all_used_sha256": list(picked_order),
                         "last_sha256": sha}
            exhausted, _, total, remaining = guild.rotation_selection(state=state)
            self.assertIsNone(exhausted)
            self.assertEqual((total, remaining), (3, 0))
            self.assertEqual(guild.banner_inventory(state)["unused"], 0)
            pool.append(("new.jpg", {"sha256": "sha_new", "artist": "@fanartist",
                                     "post_url": "https://x.com/fanartist/status/999"}))
            selected, _, total, remaining = guild.rotation_selection(state=state)
            self.assertEqual(selected[1]["sha256"], "sha_new")
            self.assertEqual((total, remaining), (4, 0))

    def test_old_post_history_blocks_repeated_art_even_after_rotation_reset(self):
        pool = guild_art(2)
        with patch.object(guild, "approved_hanayo_art", return_value=pool), \
             patch.object(guild, "collected_hanayo_art", return_value=[]):
            state = {"rotation_used": [], "all_used_post_ids": ["101"],
                     "last_sha256": None}
            remaining = guild.banner_inventory(state)
            self.assertEqual(remaining["total"], 2)
            self.assertEqual(remaining["unused"], 1)
            self.assertNotIn("sha_0", remaining["available"])
            self.assertIn("sha_1", remaining["available"])

    def test_exhaustion_does_not_patch_discord_or_reset_previous_state(self):
        current = {"rotation_used": ["sha_0", "sha_1"],
                   "all_used_sha256": ["sha_0", "sha_1"],
                   "last_sha256": "sha_1"}
        with patch.object(guild, "approved_hanayo_art", return_value=guild_art(2)), \
             patch.object(guild, "collected_hanayo_art", return_value=[]), \
             patch.object(guild, "read_state", return_value=current), \
             patch.object(guild, "slot", return_value="2026-11-08 18:00"), \
             patch.object(guild, "change_guild_banner") as patch_discord, \
             patch.object(guild, "write_state") as saved:
            result = guild.rotate_once()
        self.assertEqual(result["status"], "exhausted_candidates")
        self.assertEqual(result["remaining"], 0)
        patch_discord.assert_not_called()
        saved.assert_not_called()
        self.assertEqual(current["rotation_used"], ["sha_0", "sha_1"])

    def test_successful_history_keeps_sha_across_later_info_updates(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(guild, "media_base", return_value=Path(folder)):
            guild.write_state({"rotation_used": ["sha_old"], "last_sha256": "sha_old",
                               "post_url": "https://x.com/artist/status/101"})
            guild.write_state({"rotation_used": [], "last_sha256": None,
                               "post_url": ""})
            state = guild.read_state()
            self.assertIn("sha_old", state["all_used_sha256"])
            self.assertIn("101", state["all_used_post_ids"])


class NotificationRecycleTests(unittest.TestCase):
    def test_full_post_shows_only_once_until_every_other_post_is_used(self):
        pool = notification_posts(2)
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(url_art, "HISTORY", Path(folder) / "history.json"), \
             patch.object(url_art, "candidates", return_value=pool):
            self.assertEqual(url_art.availability("hanayo")["unused"], 2)
            first, picked1 = url_art.apply(gallery_card(), group="hanayo")
            self.assertIsNotNone(picked1)
            self.assertIn("원본 게시물", str(first["components"]))
            url_art.mark_delivered(picked1)
            self.assertEqual(url_art.availability("hanayo")["unused"], 1)
            second, picked2 = url_art.apply(gallery_card(), group="hanayo")
            self.assertIsNotNone(picked2)
            self.assertNotEqual(picked2["post_url"], picked1["post_url"])
            url_art.mark_delivered(picked2)
            self.assertEqual(url_art.availability("hanayo")["unused"], 0)
            self.assertTrue(url_art.availability("hanayo")["fallback_only"])
            third, picked3 = url_art.apply(gallery_card(), group="hanayo")
            self.assertIsNotNone(picked3)
            self.assertEqual(picked3["post_url"], picked1["post_url"])

    def test_unavailable_pool_never_falls_back_to_prohibited_images(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(url_art, "HISTORY", Path(folder) / "history.json"), \
             patch.object(url_art, "candidates", return_value=[]):
            self.assertEqual(url_art.availability("hanayo")["total"], 0)
            outgoing, selected = url_art.apply(gallery_card(), group="hanayo")
            self.assertIsNone(selected)


class LocalBannerRecycleTests(unittest.TestCase):
    def test_local_banner_reuses_only_after_all_images_shown(self):
        pool = [(Path("/tmp/fanart_a.jpg"), "shaA"),
                (Path("/tmp/fanart_b.jpg"), "shaB")]
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(local, "MEDIA_LAST", Path(folder) / "last.json"), \
             patch.object(local, "_local_candidates", return_value=pool):
            first = local.choose_local("hanayo", "banner")
            local.mark_delivered("hanayo", {"banner": first})
            second = local.choose_local("hanayo", "banner")
            self.assertNotEqual(first[1], second[1])
            local.mark_delivered("hanayo", {"banner": second})
            third = local.choose_local("hanayo", "banner")
            self.assertEqual(third[1], first[1])


class ClearDiscordCardTests(unittest.TestCase):
    def test_banner_preview_and_compact_three_statistics_sections(self):
        report = {"available": True, "server_banner_images": 2,
                  "server_banner_total": 6,
                  "groups": {"hanayo": {"posts": 201, "cached_images": 240,
                                        "notification_posts": 17},
                             "sakihane": {"posts": 169, "cached_images": 194,
                                          "notification_posts": 12}},
                  "checked_at": "2026-11-08T14:00:00+00:00",
                  "last_new_post_at": "2026-11-08T13:00:00+00:00"}
        card = guild._banner_card("123", "bannerhash", {
            "banner_change_method": "봇 자동 교체",
            "banner_changed_at": "2026-11-08 18:00 KST",
            "artist": "@artist", "post_url": "https://x.com/artist/status/123"
        }, fanart_stats=report)
        parts = card["components"][0]["components"]
        self.assertEqual(parts[0]["type"], 10)
        self.assertEqual(parts[1]["type"], 12)
        text = "\n".join(item.get("content", "") for item in parts)
        for title in ("서버 배너 정보", "현재 적용된 서버 배너", "정기 업데이트",
                      "팬아트 수집 현황", "사용 가능한 후보", "수집 기록"):
            self.assertIn(title, text)
        self.assertIn("서버 배너용 후보", text)
        self.assertIn("히요리 2장 / 전체 6장", text)
        self.assertIn("히요리 17건 · 후야 12건", text)
        self.assertLess(len(parts), 16)
        self.assertEqual(card["flags"], 32768)


if __name__ == "__main__":
    unittest.main()
