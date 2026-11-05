"""Safety regressions for discovery from public reposted X clips."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import hiyoriclip_collector as collector
from hiyoriclip_discovery import status_links,candidates_from_rss,new_accounts

class DiscoveryTests(unittest.TestCase):
    def test_extract_only_public_x_status_links(self):
        text="https://x.com/new_artist/status/2116841881615446466 and https://evil.test/no/status/2116841881615446466"
        self.assertEqual(list(status_links(text)),[("new_artist","2116841881615446466")])

    def test_repost_feed_can_discover_another_author(self):
        xml='''<rss><channel>
          <item><link>https://x.com/known/status/2116841881615446466</link>
          <description>&lt;p&gt;quoted https://x.com/new_artist/status/2116841881615446467&lt;/p&gt;</description></item>
          </channel></rss>'''
        self.assertEqual(candidates_from_rss(xml,"known",None),
                         [("new_artist","2116841881615446467")])

    def test_only_verified_video_owners_promoted(self):
        catalog={"1":{"author":"@validuser","video_ids":["vid"]},
                 "2":{"author":"@baduser","video_ids":[]},
                 "3":{"author":"@known","video_urls":["https://video.twimg.com/a.mp4"]}}
        self.assertEqual(new_accounts(catalog,["known"]),["validuser"])

    def test_profile_feed_merges_external_links(self):
        xml='''<rss><channel><title>known</title>
         <item><link>https://x.com/known/status/2116841881615446466</link>
         <title>#hiyoriclip_</title>
         <description>https://x.com/another/status/2116841881615446467</description></item>
         </channel></rss>'''
        response=Mock(content=xml.encode())
        response.raise_for_status.return_value=None
        session=Mock()
        session.get.return_value=response
        found=collector.account_feed(session,"known")
        self.assertIn(("known","2116841881615446466"),found)
        self.assertIn(("another","2116841881615446467"),found)

    def test_no_verified_post_no_discovered_account(self):
        self.assertEqual(new_accounts({},["known"]),[])

if __name__=="__main__":
    unittest.main()
