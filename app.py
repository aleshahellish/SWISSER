# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

BASE_URL = "https://api.mexc.com"
SYMBOL = "TAO_USDT"
INTERVALS = {
    "15m": ("Min15", 15 * 60),
    "1h": ("Min60", 60 * 60),
}
CANDLE_COUNT = 300
FILTER_LENGTH = 12
WICK_PERCENT = 40
REVERSAL_FILTER_ENABLED = True
TIMEOUT_SECONDS = 20
CACHE_SECONDS = 20

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "created_at": 0.0,
    "snapshot": None,
    "error": None,
}


def request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TAO-MEXC-Live/1.0",
            "Accept": "application/json",
        },
    )

    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(
            req,
            timeout=TIMEOUT_SECONDS,
            context=context,
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"MEXC HTTP {exc.code}: {exc.reason}; URL={url}; body={body[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Не удалось подключиться к MEXC; URL={url}; reason={exc.reason!r}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MEXC вернул не-JSON; URL={url}; body={raw[:300]}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Неожиданный формат ответа MEXC.")

    if payload.get("success") is False:
        code = payload.get("code", "unknown")
        message = payload.get("message") or payload.get("msg") or "no message"
        raise RuntimeError(f"MEXC error {code}: {message}")

    return payload


def to_utc_text(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_ticker() -> dict[str, Any]:
    payload = request_json("/api/v1/contract/ticker", {"symbol": SYMBOL})
    data = payload.get("data")

    if isinstance(data, list):
        match = next((item for item in data if item.get("symbol") == SYMBOL), None)
        if match is None:
            raise RuntimeError(f"Контракт {SYMBOL} не найден.")
        return match

    if not isinstance(data, dict):
        raise RuntimeError("Неожиданный формат ticker.")

    return data


def fetch_candles(interval_api: str, seconds_per_candle: int) -> list[dict[str, Any]]:
    now = int(time.time())
    start = now - seconds_per_candle * (CANDLE_COUNT + 10)

    payload = request_json(
        f"/api/v1/contract/kline/{SYMBOL}",
        {
            "interval": interval_api,
            "start": start,
            "end": now,
        },
    )
    data = payload.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(f"Неожиданный формат свечей {interval_api}.")

    required = ("time", "open", "high", "low", "close", "vol")
    for key in required:
        if key not in data or not isinstance(data[key], list):
            raise RuntimeError(f"В ответе свечей нет массива {key!r}.")

    count = min(len(data[key]) for key in required)
    candles: list[dict[str, Any]] = []

    for i in range(count):
        ts = int(data["time"][i])
        candles.append(
            {
                "time": ts,
                "time_utc": to_utc_text(ts),
                "open": float(data["open"][i]),
                "high": float(data["high"][i]),
                "low": float(data["low"][i]),
                "close": float(data["close"][i]),
                "volume": float(data["vol"][i]),
            }
        )

    candles.sort(key=lambda candle: candle["time"])
    return candles[-CANDLE_COUNT:]


def get_closed_candles(
    candles: list[dict[str, Any]],
    seconds_per_candle: int,
) -> list[dict[str, Any]]:
    now = int(time.time())
    return [
        candle
        for candle in candles
        if candle["time"] + seconds_per_candle <= now
    ]


def detect_exact_signals(
    candles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    candle2: list[dict[str, Any]] = []
    candle3: list[dict[str, Any]] = []

    bullish_c2_flags = [False] * len(candles)
    bearish_c2_flags = [False] * len(candles)

    for i in range(1, len(candles)):
        previous = candles[i - 1]
        current = candles[i]

        start = max(0, i - FILTER_LENGTH + 1)
        window = candles[start : i + 1]
        lowest = min(candle["low"] for candle in window)
        highest = max(candle["high"] for candle in window)

        bullish_filter_ok = (
            current["low"] == lowest if REVERSAL_FILTER_ENABLED else True
        )
        bearish_filter_ok = (
            current["high"] == highest if REVERSAL_FILTER_ENABLED else True
        )

        previous_is_bearish = previous["close"] < previous["open"]
        previous_is_bullish = previous["close"] > previous["open"]

        bullish_c2 = (
            bullish_filter_ok
            and previous_is_bearish
            and current["low"] < previous["low"]
            and current["close"] > previous["low"]
        )
        bearish_c2 = (
            bearish_filter_ok
            and previous_is_bullish
            and current["high"] > previous["high"]
            and current["close"] < previous["high"]
        )

        bullish_c2_flags[i] = bullish_c2
        bearish_c2_flags[i] = bearish_c2

        if bullish_c2 or bearish_c2:
            direction = "BULLISH" if bullish_c2 else "BEARISH"
            wick_threshold = 0.01 * WICK_PERCENT * (
                current["high"] - current["low"]
            )
            bullish_wick = min(current["close"], current["open"]) - current["low"]
            bearish_wick = current["high"] - max(current["close"], current["open"])
            big_wick = (
                bullish_wick > wick_threshold
                if bullish_c2
                else bearish_wick > wick_threshold
            )

            candle2.append(
                {
                    "direction": direction,
                    "time": current["time"],
                    "time_utc": current["time_utc"],
                    "previous_direction": (
                        "BEARISH" if previous_is_bearish else "BULLISH"
                    ),
                    "open": current["open"],
                    "high": current["high"],
                    "low": current["low"],
                    "close": current["close"],
                    "big_wick_40_percent": big_wick,
                }
            )

        if i >= 2:
            previous_bar = candles[i - 1]

            bearish_expansion = (
                bearish_c2_flags[i - 1]
                and current["high"] < previous_bar["high"]
                and current["close"] < previous_bar["low"]
            )
            bullish_expansion = (
                bullish_c2_flags[i - 1]
                and current["low"] > previous_bar["low"]
                and current["close"] > previous_bar["high"]
            )

            if bearish_expansion or bullish_expansion:
                candle3.append(
                    {
                        "direction": "BULLISH" if bullish_expansion else "BEARISH",
                        "time": current["time"],
                        "time_utc": current["time_utc"],
                        "after_candle2_time_utc": previous_bar["time_utc"],
                        "open": current["open"],
                        "high": current["high"],
                        "low": current["low"],
                        "close": current["close"],
                    }
                )

    return {"candle2": candle2, "candle3": candle3}


def build_snapshot() -> dict[str, Any]:
    ping = request_json("/api/v1/contract/ping")
    ticker = fetch_ticker()

    timeframes: dict[str, Any] = {}
    for label, (api_interval, seconds_per_candle) in INTERVALS.items():
        raw = fetch_candles(api_interval, seconds_per_candle)
        closed = get_closed_candles(raw, seconds_per_candle)
        signals = detect_exact_signals(closed)

        timeframes[label] = {
            "seconds_per_candle": seconds_per_candle,
            "latest_live_candle": raw[-1] if raw else None,
            "latest_closed_candle": closed[-1] if closed else None,
            "recent_closed_candles": closed[-40:],
            "recent_candle2": signals["candle2"][-20:],
            "recent_candle3": signals["candle3"][-20:],
            "candle2_total_in_loaded_history": len(signals["candle2"]),
            "candle3_total_in_loaded_history": len(signals["candle3"]),
        }

    return {
        "ok": True,
        "source": "MEXC Futures public API",
        "symbol": SYMBOL,
        "fetched_at_unix": int(time.time()),
        "fetched_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "ping": ping.get("data"),
        "current_price": ticker.get("lastPrice"),
        "high_24h": ticker.get("high24Price"),
        "low_24h": ticker.get("lower24Price"),
        "settings": {
            "reversal_filter_enabled": REVERSAL_FILTER_ENABLED,
            "filter_length": FILTER_LENGTH,
            "wick_percent": WICK_PERCENT,
        },
        "timeframes": timeframes,
    }


def get_snapshot(force: bool = False) -> dict[str, Any]:
    now = time.time()

    with _cache_lock:
        if (
            not force
            and _cache["snapshot"] is not None
            and now - float(_cache["created_at"]) < CACHE_SECONDS
        ):
            return _cache["snapshot"]

    try:
        snapshot = build_snapshot()
    except Exception as exc:
        with _cache_lock:
            _cache["error"] = str(exc)
            if _cache["snapshot"] is not None:
                stale = dict(_cache["snapshot"])
                stale["stale"] = True
                stale["refresh_error"] = str(exc)
                return stale
        raise

    with _cache_lock:
        _cache["created_at"] = now
        _cache["snapshot"] = snapshot
        _cache["error"] = None

    return snapshot


def fmt(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def signal_rows(signals: list[dict[str, Any]], limit: int = 8) -> str:
    rows = []
    for signal in signals[-limit:][::-1]:
        rows.append(
            "<tr>"
            f"<td>{fmt(signal.get('time_utc'))}</td>"
            f"<td>{fmt(signal.get('direction'))}</td>"
            f"<td>{fmt(signal.get('open'))}</td>"
            f"<td>{fmt(signal.get('high'))}</td>"
            f"<td>{fmt(signal.get('low'))}</td>"
            f"<td>{fmt(signal.get('close'))}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6">Нет сигналов</td></tr>'


def render_page(snapshot: dict[str, Any]) -> str:
    sections = []
    for tf in ("15m", "1h"):
        data = snapshot["timeframes"][tf]
        latest = data.get("latest_live_candle") or {}
        c2 = data.get("recent_candle2") or []
        c3 = data.get("recent_candle3") or []

        sections.append(
            f"""
            <section class="panel">
              <h2>{tf}</h2>
              <p><strong>Текущая свеча:</strong>
              {fmt(latest.get('time_utc'))} —
              O {fmt(latest.get('open'))},
              H {fmt(latest.get('high'))},
              L {fmt(latest.get('low'))},
              C {fmt(latest.get('close'))}</p>

              <h3>Последние Candle 2</h3>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Время UTC</th><th>Направление</th><th>O</th><th>H</th><th>L</th><th>C</th></tr></thead>
                  <tbody>{signal_rows(c2)}</tbody>
                </table>
              </div>

              <h3>Последние Candle 3</h3>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Время UTC</th><th>Направление</th><th>O</th><th>H</th><th>L</th><th>C</th></tr></thead>
                  <tbody>{signal_rows(c3)}</tbody>
                </table>
              </div>
            </section>
            """
        )

    stale_note = ""
    if snapshot.get("stale"):
        stale_note = (
            '<p class="warning">Показан последний сохранённый снимок. '
            f"Ошибка обновления: {fmt(snapshot.get('refresh_error'))}</p>"
        )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TAO MEXC Live</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: Arial, sans-serif;
    }}
    body {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
      line-height: 1.45;
    }}
    .top, .panel {{
      border: 1px solid #7775;
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 18px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit,minmax(170px,1fr));
      gap: 10px;
    }}
    .metric {{
      border: 1px solid #7774;
      border-radius: 10px;
      padding: 12px;
    }}
    .value {{ font-size: 1.35rem; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 8px;
      border-bottom: 1px solid #7774;
      text-align: left;
      white-space: nowrap;
    }}
    .table-wrap {{ overflow-x: auto; }}
    .muted {{ opacity: .72; }}
    .warning {{
      padding: 10px;
      border: 1px solid #b86;
      border-radius: 10px;
    }}
    a {{ color: inherit; }}
  </style>
