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
COUNT = 300
N = 12
WICK = 40

TFS = {
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
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
        symbol = symbol + "_USDT"

    if symbol not in SUPPORTED_SYMBOLS:
        allowed = ", ".join(sorted(SUPPORTED_SYMBOLS))
        raise ValueError(
            f"Unsupported symbol: {symbol}. Supported symbols: {allowed}"
        )

    return symbol


def req(path: str, params: dict | None = None):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = BASE + path + query

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MEXC-Live-Analyst/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=8,
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


def ticker(symbol: str):
    data = req(
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


def candles(symbol: str, api_tf: str, seconds: int):
    now = int(time.time())
    start = now - seconds * (COUNT + 10)

    data = req(
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

    return sorted(
        output,
        key=lambda candle: candle["time"],
    )[-COUNT:]


def detect(candles_list: list[dict]):
    candle2 = []
    candle3 = []

    bullish = [False] * len(candles_list)
    bearish = [False] * len(candles_list)

    for index in range(1, len(candles_list)):
        previous = candles_list[index - 1]
        current = candles_list[index]

        window = candles_list[
            max(0, index - N + 1): index + 1
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
            wick_threshold = 0.01 * WICK * candle_range

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


def build(symbol: str):
    with ThreadPoolExecutor(max_workers=3) as executor:
        ticker_future = executor.submit(ticker, symbol)

        candle_futures = {
            timeframe: executor.submit(
                candles,
                symbol,
                api_timeframe,
                seconds,
            )
            for timeframe, (api_timeframe, seconds) in TFS.items()
        }

        ticker_data = ticker_future.result()

        raw_candles = {
            timeframe: future.result()
            for timeframe, future in candle_futures.items()
        }

    now = int(time.time())
    timeframes = {}

    for timeframe, (_, seconds) in TFS.items():
        raw = raw_candles[timeframe]

        closed = [
            candle
            for candle in raw
            if candle["time"] + seconds <= now
        ]

        candle2, candle3 = detect(closed)

        timeframes[timeframe] = {
            "seconds_per_candle": seconds,
            "latest_live_candle": raw[-1] if raw else None,
            "latest_closed_candle": closed[-1] if closed else None,
            "recent_closed_candles": closed[-40:],
            "recent_candle2": candle2[-20:],
            "recent_candle3": candle3[-20:],
        }

    return {
        "ok": True,
        "source": "MEXC Futures public API",
        "symbol": symbol,
        "supported_symbols": sorted(SUPPORTED_SYMBOLS),
        "fetched_at_unix": now,
        "fetched_at_utc": utc(now),
        "current_price": ticker_data.get("lastPrice"),
        "high_24h": ticker_data.get("high24Price"),
        "low_24h": ticker_data.get("lower24Price"),
        "settings": {
            "reversal_filter_enabled": True,
            "filter_length": N,
            "wick_percent": WICK,
        },
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
