# -*- coding: utf-8 -*-
"""
Минимальный тест публичного MEXC Futures API.
Получает TAO_USDT: текущую цену и последние свечи 15m / 1h.
Никакие логины, пароли или API-ключи не нужны.
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Новый официальный домен MEXC Futures API с января 2026 года.
BASE_URL = "https://api.mexc.com"
SYMBOL = "TAO_USDT"
OUTPUT_FILE = Path(__file__).with_name("tao_snapshot.json")

INTERVALS = {
    "15m": ("Min15", 15 * 60),
    "1h": ("Min60", 60 * 60),
}

CANDLE_COUNT = 120
TIMEOUT_SECONDS = 20


def request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 TAO-MEXC-Test/2.0",
            "Accept": "application/json",
        },
    )

    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(
            request,
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
            f"MEXC вернул HTTP {exc.code}: {exc.reason}\n"
            f"URL: {url}\n"
            f"Ответ: {body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Не удалось подключиться к MEXC.\n"
            f"URL: {url}\n"
            f"Техническая причина: {exc.reason!r}\n"
            "Проверь интернет, VPN/прокси, антивирус и доступ к api.mexc.com."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            f"Истекло время ожидания ответа MEXC.\nURL: {url}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "MEXC вернул ответ, который не удалось прочитать как JSON.\n"
            f"URL: {url}\n"
            f"Начало ответа: {raw[:500]}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Неожиданный формат ответа MEXC.")

    if payload.get("success") is False:
        code = payload.get("code", "неизвестно")
        message = payload.get("message") or payload.get("msg") or "без описания"
        raise RuntimeError(f"Ошибка MEXC, код {code}: {message}")

    return payload


def unix_to_local_text(timestamp_seconds: int) -> str:
    dt = datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def fetch_ticker() -> dict[str, Any]:
    payload = request_json(
        "/api/v1/contract/ticker",
        {"symbol": SYMBOL},
    )
    data = payload.get("data")

    if isinstance(data, list):
        match = next((item for item in data if item.get("symbol") == SYMBOL), None)
        if match is None:
            raise RuntimeError(f"Контракт {SYMBOL} не найден в ответе ticker.")
        return match

    if not isinstance(data, dict):
        raise RuntimeError("Неожиданный формат ticker от MEXC.")

    return data


def fetch_candles(interval_api: str, seconds_per_candle: int) -> list[dict[str, Any]]:
    end = int(time.time())
    start = end - seconds_per_candle * (CANDLE_COUNT + 5)

    payload = request_json(
        f"/api/v1/contract/kline/{SYMBOL}",
        {
            "interval": interval_api,
            "start": start,
            "end": end,
        },
    )
    data = payload.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(f"Неожиданный формат свечей {interval_api}.")

    required = ("time", "open", "high", "low", "close", "vol")
    for key in required:
        if key not in data or not isinstance(data[key], list):
            raise RuntimeError(f"В ответе свечей {interval_api} нет массива {key!r}.")

    lengths = [len(data[key]) for key in required]
    if not lengths or min(lengths) == 0:
        raise RuntimeError(f"MEXC не вернул свечи {interval_api} для {SYMBOL}.")

    count = min(lengths)
    candles: list[dict[str, Any]] = []

    for i in range(count):
        candle_time = int(data["time"][i])
        candles.append(
            {
                "time": candle_time,
                "time_local": unix_to_local_text(candle_time),
                "open": float(data["open"][i]),
                "high": float(data["high"][i]),
                "low": float(data["low"][i]),
                "close": float(data["close"][i]),
                "volume": float(data["vol"][i]),
            }
        )

    candles.sort(key=lambda candle: candle["time"])
    return candles[-CANDLE_COUNT:]


def print_latest(label: str, candles: list[dict[str, Any]]) -> None:
    latest = candles[-1]
    previous = candles[-2] if len(candles) >= 2 else None

    print(f"\n{label}: получено свечей — {len(candles)}")
    print(f"Последняя свеча открыта: {latest['time_local']}")
    print(
        "O/H/L/C: "
        f"{latest['open']} / {latest['high']} / "
        f"{latest['low']} / {latest['close']}"
    )

    if previous:
        print(
            "Предыдущая свеча O/H/L/C: "
            f"{previous['open']} / {previous['high']} / "
            f"{previous['low']} / {previous['close']}"
        )


def main() -> int:
    print("=" * 62)
    print("Проверка публичных данных MEXC Futures — версия 2")
    print(f"Контракт: {SYMBOL}")
    print(f"API-домен: {BASE_URL}")
    print("API-ключ и вход в аккаунт не используются.")
    print("=" * 62)

    try:
        # Быстрая проверка нового API-домена.
        ping = request_json("/api/v1/contract/ping")
        print(f"Связь с MEXC установлена. Ping: {ping.get('data', 'OK')}")

        ticker = fetch_ticker()
        candles_by_interval: dict[str, list[dict[str, Any]]] = {}

        for label, (api_interval, seconds_per_candle) in INTERVALS.items():
            candles_by_interval[label] = fetch_candles(api_interval, seconds_per_candle)

        snapshot = {
            "source": "MEXC Futures public API",
            "api_base_url": BASE_URL,
            "symbol": SYMBOL,
            "fetched_at_unix": int(time.time()),
            "fetched_at_local": datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            ),
            "ticker": ticker,
            "candles": candles_by_interval,
        }

        OUTPUT_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"\nТекущая цена: {ticker.get('lastPrice', 'нет значения')}")
        print(f"Максимум за 24ч: {ticker.get('high24Price', 'нет значения')}")
        print(f"Минимум за 24ч: {ticker.get('lower24Price', 'нет значения')}")

        for label, candles in candles_by_interval.items():
            print_latest(label, candles)

        print("\nУСПЕХ: данные получены и сохранены в файл:")
        print(OUTPUT_FILE)
        print("\nСледующий этап — сравнить значения с TradingView.")
        return 0

    except Exception as exc:
        print("\nОШИБКА:")
        print(exc)
        print(
            "\nСкопируй весь текст ошибки и пришли его в чат — "
            "теперь программа показывает техническую причину подробнее."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
