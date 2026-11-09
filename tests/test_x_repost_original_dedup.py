"""The actual 2026-11-09 Hanayo RT wrapper and original artist post were duplicates."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import x_repost_delivery_ledger as ledger
import x_repost_notifier as notifier

TEXT = "스텔라이브 소속 하나요 히요리 님의 데뷔 커버곡 유령이 되고 싶어 (ゆうれいになりたい) 일러스트를 담당했습니다.\n\n데뷔 축하드려요~!👻"
WRAPPER = {"id": "2119742014694769083", "url": "https://x.com/HanayoHiyori/status/2119742014694769083",
           "text": "RT @mttpape: " + TEXT,
           "rss_title": "RT @mttpape: " + TEXT, "repost": True}
ORIGINAL = {"id": "2111459758842789889", "url": "https://x.com/mttpape/status/2111459758842789889",
            "text": TEXT + "\n\nThis post is unavailable",
            "rss_title": TEXT, "repost": True}


class CrossFeedDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.p1 = patch.object(ledger, "LEDGER", base / "seen.json")
        self.p2 = patch.object(ledger, "LOCK", base / "seen.lock")
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p2.stop()
        self.p1.stop()
        self.temp.cleanup()

    def test_original_and_wrapper_have_same_fingerprint(self):
        self.assertEqual(ledger.fingerprint("hanayo", WRAPPER),
                         ledger.fingerprint("hanayo", ORIGINAL))
        self.assertTrue(ledger.fingerprint("hanayo", WRAPPER))

    def test_two_distinct_x_ids_suppressed_across_sources_and_restart(self):
        self.assertTrue(ledger.reserve("hanayo", WRAPPER))
        ledger.mark_sent("hanayo", WRAPPER)
        self.assertFalse(ledger.reserve("hanayo", ORIGINAL))
        self.assertEqual(len(json.loads(ledger.LEDGER.read_text())), 2)

    def test_other_artist_or_other_creator_not_suppressed(self):
        self.assertTrue(ledger.reserve("hanayo", WRAPPER))
        other = dict(ORIGINAL, url="https://x.com/another_artist/status/2111459758842789888",
                     id="2111459758842789888")
        self.assertTrue(ledger.reserve("hanayo", other))
        self.assertTrue(ledger.reserve("sakihane", ORIGINAL))

    def test_unverified_short_text_not_collapsed_by_body(self):
        a = {"id": "101", "text": "안녕하세요!", "url": "https://x.com/a/status/101"}
        b = {"id": "102", "text": "안녕하세요!", "url": "https://x.com/a/status/102"}
        self.assertIsNone(ledger.fingerprint("hanayo", a))
        self.assertTrue(ledger.reserve("hanayo", a))
        self.assertTrue(ledger.reserve("hanayo", b))

    def test_uncertain_send_remains_blocked_for_manual_review(self):
        self.assertTrue(ledger.reserve("hanayo", WRAPPER))
        ledger.mark_uncertain("hanayo", WRAPPER, "ReadTimeout")
        self.assertFalse(ledger.reserve("hanayo", ORIGINAL))

    def test_notifier_second_source_never_calls_webhook(self):
        webhook = "https://discord.com/api/webhooks/12345/test"
        with patch.object(notifier, "resolve_media", create=True):
            pass
        with patch("x_post_media.resolve_media", return_value=([], "", False, True)), \
             patch.object(notifier, "get_banner", return_value=""), \
             patch.object(notifier, "avatar", return_value=""), \
             patch.object(notifier, "handle", return_value="@HanayoHiyori"), \
             patch.object(notifier, "alert", return_value={"components": [], "allowed_mentions": {}}), \
             patch.object(notifier, "send_webhook") as deliver:
            result1 = notifier.notify("hanayo", WRAPPER, webhook)
            result2 = notifier.notify("hanayo", ORIGINAL, webhook)
        self.assertEqual(result1["status"], "sent")
        self.assertEqual(result2["status"], "duplicate_suppressed")
        deliver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
