# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from candle_closure import closure_sequence_summary, detect_candle_closures
from luxalgo_structure import (
    luxalgo_market_structure,
    reference_bias_signal,
    reference_structure_summary,
)


BASE = "https://api.mexc.com"
FILTER_LENGTH = 12
WICK_PERCENT = 40
LUX_STRUCTURE_RECENT_LIMIT = 20
GPT_ACTION_CHARACTER_LIMIT = 100_000
GPT_ACTION_SAFE_TARGET = 80_000

TIMEFRAMES = {
    "1m": ("Min1", 60, 300),
    "15m": ("Min15", 900, 300),
    "1h": ("Min60", 3600, 300),
    "4h": ("Hour4", 14400, 300),
    "1d": ("Day1", 86400, 180),
    "1w": ("Week1", 604800, 104),
}

SUPPORTED_SYMBOLS = {
    "TAO_USDT",
    "HYPE_USDT",
    "XRP_USDT",
    "SOL_USDT",
    "DOGE_USDT",
    "ETH_USDT",
    "BTC_USDT",
}


def normalize_symbol(value: str | None) -> str:
    """Приводит TAO, tao_usdt, SOLUSDT и похожие записи к формату MEXC."""
    symbol = (value or "TAO_USDT").strip().upper()
    symbol = symbol.replace("-", "_").replace("/", "_")

    if symbol.endswith("USDT") and not symbol.endswith("_USDT"):
        symbol = symbol[:-4] + "_USDT"

    if "_" not in symbol:
        symbol += "_USDT"

    if symbol not in SUPPORTED_SYMBOLS:
        allowed = ", ".join(sorted(SUPPORTED_SYMBOLS))
        raise ValueError(
            f"Unsupported symbol: {symbol}. Supported symbols: {allowed}"
        )

    return symbol


def request_json(path: str, params: dict | None = None):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = BASE + path + query

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MEXC-Live-Analyst-V6/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
            context=ssl.create_default_context(),
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"MEXC connection error: {exc!r}") from exc

    if not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"Bad MEXC response: {data}")

    return data


def utc(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def ticker(symbol: str) -> dict:
    data = request_json(
        "/api/v1/contract/ticker",
        {"symbol": symbol},
    ).get("data")

    if isinstance(data, list):
        data = next(
            (item for item in data if item.get("symbol") == symbol),
            None,
        )

    if not isinstance(data, dict):
        raise RuntimeError(f"Ticker unavailable for {symbol}")

    return data


def candles(
    symbol: str,
    api_timeframe: str,
    seconds: int,
    count: int,
) -> list[dict]:
    now = int(time.time())
    start = now - seconds * (count + 10)

    data = request_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": api_timeframe,
            "start": start,
            "end": now,
        },
    ).get("data")

    keys = ("time", "open", "high", "low", "close", "vol")

    if not isinstance(data, dict) or any(key not in data for key in keys):
        raise RuntimeError(
            f"Klines unavailable for {symbol} {api_timeframe}"
        )

    length = min(len(data[key]) for key in keys)
    output = []

    for index in range(length):
        timestamp = int(data["time"][index])

        output.append(
            {
                "time": timestamp,
                "time_utc": utc(timestamp),
                "open": float(data["open"][index]),
                "high": float(data["high"][index]),
                "low": float(data["low"][index]),
                "close": float(data["close"][index]),
                "volume": float(data["vol"][index]),
            }
        )

    return sorted(
        output,
        key=lambda candle: candle["time"],
    )[-count:]


