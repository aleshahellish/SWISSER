# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler


BASE = "https://api.mexc.com"
FILTER_LENGTH = 12
WICK_PERCENT = 40
SWING_LEFT_BARS = 2
SWING_RIGHT_BARS = 2
SWING_RECENT_LIMIT = 3

TIMEFRAMES = {
    "1m": ("Min1", 60, 80),
    "15m": ("Min15", 900, 80),
    "1h": ("Min60", 3600, 80),
    "4h": ("Hour4", 14400, 80),
    "1d": ("Day1", 86400, 80),
    "1w": ("Week1", 604800, 60),
}

SUPPORTED_SYMBOLS = (
    "TAO_USDT",
    "HYPE_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
    "ETH_USDT",
    "BTC_USDT",
)


class SlidingWindowRateLimiter:
    """Не даёт сканеру превысить публичный лимит MEXC."""

    def __init__(self, max_calls: int = 18, period_seconds: float = 2.05):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.calls = deque()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            sleep_for = 0.0

            with self.lock:
                now = time.monotonic()

                while (
                    self.calls
                    and now - self.calls[0] >= self.period_seconds
                ):
                    self.calls.popleft()

                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return

                sleep_for = (
                    self.period_seconds - (now - self.calls[0]) + 0.01
                )

            time.sleep(max(sleep_for, 0.01))


RATE_LIMITER = SlidingWindowRateLimiter()


def utc(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def request_json(path: str, params: dict | None = None):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = BASE + path + query

    last_error = None

    for attempt in range(3):
        RATE_LIMITER.wait()

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 MEXC-Enhanced-Scanner-V4/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=12,
                context=ssl.create_default_context(),
            ) as response:
                data = json.loads(response.read().decode("utf-8"))

            if not isinstance(data, dict) or data.get("success") is False:
                raise RuntimeError(f"Bad MEXC response: {data}")

            return data

        except Exception as exc:
            last_error = exc

            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))

    raise RuntimeError(f"MEXC connection error: {last_error!r}")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    symbol = symbol.replace("-", "_").replace("/", "_")

    if symbol.endswith("USDT") and not symbol.endswith("_USDT"):
        symbol = symbol[:-4] + "_USDT"

    if "_" not in symbol:
        symbol += "_USDT"

    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")

    return symbol


def selected_symbols(path: str) -> list[str]:
    parsed = urllib.parse.urlparse(path)
    query = urllib.parse.parse_qs(parsed.query)
    raw = query.get("symbols", [""])[0].strip()

    if not raw:
        return list(SUPPORTED_SYMBOLS)

    output = []

    for item in raw.split(","):
        symbol = normalize_symbol(item)
        if symbol not in output:
            output.append(symbol)

    return output


def fetch_tickers() -> dict[str, dict]:
    data = request_json("/api/v1/contract/ticker").get("data")

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise RuntimeError("Ticker list unavailable")

    output = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")
        if symbol in SUPPORTED_SYMBOLS:
            output[symbol] = item

    return output


