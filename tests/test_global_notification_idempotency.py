"""Regression tests for exact-event idempotency across all Discord workers."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import requests

import discord_event_dedup as dedup
import resilience
from x_post_classification import classify


class EventLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.p = (patch.object(dedup, "LEDGER", root / "events.json"),
                  patch.object(dedup, "LOCK", root / "events.lock"))
        for obj in self.p:
            obj.start()

    def tearDown(self):
        for obj in reversed(self.p):
            obj.stop()
        self.temp.cleanup()

    def test_exact_event_across_two_sources_only_sends_once(self):
        payload = {"flags": 32768, "components": [],
                   "_abyss_event_key": "youtube:UCsomechannel123:video123"}
        with patch.object(resilience, "_send_webhook_transport") as delivery:
            resilience.send_webhook("https://discord.com/api/webhooks/123/secret", payload)
            resilience.send_webhook("https://discord.com/api/webhooks/123/secret", dict(payload))
        delivery.assert_called_once()
        self.assertNotIn("_abyss_event_key", delivery.call_args.args[1])
        self.assertEqual(next(iter(json.loads(dedup.LEDGER.read_text()).values()))["status"],"sent")

    def test_distinct_platform_and_creator_events_are_independent(self):
        keys = ["youtube:hiyori:vid1", "youtube:huya:vid1",
                "cafe:431:vid1", "x-original:hanayohiyori:vid1",
                "x-space:hanayo:vid1:scheduled", "x-space:hanayo:vid1:live"]
        self.assertTrue(all(dedup.claim(key) for key in keys))
        self.assertTrue(all(not dedup.claim(key) for key in keys))

    def test_ambiguous_error_never_retries_on_next_poll(self):
        payload={"_abyss_event_key":"cafe:431:123"}
        with patch.object(resilience,"_send_webhook_transport",side_effect=TimeoutError("response dropped")) as send:
            with self.assertRaises(TimeoutError):
                resilience.send_webhook("https://discord.com/api/webhooks/1/secret",payload)
            resilience.send_webhook("https://discord.com/api/webhooks/1/secret",payload)
        send.assert_called_once()
        state=json.loads(dedup.LEDGER.read_text())
        self.assertEqual(state["cafe:431:123"]["status"],"uncertain")

    def test_http_5xx_is_uncertain_and_never_double_posts(self):
        response = Mock(status_code=502)
        response.raise_for_status.side_effect = requests.HTTPError("Bad Gateway")
        payload = {"_abyss_event_key": "cafe:431:502", "components": []}
        with patch.object(resilience.requests, "post", return_value=response) as post, \
             patch.object(resilience, "_collect_x_image_attachments", return_value=[]), \
             patch.object(resilience, "_collect_x_video_attachments", return_value=[]), \
             patch("delivery_review.record") as review:
            with self.assertRaises(requests.HTTPError):
                resilience.send_webhook("https://discord.com/api/webhooks/1/secret", payload)
            self.assertIsNone(resilience.send_webhook(
                "https://discord.com/api/webhooks/1/secret", payload))
        post.assert_called_once()
        review.assert_called_once()
        state = json.loads(dedup.LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(state["cafe:431:502"]["status"], "uncertain")

    def test_http_429_is_still_retryable(self):
        limited = Mock(status_code=429)
        limited.json.return_value = {"retry_after": 0}
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"id": "123", "mention_roles": []}
        with patch.object(resilience.requests, "post", side_effect=[limited, accepted]) as post, \
             patch.object(resilience, "_collect_x_image_attachments", return_value=[]), \
             patch.object(resilience, "_collect_x_video_attachments", return_value=[]), \
             patch.object(resilience.time, "sleep"):
            resilience._send_webhook_transport(
                "https://discord.com/api/webhooks/1/secret", {"components": []})
        self.assertEqual(post.call_count, 2)

    def test_corrupt_history_fails_closed(self):
        dedup.LEDGER.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            dedup.claim("youtube:hanayo:123")
        dedup.LEDGER.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            dedup.claim("youtube:hanayo:123")

    def test_atomic_parallel_claim(self):
        key="chzzk-live:hanayo:abcd123"
        outcomes=[]
        def run():
            outcomes.append(dedup.claim(key))
        workers=[threading.Thread(target=run) for _ in range(12)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertEqual(outcomes.count(True),1)
        self.assertEqual(outcomes.count(False),11)


class XClassificationTests(unittest.TestCase):
    def test_own_original(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/100",
                                   "rss_title":"오늘의 공지"}, "HanayoHiyori"),"original")

    def test_explicit_wrapper_is_repost(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/101",
                          "rss_title":"RT @mttpape: 스텔라이브 일러스트"},
                          "HanayoHiyori"),"repost")

    def test_off_author_fx_original_url_is_repost(self):
        self.assertEqual(classify({"url":"https://x.com/mttpape/status/2111459758842789889",
                          "rss_title":"스텔라이브 일러스트"},
                          "HanayoHiyori"),"repost")

    def test_nitter_original_author_url_is_repost(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/101",
                        "source_link":"https://nitter.mie.foundation/mttpape/status/100"},
                         "HanayoHiyori"),"repost")

    def test_reply_excluded(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/101",
                          "rss_title":"R to @someone: 답글"},
                          "HanayoHiyori"),"reply")

    def test_short_random_text_does_not_create_fake_repost(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/102",
                          "text":"RT @artist text is quoted below, not a retweet",
                          "rss_title":"공지"},
                          "HanayoHiyori"),"original")

    def test_untrusted_redirect_cannot_make_repost(self):
        self.assertEqual(classify({"url":"https://x.com/HanayoHiyori/status/102",
                          "source_link":"https://evil.example/mttpape/status/100"},
                          "HanayoHiyori"),"original")


if __name__ == "__main__":
    unittest.main()
