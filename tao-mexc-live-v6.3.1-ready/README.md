# MEXC trading scanner v6.3.1

Сканер получает закрытые свечи MEXC Futures и строит структурированные данные
для двухэтапного GPT Action:

1. `/api/scanner_action_v6` — общий shortlist.
2. `/api/snapshot_action_v6?symbol=HYPE_USDT` — подробная проверка кандидата.

Имена endpoint сохранены, поэтому существующий Action продолжает работать.

## Что изменено

- C2 требует sweep одной стороны C1 и close обратно строго внутри диапазона C1.
- Цвет C1 больше не является условием.
- Reversal Filter 12 применяется отдельно к bullish и bearish экстремумам.
- Двусторонняя C2 может вернуть две метки одного времени.
- Sweep с закрытием за противоположной стороной C1 вынесен в
  `SWEEP_PLUS_OPPOSITE_EXPANSION`, а не называется C2.
- Wick Threshold 40% выбирает основу EQ: rejection wick либо полный диапазон.
- C3 фиксируется по close за диапазоном C2; `eq_respected` хранится отдельно
  как качество.
- Добавлены фазы C2, C3, C4, C5 и предупреждение о свежей противоположной H1
  Closure.
- BTC остаётся в сканировании как `MARKET_CONTEXT`, но получает
  `eligible_trade_candidate: false`.
- Ответы двух GPT Action endpoint имеют жёсткую проверку лимита в 100 000
  символов. Нагрузочный тест требует запас не менее 20% и сохраняет все
  внутренние расчёты, сокращая только дубли и историю однотипных событий.

Это прозрачная реализация публичного описания LuxAlgo/TTrades, а не заявление
о побитовой идентичности закрытой части индикатора. API остаётся shortlist и
источником фактов; POI/FVG/OB и финальный вход проверяются отдельно.

## Проверка

```bash
python3 -m py_compile candle_closure.py api/*.py
python3 -m unittest discover -s tests -v
```

Плотная тестовая выборка даёт примерно 62 000 символов для общего scanner и
69 000 для подробного snapshot при официальном лимите GPT Actions менее
100 000 символов.

## Активные Vercel Functions

- `api/health.py`
- `api/scanner_v6.py`
- `api/scanner_action_v6.py`
- `api/snapshot_v6.py`
- `api/snapshot_action_v6.py`

Модуль `candle_closure.py` лежит в корне и не создаёт отдельную функцию.
Оригинальный репозиторий до правок сохранён в `rollback/`.
