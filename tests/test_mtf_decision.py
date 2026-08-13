import unittest

from api import scanner_action_v6, scanner_v6, snapshot_action_v6, snapshot_v6
import mtf_decision
from mtf_decision import build_mtf_decision


def signal(direction, trigger=None):
    return {
        "primary_direction": direction,
        "latest_trigger": trigger
        or {
            "type": "NONE",
            "direction": "UNDETERMINED",
            "is_fresh": False,
        },
    }


def signals(h4, h1, m15, m1, trigger=None):
    return {
        "4h": signal(h4),
        "1h": signal(h1),
        "15m": signal(m15),
        "1m": signal(m1, trigger),
    }


BROAD_BULLISH = {"direction": "BULLISH"}


class MtfDecisionTests(unittest.TestCase):
    def test_all_endpoints_use_the_shared_decision_layer(self):
        for module in (
            scanner_v6,
            scanner_action_v6,
            snapshot_v6,
            snapshot_action_v6,
        ):
            self.assertIs(
                module.build_mtf_decision,
                mtf_decision.build_mtf_decision,
            )

    def test_hype_shape_is_wait_not_long(self):
        decision = build_mtf_decision(
            signals("BULLISH", "BULLISH", "BULLISH", "BEARISH"),
            BROAD_BULLISH,
        )

        self.assertEqual(
            decision["alignment_state"],
            "CORE_BULLISH_PULLBACK",
        )
        self.assertEqual(decision["continuation_bias"]["direction"], "LONG")
        self.assertEqual(
            decision["continuation_bias"]["role"],
            "CONTEXT_NOT_ACTIVE_TRADE",
        )
        self.assertEqual(
            decision["active_trade_scenario"]["direction"],
            "WAIT",
        )
        self.assertEqual(
            decision["trade_direction_preference"]["direction"],
            "WAIT",
        )
        self.assertEqual(
            decision["execution_state"]["state"],
            "PULLBACK_IN_PROGRESS",
        )

    def test_single_bearish_1m_does_not_create_local_short(self):
        decision = build_mtf_decision(
            signals("BEARISH", "BULLISH", "BULLISH", "BEARISH"),
            {"direction": "BEARISH"},
        )

        self.assertEqual(
            decision["active_trade_scenario"]["direction"],
            "WAIT",
        )
        self.assertEqual(
            decision["active_trade_scenario"]["kind"],
            "PULLBACK_IN_PROGRESS",
        )

    def test_aligned_15m_and_1m_can_form_confirmed_local_short(self):
        bearish_c3 = {
            "type": "C3_CONFIRMED",
            "direction": "BEARISH",
            "is_fresh": True,
        }
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BEARISH",
                "BEARISH",
                bearish_c3,
            ),
            BROAD_BULLISH,
        )

        scenario = decision["active_trade_scenario"]
        self.assertEqual(
            decision["alignment_state"],
            "LOCAL_BEARISH_COUNTER_1H",
        )
        self.assertEqual(scenario["direction"], "SHORT")
        self.assertEqual(scenario["label"], "LOCAL SHORT")
        self.assertEqual(scenario["priority"], "LOWER")
        self.assertTrue(scenario["trade_ready"])
        self.assertIn(
            "COUNTER_1H_LOCAL_SCENARIO",
            scenario["context_cautions"],
        )
        self.assertIn(
            "COUNTER_4H_STRATEGIC_CONTEXT",
            scenario["context_cautions"],
        )

    def test_local_counter_1h_without_fresh_trigger_is_wait(self):
        decision = build_mtf_decision(
            signals("BULLISH", "BULLISH", "BEARISH", "BEARISH"),
            BROAD_BULLISH,
        )

        scenario = decision["active_trade_scenario"]
        self.assertEqual(scenario["direction"], "WAIT")
        self.assertEqual(
            scenario["kind"],
            "LOCAL_COUNTER_1H_WAITING_CONFIRMATION",
        )
        self.assertEqual(
            scenario["potential_local_direction"],
            "SHORT",
        )
        self.assertEqual(
            decision["execution_state"]["state"],
            "WAITING_FOR_LOCAL_ENTRY_CONFIRMATION",
        )

    def test_four_hour_opposition_warns_but_does_not_block_core_short(self):
        bearish_c3 = {
            "type": "C3_CONFIRMED",
            "direction": "BEARISH",
            "is_fresh": True,
        }
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BEARISH",
                "BEARISH",
                "BEARISH",
                bearish_c3,
            ),
            {"direction": "BEARISH"},
        )

        scenario = decision["active_trade_scenario"]
        context = decision["strategic_4h_context"]
        self.assertEqual(scenario["direction"], "SHORT")
        self.assertEqual(scenario["label"], "SHORT")
        self.assertTrue(scenario["trade_ready"])
        self.assertEqual(
            context["relation_to_working_direction"],
            "COUNTER_CONTEXT",
        )
        self.assertFalse(context["blocks_scenario"])

    def test_15m_and_1m_disagreement_stays_wait(self):
        decision = build_mtf_decision(
            signals("BULLISH", "BULLISH", "BEARISH", "BULLISH"),
            BROAD_BULLISH,
        )

        self.assertEqual(
            decision["alignment_state"],
            "CORE_MIXED_CONFLICT",
        )
        self.assertEqual(
            decision["active_trade_scenario"]["direction"],
            "WAIT",
        )


if __name__ == "__main__":
    unittest.main()
