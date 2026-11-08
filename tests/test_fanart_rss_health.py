"""Prevent false fanart freshness when profile RSS contains no newly cached posts."""
import unittest
from unittest.mock import Mock, patch

import fanart_fx_rss as rss


def response(xml):
    item = Mock()
    item.content = xml.encode("utf-8")
    item.raise_for_status.return_value = None
    return item


class ProfileRSSWriteTests(unittest.TestCase):
    def test_empty_feed_does_not_touch_candidate_queue(self):
        xml = "<rss><channel><item><link>https://x.com/a/status/123</link>" \
              "<title>No fanart hashtag</title></item></channel></rss>"
        with patch.object(rss, "read", return_value={}), \
             patch.object(rss, "write") as save:
            result = rss.fetch_group("hanayo", session=Mock(get=Mock(return_value=response(xml))))
        self.assertEqual(result["added"], 0)
        save.assert_not_called()

    def test_new_post_preserves_artist_original_url_and_gallery(self):
        xml = ("<rss><channel><item>"
               "<link>https://x.com/real_artist/status/123456789</link>"
               "<title>히요리 #hiyoriart_</title>"
               "<description><![CDATA["
               "<img src='https://pbs.twimg.com/media/first.jpg' />"
               "<img src='https://pbs.twimg.com/media/second.jpg' />"
               "]]></description></item></channel></rss>")
        with patch.object(rss, "read", return_value={}), \
             patch.object(rss, "write") as save:
            result = rss.fetch_group("hanayo", session=Mock(get=Mock(return_value=response(xml))))
        self.assertEqual(result["added"], 1)
        saved = save.call_args.args[1]
        self.assertEqual(saved["hanayo:123456789"]["artist"], "@real_artist")
        self.assertEqual(saved["hanayo:123456789"]["post_url"],
                         "https://x.com/real_artist/status/123456789")
        self.assertEqual(len(saved["hanayo:123456789"]["image_urls"]), 2)


if __name__ == "__main__":
    unittest.main()
