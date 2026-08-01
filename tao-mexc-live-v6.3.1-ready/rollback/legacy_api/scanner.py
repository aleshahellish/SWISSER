# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler


BASE = "https://api.mexc.com"
COUNT = 80
FILTER_LENGTH = 12
WICK_PERCENT = 40

TIMEFRAMES = {
    "1m": ("Min1", 60),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
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


def utc(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def request_json(path: str, params: dict | None = None):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = BASE + path + query

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MEXC-Multi-Coin-Scanner/1.0",
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


def fetch_candles(symbol: str, api_tf: str, seconds: int) -> list[dict]:
    now = int(time.time())
    start = now - seconds * (COUNT + 10)

    data = request_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": api_tf,
            "start": start,
            "end": now,
        },
    ).get("data")

    keys = ("time", "open", "high", "low", "close", "vol")

    if not isinstance(data, dict) or any(key not in data for key in keys):
        raise RuntimeError(f"Klines unavailable for {symbol} {api_tf}")

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

    return sorted(output, key=lambda candle: candle["time"])[-COUNT:]


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
                    min(current["close"], current["open"]) - current["low"]
                    > wick_threshold
                )
            else:
                big_wick = (
                    current["high"] - max(current["close"], current["open"])
                    > wick_threshold
                )

            candle2.append(
                {
                    "direction": "BULLISH" if bullish_signal else "BEARISH",
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
                        "after_candle2_time_utc": previous_candle2["time_utc"],
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


def summarize_timeframe(
    candles_list: list[dict],
    seconds: int,
    now: int,
) -> dict:
    closed = [
        candle
        for candle in candles_list
        if candle["time"] + seconds <= now
    ]

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
        "latest_live_candle": (
            candles_list[-1] if candles_list else None
        ),
        "latest_closed_candle": closed[-1] if closed else None,
        "previous_closed_candle": closed[-2] if len(closed) >= 2 else None,
        "recent_closed_candles": closed[-8:],
        "recent_candle2": candle2[-3:],
        "recent_candle3": candle3[-3:],
        "latest_candle2_confirmed_by_candle3": latest_c2_confirmed,
        "bars_since_latest_candle2": bars_since_c2,
        "bars_since_latest_candle3": bars_since_c3,
        "close_change_percent_last_3_bars": close_change_last_3,
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

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_map = {}

        for symbol in symbols:
            for timeframe, (api_tf, seconds) in TIMEFRAMES.items():
                future = executor.submit(
                    fetch_candles,
                    symbol,
                    api_tf,
                    seconds,
                )
                future_map[future] = (
                    symbol,
                    timeframe,
                    seconds,
                )

        for future in as_completed(future_map):
            symbol, timeframe, _ = future_map[future]

            try:
                raw[symbol][timeframe] = future.result()
            except Exception as exc:
                errors[symbol].append(
                    f"{timeframe}: {exc}"
                )

    results = []

    for symbol in symbols:
        ticker = tickers.get(symbol, {})
        current_price = number(ticker.get("lastPrice"))
        high_24h = number(ticker.get("high24Price"))
        low_24h = number(ticker.get("lower24Price"))

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

        for timeframe, (_, seconds) in TIMEFRAMES.items():
            candles_list = raw[symbol].get(timeframe)

            if candles_list:
                timeframe_output[timeframe] = summarize_timeframe(
                    candles_list,
                    seconds,
                    now,
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
            "timeframes": timeframe_output,
        }

        if errors[symbol]:
            item["errors"] = errors[symbol]

        results.append(item)

    return {
        "ok": all(item["ok"] for item in results),
        "source": "MEXC Futures public API",
        "mode": "multi_coin_scanner",
        "fetched_at_unix": now,
        "fetched_at_utc": utc(now),
        "requested_symbols": symbols,
        "count": len(results),
        "settings": {
            "reversal_filter_enabled": True,
            "filter_length": FILTER_LENGTH,
            "wick_percent": WICK_PERCENT,
            "timeframes": list(TIMEFRAMES.keys()),
            "candles_loaded_per_timeframe": COUNT,
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
                {
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ).encode("utf-8")

            status_code = 400

        except Exception as exc:
            body = json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
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
