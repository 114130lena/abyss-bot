"""Offline policy checks for automated fanart and guild-banner health warnings."""
import unittest
from unittest.mock import patch
import health_monitor


class HealthWarningsTests(unittest.TestCase):
    def test_banner_pool_depletion_and_hashtag_outage(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "SERVER_BANNER_MIN_CANDIDATES": "2",
                                       "FANART_FX_SEARCH_ENABLED": "1"}):
            report = {"banner": {"approved_candidates": 0, "collected_candidates": 1},
                      "fanart": {"hashtag_search": {"hanayo": {"status": "unavailable"},
                                                  "sakihane": {"status": "ok"}},
                                 "search_age_hours": 1}}
            warnings = health_monitor.health_warnings(report)
        self.assertIn("server_banner_pool", warnings)
        self.assertIn("fanart_hashtag_search", warnings)

    def test_healthy_pipeline_does_not_warn(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "1",
                                       "FANART_FX_SEARCH_ENABLED": "1"}):
            report = {"banner": {"approved_candidates": 1, "collected_candidates": 3},
                      "fanart": {"hashtag_search": {"hanayo": {"status": "ok"},
                                                  "sakihane": {"status": "ok"}},
                                 "search_age_hours": 0.5}}
            self.assertEqual(health_monitor.health_warnings(report), {})

    def test_disabled_features_do_not_create_false_alarm(self):
        with patch.dict("os.environ", {"SERVER_BANNER_ENABLED": "0",
                                       "FANART_FX_SEARCH_ENABLED": "0"}):
            self.assertEqual(health_monitor.health_warnings({"banner": {}, "fanart": {}}), {})


if __name__ == "__main__":
    unittest.main()