def fetch_candles(
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

    return sorted(output, key=lambda candle: candle["time"])[-count:]


def detect(candles_list: list[dict]) -> tuple[list[dict], list[dict]]:
    candle2 = []
    candle3 = []

    bullish = [False] * len(candles_list)
    bearish = [False] * len(candles_list)

    for index in range(1, len(candles_list)):
        previous = candles_list[index - 1]
        current = candles_list[index]
        window = candles_list[
            max(0, index - FILTER_LENGTH + 1): index + 1
        ]

        bullish_signal = (
            current["low"] == min(item["low"] for item in window)
            and previous["close"] < previous["open"]
            and current["low"] < previous["low"]
            and current["close"] > previous["low"]
        )

        bearish_signal = (
            current["high"] == max(item["high"] for item in window)
            and previous["close"] > previous["open"]
            and current["high"] > previous["high"]
            and current["close"] < previous["high"]
        )

        bullish[index] = bullish_signal
        bearish[index] = bearish_signal

        if bullish_signal or bearish_signal:
            candle_range = current["high"] - current["low"]
            wick_threshold = 0.01 * WICK_PERCENT * candle_range

            if bullish_signal:
                big_wick = (
                    min(current["close"], current["open"])
                    - current["low"]
                    > wick_threshold
                )
            else:
                big_wick = (
                    current["high"]
                    - max(current["close"], current["open"])
                    > wick_threshold
                )

            candle2.append(
                {
                    "direction": (
                        "BULLISH" if bullish_signal else "BEARISH"
                    ),
                    "time": current["time"],
                    "time_utc": current["time_utc"],
                    "previous_direction": (
                        "BEARISH"
                        if previous["close"] < previous["open"]
                        else "BULLISH"
                    ),
                    "open": current["open"],
                    "high": current["high"],
                    "low": current["low"],
                    "close": current["close"],
                    "big_wick_40_percent": big_wick,
                }
            )

        if index >= 2:
            previous_candle2 = candles_list[index - 1]

            bearish_expansion = (
                bearish[index - 1]
                and current["high"] < previous_candle2["high"]
                and current["close"] < previous_candle2["low"]
            )

            bullish_expansion = (
                bullish[index - 1]
                and current["low"] > previous_candle2["low"]
                and current["close"] > previous_candle2["high"]
            )

            if bearish_expansion or bullish_expansion:
                candle3.append(
                    {
                        "direction": (
                            "BULLISH"
                            if bullish_expansion
                            else "BEARISH"
                        ),
                        "time": current["time"],
                        "time_utc": current["time_utc"],
                        "after_candle2_time_utc": (
                            previous_candle2["time_utc"]
                        ),
                        "open": current["open"],
                        "high": current["high"],
                        "low": current["low"],
                        "close": current["close"],
                    }
                )

    return candle2, candle3


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

    return {
        "status": "ok" if swing_highs and swing_lows else "partial",
        "method": "confirmed_fractal_2_left_2_right",
        "confirmed_only": True,
        "latest_swing_high": swing_highs[-1] if swing_highs else None,
        "latest_swing_low": swing_lows[-1] if swing_lows else None,
        "nearest_swing_high_above_price": (
            min(highs_above, key=lambda point: point["level"])
            if highs_above else None
        ),
        "nearest_swing_low_below_price": (
            max(lows_below, key=lambda point: point["level"])
            if lows_below else None
        ),
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
        "not_bos_or_choch_yet": True,
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


def summarize_timeframe(
    candles_list: list[dict],
    seconds: int,
    now: int,
    current_price,
) -> dict:
    closed, live = split_closed_and_live(candles_list, seconds, now)
    candle2, candle3 = detect(closed)

    latest_c2 = candle2[-1] if candle2 else None
    latest_c3 = candle3[-1] if candle3 else None

    latest_c2_confirmed = False
    if latest_c2 is not None:
        latest_c2_confirmed = any(
            item.get("after_candle2_time_utc") == latest_c2["time_utc"]
            for item in candle3
        )

    bars_since_c2 = None
    if latest_c2 is not None and closed:
        bars_since_c2 = int(
            (closed[-1]["time"] - latest_c2["time"]) / seconds
        )

    bars_since_c3 = None
    if latest_c3 is not None and closed:
        bars_since_c3 = int(
            (closed[-1]["time"] - latest_c3["time"]) / seconds
        )

    close_change_last_3 = None
    if len(closed) >= 4 and closed[-4]["close"] != 0:
        close_change_last_3 = round(
            (closed[-1]["close"] / closed[-4]["close"] - 1) * 100,
            6,
        )

    return {
        "seconds_per_candle": seconds,
        "latest_live_candle": live,
        "latest_closed_candle": closed[-1] if closed else None,
        "previous_closed_candle": closed[-2] if len(closed) >= 2 else None,
        "recent_closed_candles": closed[-8:],
        "recent_candle2": candle2[-3:],
        "recent_candle3": candle3[-3:],
        "latest_candle2_confirmed_by_candle3": latest_c2_confirmed,
        "bars_since_latest_candle2": bars_since_c2,
        "bars_since_latest_candle3": bars_since_c3,
        "close_change_percent_last_3_bars": close_change_last_3,
        "swing_points": swing_summary(closed, current_price),
        "swing_structure": swing_structure_summary(
            closed,
            current_price,
        ),
    }


def higher_timeframe_levels(
    daily_candles: list[dict],
    weekly_candles: list[dict],
    now: int,
) -> dict:
    daily_closed, current_day = split_closed_and_live(
        daily_candles,
        TIMEFRAMES["1d"][1],
        now,
    )
    weekly_closed, current_week = split_closed_and_live(
        weekly_candles,
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


def build(path: str) -> dict:
    symbols = selected_symbols(path)
    now = int(time.time())
    tickers = fetch_tickers()

    raw: dict[str, dict[str, list[dict]]] = {
        symbol: {} for symbol in symbols
    }
    errors: dict[str, list[str]] = {
        symbol: [] for symbol in symbols
    }

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {}

        for symbol in symbols:
            for timeframe, (
                api_timeframe,
                seconds,
                count,
            ) in TIMEFRAMES.items():
                future = executor.submit(
                    fetch_candles,
                    symbol,
                    api_timeframe,
                    seconds,
                    count,
                )
                future_map[future] = (symbol, timeframe)

        for future in as_completed(future_map):
            symbol, timeframe = future_map[future]

            try:
                raw[symbol][timeframe] = future.result()
            except Exception as exc:
                errors[symbol].append(f"{timeframe}: {exc}")

    results = []

    for symbol in symbols:
        ticker_data = tickers.get(symbol, {})
        current_price = number(ticker_data.get("lastPrice"))
        high_24h = number(ticker_data.get("high24Price"))
        low_24h = number(ticker_data.get("lower24Price"))

        range_position = None
        if (
            current_price is not None
            and high_24h is not None
            and low_24h is not None
            and high_24h > low_24h
        ):
            range_position = round(
                (current_price - low_24h)
                / (high_24h - low_24h)
                * 100,
                2,
            )

        timeframe_output = {}

        for timeframe, (_, seconds, _) in TIMEFRAMES.items():
            candles_list = raw[symbol].get(timeframe)

            if candles_list:
                timeframe_output[timeframe] = summarize_timeframe(
                    candles_list,
                    seconds,
                    now,
                    current_price,
                )

        symbol_ok = (
            symbol in tickers
            and len(timeframe_output) == len(TIMEFRAMES)
            and not errors[symbol]
        )

        item = {
            "ok": symbol_ok,
            "symbol": symbol,
            "current_price": current_price,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "range_position_24h_percent": range_position,
            "higher_timeframe_levels": higher_timeframe_levels(
                raw[symbol].get("1d", []),
                raw[symbol].get("1w", []),
                now,
            ),
            "timeframes": timeframe_output,
        }

        if errors[symbol]:
            item["errors"] = errors[symbol]

        results.append(item)

    return {
        "ok": all(item["ok"] for item in results),
        "source": "MEXC Futures public API",
        "mode": "enhanced_multi_coin_scanner_v4_1",
        "version": "4.1-prioritized-level-tiers",
        "fetched_at_unix": now,
        "fetched_at_utc": utc(now),
        "requested_symbols": symbols,
        "count": len(results),
        "settings": {
            "reversal_filter_enabled": True,
            "filter_length": FILTER_LENGTH,
            "wick_percent": WICK_PERCENT,
            "timeframes": list(TIMEFRAMES.keys()),
            "candles_loaded_per_timeframe": {
                timeframe: config[2]
                for timeframe, config in TIMEFRAMES.items()
            },
            "rate_limit_protection": True,
            "swing_detection": {
                "method": "confirmed_fractal",
                "left_bars": SWING_LEFT_BARS,
                "right_bars": SWING_RIGHT_BARS,
                "confirmed_only": True,
            },
            "swing_level_state": {
                "closed_candles_only": True,
                "outside_bars_excluded_from_sequence": True,
                "bos_choch_not_calculated_yet": True,
            },
        },
        "results": results,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = build(self.path)

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