def detect(
    candles_list: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    return detect_candle_closures(
        candles_list,
        filter_length=FILTER_LENGTH,
        wick_percent=WICK_PERCENT,
    )


def split_closed_and_live(
    candles_list: list[dict],
    seconds: int,
    now: int,
) -> tuple[list[dict], dict | None]:
    closed = [
        candle
        for candle in candles_list
        if candle["time"] + seconds <= now
    ]

    live = next(
        (
            candle
            for candle in reversed(candles_list)
            if candle["time"] + seconds > now
        ),
        None,
    )

    return closed, live



DIRECTIONAL_BIASES = {"BULLISH", "BEARISH"}

MTF_ROLE_MAP = {
    "1w": "BROAD_CONTEXT",
    "1d": "BROAD_CONTEXT",
    "4h": "HIGHER_TIMEFRAME_CONTEXT",
    "1h": "SESSION_DIRECTION",
    "15m": "SETUP_TIMEFRAME",
    "1m": "ENTRY_CONFIRMATION",
}


def _latest_trigger(timeframe_data: dict) -> dict:
    candle2_items = timeframe_data.get("recent_candle2") or []
    candle3_items = timeframe_data.get("recent_candle3") or []
    sequence = timeframe_data.get("closure_sequence") or {}
    latest_c2 = candle2_items[-1] if candle2_items else None
    latest_c3 = candle3_items[-1] if candle3_items else None

    c2_time = latest_c2.get("time", -1) if isinstance(latest_c2, dict) else -1
    c3_time = latest_c3.get("time", -1) if isinstance(latest_c3, dict) else -1

    def calculated_bars_since(event_time, explicit_value):
        if isinstance(explicit_value, int):
            return explicit_value
        latest_closed = timeframe_data.get("latest_closed_candle") or {}
        latest_time = latest_closed.get("time")
        seconds = timeframe_data.get("seconds_per_candle")
        if (
            isinstance(event_time, int)
            and isinstance(latest_time, int)
            and isinstance(seconds, int)
            and seconds > 0
            and latest_time >= event_time
        ):
            return int((latest_time - event_time) / seconds)
        return None

    def event_ref(event):
        if not isinstance(event, dict):
            return None
        return {
            "type": event.get("type"),
            "direction": event.get("direction"),
            "time": event.get("time"),
            "time_utc": event.get("time_utc"),
        }

    shared = {
        "latest_c2": event_ref(latest_c2),
        "latest_c3": event_ref(latest_c3),
        "closure_sequence": sequence,
        "freshness_rule_bars": 3,
    }

    if c3_time >= c2_time and c3_time >= 0:
        bars_since = calculated_bars_since(
            c3_time,
            timeframe_data.get("bars_since_latest_candle3"),
        )
        is_fresh = isinstance(bars_since, int) and bars_since <= 3
        return {
            "type": "C3_CONFIRMED",
            "direction": latest_c3.get("direction", "UNDETERMINED"),
            "time": latest_c3.get("time"),
            "time_utc": latest_c3.get("time_utc"),
            "bars_since": bars_since,
            "is_fresh": is_fresh,
            "is_fresh_for_entry_scan": is_fresh,
            "eq_respected": latest_c3.get("eq_respected"),
            "quality": latest_c3.get("quality"),
            **shared,
        }

    if c2_time >= 0:
        bars_since = calculated_bars_since(
            c2_time,
            timeframe_data.get("bars_since_latest_candle2"),
        )
        confirmed = timeframe_data.get("latest_candle2_confirmed_by_candle3")
        if confirmed is None:
            confirmed = any(
                (
                    item.get("after_candle2_time") == latest_c2.get("time")
                    or item.get("after_candle2_time_utc")
                    == latest_c2.get("time_utc")
                )
                for item in candle3_items
                if isinstance(item, dict)
            )
        is_fresh = isinstance(bars_since, int) and bars_since <= 3
        return {
            "type": (
                "C2_CONFIRMED_BY_C3" if confirmed else "C2_UNCONFIRMED"
            ),
            "direction": latest_c2.get("direction", "UNDETERMINED"),
            "time": latest_c2.get("time"),
            "time_utc": latest_c2.get("time_utc"),
            "bars_since": bars_since,
            "is_fresh": is_fresh,
            "is_fresh_for_entry_scan": is_fresh,
            **shared,
        }

    return {
        "type": "NONE",
        "direction": "UNDETERMINED",
        "time": None,
        "time_utc": None,
        "bars_since": None,
        "is_fresh": False,
        "is_fresh_for_entry_scan": False,
        **shared,
    }


def _timeframe_signal(timeframe: str, timeframe_data: dict | None) -> dict:
    if not isinstance(timeframe_data, dict):
        return {
            "status": "insufficient_data",
            "timeframe": timeframe,
            "role": MTF_ROLE_MAP.get(timeframe, "UNASSIGNED"),
            "primary_direction": "UNDETERMINED",
            "direction_source": "NONE",
            "confidence": "INSUFFICIENT_DATA",
            "strategic_direction": "UNDETERMINED",
            "operational_direction": "UNDETERMINED",
            "structure_relation": "PARTIAL_REFERENCE_DATA",
            "latest_swing_event": None,
            "latest_internal_event": None,
            "latest_trigger": _latest_trigger({}),
            "closure_sequence": {},
            "opposite_closure_to_primary_direction": {
                "active": False,
                "direction": None,
                "reason": "NO_PRIMARY_DIRECTION",
            },
            "internal_conflicts": [],
        }

    bias = reference_bias_signal(timeframe_data.get("reference_structure"))
    primary = bias["primary_direction"]
    source = bias["direction_source"]
    confidence = bias["confidence"]
    conflicts = bias["internal_conflicts"]

    sequence = timeframe_data.get("closure_sequence") or {}
    latest_c2_directions = sequence.get("directions") or []
    opposite_direction = (
        "BEARISH" if primary == "BULLISH"
        else "BULLISH" if primary == "BEARISH"
        else None
    )
    sequence_bars_since = sequence.get("bars_since_c2")
    opposite_is_recent = (
        opposite_direction in latest_c2_directions
        and isinstance(sequence_bars_since, int)
        and sequence_bars_since <= 3
    )
    opposite_warning = {
        "active": opposite_is_recent,
        "direction": opposite_direction if opposite_is_recent else None,
        "bars_since_c2": (
            sequence_bars_since if opposite_is_recent else None
        ),
        "reason": (
            "RECENT_C2_OPPOSES_PRIMARY_DIRECTION"
            if opposite_is_recent
            else "NONE"
        ),
    }

    return {
        "status": "ok" if primary != "UNDETERMINED" else "partial",
        "timeframe": timeframe,
        "role": MTF_ROLE_MAP.get(timeframe, "UNASSIGNED"),
        "primary_direction": primary,
        "direction_source": source,
        "confidence": confidence,
        "strategic_direction": bias["strategic_direction"],
        "operational_direction": bias["operational_direction"],
        "structure_relation": bias["structure_relation"],
        "latest_swing_event": bias["latest_swing_event"],
        "latest_internal_event": bias["latest_internal_event"],
        "latest_trigger": _latest_trigger(timeframe_data),
        "closure_sequence": sequence,
        "opposite_closure_to_primary_direction": opposite_warning,
        "internal_conflicts": conflicts,
    }


def _consensus(signals: dict[str, dict], timeframes: list[str]) -> dict:
    directional = [
        signals[timeframe]["primary_direction"]
        for timeframe in timeframes
        if timeframe in signals
        and signals[timeframe]["primary_direction"] in DIRECTIONAL_BIASES
    ]

    if not directional:
        direction = "UNDETERMINED"
        agreement = "NO_DIRECTIONAL_DATA"
    elif all(item == directional[0] for item in directional):
        direction = directional[0]
        agreement = "ALIGNED" if len(directional) > 1 else "SINGLE_SOURCE"
    else:
        direction = "MIXED"
        agreement = "CONFLICT"

    return {
        "direction": direction,
        "agreement": agreement,
        "timeframes_used": timeframes,
        "directional_sources": len(directional),
    }


def _single_bias(signals: dict[str, dict], timeframe: str) -> dict:
    signal = signals.get(timeframe) or _timeframe_signal(timeframe, None)
    return {
        "direction": signal["primary_direction"],
        "timeframe": timeframe,
        "direction_source": signal["direction_source"],
        "confidence": signal["confidence"],
        "internal_conflicts": signal["internal_conflicts"],
    }


def _alignment_state(signals: dict[str, dict]) -> str:
    h4 = signals["4h"]["primary_direction"]
    h1 = signals["1h"]["primary_direction"]
    m15 = signals["15m"]["primary_direction"]
    m1 = signals["1m"]["primary_direction"]

    core = (h4, h1, m15)
    if any(item not in DIRECTIONAL_BIASES for item in core):
        return "INSUFFICIENT_DATA"

    if h4 == h1 == m15 == m1 == "BULLISH":
        return "FULL_BULLISH_ALIGNMENT"
    if h4 == h1 == m15 == m1 == "BEARISH":
        return "FULL_BEARISH_ALIGNMENT"

    if h4 == h1 == m15 == "BULLISH" and m1 != "BULLISH":
        return "HTF_BULLISH_LTF_PULLBACK"
    if h4 == h1 == m15 == "BEARISH" and m1 != "BEARISH":
        return "HTF_BEARISH_LTF_PULLBACK"

    if h4 == h1 == "BULLISH" and m15 == "BEARISH":
        return "HTF_BULLISH_LTF_PULLBACK"
    if h4 == h1 == "BEARISH" and m15 == "BULLISH":
        return "HTF_BEARISH_LTF_PULLBACK"

    if h1 == m15 and h1 in DIRECTIONAL_BIASES and h1 != h4:
        return "LTF_REVERSAL_ATTEMPT"

    return "MIXED_CONFLICT"


def _trade_preference(
    alignment_state: str,
    signals: dict[str, dict],
    broad_context: dict,
) -> dict:
    broad_direction = broad_context.get("direction", "UNDETERMINED")

    if alignment_state == "FULL_BULLISH_ALIGNMENT":
        preference = {
            "direction": "LONG",
            "mode": "WITH_INTRADAY_TREND",
            "requires_entry_confirmation": True,
            "reason": "4h, 1h, 15m and 1m are bullish; a fresh 1m trigger is still required.",
        }
    elif alignment_state == "FULL_BEARISH_ALIGNMENT":
        preference = {
            "direction": "SHORT",
            "mode": "WITH_INTRADAY_TREND",
            "requires_entry_confirmation": True,
            "reason": "4h, 1h, 15m and 1m are bearish; a fresh 1m trigger is still required.",
        }
    elif alignment_state == "HTF_BULLISH_LTF_PULLBACK":
        preference = {
            "direction": "LONG",
            "mode": "WAIT_FOR_BULLISH_ENTRY_CONFIRMATION",
            "requires_entry_confirmation": True,
            "reason": "Higher trading timeframes are bullish while a lower timeframe is pulling back.",
        }
    elif alignment_state == "HTF_BEARISH_LTF_PULLBACK":
        preference = {
            "direction": "SHORT",
            "mode": "WAIT_FOR_BEARISH_ENTRY_CONFIRMATION",
            "requires_entry_confirmation": True,
            "reason": "Higher trading timeframes are bearish while a lower timeframe is pulling back.",
        }
    elif alignment_state == "LTF_REVERSAL_ATTEMPT":
        attempted_direction = signals["15m"]["primary_direction"]
        return {
            "direction": "WAIT",
            "mode": "REVERSAL_NOT_CONFIRMED_BY_4H",
            "requires_entry_confirmation": True,
            "possible_reversal_direction": attempted_direction,
            "broad_context_direction": broad_direction,
            "broad_context_caution": True,
            "reason": "1h and 15m oppose 4h; treat this as an attempt, not an established reversal.",
        }
    elif alignment_state == "INSUFFICIENT_DATA":
        return {
            "direction": "UNDETERMINED",
            "mode": "NO_RELIABLE_HIERARCHY",
            "requires_entry_confirmation": True,
            "broad_context_direction": broad_direction,
            "broad_context_caution": True,
            "reason": "At least one core timeframe has no directional state.",
        }
    else:
        return {
            "direction": "WAIT",
            "mode": "CONFLICTING_TIMEFRAMES",
            "requires_entry_confirmation": True,
            "broad_context_direction": broad_direction,
            "broad_context_caution": True,
            "reason": "4h, 1h and 15m do not form a clean hierarchy.",
        }

    preferred_bias = {
        "LONG": "BULLISH",
        "SHORT": "BEARISH",
    }[preference["direction"]]
    counter_broad_context = (
        broad_direction in DIRECTIONAL_BIASES
        and broad_direction != preferred_bias
    )

    preference["broad_context_direction"] = broad_direction
    preference["broad_context_caution"] = (
        counter_broad_context or broad_direction in {"MIXED", "UNDETERMINED"}
    )

    if counter_broad_context:
        if preference["mode"].startswith("WAIT_FOR_"):
            preference["mode"] += "_COUNTER_BROAD_CONTEXT"
        else:
            preference["mode"] = (
                "INTRADAY_DIRECTION_COUNTER_BROAD_CONTEXT"
            )
        preference["requires_entry_confirmation"] = True
        preference["reason"] += (
            " Daily/weekly context is opposite, so conviction must be reduced."
        )

    return preference


def _execution_state(
    preference: dict,
    entry_signal: dict,
) -> dict:
    preferred_direction = {
        "LONG": "BULLISH",
        "SHORT": "BEARISH",
    }.get(preference.get("direction"))

    entry_direction = entry_signal["primary_direction"]
    trigger = entry_signal["latest_trigger"]

    if preferred_direction is None:
        relation = "NOT_APPLICABLE"
        state = "NO_DIRECTIONAL_PREFERENCE"
    elif entry_direction == preferred_direction:
        relation = "ALIGNED"
        if (
            trigger["is_fresh"]
            and trigger["direction"] == preferred_direction
            and trigger["type"] in {"C3_CONFIRMED", "C2_CONFIRMED_BY_C3"}
        ):
            state = "FRESH_ENTRY_CONFIRMATION"
        elif (
            trigger["is_fresh"]
            and trigger["direction"] == preferred_direction
            and trigger["type"] == "C2_UNCONFIRMED"
        ):
            state = "WAITING_FOR_C3_CONFIRMATION"
        else:
            state = "ENTRY_BIAS_ALIGNED_NO_FRESH_TRIGGER"
    elif entry_direction in DIRECTIONAL_BIASES:
        relation = "OPPOSED"
        state = "ENTRY_TIMEFRAME_OPPOSED"
    else:
        relation = "UNDETERMINED"
        state = "ENTRY_TIMEFRAME_UNDETERMINED"

    return {
        "state": state,
        "preferred_direction": preferred_direction or "NONE",
        "entry_timeframe_direction": entry_direction,
        "relation_to_preference": relation,
        "latest_entry_trigger": trigger,
    }


def build_mtf_hierarchy(timeframes: dict[str, dict]) -> dict:
    signals = {
        timeframe: _timeframe_signal(timeframe, timeframes.get(timeframe))
        for timeframe in MTF_ROLE_MAP
    }

    broad_context = _consensus(signals, ["1w", "1d"])
    htf_bias = _single_bias(signals, "4h")
    htf_bias["broad_context_direction"] = broad_context["direction"]
    htf_bias["relation_to_broad_context"] = (
        "ALIGNED"
        if broad_context["direction"] == htf_bias["direction"]
        and htf_bias["direction"] in DIRECTIONAL_BIASES
        else "COUNTER_CONTEXT"
        if broad_context["direction"] in DIRECTIONAL_BIASES
        and htf_bias["direction"] in DIRECTIONAL_BIASES
        and broad_context["direction"] != htf_bias["direction"]
        else "MIXED_OR_UNDETERMINED"
    )

    alignment = _alignment_state(signals)
    preference = _trade_preference(alignment, signals, broad_context)
    execution = _execution_state(preference, signals["1m"])

    conflicts = []
    for timeframe, signal in signals.items():
        for conflict in signal["internal_conflicts"]:
            conflicts.append(
                {
                    "scope": "INTERNAL_TIMEFRAME",
                    "timeframe": timeframe,
                    "type": conflict,
                }
            )

    for higher, lower in [
        ("1w", "1d"),
        ("1d", "4h"),
        ("4h", "1h"),
        ("1h", "15m"),
        ("15m", "1m"),
    ]:
        higher_direction = signals[higher]["primary_direction"]
        lower_direction = signals[lower]["primary_direction"]
        if (
            higher_direction in DIRECTIONAL_BIASES
            and lower_direction in DIRECTIONAL_BIASES
            and higher_direction != lower_direction
        ):
            conflicts.append(
                {
                    "scope": "BETWEEN_TIMEFRAMES",
                    "higher_timeframe": higher,
                    "higher_direction": higher_direction,
                    "lower_timeframe": lower,
                    "lower_direction": lower_direction,
                    "type": "DIRECTIONAL_DISAGREEMENT",
                }
            )

    complete = all(
        signal["primary_direction"] in DIRECTIONAL_BIASES
        for signal in signals.values()
    )

    return {
        "status": "ok" if complete else "partial",
        "method": "deterministic_mtf_luxalgo_v3",
        "closed_candles_only_for_structure": True,
        "role_map": MTF_ROLE_MAP,
        "priority_rule": [
            "LUXALGO_INTERNAL_OPERATIONAL",
            "LUXALGO_SWING_STRATEGIC_CONTEXT",
            "C2_C3_TRIGGER_ONLY",
        ],
        "timeframe_signals": signals,
        "broad_context_bias": broad_context,
        "higher_timeframe_bias": htf_bias,
        "session_timeframe_bias": _single_bias(signals, "1h"),
        "hourly_closure_phase": signals["1h"].get("closure_sequence"),
        "hourly_opposite_closure_warning": signals["1h"].get(
            "opposite_closure_to_primary_direction"
        ),
        "setup_timeframe_bias": _single_bias(signals, "15m"),
        "entry_timeframe_bias": _single_bias(signals, "1m"),
        "alignment_scope": ["4h", "1h", "15m", "1m"],
        "broad_context_excluded_from_alignment_state": True,
        "alignment_state": alignment,
        "trade_direction_preference": preference,
        "execution_state": execution,
        "conflicts": conflicts,
        "scope_note": (
            "This hierarchy organizes context. It does not create an automatic trade entry; "
            "fresh 1m confirmation and risk control remain required."
        ),
    }


def build_higher_timeframe_levels(
    raw_candles: dict[str, list[dict]],
    now: int,
) -> dict:
    daily_closed, current_day = split_closed_and_live(
        raw_candles.get("1d", []),
        TIMEFRAMES["1d"][1],
        now,
    )
    weekly_closed, current_week = split_closed_and_live(
        raw_candles.get("1w", []),
        TIMEFRAMES["1w"][1],
        now,
    )

    previous_day = daily_closed[-1] if daily_closed else None
    previous_week = weekly_closed[-1] if weekly_closed else None

    flat_levels = {
        "current_day_high": (
            current_day.get("high") if current_day else None
        ),
        "current_day_low": (
            current_day.get("low") if current_day else None
        ),
        "previous_day_high": (
            previous_day.get("high") if previous_day else None
        ),
        "previous_day_low": (
            previous_day.get("low") if previous_day else None
        ),
        "current_week_high": (
            current_week.get("high") if current_week else None
        ),
        "current_week_low": (
            current_week.get("low") if current_week else None
        ),
        "previous_week_high": (
            previous_week.get("high") if previous_week else None
        ),
        "previous_week_low": (
            previous_week.get("low") if previous_week else None
        ),
    }

    status = "ok" if all(
        value is not None for value in flat_levels.values()
    ) else "partial"

    return {
        "status": status,
        "timezone": "UTC",
        "current_day": current_day,
        "previous_day": previous_day,
        "current_week": current_week,
        "previous_week": previous_week,
        "levels": flat_levels,
    }


def build(symbol: str) -> dict:
    with ThreadPoolExecutor(max_workers=7) as executor:
        ticker_future = executor.submit(ticker, symbol)

        candle_futures = {
            timeframe: executor.submit(
                candles,
                symbol,
                api_timeframe,
                seconds,
                count,
            )
            for timeframe, (
                api_timeframe,
                seconds,
                count,
            ) in TIMEFRAMES.items()
        }

        ticker_data = ticker_future.result()

        raw_candles = {
            timeframe: future.result()
            for timeframe, future in candle_futures.items()
        }

    now = int(time.time())
    timeframes = {}

    for timeframe, (_, seconds, _) in TIMEFRAMES.items():
        raw = raw_candles[timeframe]
        closed, live = split_closed_and_live(raw, seconds, now)
        candle2, candle3, sweep_displacement = detect(closed)
        sequence = closure_sequence_summary(closed, candle2, candle3)
        luxalgo = luxalgo_market_structure(
            closed,
            recent_limit=LUX_STRUCTURE_RECENT_LIMIT,
        )

        timeframes[timeframe] = {
            "seconds_per_candle": seconds,
            "latest_live_candle": live,
            "latest_closed_candle": closed[-1] if closed else None,
            "recent_closed_candles": closed[-40:],
            "recent_candle2": candle2[-20:],
            "recent_candle3": candle3[-20:],
            "recent_sweep_displacement": sweep_displacement[-20:],
            "closure_sequence": sequence,
            "luxalgo_structure": luxalgo,
            "reference_structure": reference_structure_summary(luxalgo),
        }

    return {
        "ok": True,
        "source": "MEXC Futures public API",
        "mode": "enhanced_snapshot_v6",
        "version": "6.4-luxalgo-structure",
        "symbol": symbol,
        "analysis_role": (
            "MARKET_CONTEXT"
            if symbol == "BTC_USDT"
            else "TRADE_CANDIDATE"
        ),
        "eligible_trade_candidate": symbol != "BTC_USDT",
        "supported_symbols": sorted(SUPPORTED_SYMBOLS),
        "fetched_at_unix": now,
        "fetched_at_utc": utc(now),
        "current_price": ticker_data.get("lastPrice"),
        "high_24h": ticker_data.get("high24Price"),
        "low_24h": ticker_data.get("lower24Price"),
        "settings": {
            "reversal_filter_enabled": True,
            "filter_length": FILTER_LENGTH,
            "wick_percent": WICK_PERCENT,
            "btc_role": "MARKET_CONTEXT_NOT_TRADE_CANDIDATE",
            "timeframes": list(TIMEFRAMES.keys()),
            "luxalgo_smc_structure": {
                "internal_length": 5,
                "swing_length": 50,
                "confluence_filter": False,
                "break_confirmation": "CLOSE_CROSS",
                "role": "REFERENCE_STRUCTURE",
            },
            "legacy_fractal_2_2_active": False,
        },
        "higher_timeframe_levels": build_higher_timeframe_levels(
            raw_candles,
            now,
        ),
        "mtf_hierarchy": build_mtf_hierarchy(timeframes),
        "timeframes": timeframes,
    }


def _action_candle(candle):
    if not isinstance(candle, dict):
        return None

    return {
        "time": candle.get("time"),
        "time_utc": candle.get("time_utc"),
        "open": candle.get("open"),
        "high": candle.get("high"),
        "low": candle.get("low"),
        "close": candle.get("close"),
        "volume": candle.get("volume"),
    }


def _action_lux_event(event):
    if not isinstance(event, dict):
        return None

    pivot = event.get("broken_pivot") or {}
    return {
        "event_type": event.get("event_type"),
        "direction": event.get("direction"),
        "time": event.get("time"),
        "time_utc": event.get("time_utc"),
        "close": event.get("close"),
        "broken_level": pivot.get("level"),
        "broken_pivot_time": pivot.get("time"),
    }


def _action_lux_layer(layer):
    if not isinstance(layer, dict):
        return None

    return {
        "length": layer.get("length"),
        "current_direction": layer.get("current_direction"),
        "current_high": {
            key: (layer.get("current_high") or {}).get(key)
            for key in ("level", "time", "time_utc", "crossed")
        },
        "current_low": {
            key: (layer.get("current_low") or {}).get(key)
            for key in ("level", "time", "time_utc", "crossed")
        },
        "latest_event": _action_lux_event(layer.get("latest_event")),
        "recent_events": [
            _action_lux_event(event)
            for event in (layer.get("recent_events") or [])[-6:]
        ],
    }


def _action_lux_structure(structure):
    if not isinstance(structure, dict):
        return None

    return {
        "method": structure.get("method"),
        "settings": structure.get("settings"),
        "internal": _action_lux_layer(structure.get("internal")),
        "swing": _action_lux_layer(structure.get("swing")),
    }


def _action_closure_sequence(sequence):
    if not isinstance(sequence, dict):
        return None

    return {
        "state": sequence.get("state"),
        "candle_number": sequence.get("candle_number"),
        "direction": sequence.get("direction"),
        "bars_since_c2": sequence.get("bars_since_c2"),
        "c3_confirmed": sequence.get("c3_confirmed"),
        "c3_eq_respected": sequence.get("c3_eq_respected"),
        "opposite_c2_after_confirmed_sequence": sequence.get(
            "opposite_c2_after_confirmed_sequence"
        ),
    }


def _action_trigger(trigger):
    if not isinstance(trigger, dict):
        return None

    latest_c2 = trigger.get("latest_c2") or {}
    latest_c3 = trigger.get("latest_c3") or {}

    return {
        "type": trigger.get("type"),
        "direction": trigger.get("direction"),
        "time": trigger.get("time"),
        "time_utc": trigger.get("time_utc"),
        "bars_since": trigger.get("bars_since"),
        "is_fresh": trigger.get("is_fresh"),
        "is_fresh_for_entry_scan": trigger.get(
            "is_fresh_for_entry_scan"
        ),
        "eq_respected": trigger.get("eq_respected"),
        "quality": trigger.get("quality"),
        "latest_c2": {
            "direction": latest_c2.get("direction"),
            "time": latest_c2.get("time"),
        },
        "latest_c3": {
            "direction": latest_c3.get("direction"),
            "time": latest_c3.get("time"),
        },
    }


def _action_timeframe_signal(signal):
    if not isinstance(signal, dict):
        return None

    return {
        "status": signal.get("status"),
        "timeframe": signal.get("timeframe"),
        "role": signal.get("role"),
        "primary_direction": signal.get("primary_direction"),
        "direction_source": signal.get("direction_source"),
        "confidence": signal.get("confidence"),
        "strategic_direction": signal.get("strategic_direction"),
        "operational_direction": signal.get("operational_direction"),
        "structure_relation": signal.get("structure_relation"),
        "latest_swing_event": _action_lux_event(
            signal.get("latest_swing_event")
        ),
        "latest_internal_event": _action_lux_event(
            signal.get("latest_internal_event")
        ),
        "latest_trigger": _action_trigger(signal.get("latest_trigger")),
        "closure_sequence": _action_closure_sequence(
            signal.get("closure_sequence")
        ),
        "opposite_closure_to_primary_direction": signal.get(
            "opposite_closure_to_primary_direction"
        ),
        "internal_conflicts": signal.get("internal_conflicts"),
    }


def _action_execution_state(state):
    if not isinstance(state, dict):
        return None

    return {
        "state": state.get("state"),
        "preferred_direction": state.get("preferred_direction"),
        "entry_timeframe_direction": state.get(
            "entry_timeframe_direction"
        ),
        "relation_to_preference": state.get(
            "relation_to_preference"
        ),
        "latest_entry_trigger": _action_trigger(
            state.get("latest_entry_trigger")
        ),
    }


def encode_gpt_action_payload(result: dict) -> bytes:
    """Serialize with a safety guard below GPT Actions' 100k limit."""

    text = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(text) >= GPT_ACTION_CHARACTER_LIMIT:
        raise RuntimeError(
            "Compact snapshot response exceeded the GPT Actions "
            f"limit: {len(text)} characters"
        )
    return text.encode("utf-8")


def compact_snapshot_for_gpt_action(full_result: dict) -> dict:
    """Подробный, но ограниченный по размеру snapshot для GPT Actions."""

    hierarchy = full_result.get("mtf_hierarchy") or {}

    output = {
        "ok": full_result.get("ok"),
        "source": full_result.get("source"),
        "mode": "gpt_compact_snapshot_v6",
        "version": "6.4-luxalgo-structure",
        "symbol": full_result.get("symbol"),
        "analysis_role": full_result.get("analysis_role"),
        "eligible_trade_candidate": full_result.get(
            "eligible_trade_candidate"
        ),
        "supported_symbols": full_result.get("supported_symbols"),
        "fetched_at_unix": full_result.get("fetched_at_unix"),
        "fetched_at_utc": full_result.get("fetched_at_utc"),
        "current_price": full_result.get("current_price"),
        "high_24h": full_result.get("high_24h"),
        "low_24h": full_result.get("low_24h"),
        "structure_policy": {
            "authority": "LUXALGO_SWING_INTERNAL_ONLY",
            "operational_direction": "LUXALGO_INTERNAL",
            "strategic_context": "LUXALGO_SWING",
            "legacy_fractal_2_2_active": False,
        },
        "higher_timeframe_levels": full_result.get(
            "higher_timeframe_levels"
        ),
        "mtf_hierarchy": {
            "role_map": hierarchy.get("role_map"),
            "priority_rule": hierarchy.get("priority_rule"),
            "timeframe_signals": {
                timeframe: _action_timeframe_signal(signal)
                for timeframe, signal in (
                    hierarchy.get("timeframe_signals") or {}
                ).items()
            },
            "broad_context_bias": hierarchy.get(
                "broad_context_bias"
            ),
            "higher_timeframe_bias": hierarchy.get(
                "higher_timeframe_bias"
            ),
            "session_timeframe_bias": hierarchy.get(
                "session_timeframe_bias"
            ),
            "hourly_closure_phase": hierarchy.get(
                "hourly_closure_phase"
            ),
            "hourly_opposite_closure_warning": hierarchy.get(
                "hourly_opposite_closure_warning"
            ),
            "setup_timeframe_bias": hierarchy.get(
                "setup_timeframe_bias"
            ),
            "entry_timeframe_bias": hierarchy.get(
                "entry_timeframe_bias"
            ),
            "alignment_state": hierarchy.get("alignment_state"),
            "trade_direction_preference": hierarchy.get(
                "trade_direction_preference"
            ),
            "execution_state": _action_execution_state(
                hierarchy.get("execution_state")
            ),
            "conflicts": hierarchy.get("conflicts"),
            "scope_note": hierarchy.get("scope_note"),
        },
        "timeframes": {},
    }

    for timeframe, block in (
        full_result.get("timeframes") or {}
    ).items():
        output["timeframes"][timeframe] = {
            "luxalgo_structure": _action_lux_structure(
                block.get("luxalgo_structure")
            ),
            "seconds_per_candle": block.get(
                "seconds_per_candle"
            ),
            "latest_live_candle": _action_candle(
                block.get("latest_live_candle")
            ),
            "latest_closed_candle": _action_candle(
                block.get("latest_closed_candle")
            ),
            "recent_closed_candles": [
                _action_candle(candle)
                for candle in (
                    block.get("recent_closed_candles") or []
                )[-5:]
            ],
            "recent_candle2": (
                block.get("recent_candle2") or []
            )[-2:],
            "recent_candle3": (
                block.get("recent_candle3") or []
            )[-2:],
            "recent_sweep_displacement": (
                block.get("recent_sweep_displacement") or []
            )[-2:],
            "closure_sequence": _action_closure_sequence(
                block.get("closure_sequence")
            ),
        }

    return output


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed_url.query)

            requested_symbol = query.get("symbol", ["TAO_USDT"])[0]
            symbol = normalize_symbol(requested_symbol)

            result = compact_snapshot_for_gpt_action(build(symbol))

            body = encode_gpt_action_payload(result)

            status_code = 200

        except ValueError as exc:
            body = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            status_code = 400

        except Exception as exc:
            body = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            status_code = 502

        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
