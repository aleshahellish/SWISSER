"""Deterministic Candle 2 / Candle 3 detection.

The implementation follows the public LuxAlgo/TTrades description:

* Candle 2 sweeps one side of Candle 1 and closes back inside its range.
* The optional reversal filter keeps only the highest/lowest reversal of the
  configured lookback.
* Candle 3 closes outside Candle 2 in the reversal direction.
* Equilibrium respect is reported as a quality attribute.  It does not hide
  an otherwise valid Candle 3 expansion.

This module intentionally contains no market-data or trading-decision logic.
"""

from __future__ import annotations

from collections import defaultdict


DIRECTIONS = ("BULLISH", "BEARISH")


def _previous_direction(candle: dict) -> str:
    if candle["close"] > candle["open"]:
        return "BULLISH"
    if candle["close"] < candle["open"]:
        return "BEARISH"
    return "DOJI"


def equilibrium(candle: dict, direction: str, wick_percent: float) -> dict:
    """Return the relevant midpoint and the range used to calculate it.

    For a large rejection wick, the midpoint is based on that wick alone.
    Otherwise it is based on the complete candle range.  ``direction`` is the
    expected reversal direction, so bullish uses the lower rejection wick and
    bearish uses the upper rejection wick.
    """

    candle_range = max(candle["high"] - candle["low"], 0.0)
    body_low = min(candle["open"], candle["close"])
    body_high = max(candle["open"], candle["close"])

    if direction == "BULLISH":
        wick_low = candle["low"]
        wick_high = body_low
    elif direction == "BEARISH":
        wick_low = body_high
        wick_high = candle["high"]
    else:
        raise ValueError(f"Unsupported direction: {direction}")

    wick_size = max(wick_high - wick_low, 0.0)
    wick_ratio = wick_size / candle_range if candle_range > 0 else 0.0
    use_wick = candle_range > 0 and wick_ratio > wick_percent / 100.0

    basis_low = wick_low if use_wick else candle["low"]
    basis_high = wick_high if use_wick else candle["high"]

    return {
        "basis": "REJECTION_WICK" if use_wick else "FULL_RANGE",
        "low": basis_low,
        "high": basis_high,
        "midpoint": (basis_low + basis_high) / 2.0,
        "rejection_wick_percent": round(wick_ratio * 100.0, 6),
        "wick_threshold_percent": wick_percent,
    }


def _event_candle(current: dict) -> dict:
    return {
        "time": current["time"],
        "time_utc": current.get("time_utc"),
        "open": current["open"],
        "high": current["high"],
        "low": current["low"],
        "close": current["close"],
    }


