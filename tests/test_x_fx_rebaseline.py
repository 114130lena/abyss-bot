"""Fx feed re-baselines stale snapshots instead of replaying historic retweets."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import x_fx_feed

def feed(ids):
    content = "".join(
        "<item><link>https://x.com/example_artist/status/" + str(x) +
        "</link><title>Old original artwork</title><description>Artwork</description></item>"
        for x in ids)
    return ('<rss><channel><title>@HanayoHiyori</title>' + content +
            '</channel></rss>').encode("utf-8")


class FxRepostSnapshotTests(unittest.TestCase):
    def _fetch(self, path, ids):
        response=Mock()
        response.content=feed(ids)
        response.raise_for_status.return_value=None
        common=Mock()
        common._post_body.return_value="Artwork from original poster"
        common._post_images.return_value=[]
        with patch.object(x_fx_feed,"_state_path",return_value=path), \
             patch.object(x_fx_feed.requests,"get",return_value=response):
            return x_fx_feed.fetch_creator_originals("HanayoHiyori",common)

    def test_stale_baseline_suppresses_days_old_fx_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"timeline.json"
            path.write_text(json.dumps(["1111"]))
            old=time.time()-8*3600
            os.utime(path,(old,old))
            results=self._fetch(path,["2111459758842789889"])
            self.assertEqual(results,[])
            self.assertEqual(json.loads(path.read_text()),["2111459758842789889"])

    def test_fresh_snapshot_emits_new_but_does_not_mark_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"timeline.json"
            path.write_text(json.dumps(["2111459758842789889"]))
            result=self._fetch(path,["2111459758842789889","2119742014694769083"])
            self.assertEqual([p["id"] for p in result],["2119742014694769083"])
            self.assertEqual(json.loads(path.read_text()),["2111459758842789889"])
            # If the notifier's Discord send fails, Fx still supplies the ID
            # for safe reconciliation rather than discarding it forever.
            repeated=self._fetch(path,["2111459758842789889","2119742014694769083"])
            self.assertEqual([p["id"] for p in repeated],["2119742014694769083"])


if __name__=="__main__":
    unittest.main()
