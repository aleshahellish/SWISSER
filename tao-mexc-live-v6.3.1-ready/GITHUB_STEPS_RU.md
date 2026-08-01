# Обновление через GitHub

## Безопасный порядок

1. Распаковать архив с новой версией.
2. На GitHub сначала загрузить с заменой:
   - `candle_closure.py`;
   - четыре файла `api/*v6.py`;
   - `index.html`, `README.md`, `vercel.json`.
3. Дождаться успешного Deployment в Vercel.
4. Открыть:
   - `/api/health`;
   - `/api/scanner_action_v6?symbols=HYPE_USDT`;
   - `/api/snapshot_action_v6?symbol=HYPE_USDT`.
5. Только после успешной проверки удалить из папки `api` шесть legacy-файлов:
   - `scanner.py`;
   - `snapshot.py`;
   - `scanner_v4_1.py`;
   - `scanner_v5_1.py`;
   - `snapshot_v4_1.py`;
   - `snapshot_v5_1.py`.
6. Папку `api/__pycache__` тоже удалить: это локальный мусор, не исходный код.

Удаление делается после обновления, а не до него. Все старые файлы уже лежат
в резервном ZIP внутри `rollback/`, поэтому откат не потерян.

## Откат

Распаковать ZIP из `rollback/` и заменить содержимое репозитория исходной
версией. Endpoint-имена в v6.3.1 не менялись.
