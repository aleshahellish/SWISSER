# TAO MEXC Live

Готовая публичная веб-страница с текущими данными TAO_USDT Futures с MEXC.

## Страницы

- `/` — понятная HTML-страница.
- `/api/snapshot` — полный JSON для анализа.
- `/health` — проверка работы сервиса.

## Настройки

- Candle 2 / Candle 3 по предоставленному открытому Pine-коду LuxAlgo.
- Reversal Filter: ON.
- Filter Length: 12.
- Wick Threshold: 40%.
- Таймфреймы: 15m и 1h.
- API-ключи не используются.

## Локальный запуск

```bash
python -m pip install -r requirements.txt
python app.py
```

Затем открыть: http://127.0.0.1:8000

## Render

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn app:app
```

Health check:

```text
/health
```
