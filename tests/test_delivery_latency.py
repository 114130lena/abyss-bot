"""Offline validation of bounded, body-free Discord transport metrics."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import delivery_latency as metrics


class DeliveryLatencyTests(unittest.TestCase):
    def test_bounded_aggregates_and_private_fields_never_saved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                metrics, "STATE", Path(directory) / "latency.json"):
            for index in range(120):
                metrics.record_transport(16 if index % 2 else 2, 3)
            report = metrics.summary()
            disk = json.loads(metrics.STATE.read_text())
            self.assertEqual(report["recent_success_count"], 100)
            self.assertEqual(len(disk["recent_seconds"]), 100)
            self.assertEqual(report["slow_count"], 50)
            self.assertEqual(report["average_seconds"], 9.0)
            self.assertEqual(disk["last_attachment_count"], 3)
            self.assertEqual(set(disk), {
                "recent_seconds", "recent_success_count", "average_seconds",
                "max_seconds", "last_seconds", "last_attachment_count", "slow_count"})
            self.assertNotIn("webhook", metrics.STATE.read_text())

    def test_not_yet_collected_is_unverified_not_healthy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                metrics, "STATE", Path(directory) / "absent.json"):
            self.assertEqual(metrics.summary(), {"status": "unverified"})

    def test_corrupt_metrics_are_rebuilt_without_touching_delivery(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                metrics, "STATE", Path(directory) / "latency.json"):
            metrics.STATE.write_text("{not-valid", encoding="utf-8")
            report = metrics.record_transport(1.234, 0)
            self.assertEqual(report["recent_success_count"], 1)
            self.assertEqual(report["last_seconds"], 1.234)


if __name__ == "__main__":
    unittest.main()
