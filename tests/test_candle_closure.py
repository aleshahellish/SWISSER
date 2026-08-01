from __future__ import annotations

import unittest

from api import (
    scanner_action_v6,
    scanner_v6,
    snapshot_action_v6,
    snapshot_v6,
)
from candle_closure import (
    closure_sequence_summary,
    detect_candle_closures,
    equilibrium,
)


def candle(index, open_, high, low, close):
    timestamp = index * 60
    return {
        "time": timestamp,
        "time_utc": f"T{index}",
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": 1.0,
    }


class CandleClosureTests(unittest.TestCase):
    def test_c1_colour_is_not_a_c2_condition(self):
        candles = [
            candle(0, 102, 106, 100, 104),
            candle(1, 104, 108, 101, 106),
            # Green C1.
            candle(2, 103, 110, 100, 108),
            candle(3, 101, 109, 98, 105),
        ]

        c2, c3, displacement = detect_candle_closures(
            candles, filter_length=3
        )

        self.assertEqual(len(c2), 1)
        self.assertEqual(c2[0]["direction"], "BULLISH")
        self.assertEqual(c2[0]["previous_direction"], "BULLISH")
        self.assertFalse(c3)
        self.assertFalse(displacement)

    def test_close_beyond_opposite_c1_side_is_not_c2(self):
        candles = [
            candle(0, 102, 106, 100, 104),
            candle(1, 104, 108, 101, 106),
            candle(2, 103, 110, 100, 108),
            candle(3, 101, 113, 98, 112),
        ]

        c2, _, displacement = detect_candle_closures(
            candles, filter_length=3
        )

        self.assertFalse(c2)
        self.assertEqual(len(displacement), 1)
        self.assertEqual(displacement[0]["direction"], "BULLISH")
        self.assertEqual(
            displacement[0]["type"],
            "SWEEP_PLUS_OPPOSITE_EXPANSION",
        )

    def test_two_sided_c2_keeps_both_directions(self):
        candles = [
            candle(0, 102, 106, 100, 104),
            candle(1, 104, 108, 101, 106),
            candle(2, 103, 110, 100, 108),
            candle(3, 104, 112, 98, 105),
        ]

        c2, _, _ = detect_candle_closures(candles, filter_length=3)

        self.assertEqual(
            {event["direction"] for event in c2},
            {"BULLISH", "BEARISH"},
        )
        self.assertTrue(
            all(event["closure_type"] == "TWO_SIDED" for event in c2)
        )

    def test_reversal_filter_rejects_minor_closure(self):
        candles = [
            candle(0, 101, 106, 95, 104),
            candle(1, 104, 108, 101, 106),
            candle(2, 103, 110, 100, 108),
            candle(3, 101, 109, 98, 105),
        ]

        c2, _, _ = detect_candle_closures(candles, filter_length=4)
        self.assertFalse(c2)

    def test_large_wick_selects_wick_equilibrium(self):
        event = candle(3, 100, 109, 90, 105)
        eq = equilibrium(event, "BULLISH", wick_percent=40)

        self.assertEqual(eq["basis"], "REJECTION_WICK")
        self.assertEqual(eq["low"], 90)
        self.assertEqual(eq["high"], 100)
        self.assertEqual(eq["midpoint"], 95)

    def test_c3_is_kept_and_eq_quality_is_explicit(self):
        base = [
            candle(0, 102, 106, 99, 104),
            candle(1, 104, 108, 98, 106),
            candle(2, 103, 109, 97, 107),
            candle(3, 100, 109, 90, 105),
        ]

        respected = base + [candle(4, 105, 111, 96, 110)]
        _, c3_good, _ = detect_candle_closures(
            respected, filter_length=4
        )
        self.assertEqual(len(c3_good), 1)
        self.assertTrue(c3_good[0]["eq_respected"])

        not_respected = base + [candle(4, 105, 111, 94, 110)]
        _, c3_weak, _ = detect_candle_closures(
            not_respected, filter_length=4
        )
        self.assertEqual(len(c3_weak), 1)
        self.assertFalse(c3_weak[0]["eq_respected"])
        self.assertEqual(
            c3_weak[0]["quality"],
            "EXPANSION_EQ_NOT_RESPECTED",
        )

    def test_sequence_reports_c4_and_c5(self):
        candles = [
            candle(0, 102, 106, 99, 104),
            candle(1, 104, 108, 98, 106),
            candle(2, 103, 109, 97, 107),
            candle(3, 100, 109, 90, 105),
            candle(4, 105, 111, 96, 110),
            candle(5, 110, 112, 107, 111),
        ]
        c2, c3, _ = detect_candle_closures(candles, filter_length=4)
        phase = closure_sequence_summary(candles, c2, c3)
        self.assertEqual(phase["state"], "C4_EXPECTATION_PHASE")
        self.assertEqual(phase["candle_number"], 4)

        candles.append(candle(6, 111, 113, 108, 112))
        c2, c3, _ = detect_candle_closures(candles, filter_length=4)
        phase = closure_sequence_summary(candles, c2, c3)
        self.assertEqual(phase["state"], "C5_LATE_SEQUENCE_PHASE")
        self.assertEqual(phase["candle_number"], 5)

    def test_all_four_endpoints_share_the_same_detector(self):
        candles = [
            candle(0, 102, 106, 100, 104),
            candle(1, 104, 108, 101, 106),
            candle(2, 103, 110, 100, 108),
            candle(3, 101, 109, 98, 105),
        ]
        expected = scanner_v6.detect(candles)

        self.assertEqual(scanner_action_v6.detect(candles), expected)
        self.assertEqual(snapshot_v6.detect(candles), expected)
        self.assertEqual(snapshot_action_v6.detect(candles), expected)


if __name__ == "__main__":
    unittest.main()
