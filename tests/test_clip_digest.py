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
        self.assertEqual([x["id"] for x in recent], ["clip456", "clip123"])

    def test_refresh_schedule_every_two_hours(self):
        self.assertTrue(clips.clip_refresh_due(NOW, None))
        self.assertFalse(clips.clip_refresh_due(NOW, "2026-11-08 18"))
        self.assertFalse(clips.clip_refresh_due(NOW.replace(hour=19), None))
        self.assertTrue(clips.clip_refresh_due(NOW.replace(hour=20), "2026-11-08 18"))

    def test_publish_slots_are_only_08_and_18(self):
        for hour in (8, 18):
            now = NOW.replace(hour=hour, minute=27)
            self.assertTrue(clips.clip_publish_due(now, None))
            self.assertFalse(clips.clip_publish_due(now, now.strftime("%Y-%m-%d %H")))
        for hour in (0, 2, 6, 10, 12, 16, 20, 22):
            self.assertFalse(clips.clip_publish_due(NOW.replace(hour=hour), None))

    def test_collect_preserves_discord_history_and_never_edits(self):
        clip = {"id": "clip1234", "title": "clip", "url": "https://chzzk.naver.com/clips/clip1234",
                "thumbnail": "", "views": 4, "date": NOW.isoformat()}
        state = {"history": {"2026-11-07": {"id": "old"}}, "last_published_slot": "2026-11-08 08"}
        with patch.object(clips, "fetch_recent_clips", return_value=[clip]), \
             patch.object(clips, "load_json", return_value=state), \
             patch.object(clips, "save_json") as save, \
             patch("discord_message_sync.update_original") as edit:
            result = clips.collect_once(NOW)
        self.assertEqual(result["status"], "collected")
        saved = save.call_args.args[1]
        self.assertEqual(saved["last_published_slot"], "2026-11-08 08")
        self.assertEqual(saved["clips"][0]["id"], "clip1234")
        edit.assert_not_called()

    def test_publish_cached_clips_without_recollecting(self):
        clip = {"id": "clip1234", "title": "clip", "url": "https://chzzk.naver.com/clips/clip1234",
                "thumbnail": "", "views": 4, "date": NOW.isoformat()}
        state = {"clips": [clip], "collected_at": NOW.isoformat(), "history": {}}
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "dummy",
                                      "CHZZK_CLIP_CHANNEL_ID": "100", "CHZZK_CLIP_MESSAGE_ID": "200"}), \
             patch.object(clips, "load_json", return_value=state), \
             patch.object(clips, "fetch_recent_clips") as fetch, \
             patch.object(clips, "clip_video_url", return_value=""), \
             patch("discord_message_sync.update_original", return_value="updated") as edit, \
             patch.object(clips, "save_json") as save:
            result = clips.publish_once(NOW)
        self.assertEqual(result["status"], "updated")
        fetch.assert_not_called()
        edit.assert_called_once()
        self.assertEqual(save.call_args.args[1]["last_published_slot"], "2026-11-08 18")

    def test_view_ranking_scans_later_pages_even_when_first_has_ten(self):
        def item(uid, views):
            return {"clipUID": uid, "clipTitle": uid, "ownerChannelId": clips.CHANNEL_ID,
                    "readCount": views, "createdDate": NOW.isoformat(),
                    "thumbnailImageUrl": "https://example.com/" + uid + ".jpg"}
        first = [item("first" + str(i), i) for i in range(10)]
        second = [item("winner789", 1000)]
        get = Mock(side_effect=[
            fake_response({"code": 200, "content": {"data": first}}),
            fake_response({"code": 200, "content": {"data": second}}),
            fake_response({"code": 200, "content": {"data": []}}),
        ])
        self.assertEqual(clips.fetch_recent_clips(NOW, get=get)[0]["id"], "winner789")
        self.assertEqual(get.call_count, 3)

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
        self.assertEqual(len(payload["components"]), 1)
        self.assertEqual(len(gallery), 1)
        self.assertEqual(len(gallery[0]["items"]), 10)
        self.assertEqual([p["media"]["url"] for p in gallery[0]["items"]],
                         ["https://example.com/" + str(i) + ".jpg" for i in range(10)])
        self.assertEqual([p["description"].split(".")[0]
                          for p in gallery[0]["items"]],
                         [str(i).zfill(2) for i in range(1, 11)])
        self.assertLessEqual(len(payload["components"][0]["components"]), 10)

    def test_grid_title_labels_match_media_by_number_without_long_rows(self):
        items = [{"id": "clip" + str(i).zfill(4),
                  "title": "제목 " + str(i + 1),
                  "url": "https://chzzk.naver.com/clips/clip" + str(i).zfill(4),
                  "thumbnail": "https://example.com/" + str(i + 1) + ".jpg",
                  "views": 100 - i, "date": NOW.isoformat()}
                 for i in range(10)]
        payload = clips._render(items, {"2026-11-08": {"id": "clipold123"}})
        parts = payload["components"][0]["components"]
        self.assertEqual([p["type"] for p in parts], [10, 12, 10, 14, 10])
        self.assertIn("## 오늘의 히요리 클립", parts[0]["content"])
        self.assertIn("**수집·갱신 현황**", parts[0]["content"])
        self.assertIn("**정기 업데이트**", parts[0]["content"])
        self.assertEqual(len(parts[1]["items"]), 10)
        self.assertEqual(len(payload["components"]), 1)
        legend = parts[2]["content"]
        self.assertEqual(legend.count("\n"), 11)
        self.assertNotIn("  ·  ", legend)
        self.assertIn("**05** [제목 5]", legend.split("\n\n")[0])
        self.assertTrue(legend.split("\n\n")[1].startswith("**06** [제목 6]"))
        self.assertIn("**10** [제목 10]", legend.split("\n\n")[1])
        for index, item in enumerate(items, 1):
            self.assertIn("**%02d** [제목 %s]" % (index, index), legend)
            self.assertIn(item["url"], legend)
            self.assertIn("제목 %s" % index, parts[1]["items"][index - 1]["description"])
        self.assertIn("지난 대표 클립", parts[-1]["content"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_missing_thumbnail_retains_clickable_title_without_blank_grid_tile(self):
        items = [
            {"id": "clip1234", "title": "미리보기 없음",
             "url": "https://chzzk.naver.com/clips/clip1234", "thumbnail": ""},
            {"id": "clip5678", "title": "정상 영상",
             "url": "https://chzzk.naver.com/clips/clip5678",
             "thumbnail": "https://example.com/clip5678.jpg"},
        ]
        payload = clips._render(items, {})
        parts = payload["components"][0]["components"]
        self.assertEqual(len(parts[1]["items"]), 1)
        self.assertTrue(parts[1]["items"][0]["description"].startswith("02."))
        self.assertIn("**01** [미리보기 없음]", parts[2]["content"])
        self.assertIn("**02** [정상 영상]", parts[2]["content"])

    def test_inline_video_media_url_overrides_thumbnail(self):
        clip = {"id": "clip1234", "title": "영상",
                "url": "https://chzzk.naver.com/clips/clip1234",
                "thumbnail": "https://example.com/image.jpg",
                "video_url": "https://example.com/clip.mp4",
                "views": 1, "date": NOW.isoformat()}
        payload = clips._render([clip], {})
        gallery = next(p for p in payload["components"][0]["components"]
                       if p["type"] == 12)
        self.assertEqual(gallery["items"][0]["media"]["url"], "https://example.com/clip.mp4")

    def test_playback_resolution_from_videohub(self):
        detail = fake_response({"code": 200, "content": {
            "videoId": "A" * 32, "vodStatus": "ABR_HLS"}})
        playback_data = {"card": {"content": {"vod": {
            "playable": True,
            "playback": {"MPD": [{"Period": [{"AdaptationSet": [
                {"Representation": [{"BaseURL": [
                    "https://video.pstatic.net/path/video.mp4?token=test"
                ]}]}
            ]}]}]}
        }}}}
        playback = fake_response(playback_data)
        get = Mock(side_effect=[detail, playback])
        self.assertEqual(clips.clip_video_url({"id": "clip1234"}, get=get),
                         "https://video.pstatic.net/path/video.mp4?token=test")
        self.assertEqual(get.call_count, 2)

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
        self.assertTrue(save.called)  # Offline snapshot survives even when the Discord edit fails.
        self.assertNotIn("last_updated", save.call_args.args[1] if len(save.call_args.args)>1 else {})
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
