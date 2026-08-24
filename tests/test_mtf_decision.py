import unittest

from api import scanner_action_v6, scanner_v6, snapshot_action_v6, snapshot_v6
import mtf_decision
from mtf_decision import build_mtf_decision


def structure_event(event_type, direction, bars_since, bar_index):
    return {
        "event_type": event_type,
        "direction": direction,
        "bars_since": bars_since,
        "bar_index": bar_index,
    }


def signal(direction, trigger=None, structure_events=None):
    structure_events = structure_events or []
    return {
        "primary_direction": direction,
        "latest_internal_event": (
            structure_events[-1] if structure_events else None
        ),
        "recent_internal_events": structure_events,
        "latest_trigger": trigger
        or {
            "type": "NONE",
            "direction": "UNDETERMINED",
            "is_fresh": False,
        },
    }


def signals(
    h4,
    h1,
    m15,
    m1,
    trigger=None,
    m15_events=None,
    m1_events=None,
):
    return {
        "4h": signal(h4),
        "1h": signal(h1),
        "15m": signal(m15, structure_events=m15_events),
        "1m": signal(m1, trigger, m1_events),
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
        bearish_choch = structure_event("CHOCH", "BEARISH", 0, 100)
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BEARISH",
                "BEARISH",
                m1_events=[bearish_choch],
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
        bearish_choch = structure_event("CHOCH", "BEARISH", 0, 100)
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BEARISH",
                "BEARISH",
                "BEARISH",
                m1_events=[bearish_choch],
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

    def test_fresh_1m_choch_is_ready_without_c2_c3(self):
        bullish_choch = structure_event("CHOCH", "BULLISH", 1, 100)
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m1_events=[bullish_choch],
            ),
            BROAD_BULLISH,
        )

        execution = decision["execution_state"]
        self.assertTrue(execution["trade_ready"])
        self.assertEqual(
            execution["state"],
            "FRESH_ENTRY_STRUCTURE_CONFIRMATION",
        )
        self.assertEqual(
            execution["entry_structure_confirmation"]["confirmation_type"],
            "CHOCH",
        )
        self.assertEqual(
            execution["c2_c3_role"],
            "OPTIONAL_CONFLUENCE_NOT_READINESS_GATE",
        )

    def test_fresh_c2_c3_without_1m_choch_is_not_ready(self):
        bullish_c3 = {
            "type": "C3_CONFIRMED",
            "direction": "BULLISH",
            "is_fresh": True,
        }
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                trigger=bullish_c3,
            ),
            BROAD_BULLISH,
        )

        self.assertFalse(decision["execution_state"]["trade_ready"])
        self.assertEqual(
            decision["execution_state"]["entry_structure_confirmation"][
                "reason"
            ],
            "NO_1M_INTERNAL_STRUCTURE_EVENT",
        )

    def test_stale_1m_choch_is_not_relabelled_as_ready(self):
        stale_choch = structure_event("CHOCH", "BULLISH", 4, 96)
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m1_events=[stale_choch],
            ),
            BROAD_BULLISH,
        )

        self.assertFalse(decision["execution_state"]["trade_ready"])
        self.assertEqual(
            decision["execution_state"]["entry_structure_confirmation"][
                "reason"
            ],
            "LATEST_1M_STRUCTURE_EVENT_IS_STALE",
        )

    def test_fresh_bos_keeps_the_preceding_choch_chain(self):
        events = [
            structure_event("CHOCH", "BULLISH", 2, 100),
            structure_event("BOS", "BULLISH", 0, 102),
        ]
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m1_events=events,
            ),
            BROAD_BULLISH,
        )

        confirmation = decision["execution_state"][
            "entry_structure_confirmation"
        ]
        self.assertTrue(decision["execution_state"]["trade_ready"])
        self.assertEqual(confirmation["confirmation_type"], "CHOCH_THEN_BOS")
        self.assertEqual(confirmation["origin_choch"]["event_type"], "CHOCH")

    def test_bos_keeps_choch_origin_beyond_compact_event_history(self):
        origin = structure_event("CHOCH", "BULLISH", 12, 88)
        latest_bos = structure_event("BOS", "BULLISH", 0, 100)
        latest_bos["active_leg_origin_choch"] = origin
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m1_events=[latest_bos],
            ),
            BROAD_BULLISH,
        )

        confirmation = decision["execution_state"][
            "entry_structure_confirmation"
        ]
        self.assertTrue(decision["execution_state"]["trade_ready"])
        self.assertEqual(confirmation["confirmation_type"], "CHOCH_THEN_BOS")
        self.assertEqual(confirmation["origin_choch"]["bar_index"], 88)

    def test_fresh_bos_without_choch_origin_is_not_ready(self):
        bullish_bos = structure_event("BOS", "BULLISH", 0, 100)
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m1_events=[bullish_bos],
            ),
            BROAD_BULLISH,
        )

        self.assertFalse(decision["execution_state"]["trade_ready"])
        self.assertEqual(
            decision["execution_state"]["entry_structure_confirmation"][
                "reason"
            ],
            "NO_CHOCH_IN_ACTIVE_1M_STRUCTURE_LEG",
        )

    def test_15m_bos_is_valid_setup_structure(self):
        decision = build_mtf_decision(
            signals(
                "BULLISH",
                "BULLISH",
                "BULLISH",
                "BULLISH",
                m15_events=[
                    structure_event("BOS", "BULLISH", 0, 100)
                ],
                m1_events=[
                    structure_event("CHOCH", "BULLISH", 0, 100)
                ],
            ),
            BROAD_BULLISH,
        )

        self.assertEqual(
            decision["alignment_state"],
            "CORE_FULL_BULLISH_ALIGNMENT",
        )
        self.assertTrue(decision["execution_state"]["trade_ready"])

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
