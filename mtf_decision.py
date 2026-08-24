"""Shared deterministic decision layer for the SWISSER MTF hierarchy.

The market-structure detector remains in ``luxalgo_structure.py``.  This
module only decides how already-computed timeframe directions are combined
into a continuation bias, a current trade scenario and an execution state.
"""
from __future__ import annotations


DIRECTIONAL_BIASES = {"BULLISH", "BEARISH"}
TRADE_TO_BIAS = {"LONG": "BULLISH", "SHORT": "BEARISH"}
BIAS_TO_TRADE = {value: key for key, value in TRADE_TO_BIAS.items()}
ENTRY_STRUCTURE_FRESHNESS_BARS = 3


def _direction(signals: dict[str, dict], timeframe: str) -> str:
    return (signals.get(timeframe) or {}).get(
        "primary_direction",
        "UNDETERMINED",
    )


def _core_alignment_state(signals: dict[str, dict]) -> str:
    """Classify only the operational 1h -> 15m -> 1m stack."""

    h1 = _direction(signals, "1h")
    m15 = _direction(signals, "15m")
    m1 = _direction(signals, "1m")

    if any(item not in DIRECTIONAL_BIASES for item in (h1, m15, m1)):
        return "INSUFFICIENT_DATA"

    if h1 == m15 == m1 == "BULLISH":
        return "CORE_FULL_BULLISH_ALIGNMENT"
    if h1 == m15 == m1 == "BEARISH":
        return "CORE_FULL_BEARISH_ALIGNMENT"

    if h1 == m15 == "BULLISH" and m1 == "BEARISH":
        return "CORE_BULLISH_PULLBACK"
    if h1 == m15 == "BEARISH" and m1 == "BULLISH":
        return "CORE_BEARISH_PULLBACK"

    if h1 != m15 and m15 == m1 == "BULLISH":
        return "LOCAL_BULLISH_COUNTER_1H"
    if h1 != m15 and m15 == m1 == "BEARISH":
        return "LOCAL_BEARISH_COUNTER_1H"

    return "CORE_MIXED_CONFLICT"


def _continuation_bias(
    signals: dict[str, dict],
    broad_context: dict,
) -> dict:
    """Describe the 1h/15m continuation idea without calling it a trade."""

    h1 = _direction(signals, "1h")
    m15 = _direction(signals, "15m")
    broad_direction = broad_context.get("direction", "UNDETERMINED")

    if h1 in DIRECTIONAL_BIASES and h1 == m15:
        direction = BIAS_TO_TRADE[h1]
        return {
            "direction": direction,
            "bias_direction": h1,
            "role": "CONTEXT_NOT_ACTIVE_TRADE",
            "source_timeframes": ["1h", "15m"],
            "mode": "CORE_CONTINUATION_BIAS",
            "broad_context_direction": broad_direction,
            "reason": (
                "1h and 15m support the same continuation direction. "
                "The active scenario is decided separately by 1m."
            ),
        }

    return {
        "direction": "WAIT",
        "bias_direction": "MIXED" if h1 != m15 else "UNDETERMINED",
        "role": "CONTEXT_NOT_ACTIVE_TRADE",
        "source_timeframes": ["1h", "15m"],
        "mode": "SESSION_SETUP_CONFLICT",
        "broad_context_direction": broad_direction,
        "reason": "1h and 15m do not support one continuation direction.",
    }


