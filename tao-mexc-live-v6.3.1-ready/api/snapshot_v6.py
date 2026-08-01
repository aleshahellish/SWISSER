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


BASE = "https://api.mexc.com"
FILTER_LENGTH = 12
WICK_PERCENT = 40
SWING_LEFT_BARS = 2
SWING_RIGHT_BARS = 2
SWING_RECENT_LIMIT = 8

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



def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_swings(
    closed_candles: list[dict],
    left_bars: int = SWING_LEFT_BARS,
    right_bars: int = SWING_RIGHT_BARS,
) -> tuple[list[dict], list[dict]]:
    """Подтверждённые swing high/low по закрытым свечам.

    Центральная свеча считается swing только после появления right_bars
    закрытых свечей справа. Равные экстремумы не считаются swing, чтобы
    не смешивать swings с будущим модулем equal highs/equal lows.
    """
    swing_highs = []
    swing_lows = []

    if len(closed_candles) < left_bars + right_bars + 1:
        return swing_highs, swing_lows

    for index in range(left_bars, len(closed_candles) - right_bars):
        center = closed_candles[index]
        left = closed_candles[index - left_bars:index]
        right = closed_candles[index + 1:index + right_bars + 1]
        neighbors = left + right
        confirmation = closed_candles[index + right_bars]

        if all(center["high"] > candle["high"] for candle in neighbors):
            swing_highs.append(
                {
                    "type": "SWING_HIGH",
                    "level": center["high"],
                    "time": center["time"],
                    "time_utc": center["time_utc"],
                    "confirmed_at": confirmation["time"],
                    "confirmed_at_utc": confirmation["time_utc"],
                }
            )

        if all(center["low"] < candle["low"] for candle in neighbors):
            swing_lows.append(
                {
                    "type": "SWING_LOW",
                    "level": center["low"],
                    "time": center["time"],
                    "time_utc": center["time_utc"],
                    "confirmed_at": confirmation["time"],
                    "confirmed_at_utc": confirmation["time_utc"],
                }
            )

    return swing_highs, swing_lows


def swing_summary(
    closed_candles: list[dict],
    current_price,
    recent_limit: int = SWING_RECENT_LIMIT,
) -> dict:
    swing_highs, swing_lows = detect_swings(closed_candles)
    price = number(current_price)

    highs_above = [
        point for point in swing_highs
        if price is not None and point["level"] > price
    ]
    lows_below = [
        point for point in swing_lows
        if price is not None and point["level"] < price
    ]

    nearest_above = (
        min(highs_above, key=lambda point: point["level"])
        if highs_above else None
    )
    nearest_below = (
        max(lows_below, key=lambda point: point["level"])
        if lows_below else None
    )

    status = "ok" if swing_highs and swing_lows else "partial"

    return {
        "status": status,
        "method": "confirmed_fractal_2_left_2_right",
        "confirmed_only": True,
        "left_bars": SWING_LEFT_BARS,
        "right_bars": SWING_RIGHT_BARS,
        "latest_swing_high": swing_highs[-1] if swing_highs else None,
        "latest_swing_low": swing_lows[-1] if swing_lows else None,
        "nearest_swing_high_above_price": nearest_above,
        "nearest_swing_low_below_price": nearest_below,
        "recent_swing_highs": swing_highs[-recent_limit:],
        "recent_swing_lows": swing_lows[-recent_limit:],
    }



def _sequence_label(points: list[dict], high_side: bool) -> dict:
    """Классифицирует последовательность последних подтверждённых swings.

    Для структуры используются только однозначные точки. Dual swing / outside
    bar не участвует, потому что одна широкая свеча не должна одновременно
    задавать самостоятельный high и low структуры.
    """
    usable = [point for point in points if not point.get("ambiguous_outside_bar")]
    recent = usable[-3:]

    if len(recent) < 2:
        return {
            "status": "insufficient_data",
            "label": "INSUFFICIENT_DATA",
            "points_used": recent,
        }

    levels = [point["level"] for point in recent]
    differences = [
        levels[index] - levels[index - 1]
        for index in range(1, len(levels))
    ]

    if all(value > 0 for value in differences):
        label = "HIGHER_HIGHS" if high_side else "HIGHER_LOWS"
        status = "confirmed_sequence"
    elif all(value < 0 for value in differences):
        label = "LOWER_HIGHS" if high_side else "LOWER_LOWS"
        status = "confirmed_sequence"
    elif differences[-1] > 0:
        label = "LAST_HIGHER_HIGH" if high_side else "LAST_HIGHER_LOW"
        status = "mixed_history"
    elif differences[-1] < 0:
        label = "LAST_LOWER_HIGH" if high_side else "LAST_LOWER_LOW"
        status = "mixed_history"
    else:
        label = "EQUAL_LAST_LEVELS"
        status = "mixed_history"

    return {
        "status": status,
        "label": label,
        "levels": levels,
        "points_used": recent,
    }


