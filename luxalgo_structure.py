"""LuxAlgo SMC-compatible market-structure detection.

This module is a clean Python implementation of the market-structure rules in
the open-source TradingView script ``Smart Money Concepts [LuxAlgo]``.  It is
limited deliberately to the parts needed by the API: internal/swing pivots,
BOS/CHoCH events, and the current direction of each structure layer.

The user's TradingView settings are represented by ``DEFAULT_SETTINGS``:

* internal structure length: 5 (fixed by the Pine script)
* swing structure length: 50
* confluence filter: disabled
* bullish/bearish structures: all

Order blocks, EQH/EQL, FVGs, label sizes and colors do not change the market
structure events and therefore do not belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


BULLISH = 1
BEARISH = -1

BULLISH_LEG = 1
BEARISH_LEG = 0

DEFAULT_SETTINGS = {
    "internal_length": 5,
    "swing_length": 50,
    "confluence_filter": False,
}

DIRECTIONAL_BIASES = {"BULLISH", "BEARISH"}

USER_TRADINGVIEW_PROFILE = {
    "indicator": "Smart Money Concepts [LuxAlgo]",
    "mode": "HISTORICAL",
    "show_internal_structure": True,
    "internal_events": "ALL",
    "show_swing_structure": True,
    "swing_events": "ALL",
    "break_confirmation": "CLOSE_CROSS",
}


def _direction_name(value: int) -> str:
    if value == BULLISH:
        return "BULLISH"
    if value == BEARISH:
        return "BEARISH"
    return "UNDETERMINED"


@dataclass
class _Pivot:
    current_level: float | None = None
    last_level: float | None = None
    crossed: bool = False
    time: int | None = None
    time_utc: str | None = None
    index: int | None = None

    def update(self, candle: dict[str, Any], index: int) -> None:
        self.last_level = self.current_level
        self.current_level = float(candle["low"])
        self.crossed = False
        self.time = int(candle["time"])
        self.time_utc = candle.get("time_utc")
        self.index = index

    def update_high(self, candle: dict[str, Any], index: int) -> None:
        self.last_level = self.current_level
        self.current_level = float(candle["high"])
        self.crossed = False
        self.time = int(candle["time"])
        self.time_utc = candle.get("time_utc")
        self.index = index

    def public(self) -> dict[str, Any] | None:
        if self.current_level is None:
            return None
        return {
            "level": self.current_level,
            "previous_level": self.last_level,
            "crossed": self.crossed,
            "time": self.time,
            "time_utc": self.time_utc,
            "index": self.index,
        }


class _StructureLayer:
    def __init__(self, name: str, length: int) -> None:
        if length < 1:
            raise ValueError("Structure length must be positive")
        self.name = name
        self.length = length
        self.leg = BEARISH_LEG
        self.high = _Pivot()
        self.low = _Pivot()
        self.trend = 0
        self.events: list[dict[str, Any]] = []

    def update_leg_and_pivot(
        self,
        candles: list[dict[str, Any]],
        current_index: int,
    ) -> tuple[float | None, float | None]:
        """Mirror LuxAlgo's ``leg`` and ``getCurrentStructure`` functions.

        The candidate is ``length`` bars old and is compared with the bars
        that followed it.  A pivot is stored only when the detected leg flips.
        The returned values are the previous-bar pivot levels, which Pine uses
        in ``ta.crossover``/``ta.crossunder``.
        """

        previous_high_level = self.high.current_level
        previous_low_level = self.low.current_level

        if current_index < self.length:
            return previous_high_level, previous_low_level

        candidate_index = current_index - self.length
        candidate = candles[candidate_index]
        following = candles[candidate_index + 1:current_index + 1]

        new_leg_high = float(candidate["high"]) > max(
            float(candle["high"]) for candle in following
        )
        new_leg_low = float(candidate["low"]) < min(
            float(candle["low"]) for candle in following
        )

        new_leg = self.leg
        if new_leg_high:
            new_leg = BEARISH_LEG
        elif new_leg_low:
            new_leg = BULLISH_LEG

        if new_leg != self.leg:
            if new_leg == BULLISH_LEG:
                self.low.update(candidate, candidate_index)
            else:
                self.high.update_high(candidate, candidate_index)

        self.leg = new_leg
        return previous_high_level, previous_low_level

    def _event(
        self,
        direction: int,
        candle: dict[str, Any],
        candle_index: int,
        pivot: _Pivot,
    ) -> None:
        event_type = (
            "CHOCH"
            if (direction == BULLISH and self.trend == BEARISH)
            or (direction == BEARISH and self.trend == BULLISH)
            else "BOS"
        )
        self.events.append(
            {
                "event_type": event_type,
                "direction": _direction_name(direction),
                "time": int(candle["time"]),
                "time_utc": candle.get("time_utc"),
                "close": float(candle["close"]),
                "bar_index": candle_index,
                "broken_pivot": {
                    "level": pivot.current_level,
                    "time": pivot.time,
                    "time_utc": pivot.time_utc,
                    "index": pivot.index,
                },
            }
        )
        pivot.crossed = True
        self.trend = direction

    def detect_breaks(
        self,
        candle: dict[str, Any],
        candle_index: int,
        previous_close: float | None,
        previous_high_level: float | None,
        previous_low_level: float | None,
        swing_layer: "_StructureLayer",
        confluence_filter: bool,
    ) -> None:
        if previous_close is None:
            return

        close = float(candle["close"])
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])

        bullish_bar = True
        bearish_bar = True
        if confluence_filter:
            # Preserve the expressions used by the published Pine script.
            bullish_bar = high - max(close, open_price) > min(
                close,
                open_price - low,
            )
            bearish_bar = high - max(close, open_price) < min(
                close,
                open_price - low,
            )

        high_is_distinct = (
            self.name != "internal"
            or self.high.current_level != swing_layer.high.current_level
        )
        bullish_cross = (
            self.high.current_level is not None
            and previous_high_level is not None
            and close > self.high.current_level
            and previous_close <= previous_high_level
        )
        if (
            bullish_cross
            and not self.high.crossed
            and high_is_distinct
            and bullish_bar
        ):
            self._event(
                BULLISH,
                candle,
                candle_index,
                self.high,
            )

        low_is_distinct = (
            self.name != "internal"
            or self.low.current_level != swing_layer.low.current_level
        )
        bearish_cross = (
            self.low.current_level is not None
            and previous_low_level is not None
            and close < self.low.current_level
            and previous_close >= previous_low_level
        )
        if (
            bearish_cross
            and not self.low.crossed
            and low_is_distinct
            and bearish_bar
        ):
            self._event(
                BEARISH,
                candle,
                candle_index,
                self.low,
            )

    def public(self, recent_limit: int) -> dict[str, Any]:
        latest = self.events[-1] if self.events else None
        return {
            "length": self.length,
            "current_direction": _direction_name(self.trend),
            "latest_event": latest,
            "current_high": self.high.public(),
            "current_low": self.low.public(),
            "event_count": len(self.events),
            "recent_events": self.events[-recent_limit:],
        }


def luxalgo_market_structure(
    candles: Iterable[dict[str, Any]],
    *,
    internal_length: int = DEFAULT_SETTINGS["internal_length"],
    swing_length: int = DEFAULT_SETTINGS["swing_length"],
    confluence_filter: bool = DEFAULT_SETTINGS["confluence_filter"],
    recent_limit: int = 8,
) -> dict[str, Any]:
    """Return LuxAlgo-compatible internal and swing market structure.

    ``candles`` must contain closed OHLC candles in chronological order.  Each
    candle needs ``time``, ``open``, ``high``, ``low`` and ``close`` fields;
    ``time_utc`` is retained when present.
    """

    ordered = sorted(
        (dict(candle) for candle in candles),
        key=lambda candle: int(candle["time"]),
    )
    swing = _StructureLayer("swing", swing_length)
    internal = _StructureLayer("internal", internal_length)

    previous_close: float | None = None
    for index, candle in enumerate(ordered):
        swing_previous = swing.update_leg_and_pivot(ordered, index)
        internal_previous = internal.update_leg_and_pivot(ordered, index)

        # The Pine script processes internal breaks before swing breaks.
        internal.detect_breaks(
            candle,
            index,
            previous_close,
            *internal_previous,
            swing_layer=swing,
            confluence_filter=confluence_filter,
        )
        swing.detect_breaks(
            candle,
            index,
            previous_close,
            *swing_previous,
            swing_layer=swing,
            confluence_filter=False,
        )
        previous_close = float(candle["close"])

    return {
        "status": "ok" if ordered else "insufficient_data",
        "method": "LUXALGO_SMC_OPEN_SOURCE_V7_STRUCTURE_PARITY",
        "source_scope": "INTERNAL_AND_SWING_BOS_CHOCH_ONLY",
        "closed_candles_only": True,
        "settings": {
            "internal_length": internal_length,
            "swing_length": swing_length,
            "confluence_filter": confluence_filter,
        },
        "tradingview_profile": dict(USER_TRADINGVIEW_PROFILE),
        "candles_processed": len(ordered),
        "internal": internal.public(recent_limit),
        "swing": swing.public(recent_limit),
    }


def reference_structure_summary(
    luxalgo: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the only two layers allowed to define market structure."""

    luxalgo = luxalgo if isinstance(luxalgo, dict) else {}
    swing = luxalgo.get("swing") or {}
    internal = luxalgo.get("internal") or {}
    swing_direction = swing.get("current_direction", "UNDETERMINED")
    internal_direction = internal.get(
        "current_direction",
        "UNDETERMINED",
    )

    if internal_direction in DIRECTIONAL_BIASES:
        operational_direction = internal_direction
        operational_source = "LUXALGO_INTERNAL"
    elif swing_direction in DIRECTIONAL_BIASES:
        operational_direction = swing_direction
        operational_source = "LUXALGO_SWING_FALLBACK"
    else:
        operational_direction = "UNDETERMINED"
        operational_source = "NONE"

    if (
        swing_direction in DIRECTIONAL_BIASES
        and internal_direction in DIRECTIONAL_BIASES
    ):
        relation = (
            "SWING_INTERNAL_ALIGNED"
            if swing_direction == internal_direction
            else "INTERNAL_COUNTER_SWING"
        )
    else:
        relation = "PARTIAL_REFERENCE_DATA"

    return {
        "method": "LUXALGO_SWING_INTERNAL_REFERENCE_V1",
        "only_bos_choch_authority": True,
        "operational_direction": operational_direction,
        "operational_direction_source": operational_source,
        "relation": relation,
        "strategic": {
            "layer": "LUXALGO_SWING",
            "role": "STRATEGIC_CONTEXT",
            "length": swing.get("length"),
            "direction": swing_direction,
            "latest_event": swing.get("latest_event"),
        },
        "operational": {
            "layer": "LUXALGO_INTERNAL",
            "role": "TIMEFRAME_OPERATIONAL_STRUCTURE",
            "length": internal.get("length"),
            "direction": internal_direction,
            "latest_event": internal.get("latest_event"),
        },
    }


