"""Live alerts must not scan/decode/hash 200+ server banner files."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import fanart_usage


def art(n, group="hanayo"):
    return {"group":group,"hashtag":"#hiyoriart_" if group=="hanayo" else "#huyart",
            "text":"하나요 히요리 팬아트 그림" if group=="hanayo" else "후야 팬아트 그림",
            "artist":"@artist",
            "post_url":f"https://x.com/artist/status/{n}",
            "cached_files":[f"fanart_candidates/{group}/{n}.png"]}


class BannerReservationFastPathTests(unittest.TestCase):
    def test_never_calls_pil_hashing_or_heavy_banner_selection(self):
        queue={str(i):art(i) for i in range(100,325)}
        with patch.object(fanart_usage, "read", side_effect=lambda p:
                          queue if p.name=="fanart_discovery.json" else {}), \
             patch("guild_server_banner.collected_hanayo_art",
                   side_effect=AssertionError("full image scan forbidden")), \
             patch("guild_server_banner.approved_hanayo_art",
                   side_effect=AssertionError("full image hash forbidden")), \
             patch("fanart_relevance.eligible_hanayo_banner", return_value=True), \
             patch("fanart_vision.enabled", return_value=False):
            started=time.monotonic()
            reserved=fanart_usage.server_reserved_post_ids("hanayo")
            elapsed=time.monotonic()-started
        self.assertEqual(len(reserved),225)
        self.assertLess(elapsed,2.0)

    def test_conservatively_reserves_even_missing_art_file(self):
        # An unreadable image may later be repaired and become a guild banner;
        # proactively keep its post out of notification footer rotation.
        queue={"a":art(123), "b":dict(art(456),review_status="blocked"),
               "c":dict(art(789),cached_files=[])}
        with patch.object(fanart_usage,"read",side_effect=lambda p:
                          queue if p.name=="fanart_discovery.json" else {}), \
             patch("fanart_relevance.eligible_hanayo_banner",return_value=True), \
             patch("fanart_vision.enabled",return_value=False):
            self.assertEqual(fanart_usage.server_reserved_post_ids("hanayo"),{"123"})

    def test_reserves_approved_art_without_reading_image_bytes(self):
        library={"hanayo":[{"approved":True,"permission_confirmed":True,
              "hashtag":"#hiyoriart_","sha256":"testhash",
              "original_path":"fanart/hanayo/original.png",
              "post_url":"https://x.com/artist/status/903"}]}
        with patch.object(fanart_usage,"read",side_effect=lambda p:
                          library if p.name=="fanart_index.json" else {}):
            self.assertIn("903",fanart_usage.server_reserved_post_ids("hanayo"))

    def test_keeps_huya_banner_reserved_separately(self):
        queue={"h":art(505,"sakihane"),"i":art(506)}
        with patch.object(fanart_usage,"read",side_effect=lambda p:
                          queue if p.name=="fanart_discovery.json" else {}), \
             patch("fanart_relevance.eligible_huya_banner",return_value=True):
            self.assertEqual(fanart_usage.server_reserved_post_ids("sakihane"),{"505"})


if __name__=="__main__":
    unittest.main()
