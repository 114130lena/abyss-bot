"""Conservative duplicate-clip selection and freshness tiebreak regressions."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import chzzk_clip_digest as clips

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def item(uid, title, image, views, minutes=15):
    return {
        "clipUID": uid, "clipTitle": title,
        "ownerChannelId": clips.CHANNEL_ID,
        "readCount": views,
        "createdDate": (NOW - timedelta(minutes=minutes)).isoformat(),
        "thumbnailImageUrl": image,
    }


def response(items):
    result = Mock()
    result.json.return_value = {"code": 200, "content": {"data": items}}
    return result


def query(items):
    get = Mock(side_effect=[response(items), response([])])
    return clips.fetch_recent_clips(NOW, get=get)


class ClipQualityTests(unittest.TestCase):
    def test_same_preview_and_title_keeps_most_viewed(self):
        same = "https://example.com/preview.jpg"
        result = query([
            item("clip0001", " 같은  제목 ", same, 20),
            item("clip0002", "같은 제목", same, 50),
            item("clip0003", "다른 제목", same, 10),
        ])
        self.assertEqual([x["id"] for x in result], ["clip0002", "clip0003"])

    def test_equal_title_with_distinct_previews_is_not_removed(self):
        result = query([
            item("clip0001", "비슷한 장면", "https://example.com/a.jpg", 20),
            item("clip0002", "비슷한 장면", "https://example.com/b.jpg", 30),
        ])
        self.assertEqual(len(result), 2)

    def test_missing_preview_does_not_merge_distinct_clips(self):
        result = query([
            item("clip0001", "방송", "", 20),
            item("clip0002", "방송", "", 30),
        ])
        self.assertEqual(len(result), 2)

    def test_equal_views_rank_newer_first(self):
        result = query([
            item("clip0001", "첫째", "https://example.com/a.jpg", 100, minutes=120),
            item("clip0002", "둘째", "https://example.com/b.jpg", 100, minutes=5),
        ])
        self.assertEqual(result[0]["id"], "clip0002")


if __name__ == "__main__":
    unittest.main()