def reference_bias_signal(
    reference_structure: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a directional signal exclusively from LuxAlgo structure."""

    reference_structure = (
        reference_structure
        if isinstance(reference_structure, dict)
        else {}
    )
    strategic = reference_structure.get("strategic") or {}
    operational = reference_structure.get("operational") or {}
    strategic_direction = strategic.get("direction", "UNDETERMINED")
    operational_direction = operational.get(
        "direction",
        "UNDETERMINED",
    )
    primary = reference_structure.get(
        "operational_direction",
        "UNDETERMINED",
    )
    source = reference_structure.get(
        "operational_direction_source",
        "NONE",
    )

    conflicts = []
    if (
        strategic_direction in DIRECTIONAL_BIASES
        and operational_direction in DIRECTIONAL_BIASES
        and strategic_direction != operational_direction
    ):
        conflicts.append("LUXALGO_INTERNAL_COUNTER_SWING")

    if primary not in DIRECTIONAL_BIASES:
        confidence = "INSUFFICIENT_DATA"
    elif (
        strategic_direction == operational_direction == primary
        and source == "LUXALGO_INTERNAL"
    ):
        confidence = "HIGH"
    elif source == "LUXALGO_INTERNAL":
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "primary_direction": primary,
        "direction_source": source,
        "confidence": confidence,
        "strategic_direction": strategic_direction,
        "operational_direction": operational_direction,
        "structure_relation": reference_structure.get(
            "relation",
            "PARTIAL_REFERENCE_DATA",
        ),
        "latest_swing_event": strategic.get("latest_event"),
        "latest_internal_event": operational.get("latest_event"),
        "internal_conflicts": conflicts,
    }
