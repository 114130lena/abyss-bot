"""Free fanart providers cannot enable paid official searches."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fanart_refresh
import fanart_discovery


class FreeFanartRefreshTests(unittest.TestCase):
    def test_no_paid_requests_even_when_old_flag_is_on(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {
                 "FANART_USE_X_API": "1", "X_BEARER_TOKEN": "dummy",
                 "FANART_FREE_INDEX_SEARCH_ENABLED": "1",
                 "FANART_ARTIST_WATCH_ENABLED": "1",
                 "FANART_CACHE_CANDIDATES": "0"}), \
             patch("fanart_fx_rss.run", return_value={
                 "hanayo": {"added": 0}, "sakihane": {"added": 0}}), \
             patch("fanart_fx_search.discover", return_value={
                 "status": "partial", "added": 0,
                 "groups": {"hanayo": {"status": "unavailable"},
                            "sakihane": {"status": "unavailable"}}}), \
             patch("fanart_free_search.discover",
                   return_value={"status": "partial_index_only"}) as free, \
             patch("fanart_artist_watch.discover", return_value={"added": 0}), \
             patch("fanart_global_sources.update") as update, \
             patch("fanart_discovery.discover") as paid, \
             patch.object(fanart_discovery, "BASE", Path(folder)):
            fanart_refresh.check_fanart()
        free.assert_called_once()
        paid.assert_not_called()
        self.assertTrue(any(call.args == ("official_x", {"status": "disabled"})
                            for call in update.call_args_list))


if __name__ == "__main__":
    unittest.main()
