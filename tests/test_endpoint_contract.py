from __future__ import annotations

import unittest
from unittest.mock import patch

from api import scanner_action_v6, snapshot_action_v6


FIXED_NOW = 2_000_000_000


def candles_for(seconds, count):
    start = FIXED_NOW - seconds * (count + 1)
    output = []
    for index in range(count):
        base = 100.0 + index * 0.015 + ((index % 8) - 4) * 0.22
        open_price = base + (0.12 if index % 2 else -0.12)
        close_price = base + (-0.16 if index % 3 else 0.18)
        timestamp = start + index * seconds
        output.append(
            {
                "time": timestamp,
                "time_utc": (
                    f"2026-08-01 {index % 24:02d}:"
                    f"{index % 60:02d}:00 UTC"
                ),
                "open": open_price,
                "high": max(open_price, close_price)
                + 0.35
                + (index % 5) * 0.03,
                "low": min(open_price, close_price)
                - 0.32
                - (index % 7) * 0.02,
                "close": close_price,
                "volume": 1000.0 + index * 13.7,
            }
        )
    return output


class EndpointContractTests(unittest.TestCase):
    def test_dense_action_payloads_keep_twenty_percent_headroom(self):
        tickers = {
            symbol: {
                "lastPrice": 108.0,
                "high24Price": 110.0,
                "lower24Price": 95.0,
            }
            for symbol in scanner_action_v6.SUPPORTED_SYMBOLS
        }

        with (
            patch.object(
                scanner_action_v6,
                "fetch_tickers",
                return_value=tickers,
            ),
            patch.object(
                scanner_action_v6,
                "fetch_candles",
                side_effect=lambda symbol, api_timeframe, seconds, count: (
                    candles_for(seconds, count)
                ),
            ),
            patch.object(
                scanner_action_v6.time,
                "time",
                return_value=FIXED_NOW,
            ),
        ):
            full_scanner = scanner_action_v6.build(
                "/api/scanner_action_v6"
            )
            compact_scanner = scanner_action_v6.compact_for_gpt_action(
                full_scanner
            )

        with (
            patch.object(
                snapshot_action_v6,
                "ticker",
                return_value={
                    "lastPrice": 108.0,
                    "high24Price": 110.0,
                    "lower24Price": 95.0,
                },
            ),
            patch.object(
                snapshot_action_v6,
                "candles",
                side_effect=lambda symbol, api_timeframe, seconds, count: (
                    candles_for(seconds, count)
                ),
            ),
            patch.object(
                snapshot_action_v6.time,
                "time",
                return_value=FIXED_NOW,
            ),
        ):
            full_snapshot = snapshot_action_v6.build("HYPE_USDT")
            compact_snapshot = (
                snapshot_action_v6.compact_snapshot_for_gpt_action(
                    full_snapshot
                )
            )

        scanner_size = len(
            scanner_action_v6.encode_gpt_action_payload(
                compact_scanner
            ).decode("utf-8")
        )
        snapshot_size = len(
            snapshot_action_v6.encode_gpt_action_payload(
                compact_snapshot
            ).decode("utf-8")
        )

        self.assertLess(
            scanner_size,
            scanner_action_v6.GPT_ACTION_SAFE_TARGET,
        )
        self.assertLess(
            snapshot_size,
            snapshot_action_v6.GPT_ACTION_SAFE_TARGET,
        )

    def test_compact_scanner_keeps_btc_as_context_only(self):
        tickers = {
            symbol: {
                "lastPrice": 108.0,
                "high24Price": 110.0,
                "lower24Price": 95.0,
            }
            for symbol in ("HYPE_USDT", "BTC_USDT")
        }

        def fake_fetch_candles(symbol, api_timeframe, seconds, count):
            return candles_for(seconds, count)

        with (
            patch.object(scanner_action_v6, "fetch_tickers", return_value=tickers),
            patch.object(
                scanner_action_v6,
                "fetch_candles",
                side_effect=fake_fetch_candles,
            ),
            patch.object(scanner_action_v6.time, "time", return_value=FIXED_NOW),
        ):
            full = scanner_action_v6.build(
                "/api/scanner_action_v6?symbols=HYPE_USDT,BTC_USDT"
            )
            compact = scanner_action_v6.compact_for_gpt_action(full)

        self.assertEqual(compact["candidate_symbols"], ["HYPE_USDT"])
        self.assertEqual(compact["context_symbols"], ["BTC_USDT"])
        by_symbol = {item["symbol"]: item for item in compact["results"]}
        self.assertTrue(by_symbol["HYPE_USDT"]["eligible_trade_candidate"])
        self.assertFalse(by_symbol["BTC_USDT"]["eligible_trade_candidate"])
        self.assertEqual(
            by_symbol["BTC_USDT"]["analysis_role"],
            "MARKET_CONTEXT",
        )
        self.assertIn(
            "hourly_closure_phase",
            by_symbol["HYPE_USDT"]["mtf_hierarchy"],
        )
        hierarchy = by_symbol["HYPE_USDT"]["mtf_hierarchy"]
        self.assertEqual(
            hierarchy["alignment_scope"],
            ["1h", "15m", "1m"],
        )
        self.assertIn("continuation_bias", hierarchy)
        self.assertIn("active_trade_scenario", hierarchy)
        self.assertIn("strategic_4h_context", hierarchy)
        for block in by_symbol["HYPE_USDT"]["timeframe_summary"].values():
            self.assertIn("luxalgo_structure", block)
            self.assertNotIn("protected_structure", block)
            self.assertNotIn("current_cisd_direction", block)
            self.assertNotIn("latest_structure_break", block)
            self.assertEqual(
                set(block["luxalgo_structure"]),
                {"internal", "swing"},
            )
            for layer in ("internal", "swing"):
                compact_layer = block["luxalgo_structure"][layer]
                self.assertIn("current_direction", compact_layer)
                self.assertIn("current_high_level", compact_layer)
                self.assertIn("current_low_level", compact_layer)
                self.assertIn("latest_event", compact_layer)
                self.assertNotIn("recent_events", compact_layer)
            displacement = block["latest_sweep_displacement"]
            if displacement is not None:
                self.assertEqual(
                    set(displacement),
                    {
                        "type",
                        "direction",
                        "time",
                        "time_utc",
                        "swept_side",
                        "closed_beyond",
                    },
                )
        encoded = scanner_action_v6.encode_gpt_action_payload(compact)
        self.assertLess(
            len(encoded.decode("utf-8")),
            scanner_action_v6.GPT_ACTION_SAFE_TARGET,
        )

    def test_compact_snapshot_exposes_sequence_and_displacement_fields(self):
        ticker = {
            "lastPrice": 108.0,
            "high24Price": 110.0,
            "lower24Price": 95.0,
        }

        def fake_candles(symbol, api_timeframe, seconds, count):
            return candles_for(seconds, count)

        with (
            patch.object(snapshot_action_v6, "ticker", return_value=ticker),
            patch.object(
                snapshot_action_v6,
                "candles",
                side_effect=fake_candles,
            ),
            patch.object(snapshot_action_v6.time, "time", return_value=FIXED_NOW),
        ):
            full = snapshot_action_v6.build("BTC_USDT")
            compact = snapshot_action_v6.compact_snapshot_for_gpt_action(full)

        self.assertFalse(compact["eligible_trade_candidate"])
        self.assertEqual(compact["analysis_role"], "MARKET_CONTEXT")
        self.assertIn("hourly_closure_phase", compact["mtf_hierarchy"])
        self.assertEqual(
            compact["mtf_hierarchy"]["alignment_scope"],
            ["1h", "15m", "1m"],
        )
        self.assertIn(
            "active_trade_scenario",
            compact["mtf_hierarchy"],
        )
        for block in compact["timeframes"].values():
            self.assertIn("closure_sequence", block)
            self.assertIn("recent_sweep_displacement", block)
            self.assertIn("luxalgo_structure", block)
            self.assertIn("internal", block["luxalgo_structure"])
            self.assertIn("swing", block["luxalgo_structure"])
            self.assertNotIn("swing_points", block)
            self.assertNotIn("swing_structure", block)
            self.assertNotIn("protected_structure", block)
        encoded = snapshot_action_v6.encode_gpt_action_payload(compact)
        self.assertLess(
            len(encoded.decode("utf-8")),
            snapshot_action_v6.GPT_ACTION_SAFE_TARGET,
        )


if __name__ == "__main__":
    unittest.main()