def _point_level_state(
    point: dict,
    closed_candles: list[dict],
    index_by_time: dict[int, int],
    ambiguous_times: set[int],
) -> dict:
    """Определяет судьбу swing-уровня только по закрытым свечам."""
    output = dict(point)
    output["ambiguous_outside_bar"] = point["time"] in ambiguous_times
    output["eligible_for_structure"] = not output["ambiguous_outside_bar"]

    confirmation_index = index_by_time.get(point.get("confirmed_at"))
    if confirmation_index is None:
        confirmation_index = index_by_time.get(point["time"], -1)

    later = closed_candles[confirmation_index + 1:]
    level = point["level"]
    is_high = point["type"] == "SWING_HIGH"

    if is_high:
        wick_events = [c for c in later if c["high"] > level]
        close_events = [c for c in later if c["close"] > level]
        latest_on_broken_side = bool(
            closed_candles and closed_candles[-1]["close"] > level
        )
        reclaim_events = []
        if close_events:
            first_break_time = close_events[0]["time"]
            reclaim_events = [
                c for c in later
                if c["time"] > first_break_time and c["close"] <= level
            ]
        current_side = "ABOVE" if latest_on_broken_side else "BELOW_OR_AT"
        reclaim_label = "RECLAIMED_BELOW_LEVEL"
    else:
        wick_events = [c for c in later if c["low"] < level]
        close_events = [c for c in later if c["close"] < level]
        latest_on_broken_side = bool(
            closed_candles and closed_candles[-1]["close"] < level
        )
        reclaim_events = []
        if close_events:
            first_break_time = close_events[0]["time"]
            reclaim_events = [
                c for c in later
                if c["time"] > first_break_time and c["close"] >= level
            ]
        current_side = "BELOW" if latest_on_broken_side else "ABOVE_OR_AT"
        reclaim_label = "RECLAIMED_ABOVE_LEVEL"

    if not wick_events:
        state = "ACTIVE_UNTOUCHED"
    elif not close_events:
        state = "WICK_SWEPT_NO_CLOSE_BREAK"
    elif latest_on_broken_side:
        state = "BROKEN_BY_CLOSE"
    else:
        state = reclaim_label

    currently_active = not latest_on_broken_side

    output.update(
        {
            "current_state": state,
            "current_close_side": current_side,
            "is_currently_active": currently_active,
            "ever_wick_breached": bool(wick_events),
            "ever_close_broken": bool(close_events),
            "ever_reclaimed_after_close_break": bool(reclaim_events),
            "first_wick_breach": wick_events[0] if wick_events else None,
            "first_close_break": close_events[0] if close_events else None,
            "last_close_break": close_events[-1] if close_events else None,
            "first_reclaim_after_close_break": (
                reclaim_events[0] if reclaim_events else None
            ),
            "last_reclaim_after_close_break": (
                reclaim_events[-1] if reclaim_events else None
            ),
            "closed_bars_after_confirmation": len(later),
        }
    )

    return output


