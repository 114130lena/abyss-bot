"""Clip selection and no-duplicate delivery tests."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import chzzk_clip_digest as clips

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=KST)

def fake_response(data, status=200):
    res = Mock(status_code=status)
    res.json.return_value = data
    return res

class ClipTests(unittest.TestCase):
    def test_recent_top_views_and_no_wrong_channel(self):
        def item(uid, hours, views, channel=None):
            return {"clipUID": uid, "clipTitle": uid, "ownerChannelId": channel or clips.CHANNEL_ID,
                    "readCount": views, "createdDate": (NOW - timedelta(hours=hours)).isoformat(),
                    "thumbnailImageUrl": "https://example.com/" + uid + ".jpg"}
        items = [item("clip123", 3, 100), item("clip456", 5, 300),
                 item("clip789", 30, 9000), item("clip333", 1, 9999, "wrong")]
        get = Mock(return_value=fake_response({"code": 200, "content": {"data": items}}))
        recent = clips.fetch_recent_clips(NOW, get)
        self.assertEqual([x["id"] for x in recent], ["clip456", "clip123", "clip789"])

    def test_timeout_retries_with_smaller_clip_page(self):
        from requests.exceptions import ReadTimeout
        clips_api={"code": 200, "content": {"data": []}}
        fetch=Mock(side_effect=[ReadTimeout("timed out"), fake_response(clips_api), fake_response(clips_api)])
        self.assertEqual(clips.fetch_recent_clips(NOW, fetch), [])
        self.assertGreaterEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].kwargs["params"]["size"], 30)
        self.assertEqual(fetch.call_args_list[1].kwargs["params"]["size"], 10)

    def test_renders_ten_clips_without_view_counts_or_duration(self):
        items = [{"id": "clip" + str(i).zfill(4), "title": "영상 " + str(i),
                  "url": "https://chzzk.naver.com/clips/clip" + str(i).zfill(4),
                  "thumbnail": "", "views": 10000, "date": NOW.isoformat()} for i in range(12)]
        payload = clips._render(items, {})
        parts = payload["components"][0]["components"]
        text = "\\n".join(p.get("content", "") for p in parts)
        self.assertEqual(text.count("chzzk.naver.com/clips/"), 10)
        self.assertIn("오늘의 히요리 클립", text)
        self.assertNotIn("재생시간", text)
        self.assertNotIn("트로피", text)
        self.assertEqual(payload["components"][0]["type"], 17)

    def test_gallery_contains_ten_distinct_thumbnails(self):
        items = [{"id": "clip" + str(i).zfill(4), "title": "영상 " + str(i),
                  "url": "https://chzzk.naver.com/clips/clip" + str(i).zfill(4),
                  "thumbnail": "https://example.com/" + str(i) + ".jpg",
                  "views": 100 - i, "date": NOW.isoformat()} for i in range(10)]
        payload = clips._render(items, {})
        gallery = [p for p in payload["components"][0]["components"]
                   if p["type"] == 12]
        self.assertEqual(len(gallery), 2)
        self.assertEqual(len(gallery[0]["items"]), 1)
        self.assertEqual(len(gallery[1]["items"]), 9)

    def test_never_creates_message_if_unconfigured(self):
        with patch.dict("os.environ", {"CHZZK_CLIP_CHANNEL_ID": "", "CHZZK_CLIP_MESSAGE_ID": ""}), \
             patch.object(clips.requests, "post") as post, \
             patch.object(clips.requests, "patch") as edit:
            self.assertEqual(clips.refresh_once(NOW)["status"], "not_configured")
            post.assert_not_called()
            edit.assert_not_called()

    def test_uneditable_original_is_not_replaced(self):
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "dummy",
                                       "CHZZK_CLIP_CHANNEL_ID": "100", "CHZZK_CLIP_MESSAGE_ID": "200"}), \
             patch.object(clips, "fetch_recent_clips", return_value=[
                 {"id": "clip1234", "title": "클립", "url": "https://chzzk.naver.com/clips/clip1234",
                  "thumbnail": "", "views": 1, "date": NOW.isoformat()}]), \
             patch.object(clips, "load_json", return_value={}), \
             patch.object(clips, "save_json") as save, \
             patch.object(clips.requests, "post") as post:
            result = clips.refresh_once(NOW, patch=Mock(return_value=fake_response({}, 403)))
        self.assertEqual(result["status"], "original_message_uneditable")
        save.assert_not_called()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