def detect_candle_closures(
    candles_list: list[dict],
    filter_length: int = 12,
    wick_percent: float = 40,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Detect C2, C3, and sweep-plus-opposite-expansion events.

    Only closed candles should be supplied by callers.  Two-sided outside bars
    can produce both a bullish and bearish C2 when both reversal filters pass.
    """

    if filter_length < 1:
        raise ValueError("filter_length must be at least 1")

    candle2: list[dict] = []
    candle3: list[dict] = []
    sweep_displacement: list[dict] = []
    c2_by_index: dict[int, list[dict]] = defaultdict(list)

    for index in range(1, len(candles_list)):
        previous = candles_list[index - 1]
        current = candles_list[index]
        window = candles_list[max(0, index - filter_length + 1):index + 1]
        filter_ready = len(window) == filter_length

        lowest_in_window = current["low"] == min(
            item["low"] for item in window
        )
        highest_in_window = current["high"] == max(
            item["high"] for item in window
        )
        swept_low = current["low"] < previous["low"]
        swept_high = current["high"] > previous["high"]
        closed_inside_c1 = (
            current["close"] > previous["low"]
            and current["close"] < previous["high"]
        )

        bullish_signal = (
            filter_ready
            and swept_low
            and closed_inside_c1
            and lowest_in_window
        )
        bearish_signal = (
            filter_ready
            and swept_high
            and closed_inside_c1
            and highest_in_window
        )
        two_sided = swept_low and swept_high and closed_inside_c1

        for direction, passed, filter_extreme in (
            ("BULLISH", bullish_signal, current["low"]),
            ("BEARISH", bearish_signal, current["high"]),
        ):
            if not passed:
                continue

            eq = equilibrium(current, direction, wick_percent)
            event = {
                "type": "C2_REVERSAL_CLOSURE",
                "direction": direction,
                **_event_candle(current),
                "candle1_time": previous["time"],
                "candle1_time_utc": previous.get("time_utc"),
                "candle1_low": previous["low"],
                "candle1_high": previous["high"],
                "previous_direction": _previous_direction(previous),
                "closure_type": "TWO_SIDED" if two_sided else "ONE_SIDED",
                "swept_low": swept_low,
                "swept_high": swept_high,
                "closed_inside_candle1_range": True,
                "reversal_filter_passed": True,
                "reversal_filter_length": filter_length,
                "filter_extreme": filter_extreme,
                "equilibrium": eq,
                # Kept for compatibility with existing GPT instructions.
                "big_wick_40_percent": eq["basis"] == "REJECTION_WICK",
            }
            candle2.append(event)
            c2_by_index[index].append(event)

        # Important, but not a C2: one candle sweeps one side of C1 and closes
        # beyond the opposite side.  Keep it visible under a separate label.
        bullish_sweep_expansion = (
            swept_low
            and current["close"] > previous["high"]
            and filter_ready
            and lowest_in_window
        )
        bearish_sweep_expansion = (
            swept_high
            and current["close"] < previous["low"]
            and filter_ready
            and highest_in_window
        )

        for direction, passed, swept_side, closed_beyond in (
            (
                "BULLISH",
                bullish_sweep_expansion,
                "CANDLE1_LOW",
                "CANDLE1_HIGH",
            ),
            (
                "BEARISH",
                bearish_sweep_expansion,
                "CANDLE1_HIGH",
                "CANDLE1_LOW",
            ),
        ):
            if not passed:
                continue
            sweep_displacement.append(
                {
                    "type": "SWEEP_PLUS_OPPOSITE_EXPANSION",
                    "direction": direction,
                    **_event_candle(current),
                    "candle1_time": previous["time"],
                    "candle1_time_utc": previous.get("time_utc"),
                    "candle1_low": previous["low"],
                    "candle1_high": previous["high"],
                    "swept_side": swept_side,
                    "closed_beyond": closed_beyond,
                    "reversal_filter_passed": True,
                    "reversal_filter_length": filter_length,
                    "classification_note": (
                        "Important displacement event, but not a C2 because "
                        "the close is outside Candle 1."
                    ),
                }
            )

        if index < 2:
            continue

        for c2_event in c2_by_index.get(index - 1, []):
            direction = c2_event["direction"]
            if direction == "BULLISH":
                expansion_closed = current["close"] > c2_event["high"]
                eq_respected = (
                    current["low"] >= c2_event["equilibrium"]["midpoint"]
                )
                invalidation_level = c2_event["low"]
            else:
                expansion_closed = current["close"] < c2_event["low"]
                eq_respected = (
                    current["high"] <= c2_event["equilibrium"]["midpoint"]
                )
                invalidation_level = c2_event["high"]

            if not expansion_closed:
                continue

            c3_eq = {
                "basis": "FULL_RANGE",
                "low": current["low"],
                "high": current["high"],
                "midpoint": (current["low"] + current["high"]) / 2.0,
            }
            candle3.append(
                {
                    "type": "C3_EXPANSION",
                    "direction": direction,
                    **_event_candle(current),
                    "after_candle2_time": c2_event["time"],
                    "after_candle2_time_utc": c2_event.get("time_utc"),
                    "closed_outside_candle2_range": True,
                    "candle2_equilibrium": c2_event["equilibrium"],
                    "eq_respected": eq_respected,
                    "quality": (
                        "TEXTBOOK_EQ_RESPECTED"
                        if eq_respected
                        else "EXPANSION_EQ_NOT_RESPECTED"
                    ),
                    "invalidation_level": invalidation_level,
                    "equilibrium": c3_eq,
                }
            )

    return candle2, candle3, sweep_displacement


def closure_sequence_summary(
    closed_candles: list[dict],
    candle2: list[dict],
    candle3: list[dict],
) -> dict:
    """Describe the current C2 -> C3 -> C4/C5 phase without hiding events."""

    if not closed_candles or not candle2:
        return {
            "state": "NO_C2_IN_LOADED_WINDOW",
            "candle_number": None,
            "direction": "UNDETERMINED",
            "directions": [],
            "bars_since_c2": None,
            "c3_confirmed": False,
            "c3_eq_respected": None,
            "latest_c2_time": None,
            "latest_c2_time_utc": None,
            "opposite_c2_after_confirmed_sequence": None,
            "phase_is_chronological_not_trade_confirmation": True,
        }

    latest_time = max(event["time"] for event in candle2)
    latest_events = [event for event in candle2 if event["time"] == latest_time]
    directions = sorted({event["direction"] for event in latest_events})
    latest_closed_time = closed_candles[-1]["time"]
    index_by_time = {
        candle["time"]: index for index, candle in enumerate(closed_candles)
    }
    bars_since = (
        index_by_time[latest_closed_time] - index_by_time[latest_time]
        if latest_time in index_by_time
        else None
    )
    matching_c3 = [
        event for event in candle3
        if event.get("after_candle2_time") == latest_time
    ]
    confirmed_directions = sorted(
        {event["direction"] for event in matching_c3}
    )

    if len(confirmed_directions) == 1:
        direction = confirmed_directions[0]
    elif len(directions) == 1:
        direction = directions[0]
    else:
        direction = "TWO_SIDED"

    if bars_since == 0:
        state = "C2_CLOSED_AWAITING_C3"
        candle_number = 2
    elif matching_c3:
        candle_number = bars_since + 2 if isinstance(bars_since, int) else 3
        if candle_number == 3:
            state = "C3_CONFIRMED"
        elif candle_number == 4:
            state = "C4_EXPECTATION_PHASE"
        elif candle_number == 5:
            state = "C5_LATE_SEQUENCE_PHASE"
        else:
            state = "POST_C5_SEQUENCE"
    else:
        candle_number = bars_since + 2 if isinstance(bars_since, int) else None
        state = "C3_NOT_CONFIRMED"

    latest_confirmed = max(candle3, key=lambda event: event["time"], default=None)
    opposite_event = None
    if latest_confirmed is not None:
        opposite = (
            "BEARISH"
            if latest_confirmed["direction"] == "BULLISH"
            else "BULLISH"
        )
        candidates = [
            event for event in candle2
            if event["time"] > latest_confirmed["time"]
            and event["direction"] == opposite
        ]
        if candidates:
            event = candidates[-1]
            opposite_event = {
                "direction": event["direction"],
                "time": event["time"],
                "time_utc": event.get("time_utc"),
            }

    c3_eq_states = [event.get("eq_respected") for event in matching_c3]

    return {
        "state": state,
        "candle_number": candle_number,
        "direction": direction,
        "directions": directions,
        "bars_since_c2": bars_since,
        "c3_confirmed": bool(matching_c3),
        "c3_confirmed_directions": confirmed_directions,
        "c3_eq_respected": (
            any(value is True for value in c3_eq_states)
            if c3_eq_states
            else None
        ),
        "latest_c2_time": latest_events[-1]["time"],
        "latest_c2_time_utc": latest_events[-1].get("time_utc"),
        "opposite_c2_after_confirmed_sequence": opposite_event,
        "phase_is_chronological_not_trade_confirmation": True,
    }