def swing_structure_summary(
    closed_candles: list[dict],
    current_price,
    recent_limit: int = SWING_RECENT_LIMIT,
) -> dict:
    """Состояние swing-уровней и предварительная swing-структура.

    Это ещё не BOS/CHoCH. Модуль отвечает только на проверяемые вопросы:
    был ли уровень снят тенью, пробит закрытием, возвращён обратно и остаётся
    ли он активным относительно последней закрытой свечи.
    """
    swing_highs, swing_lows = detect_swings(closed_candles)
    price = number(current_price)
    index_by_time = {
        candle["time"]: index
        for index, candle in enumerate(closed_candles)
    }

    high_times = {point["time"] for point in swing_highs}
    low_times = {point["time"] for point in swing_lows}
    ambiguous_times = high_times & low_times

    high_states = [
        _point_level_state(
            point,
            closed_candles,
            index_by_time,
            ambiguous_times,
        )
        for point in swing_highs
    ]
    low_states = [
        _point_level_state(
            point,
            closed_candles,
            index_by_time,
            ambiguous_times,
        )
        for point in swing_lows
    ]

    primary_states = {
        "ACTIVE_UNTOUCHED",
        "WICK_SWEPT_NO_CLOSE_BREAK",
    }
    secondary_states = {
        "RECLAIMED_BELOW_LEVEL",
        "RECLAIMED_ABOVE_LEVEL",
    }

    primary_highs = [
        point for point in high_states
        if (
            point["eligible_for_structure"]
            and point["current_state"] in primary_states
            and price is not None
            and point["level"] > price
        )
    ]
    primary_lows = [
        point for point in low_states
        if (
            point["eligible_for_structure"]
            and point["current_state"] in primary_states
            and price is not None
            and point["level"] < price
        )
    ]
    secondary_highs = [
        point for point in high_states
        if (
            point["eligible_for_structure"]
            and point["current_state"] in secondary_states
            and price is not None
            and point["level"] > price
        )
    ]
    secondary_lows = [
        point for point in low_states
        if (
            point["eligible_for_structure"]
            and point["current_state"] in secondary_states
            and price is not None
            and point["level"] < price
        )
    ]

    primary_highs.sort(key=lambda point: point["level"])
    primary_lows.sort(key=lambda point: point["level"], reverse=True)
    secondary_highs.sort(key=lambda point: point["level"])
    secondary_lows.sort(key=lambda point: point["level"], reverse=True)

    # Совместимые поля nearest_active... теперь всегда отдают сначала
    # свежий основной уровень. Возвращённые уровни используются только как
    # запасной вариант, если свежих уровней с нужной стороны нет.
    selected_highs = primary_highs or secondary_highs
    selected_lows = primary_lows or secondary_lows

    high_sequence = _sequence_label(high_states, high_side=True)
    low_sequence = _sequence_label(low_states, high_side=False)

    bullish_high_labels = {"HIGHER_HIGHS", "LAST_HIGHER_HIGH"}
    bullish_low_labels = {"HIGHER_LOWS", "LAST_HIGHER_LOW"}
    bearish_high_labels = {"LOWER_HIGHS", "LAST_LOWER_HIGH"}
    bearish_low_labels = {"LOWER_LOWS", "LAST_LOWER_LOW"}

    if (
        high_sequence["label"] in bullish_high_labels
        and low_sequence["label"] in bullish_low_labels
    ):
        provisional_bias = "BULLISH"
    elif (
        high_sequence["label"] in bearish_high_labels
        and low_sequence["label"] in bearish_low_labels
    ):
        provisional_bias = "BEARISH"
    elif (
        high_sequence["label"] == "INSUFFICIENT_DATA"
        or low_sequence["label"] == "INSUFFICIENT_DATA"
    ):
        provisional_bias = "INSUFFICIENT_DATA"
    else:
        provisional_bias = "MIXED"

    dual_swings = []
    for timestamp in sorted(ambiguous_times):
        high = next(
            point for point in high_states if point["time"] == timestamp
        )
        low = next(
            point for point in low_states if point["time"] == timestamp
        )
        dual_swings.append(
            {
                "type": "DUAL_SWING_OUTSIDE_BAR",
                "time": timestamp,
                "time_utc": high["time_utc"],
                "high_level": high["level"],
                "low_level": low["level"],
                "excluded_from_structure_sequence": True,
            }
        )

    return {
        "status": (
            "ok" if high_states and low_states else "partial"
        ),
        "method": "confirmed_swings_with_prioritized_level_state",
        "closed_candles_only": True,
        "not_bos_or_choch_yet": False,
        "bos_choch_available_in": "protected_structure.structure_breaks",
        "this_block_role": "SWING_SEQUENCE_AND_LEVEL_STATE_ONLY",
        "provisional_swing_bias": provisional_bias,
        "high_sequence": high_sequence,
        "low_sequence": low_sequence,
        "latest_swing_high_state": high_states[-1] if high_states else None,
        "latest_swing_low_state": low_states[-1] if low_states else None,
        "level_priority_policy": {
            "primary_states": sorted(primary_states),
            "secondary_states": sorted(secondary_states),
            "selection_rule": "PRIMARY_FIRST_SECONDARY_ONLY_IF_NO_PRIMARY",
        },
        "nearest_primary_swing_high_above_price": (
            primary_highs[0] if primary_highs else None
        ),
        "nearest_primary_swing_low_below_price": (
            primary_lows[0] if primary_lows else None
        ),
        "nearest_secondary_swing_high_above_price": (
            secondary_highs[0] if secondary_highs else None
        ),
        "nearest_secondary_swing_low_below_price": (
            secondary_lows[0] if secondary_lows else None
        ),
        "nearest_active_swing_high_above_price": (
            selected_highs[0] if selected_highs else None
        ),
        "nearest_active_swing_low_below_price": (
            selected_lows[0] if selected_lows else None
        ),
        "primary_active_swing_highs_above_price": (
            primary_highs[:recent_limit]
        ),
        "primary_active_swing_lows_below_price": (
            primary_lows[:recent_limit]
        ),
        "secondary_reclaimed_swing_highs_above_price": (
            secondary_highs[:recent_limit]
        ),
        "secondary_reclaimed_swing_lows_below_price": (
            secondary_lows[:recent_limit]
        ),
        "active_swing_highs_above_price": (
            (primary_highs + secondary_highs)[:recent_limit]
        ),
        "active_swing_lows_below_price": (
            (primary_lows + secondary_lows)[:recent_limit]
        ),
        "recent_swing_high_states": high_states[-recent_limit:],
        "recent_swing_low_states": low_states[-recent_limit:],
        "dual_swing_outside_bars": dual_swings[-recent_limit:],
    }

