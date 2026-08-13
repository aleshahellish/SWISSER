#!/usr/bin/env python3
"""Verify the user's two TAO LuxAlgo reference labels in production."""

from __future__ import annotations

import json
import sys
import urllib.request


DEFAULT_URL = (
    "https://tao-mexc-live.vercel.app/api/snapshot_v6?symbol=TAO_USDT"
)

EXPECTED = (
    {
        "timeframe": "4h",
        "time": 1_786_219_200,
        "event_type": "BOS",
        "direction": "BULLISH",
        "label_msk": "2026-08-08 23:00 MSK",
    },
    {
        "timeframe": "1h",
        "time": 1_786_492_800,
        "event_type": "CHOCH",
        "direction": "BULLISH",
        "label_msk": "2026-08-12 03:00 MSK",
    },
)


def load_snapshot(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TAO-LuxAlgo-Parity-Check/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def matching_events(snapshot: dict, expected: dict) -> list[dict]:
    timeframe = (snapshot.get("timeframes") or {}).get(
        expected["timeframe"],
        {},
    )
    structure = timeframe.get("luxalgo_structure") or {}
    matches = []

    for layer_name in ("internal", "swing"):
        layer = structure.get(layer_name) or {}
        for event in layer.get("recent_events") or []:
            if (
                event.get("time") == expected["time"]
                and event.get("event_type") == expected["event_type"]
                and event.get("direction") == expected["direction"]
            ):
                matches.append({"layer": layer_name, **event})

    return matches


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    snapshot = load_snapshot(url)
    failed = False

    for expected in EXPECTED:
        matches = matching_events(snapshot, expected)
        if matches:
            for match in matches:
                pivot = match.get("broken_pivot") or {}
                print(
                    "PASS",
                    expected["timeframe"],
                    expected["label_msk"],
                    match["layer"],
                    match["event_type"],
                    match["direction"],
                    f"level={pivot.get('level')}",
                )
        else:
            failed = True
            print(
                "FAIL",
                expected["timeframe"],
                expected["label_msk"],
                expected["event_type"],
                expected["direction"],
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