</head>
<body>
  <div class="top">
    <h1>TAO_USDT — MEXC Futures</h1>
    <div class="metrics">
      <div class="metric"><div class="muted">Текущая цена</div><div class="value">{fmt(snapshot.get('current_price'))}</div></div>
      <div class="metric"><div class="muted">Максимум 24ч</div><div class="value">{fmt(snapshot.get('high_24h'))}</div></div>
      <div class="metric"><div class="muted">Минимум 24ч</div><div class="value">{fmt(snapshot.get('low_24h'))}</div></div>
      <div class="metric"><div class="muted">Обновлено</div><div class="value">{fmt(snapshot.get('fetched_at_utc'))}</div></div>
    </div>
    <p>Логика: Candle 2 / Candle 3 LuxAlgo, Reversal Filter 12, Wick Threshold 40%.</p>
    <p><a href="/api/snapshot">Открыть точный JSON</a> · <a href="/?refresh=1">Обновить принудительно</a></p>
    {stale_note}
  </div>
  {''.join(sections)}
</body>
</html>"""


@app.get("/")
def index() -> Response:
    force = request.args.get("refresh") == "1"
    try:
        snapshot = get_snapshot(force=force)
        return Response(render_page(snapshot), content_type="text/html; charset=utf-8")
    except Exception as exc:
        body = (
            "<h1>Ошибка получения данных</h1>"
            f"<pre>{html.escape(str(exc))}</pre>"
        )
        return Response(body, status=502, content_type="text/html; charset=utf-8")


@app.get("/api/snapshot")
def api_snapshot() -> Response:
    force = request.args.get("refresh") == "1"
    try:
        return jsonify(get_snapshot(force=force))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/health")
def health() -> Response:
    return jsonify({"ok": True, "service": "tao-mexc-live"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