def _entry_structure_confirmation(
    entry_signal: dict,
    expected_direction: str,
) -> dict:
    """Validate a fresh LuxAlgo 1m CHoCH-led structure chain.

    Candle 2/Candle 3 remains available as phase context, but it is not an
    execution gate.  A fresh CHoCH is sufficient; a subsequent same-direction
    BOS remains valid only when the active structure leg contains that CHoCH.
    """

    events = [
        event
        for event in (entry_signal.get("recent_internal_events") or [])
        if isinstance(event, dict)
    ]
    latest = entry_signal.get("latest_internal_event") or {}
    if latest and (
        not events
        or (
            events[-1].get("bar_index"),
            events[-1].get("event_type"),
            events[-1].get("direction"),
        )
        != (
            latest.get("bar_index"),
            latest.get("event_type"),
            latest.get("direction"),
        )
    ):
        events.append(latest)

    latest = events[-1] if events else latest
    latest_direction = latest.get("direction", "UNDETERMINED")
    latest_type = latest.get("event_type")
    latest_bars_since = latest.get("bars_since")
    latest_is_fresh = bool(
        isinstance(latest_bars_since, int)
        and 0 <= latest_bars_since <= ENTRY_STRUCTURE_FRESHNESS_BARS
    )

    active_leg = []
    for event in reversed(events):
        if event.get("direction") != expected_direction:
            break
        active_leg.append(event)

    origin_choch = next(
        (
            event
            for event in active_leg
            if event.get("event_type") == "CHOCH"
        ),
        None,
    )
    if origin_choch is None:
        embedded_origin = latest.get("active_leg_origin_choch")
        if (
            isinstance(embedded_origin, dict)
            and embedded_origin.get("direction") == expected_direction
            and embedded_origin.get("event_type") == "CHOCH"
        ):
            origin_choch = embedded_origin
    confirmed = bool(
        latest_direction == expected_direction
        and latest_type in {"CHOCH", "BOS"}
        and latest_is_fresh
        and origin_choch is not None
    )

    if not latest:
        reason = "NO_1M_INTERNAL_STRUCTURE_EVENT"
    elif latest_direction != expected_direction:
        reason = "LATEST_1M_STRUCTURE_EVENT_OPPOSED"
    elif latest_type not in {"CHOCH", "BOS"}:
        reason = "LATEST_1M_EVENT_IS_NOT_CHOCH_OR_BOS"
    elif not latest_is_fresh:
        reason = "LATEST_1M_STRUCTURE_EVENT_IS_STALE"
    elif origin_choch is None:
        reason = "NO_CHOCH_IN_ACTIVE_1M_STRUCTURE_LEG"
    else:
        reason = "FRESH_CHOCH_LED_1M_STRUCTURE_CONFIRMATION"

    return {
        "confirmed": confirmed,
        "expected_direction": expected_direction,
        "freshness_rule_bars": ENTRY_STRUCTURE_FRESHNESS_BARS,
        "latest_event": latest or None,
        "origin_choch": origin_choch,
        "confirmation_type": (
            "CHOCH"
            if confirmed and latest_type == "CHOCH"
            else "CHOCH_THEN_BOS"
            if confirmed
            else "NONE"
        ),
        "reason": reason,
    }


def _has_fresh_structure_confirmation(
    signals: dict[str, dict],
    expected_direction: str,
) -> bool:
    return _entry_structure_confirmation(
        signals.get("1m") or {},
        expected_direction,
    )["confirmed"]


