"""Prevent historical first-ever X posts from being mistaken for new alerts."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import twitter_notifier as hi
import twitter_huya_notifier as huya

NOW = datetime(2026, 11, 11, 1, 30, tzinfo=timezone.utc)
EPOCH_MS = 1288834974657


def snowflake(when):
    return str(((int(when.timestamp() * 1000) - EPOCH_MS) << 22) + 1)


def original(username, when, pubdate=None):
    value = {
        "id": snowflake(when), "text": "First ever old post",
        "url": "https://x.com/" + username + "/status/" + snowflake(when),
        "rss_title": "First ever old post",
        "source_link": "https://nitter.gq/" + username + "/status/" + snowflake(when),
        "source": "https://nitter.gq/" + username + "/rss", "images": []
    }
    if pubdate is not None:
        value["pub_date"] = format_datetime(pubdate)
    return value


class HistoricalOriginalTests(unittest.TestCase):
    def test_recovered_feed_rejects_old_first_post(self):
        old = original("HanayoHiyori", NOW - timedelta(days=90),
                       NOW - timedelta(days=90))
        self.assertTrue(hi._historical_original(old, now=NOW))

    def test_misleading_recent_rss_date_cannot_revive_old_status(self):
        old = original("HanayoHiyori", NOW - timedelta(days=120), NOW)
        self.assertTrue(hi._historical_original(old, now=NOW))

    def test_missing_pubdate_uses_x_id_timestamp(self):
        old = original("SakihaneHuya", NOW - timedelta(days=12))
        self.assertTrue(hi._historical_original(old, now=NOW))

    def test_recent_originals_still_allowed(self):
        fresh = original("HanayoHiyori", NOW - timedelta(minutes=40),
                         NOW - timedelta(minutes=40))
        self.assertFalse(hi._historical_original(fresh, now=NOW))

    def test_missing_timestamp_small_fixture_does_not_block(self):
        self.assertFalse(hi._historical_original({"id": "123"}, now=NOW))

    def test_old_originals_never_sent_in_either_account(self):
        for watcher in (hi, huya):
            with self.subTest(watcher=watcher.__name__), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                username = watcher.USERNAME
                old = original(username, NOW - timedelta(days=30),
                               NOW - timedelta(days=30))
                state_file = root / ("x_seen_posts.json" if watcher is hi else "x_huya_seen_posts.json")
                repost_file = root / ("x_repost_seen_posts.json" if watcher is hi else "x_huya_repost_seen_posts.json")
                state_file.write_text("[]", encoding="utf-8")
                repost_file.write_text("[]", encoding="utf-8")
                def get_posts(*args, **kwargs):
                    kwargs["on_batch"]([old])
                    return [old]
                method = "check_twitter" if watcher is hi else "check_huya_x"
                with patch.object(watcher, "BASE_DIR", root), \
                     patch.object(watcher, "SEEN_FILE", state_file), \
                     patch.object(watcher, "next_check", 0.0), \
                     patch.object(watcher, "WEBHOOK_URL", "https://discord.com/api/webhooks/111/token"), \
                     patch.object(watcher, "_fetch_merged_posts", side_effect=get_posts), \
                     patch.object(watcher, "_notify") as notify, \
                     patch.object(hi, "_historical_original", return_value=True), \
                     patch("notice_intelligence.report_source"):
                    getattr(watcher, method)()
                notify.assert_not_called()
                self.assertEqual(json.loads(state_file.read_text()), [old["id"]])

    def test_old_repost_source_not_blocked_by_original_freshness_rule(self):
        # A new creator repost may legitimately reference a ten-year-old tweet.
        # Historical filtering runs only in the ORIGINAL delivery lane.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "x_repost_seen_posts.json").write_text("[]", encoding="utf-8")
            post = original("HanayoHiyori", NOW - timedelta(days=180),
                            NOW - timedelta(days=180))
            post.update({"repost": True, "rss_title": "RT @artist: old artwork",
                         "source_link": "https://x.com/artist/status/" + post["id"]})
            def get_posts(_include_backups=True, on_batch=None):
                on_batch([post])
                return [post]
            with patch.object(hi, "BASE_DIR", root), \
                 patch.object(hi, "next_check", 0.0), \
                 patch.object(hi, "WEBHOOK_URL", "https://discord.com/api/webhooks/123/token"), \
                 patch.object(hi, "_load_seen", return_value=set()), \
                 patch.object(hi, "_fetch_merged_posts", side_effect=get_posts), \
                 patch("x_repost_notifier.notify") as repost, \
                 patch.object(hi, "_notify") as original_send, \
                 patch("notice_intelligence.report_source"):
                hi.check_twitter()
            repost.assert_called_once()
            original_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
