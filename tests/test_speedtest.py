import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import speedtest


class SpeedtestTests(unittest.TestCase):
    def test_midnight_is_beijing_time_not_host_timezone(self):
        midnight = datetime(2026, 10, 15, 16, 0, tzinfo=UTC).timestamp()
        self.assertEqual(speedtest.due_day(midnight), "2026-10-16")
        self.assertEqual(speedtest.due_day(midnight + 299), "2026-10-16")
        self.assertIsNone(speedtest.due_day(midnight + 300))
        self.assertIsNone(speedtest.due_day(midnight + 36000))

    def test_measurement_converts_bytes_per_second_and_omits_private_fields(self):
        raw = json.dumps(
            {
                "download": {"bandwidth": 12500000},
                "upload": {"bandwidth": 2500000},
                "ping": {"latency": 10.2},
                "interface": {"externalIp": "private"},
                "server": {"name": "test", "ip": "private"},
            }
        )
        result = speedtest.measurement(raw)
        self.assertEqual(result["download_bps"], 100000000)
        self.assertEqual(result["upload_bps"], 20000000)
        self.assertNotIn("private", json.dumps(result))

    def test_invalid_measurement_rejected(self):
        with self.assertRaises(ValueError):
            speedtest.measurement(
                json.dumps(
                    {
                        "download": {"bandwidth": -1},
                        "upload": {"bandwidth": 1},
                        "ping": {"latency": 0},
                    }
                )
            )