def _active_scenario(
    alignment_state: str,
    signals: dict[str, dict],
) -> dict:
    h1 = _direction(signals, "1h")
    m15 = _direction(signals, "15m")
    m1 = _direction(signals, "1m")

    if alignment_state == "CORE_FULL_BULLISH_ALIGNMENT":
        return {
            "direction": "LONG",
            "label": "LONG",
            "kind": "CORE_CONTINUATION",
            "priority": "NORMAL",
            "is_local_counter_1h": False,
            "requires_entry_confirmation": True,
            "reason": "1h, 15m and 1m are bullish.",
        }
    if alignment_state == "CORE_FULL_BEARISH_ALIGNMENT":
        return {
            "direction": "SHORT",
            "label": "SHORT",
            "kind": "CORE_CONTINUATION",
            "priority": "NORMAL",
            "is_local_counter_1h": False,
            "requires_entry_confirmation": True,
            "reason": "1h, 15m and 1m are bearish.",
        }
    if alignment_state == "LOCAL_BULLISH_COUNTER_1H":
        if not _has_fresh_structure_confirmation(signals, "BULLISH"):
            return {
                "direction": "WAIT",
                "label": "WAIT",
                "kind": "LOCAL_COUNTER_1H_WAITING_CONFIRMATION",
                "priority": "NONE",
                "is_local_counter_1h": True,
                "potential_local_direction": "LONG",
                "current_local_direction": "LONG",
                "against_timeframe": "1h",
                "requires_entry_confirmation": True,
                "reason": (
                    "15m and 1m are bullish against bearish 1h, but there is "
                    "no fresh bullish 1m CHoCH-led structure confirmation yet."
                ),
            }
        return {
            "direction": "LONG",
            "label": "LOCAL LONG",
            "kind": "LOCAL_COUNTER_1H",
            "priority": "LOWER",
            "is_local_counter_1h": True,
            "against_timeframe": "1h",
            "requires_entry_confirmation": True,
            "target_policy": "CLOSER_LOCAL_TARGETS",
            "reason": (
                "15m and 1m are bullish against a bearish 1h; this is a "
                "local scenario, not a confirmed 1h reversal."
            ),
        }
    if alignment_state == "LOCAL_BEARISH_COUNTER_1H":
        if not _has_fresh_structure_confirmation(signals, "BEARISH"):
            return {
                "direction": "WAIT",
                "label": "WAIT",
                "kind": "LOCAL_COUNTER_1H_WAITING_CONFIRMATION",
                "priority": "NONE",
                "is_local_counter_1h": True,
                "potential_local_direction": "SHORT",
                "current_local_direction": "SHORT",
                "against_timeframe": "1h",
                "requires_entry_confirmation": True,
                "reason": (
                    "15m and 1m are bearish against bullish 1h, but there is "
                    "no fresh bearish 1m CHoCH-led structure confirmation yet."
                ),
            }
        return {
            "direction": "SHORT",
            "label": "LOCAL SHORT",
            "kind": "LOCAL_COUNTER_1H",
            "priority": "LOWER",
            "is_local_counter_1h": True,
            "against_timeframe": "1h",
            "requires_entry_confirmation": True,
            "target_policy": "CLOSER_LOCAL_TARGETS",
            "reason": (
                "15m and 1m are bearish against a bullish 1h; this is a "
                "local scenario, not a confirmed 1h reversal."
            ),
        }
    if alignment_state == "CORE_BULLISH_PULLBACK":
        return {
            "direction": "WAIT",
            "label": "WAIT",
            "kind": "PULLBACK_IN_PROGRESS",
            "priority": "NONE",
            "is_local_counter_1h": False,
            "potential_continuation_direction": "LONG",
            "current_local_direction": "SHORT",
            "requires_entry_confirmation": True,
            "reason": (
                "1h and 15m remain bullish, but 1m is bearish. Do not label "
                "the current trade as LONG until 1m turns back up."
            ),
        }
    if alignment_state == "CORE_BEARISH_PULLBACK":
        return {
            "direction": "WAIT",
            "label": "WAIT",
            "kind": "PULLBACK_IN_PROGRESS",
            "priority": "NONE",
            "is_local_counter_1h": False,
            "potential_continuation_direction": "SHORT",
            "current_local_direction": "LONG",
            "requires_entry_confirmation": True,
            "reason": (
                "1h and 15m remain bearish, but 1m is bullish. Do not label "
                "the current trade as SHORT until 1m turns back down."
            ),
        }
    if alignment_state == "INSUFFICIENT_DATA":
        reason = "At least one timeframe in the 1h/15m/1m core has no direction."
    else:
        reason = (
            "1h, 15m and 1m do not form a tradable continuation or local "
            "counter-1h stack."
        )

    return {
        "direction": "WAIT",
        "label": "WAIT",
        "kind": (
            "INSUFFICIENT_DATA"
            if alignment_state == "INSUFFICIENT_DATA"
            else "CONFLICT"
        ),
        "priority": "NONE",
        "is_local_counter_1h": False,
        "requires_entry_confirmation": True,
        "observed_core_directions": {
            "1h": h1,
            "15m": m15,
            "1m": m1,
        },
        "reason": reason,
    }


def _execution_state(scenario: dict, entry_signal: dict) -> dict:
    preferred_direction = TRADE_TO_BIAS.get(scenario.get("direction"))
    entry_direction = entry_signal.get("primary_direction", "UNDETERMINED")
    trigger = entry_signal.get("latest_trigger") or {}
    structure_confirmation = _entry_structure_confirmation(
        entry_signal,
        preferred_direction or "NONE",
    )

    if preferred_direction is None:
        relation = "NOT_APPLICABLE"
        scenario_kind = scenario.get("kind")
        if scenario_kind == "PULLBACK_IN_PROGRESS":
            state = "PULLBACK_IN_PROGRESS"
        elif scenario_kind == "LOCAL_COUNTER_1H_WAITING_CONFIRMATION":
            state = "WAITING_FOR_LOCAL_ENTRY_CONFIRMATION"
        elif scenario_kind == "INSUFFICIENT_DATA":
            state = "INSUFFICIENT_DATA"
        else:
            state = "NO_ACTIVE_SCENARIO"
        trade_ready = False
    elif entry_direction == preferred_direction:
        relation = "ALIGNED"
        if structure_confirmation["confirmed"]:
            state = "FRESH_ENTRY_STRUCTURE_CONFIRMATION"
            trade_ready = True
        else:
            state = "ENTRY_BIAS_ALIGNED_NO_FRESH_CHOCH_CONFIRMATION"
            trade_ready = False
    elif entry_direction in DIRECTIONAL_BIASES:
        relation = "OPPOSED"
        state = "ENTRY_TIMEFRAME_OPPOSED"
        trade_ready = False
    else:
        relation = "UNDETERMINED"
        state = "ENTRY_TIMEFRAME_UNDETERMINED"
        trade_ready = False

    return {
        "state": state,
        "trade_ready": trade_ready,
        "preferred_direction": preferred_direction or "NONE",
        "entry_timeframe_direction": entry_direction,
        "relation_to_preference": relation,
        "entry_structure_confirmation": structure_confirmation,
        "latest_entry_trigger": trigger,
        "c2_c3_role": "OPTIONAL_CONFLUENCE_NOT_READINESS_GATE",
    }


