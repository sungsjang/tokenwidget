import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timezone

from codex_usage_widget import _account_from_payloads, find_latest_usage, find_latest_windows, format_clock, window_label
from openrouter_credit import remaining_from_payload


class UsageParsingTests(unittest.TestCase):
    def test_reads_newest_rate_limit_record(self):
        with tempfile.TemporaryDirectory() as temp:
            session_dir = Path(temp) / "sessions" / "2026" / "07" / "21"
            session_dir.mkdir(parents=True)
            rows = [
                self._row("2026-07-21T10:00:00Z", 12.0),
                {"timestamp": "2026-07-21T10:01:00Z", "type": "event_msg", "payload": {"type": "other"}},
                self._row("2026-07-21T10:02:00Z", 27.0),
            ]
            path = session_dir / "rollout.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            usage = find_latest_usage(Path(temp))

            self.assertIsNotNone(usage)
            self.assertEqual(usage.remaining_percent, 73)
            self.assertEqual(usage.resets_at, 1786974780)
            self.assertEqual(find_latest_windows(Path(temp)).weekly.remaining_percent, 55)

    def test_window_labels(self):
        self.assertEqual(window_label(43800), "월간 한도")
        self.assertEqual(window_label(10080), "7일 한도")
        self.assertEqual(window_label(300), "5시간 한도")

    def test_parses_reset_credit_details(self):
        usage = {
            "plan_type": "team",
            "rate_limit": {
                "primary_window": {"used_percent": 28, "limit_window_seconds": 18000, "reset_at": 1786974780},
                "secondary_window": {"used_percent": 55, "limit_window_seconds": 604800, "reset_at": 1787406780},
            },
        }
        resets = {
            "available_count": 3,
            "credits": [
                {"status": "available", "title": "Full reset", "expires_at": "2026-07-26T23:46:47Z"},
                {"status": "redeemed", "title": "Old", "expires_at": "2026-07-01T00:00:00Z"},
            ],
        }
        result = _account_from_payloads(usage, resets)
        self.assertEqual(result.usage.remaining_percent, 72)
        self.assertEqual(result.weekly_usage.remaining_percent, 45)
        self.assertEqual(result.reset_count, 3)
        self.assertEqual(len(result.reset_credits), 1)
        self.assertEqual(result.reset_credits[0].title, "Full reset")

    def test_dual_clock_uses_all_three_final_timezones(self):
        moment = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_clock(moment, "Asia/Seoul")[0], "09:00:00")
        self.assertEqual(format_clock(moment, "America/Vancouver")[0], "17:00:00")
        self.assertEqual(format_clock(moment, "Etc/UTC")[0], "00:00:00")

    def test_openrouter_remaining_credit(self):
        self.assertEqual(remaining_from_payload({"data": {"total_credits": 100.5, "total_usage": 25.75}}), 74.75)

    @staticmethod
    def _row(timestamp: str, used: float) -> dict:
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": used,
                        "window_minutes": 300,
                        "resets_at": 1786974780,
                    },
                    "secondary": {
                        "used_percent": 45,
                        "window_minutes": 10080,
                        "resets_at": 1787406780,
                    },
                    "plan_type": "team",
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
