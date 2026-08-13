from datetime import datetime, timezone
import unittest

from luxalgo_structure import luxalgo_market_structure
from api import (
    scanner_action_v6,
    scanner_v6,
    snapshot_action_v6,
    snapshot_v6,
)


def candle(index, open_price, high, low, close):
    timestamp = 1_700_000_000 + index * 60
    return {
        "time": timestamp,
        "time_utc": datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


class LuxAlgoStructureTests(unittest.TestCase):
    def test_internal_bos_then_choch_uses_close_breaks(self):
        candles = []

        # Establish alternating five-bar legs and an internal pivot high/low.
        prices = [
            (10.0, 10.2, 8.0, 10.0),
            (10.0, 11.2, 9.9, 11.0),
            (11.0, 10.9, 10.4, 10.6),
            (10.6, 10.8, 10.2, 10.4),
            (10.4, 10.7, 10.1, 10.3),
            (10.3, 10.6, 10.0, 10.2),
            (10.2, 10.5, 9.9, 10.1),
            (10.1, 10.4, 8.8, 9.0),
            (9.0, 9.6, 9.0, 9.4),
            (9.4, 9.8, 9.2, 9.6),
            (9.6, 10.0, 9.4, 9.8),
            (9.8, 10.2, 9.6, 10.0),
            (10.0, 10.4, 9.8, 10.2),
            # A wick through 11.2 must not create a structure break.
            (10.2, 11.4, 10.1, 11.1),
            (11.1, 11.5, 10.8, 11.3),
            (11.0, 11.2, 10.4, 10.6),
            (10.6, 10.8, 9.7, 9.9),
            (9.9, 10.0, 8.6, 8.7),
        ]
        for index, values in enumerate(prices):
            candles.append(candle(index, *values))

        result = luxalgo_market_structure(
            candles,
            internal_length=5,
            swing_length=50,
        )
        events = result["internal"]["recent_events"]

        self.assertEqual(events[-2]["event_type"], "BOS")
        self.assertEqual(events[-2]["direction"], "BULLISH")
        self.assertEqual(events[-2]["bar_index"], 14)
        self.assertEqual(events[-1]["event_type"], "CHOCH")
        self.assertEqual(events[-1]["direction"], "BEARISH")
        self.assertEqual(result["internal"]["current_direction"], "BEARISH")

    def test_default_settings_match_users_structure_settings(self):
        result = luxalgo_market_structure([])

        self.assertEqual(
            result["settings"],
            {
                "internal_length": 5,
                "swing_length": 50,
                "confluence_filter": False,
            },
        )
        self.assertEqual(
            result["tradingview_profile"],
            {
                "indicator": "Smart Money Concepts [LuxAlgo]",
                "mode": "HISTORICAL",
                "show_internal_structure": True,
                "internal_events": "ALL",
                "show_swing_structure": True,
                "swing_events": "ALL",
                "break_confirmation": "CLOSE_CROSS",
            },
        )

    def test_all_four_endpoints_share_the_same_luxalgo_detector(self):
        self.assertIs(scanner_v6.luxalgo_market_structure, luxalgo_market_structure)
        self.assertIs(
            scanner_action_v6.luxalgo_market_structure,
            luxalgo_market_structure,
        )
        self.assertIs(snapshot_v6.luxalgo_market_structure, luxalgo_market_structure)
        self.assertIs(
            snapshot_action_v6.luxalgo_market_structure,
            luxalgo_market_structure,
        )


if __name__ == "__main__":
    unittest.main()