def _strategic_4h_context(
    signals: dict[str, dict],
    scenario: dict,
    continuation: dict,
) -> dict:
    h4 = _direction(signals, "4h")
    comparison_trade = scenario.get("direction")
    if comparison_trade not in TRADE_TO_BIAS:
        comparison_trade = continuation.get("direction")
    comparison_bias = TRADE_TO_BIAS.get(comparison_trade)

    if h4 not in DIRECTIONAL_BIASES or comparison_bias is None:
        relation = "MIXED_OR_UNDETERMINED"
    elif h4 == comparison_bias:
        relation = "ALIGNED"
    else:
        relation = "COUNTER_CONTEXT"

    return {
        "direction": h4,
        "role": "STRATEGIC_CONTEXT_NOT_CORE_VOTE",
        "compared_with": comparison_trade or "NONE",
        "relation_to_working_direction": relation,
        "caution": relation != "ALIGNED",
        "blocks_scenario": False,
    }


def _active_preference_alias(
    scenario: dict,
    broad_context: dict,
    strategic_4h: dict,
) -> dict:
    """Keep the old field safe for clients that have not migrated yet."""

    active_bias = TRADE_TO_BIAS.get(scenario.get("direction"))
    broad_direction = broad_context.get("direction", "UNDETERMINED")
    broad_caution = broad_direction in {"MIXED", "UNDETERMINED"} or (
        active_bias in DIRECTIONAL_BIASES
        and broad_direction in DIRECTIONAL_BIASES
        and active_bias != broad_direction
    )

    return {
        "direction": scenario.get("direction", "WAIT"),
        "label": scenario.get("label", "WAIT"),
        "mode": scenario.get("kind", "CONFLICT"),
        "role": "ACTIVE_SCENARIO_COMPATIBILITY_FIELD",
        "requires_entry_confirmation": True,
        "broad_context_direction": broad_direction,
        "broad_context_caution": broad_caution,
        "four_hour_context_direction": strategic_4h.get("direction"),
        "four_hour_context_caution": strategic_4h.get("caution"),
        "reason": scenario.get("reason"),
    }


def build_mtf_decision(
    signals: dict[str, dict],
    broad_context: dict,
) -> dict:
    """Return the shared decision for scanner and snapshot endpoints."""

    alignment_state = _core_alignment_state(signals)
    continuation = _continuation_bias(signals, broad_context)
    scenario = _active_scenario(alignment_state, signals)
    strategic_4h = _strategic_4h_context(
        signals,
        scenario,
        continuation,
    )
    execution = _execution_state(scenario, signals.get("1m") or {})

    scenario = dict(scenario)
    scenario["execution_state"] = execution["state"]
    scenario["trade_ready"] = execution["trade_ready"]
    scenario["status"] = (
        "READY"
        if execution["trade_ready"]
        else "WAITING_CONFIRMATION"
        if scenario["direction"] in TRADE_TO_BIAS
        else "WAIT"
    )

    cautions = []
    if scenario.get("is_local_counter_1h"):
        cautions.append("COUNTER_1H_LOCAL_SCENARIO")
    if strategic_4h["relation_to_working_direction"] == "COUNTER_CONTEXT":
        cautions.append("COUNTER_4H_STRATEGIC_CONTEXT")

    active_bias = TRADE_TO_BIAS.get(scenario.get("direction"))
    broad_direction = broad_context.get("direction", "UNDETERMINED")
    if (
        active_bias in DIRECTIONAL_BIASES
        and broad_direction in DIRECTIONAL_BIASES
        and active_bias != broad_direction
    ):
        cautions.append("COUNTER_1D_1W_BROAD_CONTEXT")

    scenario["context_cautions"] = cautions

    return {
        "alignment_state": alignment_state,
        "continuation_bias": continuation,
        "active_trade_scenario": scenario,
        "strategic_4h_context": strategic_4h,
        "trade_direction_preference": _active_preference_alias(
            scenario,
            broad_context,
            strategic_4h,
        ),
        "execution_state": execution,
    }
