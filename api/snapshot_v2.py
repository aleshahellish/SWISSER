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


BASE = "https://api.mexc.com"
FILTER_LENGTH = 12
WICK_PERCENT = 40

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
            "User-Agent": "Mozilla/5.0 MEXC-Live-Analyst-V2/1.0",
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
        candle2, candle3 = detect(closed)

        timeframes[timeframe] = {
            "seconds_per_candle": seconds,
            "latest_live_candle": live,
            "latest_closed_candle": closed[-1] if closed else None,
            "recent_closed_candles": closed[-40:],
            "recent_candle2": candle2[-20:],
            "recent_candle3": candle3[-20:],
        }

    return {
        "ok": True,
        "source": "MEXC Futures public API",
        "mode": "enhanced_snapshot_v2",
        "version": "2.0-day-week-levels",
        "symbol": symbol,
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
            "timeframes": list(TIMEFRAMES.keys()),
        },
        "higher_timeframe_levels": build_higher_timeframe_levels(
            raw_candles,
            now,
        ),
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