def _eligible_swings(closed_candles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Возвращает только однозначные подтверждённые swings."""
    swing_highs, swing_lows = detect_swings(closed_candles)
    high_times = {point["time"] for point in swing_highs}
    low_times = {point["time"] for point in swing_lows}
    ambiguous_times = high_times & low_times

    eligible_highs = [
        point for point in swing_highs
        if point["time"] not in ambiguous_times
    ]
    eligible_lows = [
        point for point in swing_lows
        if point["time"] not in ambiguous_times
    ]
    return eligible_highs, eligible_lows


def _first_close_break_after_confirmation(
    point: dict,
    closed_candles: list[dict],
    index_by_time: dict[int, int],
) -> dict | None:
    confirmation_index = index_by_time.get(point.get("confirmed_at"))
    if confirmation_index is None:
        return None

    is_high = point["type"] == "SWING_HIGH"
    level = point["level"]

    for candle in closed_candles[confirmation_index + 1:]:
        if is_high and candle["close"] > level:
            return candle
        if not is_high and candle["close"] < level:
            return candle

    return None


def _protected_candidate_state(
    point: dict | None,
    direction: str,
    break_candle: dict,
    closed_candles: list[dict],
    index_by_time: dict[int, int],
) -> dict | None:
    """Проверяет, остался ли защищаемый swing действующим после break."""
    if point is None:
        return None

    output = dict(point)
    break_index = index_by_time.get(break_candle["time"], -1)
    later = closed_candles[break_index + 1:]
    level = point["level"]

    if direction == "BULLISH":
        wick_breaches = [c for c in later if c["low"] < level]
        invalidations = [c for c in later if c["close"] < level]
        protected_type = "PROTECTED_LOW"
        invalidation_rule = "CLOSE_BELOW_PROTECTED_LOW"
    else:
        wick_breaches = [c for c in later if c["high"] > level]
        invalidations = [c for c in later if c["close"] > level]
        protected_type = "PROTECTED_HIGH"
        invalidation_rule = "CLOSE_ABOVE_PROTECTED_HIGH"

    output.update(
        {
            "protected_type": protected_type,
            "created_by_break_direction": direction,
            "created_at": break_candle["time"],
            "created_at_utc": break_candle["time_utc"],
            "current_state": (
                "INVALIDATED_BY_CLOSE"
                if invalidations
                else "ACTIVE_PROTECTED_LEVEL"
            ),
            "is_currently_protected": not bool(invalidations),
            "invalidation_rule": invalidation_rule,
            "ever_wick_breached_after_creation": bool(wick_breaches),
            "first_wick_breach_after_creation": (
                wick_breaches[0] if wick_breaches else None
            ),
            "first_close_invalidation": (
                invalidations[0] if invalidations else None
            ),
            "closed_bars_after_creation": len(later),
        }
    )
    return output


def protected_break_events(
    closed_candles: list[dict],
    recent_limit: int = SWING_RECENT_LIMIT,
) -> dict:
    """Строит raw BOS/CHoCH и protected high/low без lookahead.

    Это собственная детерминированная реализация поверх подтверждённых
    fractal 2/2, а не попытка точно воспроизвести LuxAlgo/ICT.
    """
    eligible_highs, eligible_lows = _eligible_swings(closed_candles)
    index_by_time = {
        candle["time"]: index
        for index, candle in enumerate(closed_candles)
    }

    raw_events = []

    for point in eligible_highs:
        break_candle = _first_close_break_after_confirmation(
            point,
            closed_candles,
            index_by_time,
        )
        if break_candle is None:
            continue

        candidate_low = next(
            (
                low for low in reversed(eligible_lows)
                if low["time"] < break_candle["time"]
                and low["confirmed_at"] <= break_candle["time"]
            ),
            None,
        )
        raw_events.append(
            {
                "direction": "BULLISH",
                "broken_swing": point,
                "break_candle": break_candle,
                "protected_candidate": candidate_low,
            }
        )

    for point in eligible_lows:
        break_candle = _first_close_break_after_confirmation(
            point,
            closed_candles,
            index_by_time,
        )
        if break_candle is None:
            continue

        candidate_high = next(
            (
                high for high in reversed(eligible_highs)
                if high["time"] < break_candle["time"]
                and high["confirmed_at"] <= break_candle["time"]
            ),
            None,
        )
        raw_events.append(
            {
                "direction": "BEARISH",
                "broken_swing": point,
                "break_candle": break_candle,
                "protected_candidate": candidate_high,
            }
        )

    # Одна свеча может закрытием пробить несколько вложенных swing-уровней.
    # Для события структуры оставляем самый свежий swing каждого направления.
    deduped: dict[tuple[int, str], dict] = {}
    for event in raw_events:
        key = (event["break_candle"]["time"], event["direction"])
        current = deduped.get(key)
        if (
            current is None
            or event["broken_swing"]["time"]
            > current["broken_swing"]["time"]
        ):
            deduped[key] = event

    events = sorted(
        deduped.values(),
        key=lambda item: (
            item["break_candle"]["time"],
            0 if item["direction"] == "BULLISH" else 1,
        ),
    )

    previous_direction = None
    classified = []

    for event in events:
        direction = event["direction"]
        if previous_direction is None:
            event_type = "INITIAL_BREAK"
        elif previous_direction == direction:
            event_type = "BOS"
        else:
            event_type = "CHOCH"

        protected = _protected_candidate_state(
            event["protected_candidate"],
            direction,
            event["break_candle"],
            closed_candles,
            index_by_time,
        )

        classified.append(
            {
                "event_type": event_type,
                "direction": direction,
                "time": event["break_candle"]["time"],
                "time_utc": event["break_candle"]["time_utc"],
                "broken_swing": event["broken_swing"],
                "break_candle": event["break_candle"],
                "protected_level_created": protected,
            }
        )
        previous_direction = direction

    latest = classified[-1] if classified else None
    bullish_events = [e for e in classified if e["direction"] == "BULLISH"]
    bearish_events = [e for e in classified if e["direction"] == "BEARISH"]

    latest_bullish_protection = (
        bullish_events[-1]["protected_level_created"]
        if bullish_events else None
    )
    latest_bearish_protection = (
        bearish_events[-1]["protected_level_created"]
        if bearish_events else None
    )

    historical_active_protected_low = (
        latest_bullish_protection
        if latest_bullish_protection
        and latest_bullish_protection["is_currently_protected"]
        else None
    )
    historical_active_protected_high = (
        latest_bearish_protection
        if latest_bearish_protection
        and latest_bearish_protection["is_currently_protected"]
        else None
    )

    # Только уровень, соответствующий текущему направлению структуры,
    # называется current_protected_*. Противоположный старый кандидат
    # сохраняется отдельно как historical/latest directional candidate.
    if latest and latest["direction"] == "BULLISH":
        current_protected_low = historical_active_protected_low
        current_protected_high = None
        active_directional = current_protected_low
    elif latest and latest["direction"] == "BEARISH":
        current_protected_low = None
        current_protected_high = historical_active_protected_high
        active_directional = current_protected_high
    else:
        current_protected_low = None
        current_protected_high = None
        active_directional = None

    return {
        "status": "ok" if classified else "no_confirmed_breaks",
        "method": "confirmed_fractal_breaks_with_protected_opposite_swing",
        "closed_candles_only": True,
        "event_classification": (
            "FIRST_BREAK_INITIAL_THEN_SAME_DIRECTION_BOS_"
            "OPPOSITE_DIRECTION_CHOCH"
        ),
        "current_structure_direction": (
            latest["direction"] if latest else "UNDETERMINED"
        ),
        "latest_structure_break": latest,
        "latest_bullish_protected_low_candidate": latest_bullish_protection,
        "latest_bearish_protected_high_candidate": latest_bearish_protection,
        "current_protected_low": current_protected_low,
        "current_protected_high": current_protected_high,
        "active_directional_protection": active_directional,
        "historical_active_protected_low": historical_active_protected_low,
        "historical_active_protected_high": historical_active_protected_high,
        "current_protection_policy": "ONLY_MATCH_CURRENT_STRUCTURE_DIRECTION",
        "recent_structure_break_events": classified[-recent_limit:],
        "break_event_count": len(classified),
    }


def _delivery_candle_for_swing(
    point: dict,
    opposite_points: list[dict],
    closed_candles: list[dict],
    index_by_time: dict[int, int],
) -> tuple[dict | None, int | None]:
    swing_index = index_by_time.get(point["time"])
    if swing_index is None:
        return None, None

    previous_opposite = next(
        (
            opposite for opposite in reversed(opposite_points)
            if opposite["time"] < point["time"]
        ),
        None,
    )
    start_index = (
        index_by_time.get(previous_opposite["time"], max(0, swing_index - 12))
        if previous_opposite
        else max(0, swing_index - 12)
    )

    leg = closed_candles[start_index:swing_index + 1]
    if point["type"] == "SWING_LOW":
        delivery = next(
            (c for c in reversed(leg) if c["close"] < c["open"]),
            None,
        )
    else:
        delivery = next(
            (c for c in reversed(leg) if c["close"] > c["open"]),
            None,
        )

    return delivery, start_index


def cisd_summary(
    closed_candles: list[dict],
    recent_limit: int = SWING_RECENT_LIMIT,
) -> dict:
    """Консервативный CISD, привязанный к подтверждённому swing.

    Bullish: закрытие выше open последней медвежьей delivery-свечи в ноге,
    ведущей к swing low. Bearish — зеркально. Поиск сигнала начинается не
    раньше подтверждения swing, чтобы не использовать будущие данные.
    """
    eligible_highs, eligible_lows = _eligible_swings(closed_candles)
    index_by_time = {
        candle["time"]: index
        for index, candle in enumerate(closed_candles)
    }
    candidates = []

    for point in eligible_lows:
        delivery, leg_start_index = _delivery_candle_for_swing(
            point,
            eligible_highs,
            closed_candles,
            index_by_time,
        )
        if delivery is None:
            continue

        confirmation_index = index_by_time.get(point["confirmed_at"])
        if confirmation_index is None:
            continue

        level = delivery["open"]
        signal = next(
            (
                candle for candle in closed_candles[confirmation_index:]
                if candle["close"] > level
            ),
            None,
        )
        latest_close = closed_candles[-1]["close"] if closed_candles else None

        candidates.append(
            {
                "direction": "BULLISH",
                "status": "CONFIRMED" if signal else "PENDING",
                "cisd_level": level,
                "rule": "CLOSE_ABOVE_OPEN_OF_LAST_BEARISH_DELIVERY_CANDLE",
                "anchor_swing": point,
                "delivery_candle": delivery,
                "leg_start_time": closed_candles[leg_start_index]["time"],
                "leg_start_time_utc": closed_candles[leg_start_index]["time_utc"],
                "confirmed_at": signal["time"] if signal else None,
                "confirmed_at_utc": signal["time_utc"] if signal else None,
                "confirmation_candle": signal,
                "current_state": (
                    "HOLDING_ABOVE_CISD_LEVEL"
                    if signal and latest_close is not None and latest_close > level
                    else "FAILED_BACK_BELOW_CISD_LEVEL"
                    if signal
                    else "WAITING_FOR_CLOSE_ABOVE_LEVEL"
                ),
            }
        )

    for point in eligible_highs:
        delivery, leg_start_index = _delivery_candle_for_swing(
            point,
            eligible_lows,
            closed_candles,
            index_by_time,
        )
        if delivery is None:
            continue

        confirmation_index = index_by_time.get(point["confirmed_at"])
        if confirmation_index is None:
            continue

        level = delivery["open"]
        signal = next(
            (
                candle for candle in closed_candles[confirmation_index:]
                if candle["close"] < level
            ),
            None,
        )
        latest_close = closed_candles[-1]["close"] if closed_candles else None

        candidates.append(
            {
                "direction": "BEARISH",
                "status": "CONFIRMED" if signal else "PENDING",
                "cisd_level": level,
                "rule": "CLOSE_BELOW_OPEN_OF_LAST_BULLISH_DELIVERY_CANDLE",
                "anchor_swing": point,
                "delivery_candle": delivery,
                "leg_start_time": closed_candles[leg_start_index]["time"],
                "leg_start_time_utc": closed_candles[leg_start_index]["time_utc"],
                "confirmed_at": signal["time"] if signal else None,
                "confirmed_at_utc": signal["time_utc"] if signal else None,
                "confirmation_candle": signal,
                "current_state": (
                    "HOLDING_BELOW_CISD_LEVEL"
                    if signal and latest_close is not None and latest_close < level
                    else "FAILED_BACK_ABOVE_CISD_LEVEL"
                    if signal
                    else "WAITING_FOR_CLOSE_BELOW_LEVEL"
                ),
            }
        )

    confirmed = sorted(
        [item for item in candidates if item["status"] == "CONFIRMED"],
        key=lambda item: item["confirmed_at"],
    )
    pending = sorted(
        [item for item in candidates if item["status"] == "PENDING"],
        key=lambda item: item["anchor_swing"]["confirmed_at"],
    )
    bullish_confirmed = [
        item for item in confirmed if item["direction"] == "BULLISH"
    ]
    bearish_confirmed = [
        item for item in confirmed if item["direction"] == "BEARISH"
    ]
    holding_confirmed = [
        item for item in confirmed
        if item["current_state"].startswith("HOLDING_")
    ]
    failed_confirmed = [
        item for item in confirmed
        if item["current_state"].startswith("FAILED_")
    ]
    latest_holding = holding_confirmed[-1] if holding_confirmed else None
    latest_failed = failed_confirmed[-1] if failed_confirmed else None

    return {
        "status": "ok" if confirmed or pending else "insufficient_data",
        "method": "confirmed_swing_anchored_delivery_shift_no_lookahead",
        "closed_candles_only": True,
        "not_exact_luxalgo_or_discretionary_ict": True,
        "latest_confirmed_cisd": confirmed[-1] if confirmed else None,
        "latest_effective_cisd": latest_holding,
        "latest_failed_cisd": latest_failed,
        "current_cisd_direction": (
            latest_holding["direction"] if latest_holding else "UNDETERMINED"
        ),
        "latest_bullish_cisd": (
            bullish_confirmed[-1] if bullish_confirmed else None
        ),
        "latest_bearish_cisd": (
            bearish_confirmed[-1] if bearish_confirmed else None
        ),
        "latest_pending_cisd": pending[-1] if pending else None,
        "recent_confirmed_cisd": confirmed[-recent_limit:],
        "recent_pending_cisd": pending[-recent_limit:],
        "confirmed_count": len(confirmed),
        "holding_count": len(holding_confirmed),
        "failed_count": len(failed_confirmed),
        "pending_count": len(pending),
    }


def protected_structure_summary(
    closed_candles: list[dict],
    recent_limit: int = SWING_RECENT_LIMIT,
) -> dict:
    return {
        "status": "ok" if closed_candles else "insufficient_data",
        "method": "protected_levels_plus_conservative_cisd_v1",
        "closed_candles_only": True,
        "basis": "confirmed_fractal_2_left_2_right",
        "scope_note": "States are valid only inside the loaded candle window.",
        "structure_breaks": protected_break_events(
            closed_candles,
            recent_limit,
        ),
        "cisd": cisd_summary(
            closed_candles,
            recent_limit,
        ),
    }

DIRECTIONAL_BIASES = {"BULLISH", "BEARISH"}

MTF_ROLE_MAP = {
    "1w": "BROAD_CONTEXT",
    "1d": "BROAD_CONTEXT",
    "4h": "HIGHER_TIMEFRAME_CONTEXT",
    "1h": "SESSION_DIRECTION",
    "15m": "SETUP_TIMEFRAME",
    "1m": "ENTRY_CONFIRMATION",
}


def _nested_value(data: dict, path: tuple[str, ...], default=None):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


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
            "structure_direction": "UNDETERMINED",
            "effective_cisd_direction": "UNDETERMINED",
            "swing_sequence_bias": "INSUFFICIENT_DATA",
            "latest_trigger": _latest_trigger({}),
            "closure_sequence": {},
            "opposite_closure_to_primary_direction": {
                "active": False,
                "direction": None,
                "reason": "NO_PRIMARY_DIRECTION",
            },
            "internal_conflicts": [],
        }

    structure = _nested_value(
        timeframe_data,
        ("protected_structure", "structure_breaks", "current_structure_direction"),
        "UNDETERMINED",
    )
    cisd = _nested_value(
        timeframe_data,
        ("protected_structure", "cisd", "current_cisd_direction"),
        "UNDETERMINED",
    )
    swing = _nested_value(
        timeframe_data,
        ("swing_structure", "provisional_swing_bias"),
        "INSUFFICIENT_DATA",
    )

    conflicts = []

    if structure in DIRECTIONAL_BIASES:
        primary = structure
        source = "CONFIRMED_STRUCTURE"
        if cisd in DIRECTIONAL_BIASES and cisd != structure:
            conflicts.append("EFFECTIVE_CISD_OPPOSES_STRUCTURE")
        if swing in DIRECTIONAL_BIASES and swing != structure:
            conflicts.append("SWING_SEQUENCE_OPPOSES_STRUCTURE")
    elif cisd in DIRECTIONAL_BIASES:
        primary = cisd
        source = "EFFECTIVE_CISD"
        if swing in DIRECTIONAL_BIASES and swing != cisd:
            conflicts.append("SWING_SEQUENCE_OPPOSES_CISD")
    elif swing in DIRECTIONAL_BIASES:
        primary = swing
        source = "SWING_SEQUENCE"
    else:
        primary = "UNDETERMINED"
        source = "NONE"

    if primary == "UNDETERMINED":
        confidence = "INSUFFICIENT_DATA"
    elif source == "CONFIRMED_STRUCTURE":
        aligned_support = 0
        if cisd == primary:
            aligned_support += 1
        if swing == primary:
            aligned_support += 1
        if conflicts:
            confidence = "MEDIUM"
        elif aligned_support >= 1:
            confidence = "HIGH"
        else:
            confidence = "MEDIUM"
    elif source == "EFFECTIVE_CISD":
        confidence = "MEDIUM" if swing == primary else "LOW"
    else:
        confidence = "LOW"

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
        "structure_direction": structure,
        "effective_cisd_direction": cisd,
        "swing_sequence_bias": swing,
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
        "method": "deterministic_mtf_hierarchy_v2",
        "closed_candles_only_for_structure": True,
        "role_map": MTF_ROLE_MAP,
        "priority_rule": [
            "CONFIRMED_STRUCTURE",
            "EFFECTIVE_CISD",
            "SWING_SEQUENCE",
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

        timeframes[timeframe] = {
            "seconds_per_candle": seconds,
            "latest_live_candle": live,
            "latest_closed_candle": closed[-1] if closed else None,
            "recent_closed_candles": closed[-40:],
            "recent_candle2": candle2[-20:],
            "recent_candle3": candle3[-20:],
            "recent_sweep_displacement": sweep_displacement[-20:],
            "closure_sequence": sequence,
            "swing_points": swing_summary(
                closed,
                ticker_data.get("lastPrice"),
            ),
            "swing_structure": swing_structure_summary(
                closed,
                ticker_data.get("lastPrice"),
            ),
            "protected_structure": protected_structure_summary(closed),
        }

    return {
        "ok": True,
        "source": "MEXC Futures public API",
        "mode": "enhanced_snapshot_v6",
        "version": "6.3-closure-sequence",
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
            "swing_detection": {
                "method": "confirmed_fractal",
                "left_bars": SWING_LEFT_BARS,
                "right_bars": SWING_RIGHT_BARS,
                "confirmed_only": True,
            },
            "swing_level_state": {
                "closed_candles_only": True,
                "outside_bars_excluded_from_sequence": True,
                "bos_choch_not_calculated_yet": False,
                "protected_high_low_enabled": True,
                "conservative_cisd_enabled": True,
                "mtf_hierarchy_enabled": True,
            },
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
