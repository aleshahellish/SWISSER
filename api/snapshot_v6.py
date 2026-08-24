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
from mtf_decision import build_mtf_decision


BASE = "https://api.mexc.com"
FILTER_LENGTH = 12
WICK_PERCENT = 40
LUX_STRUCTURE_RECENT_LIMIT = 20

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
            "recent_internal_events": [],
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
        "recent_internal_events": bias["recent_internal_events"],
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

    decision = build_mtf_decision(signals, broad_context)

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
        "method": "deterministic_mtf_luxalgo_core",
        "closed_candles_only_for_structure": True,
        "role_map": MTF_ROLE_MAP,
        "priority_rule": [
            "CORE_DIRECTION_1H",
            "SETUP_CONFIRMATION_15M",
            "ENTRY_CONFIRMATION_1M",
            "4H_STRATEGIC_CONTEXT_ONLY",
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
        "alignment_scope": ["1h", "15m", "1m"],
        "strategic_context_scope": ["4h", "1d", "1w"],
        "broad_context_excluded_from_alignment_state": True,
        "four_hour_excluded_from_alignment_state": True,
        "alignment_state": decision["alignment_state"],
        "continuation_bias": decision["continuation_bias"],
        "active_trade_scenario": decision["active_trade_scenario"],
        "strategic_4h_context": decision["strategic_4h_context"],
        "trade_direction_preference": decision[
            "trade_direction_preference"
        ],
        "execution_state": decision["execution_state"],
        "conflicts": conflicts,
        "scope_note": (
            "The active scenario is decided by 1h -> 15m -> 1m. 4h, 1d and "
            "1w are strategic context and cannot overwrite the current trade "
            "label. Fresh 1m confirmation and risk control remain required."
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
        "mode": "swisser_market_snapshot",
        "version": "1.0.0",
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed_url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed_url.query)

            requested_symbol = query.get("symbol", ["TAO_USDT"])[0]
            symbol = normalize_symbol(requested_symbol)

            result = build(symbol)

            body = json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

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
